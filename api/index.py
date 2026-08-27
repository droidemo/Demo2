"""
Telegram Flask webhook-бот для развертывания на Vercel как serverless-функция.
Каждый входящий POST-запрос от Telegram обрабатывается как отдельный вызов функции.
Никаких polling-циклов — чисто обработка через вебхуки.
"""

import os
import telebot
from flask import Flask, request, jsonify

# ---------------------------------------------------------------------------
# Инициализация приложения и бота
# ---------------------------------------------------------------------------
app = Flask(__name__)

# Токен берется из переменных окружения Vercel.
# Запасной вариант — пустая строка, чтобы модуль импортировался даже если
# окружение настроено криво (Vercel соберёт функцию, но обработчики упадут
# в рантайме, пока реальный токен не будет указан в
# Settings → Environment Variables).
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ---------------------------------------------------------------------------
# Обработчики бота — здесь пишется логика диалога
# ---------------------------------------------------------------------------

@bot.message_handler(commands=["start"])
def handle_start(message):
    """Приветствие при команде /start."""
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
    """Обработчик-заглушка: повторяет любой текст обратно."""
    bot.send_message(message.chat.id, f"Ты сказал: {message.text}")


# ---------------------------------------------------------------------------
# Flask-роуты
# ---------------------------------------------------------------------------

@app.route("/", methods=["POST"])
def webhook():
    """
    Сюда Telegram присылает обновления. Мы десериализуем JSON в объект
    Update из telebot, передаём его в диспетчер и сразу возвращаем 200 OK,
    чтобы Telegram не пытался повторить запрос.
    """
    if not request.is_json:
        return jsonify({"error": "expected application/json"}), 400

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "invalid JSON"}), 400

    # Оборачиваем сырой JSON в Update из telebot и запускаем обработку синхронно.
    # process_new_updates выполняет все подходящие обработчики inline,
    # поэтому к моменту return ответ уже отправлен в Telegram.
    update = telebot.types.Update.de_json(payload)
    try:
        bot.process_new_updates([update])
    except Exception as exc:
        # Пишем в логи Vercel, но всё равно возвращаем 200 для Telegram.
        # Если вернуть не 200, Telegram будет повторять тот же апдейт до 7 раз,
        # что почти никогда не нужно — пользователи получат дубли ответов.
        app.logger.error("Ошибка при обработке апдейта: %s", exc)
        return "OK", 200

    return "OK", 200


@app.route("/", methods=["GET"])
def health():
    """Простая проверка живости — можно дёрнуть в браузере и убедиться, что бот жив."""
    return jsonify({"status": "running", "bot_configured": bool(BOT_TOKEN)}), 200


# ---------------------------------------------------------------------------
# Локальный запуск — Vercel в serverless-режиме это игнорирует.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Только для локальной разработки. В проде Vercel импортирует `app` напрямую.
    # Чтобы запустить локально: задай переменную окружения BOT_TOKEN,
    # выполни `python api/index.py`, затем туннель через ngrok или `vercel dev`.
    if BOT_TOKEN:
        bot.remove_webhook()  # сбрасываем старый вебхук перед локальным поллингом
        bot.infinity_polling()
    else:
        app.run(host="0.0.0.0", port=3000, debug=True)
