"""
Telegram bot with NOWPayments crypto payments + Supabase logging.
Flask + Vercel serverless. Threaded OFF — fully synchronous.
"""

import os
import time
import hmac
import hashlib
import json
import logging
import requests
import telebot
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ===========================================================================
# CONFIGURATION
# ===========================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = 7480818625

NOWPAYMENTS_API_KEY = os.environ.get("NOWPAYMENTS_API_KEY", "")
NOWPAYMENTS_IPN_KEY = os.environ.get("NOWPAYMENTS_IPN_KEY", "")

WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL", "")

# Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# Product
PRODUCT_NAME = os.environ.get("PRODUCT_NAME", "Premium Access")
PRODUCT_PRICE = os.environ.get("PRODUCT_PRICE", "5.00") # Цена в USD
PRODUCT_DELIVERY = os.environ.get("PRODUCT_DELIVERY",
    "Thank you for your purchase! 🎉\n"
    "Here is your product: <code>XXXX-XXXX-XXXX</code>\n"
    "(replace this text with your actual delivery — key, link, file, etc.)"
)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
bot.threaded = False

# ===========================================================================
# SUPABASE: LOG PAYMENT TO QUEUE
# ===========================================================================
def write_to_payout_queue(payment_data: dict):
    """Записывает платеж в Supabase таблицу payout_queue."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("Supabase not configured — skipping DB write")
        return False

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

    record = {
        "order_id": payment_data.get("order_id"),
        "amount_usd": payment_data.get("price_amount"),
        "amount_crypto": payment_data.get("pay_amount"),
        "currency": "usd",
        "payer_currency": payment_data.get("pay_currency"),
        "txid": payment_data.get("payin_hash"),
        "status": "pending_payout",
        "processed": False,
        "created_at": int(time.time())
    }

    try:
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/payout_queue",
            json=record,
            headers=headers,
            timeout=10
        )
        resp.raise_for_status()
        logger.info("Payment logged to Supabase: %s", payment_data.get("order_id"))
        return True
    except requests.RequestException as e:
        logger.error("Supabase insert error: %s", e)
        return False

# ===========================================================================
# NOWPAYMENTS: INVOICE CREATION
# ===========================================================================
def create_invoice(amount: str, order_id: str) -> dict | None:
    url = "https://api.nowpayments.io/v1/invoice"
    payload = {
        "price_amount": amount,
        "price_currency": "usd",
        "pay_currency": "usdttrc20",
        "order_id": order_id,
        "order_description": PRODUCT_NAME,
        "ipn_callback_url": f"{WEBHOOK_BASE_URL}/payment-webhook",
        "success_url": "https://t.me/",
        "cancel_url": "https://t.me/"
    }
    headers = {
        "x-api-key": NOWPAYMENTS_API_KEY,
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        # Если код не 200, выводим тело ошибки в логи Vercel!
        if resp.status_code != 200:
            logger.error("NOWPayments API error %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error("NOWPayments request failed: %s", e)
        return None

# ===========================================================================
# BOT HANDLERS
# ===========================================================================
@bot.message_handler(commands=["start"])
def handle_start(message):
    bot.send_message(
        message.chat.id,
        f"Hey {message.from_user.first_name}! ⚡\n\n"
        f"📦 Product: <b>{PRODUCT_NAME}</b>\n"
        f"💵 Price: <b>${PRODUCT_PRICE}</b>\n\n"
        f"Tap /pay to pay with crypto."
    )

@bot.message_handler(commands=["pay"])
def handle_pay(message):
    chat_id = message.chat.id
    order_id = f"order_{chat_id}_{int(time.time())}"

    bot.send_message(chat_id, "⏳ Creating invoice...")

    result = create_invoice(PRODUCT_PRICE, order_id)

    if not result or not result.get("invoice_url"):
        bot.send_message(chat_id, "❌ Failed to create invoice. Try again later.")
        return

    payment_url = result["invoice_url"]
    keyboard = telebot.types.InlineKeyboardMarkup()
    keyboard.add(
        telebot.types.InlineKeyboardButton(
            text=f"💎 Pay ${PRODUCT_PRICE} (USDT TRC20)",
            url=payment_url
        )
    )

    bot.send_message(
        chat_id,
        f"🧾 <b>Invoice created!</b>\n\n"
        f"📦 Product: <b>{PRODUCT_NAME}</b>\n"
        f"💵 Amount: <b>${PRODUCT_PRICE}</b>\n"
        f"🔖 Order: <code>{order_id}</code>\n\n"
        f"Tap the button below to proceed.\n"
        f"Your product will be delivered here automatically after payment.",
        reply_markup=keyboard
    )

# ===========================================================================
# TELEGRAM WEBHOOK
# ===========================================================================
@app.route("/", methods=["POST"])
def telegram_webhook():
    if not request.is_json:
        return jsonify({"error": "expected application/json"}), 400

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "invalid JSON"}), 400

    update = telebot.types.Update.de_json(payload)
    try:
        bot.process_new_updates([update])
    except Exception as exc:
        logger.exception("Error processing update: %s", exc)
        return "OK", 200

    return "OK", 200

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "running",
        "bot_configured": bool(BOT_TOKEN),
        "nowpayments_configured": bool(NOWPAYMENTS_API_KEY),
        "supabase_configured": bool(SUPABASE_URL and SUPABASE_KEY),
    }), 200

# ===========================================================================
# NOWPAYMENTS WEBHOOK
# ===========================================================================
@app.route("/payment-webhook", methods=["POST"])
def payment_webhook():
    if not request.is_json:
        return jsonify({"error": "expected application/json"}), 400

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "invalid JSON"}), 400

    # --- Проверка подписи NOWPayments ---
    sig_header = request.headers.get("x-nowpayments-sig", "")
    if not sig_header:
        return jsonify({"error": "no signature"}), 403

    mac, sorted_data = sig_header.split(":", 1)
    expected_mac = hmac.new(NOWPAYMENTS_IPN_KEY.encode('utf-8'), sorted_data.encode('utf-8'), hashlib.sha512).hexdigest()

    if not hmac.compare_digest(expected_mac, mac):
        logger.warning("Invalid NOWPayments signature")
        return jsonify({"error": "invalid sign"}), 403

    # --- Обработка платежа ---
    payment_status = payload.get("payment_status", "")
    order_id = payload.get("order_id", "")
    price_amount = payload.get("price_amount", "")
    pay_amount = payload.get("pay_amount", "")
    pay_currency = payload.get("pay_currency", "")
    txid = payload.get("payin_hash", "N/A")

    logger.info("NOWPayments: order=%s status=%s amount=%s %s", order_id, payment_status, pay_amount, pay_currency)

    # Статус finished означает, что деньги подтверждены
    if payment_status in ("finished", "confirmed"):
        customer_chat_id = None
        parts = order_id.split("_")
        if len(parts) >= 3:
            try:
                customer_chat_id = int(parts[1])
            except ValueError:
                pass

        # 1. Запись в Supabase
        payment_record = {
            "order_id": order_id,
            "price_amount": price_amount,
            "pay_amount": pay_amount,
            "pay_currency": pay_currency,
            "payin_hash": txid,
        }
        write_to_payout_queue(payment_record)

        # 2. Уведомление админу
        admin_text = (
            "💰 <b>New payment received!</b>\n\n"
            f"📦 Order: <code>{order_id}</code>\n"
            f"💵 Amount: <b>{price_amount} USD</b>\n"
            f"💱 Paid in: {pay_amount} {pay_currency}\n"
            f"✅ Status: <b>PAID</b>\n"
            f"🔗 TX: <code>{txid}</code>\n"
        )
        if customer_chat_id:
            admin_text += f"👤 Chat ID: <code>{customer_chat_id}</code>\n"
            admin_text += f"⏳ <i>Logged to Supabase. Render worker will process it.</i>\n"

        try:
            bot.send_message(ADMIN_CHAT_ID, admin_text)
        except Exception as e:
            logger.error("Failed to notify admin: %s", e)

        # 3. Выдача товара
        if customer_chat_id:
            try:
                bot.send_message(
                    customer_chat_id,
                    f"✅ <b>Payment received!</b>\n\n"
                    f"📦 Product: <b>{PRODUCT_NAME}</b>\n\n"
                    f"{PRODUCT_DELIVERY}"
                )
                logger.info("Product delivered: chat_id=%s", customer_chat_id)
            except Exception as e:
                logger.error("Failed to deliver product: %s", e)

    elif payment_status in ("failed", "expired"):
        try:
            bot.send_message(
                ADMIN_CHAT_ID,
                f"❌ <b>Payment failed</b>\n"
                f"Order: <code>{order_id}</code>\n"
                f"Status: {payment_status}"
            )
        except:
            pass

    return jsonify({"status": "ok"}), 200

@app.route("/payment-webhook", methods=["GET"])
def payment_webhook_check():
    return jsonify({
        "status": "ok",
        "message": "NOWPayments webhook endpoint is active",
        "nowpayments_configured": bool(NOWPAYMENTS_API_KEY),
    }), 200

if __name__ == "__main__":
    if BOT_TOKEN:
        bot.remove_webhook()
        bot.infinity_polling()
    else:
        app.run(host="0.0.0.0", port=3000, debug=True)
