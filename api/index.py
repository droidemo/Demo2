"""
Telegram Flask webhook-бот для Vercel serverless.
Фикс: отключено threading, обработчики выполняются синхронно,
пока send_message не дойдёт до Telegram, 200 не возвращается.
"""

import os
import logging
import telebot
from flask import Flask, request, jsonify

# ---------------------------------------------------------------------------
# Логирование — чтобы в логах Vercel видеть реальные ошибки, а не пустоту
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# КЛЮЧЕВОЙ ФИКС: отключаем потоки. В serverless каждый обработчик
# должен выполниться синхронно внутри вызова функции, иначе Vercel
# убьёт инстанс до того, как bot.send_message дойдёт до Telegram API.
bot.threaded = False

# ---------------------------------------------------------------------------
# Обработчики бота
# ---------------------------------------------------------------------------

@bot.message_handler(commands=["start"])
def handle_start(message):
    bot.send_message(
        message.chat.id,
        f"Привет, {message.from_user.first_name}! ⚡\n"
        "Я работаю на Vercel serverless через Flask вебхуки.\n"
        "Пришли мне любой текст, и я его повторю."
    )


@bot.message_handler(commands=["help"])
def handle_help(message):
    bot.send_message(
        message.chat.id,
        "Доступные команды:\n"
        "/start — приветствие\n"
        "/help — эта справка\n"
        "Любой текст — я его повторю."
    )


@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    bot.send_message(message.chat.id, f"Ты сказал: {message.text}")


# ---------------------------------------------------------------------------
# Flask-роуты
# ---------------------------------------------------------------------------

@app.route("/", methods=["POST"])
def webhook():
    if not request.is_json:
        return jsonify({"error": "expected application/json"}), 400

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "invalid JSON"}), 400

    update = telebot.types.Update.de_json(payload)
    try:
        # С threaded=False это блокирующий вызов. Он не вернётся,
        # пока обработчик полностью не отработает, включая bot.send_message.
        bot.process_new_updates([update])
    except Exception as exc:
        # Логируем полный traceback в логи Vercel, чтобы видеть реально что падает.
        # 200 всё равно возвращаем — иначе Телеграм будет повторять апдейт 7 раз.
        logger.exception("Ошибка при обработке апдейта: %s", exc)
        return "OK", 200

    return "OK", 200


@app.route("/", methods=["GET"])
def health():
    """Проверка живости + видно, задан ли токен."""
    return jsonify({
        "status": "running",
        "bot_configured": bool(BOT_TOKEN),
        "threading_disabled": not bot.threaded
    }), 200


@app.route("/debug", methods=["GET"])
def debug():
    """
    Дёргает getMe у Telegram, чтобы проверить, реально ли токен работает
    в текущем окружении Vercel. Если вернулся 200 с данными бота — всё ок.
    """
    if not BOT_TOKEN:
        return jsonify({"error": "BOT_TOKEN не задан в env"}), 500
    try:
        me = bot.get_me()
        return jsonify({
            "ok": True,
            "bot_username": me.username,
            "bot_first_name": me.first_name
        }), 200
    except Exception as exc:
        logger.exception("getMe упал: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Локальный запуск
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if BOT_TOKEN:
        bot.remove_webhook()
        bot.infinity_polling()
    else:
        app.run(host="0.0.0.0", port=3000, debug=True)
