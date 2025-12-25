import os
from flask import Flask, request
from telebot import TeleBot, types
import logging

# ───────── CONFIG (ENV VARS) ─────────
BOT_TOKEN = os.environ["BOT_TOKEN"]              # required
WEBAPP_URL = os.environ["WEBAPP_URL"]            # required
WEBHOOK_URL = os.environ["WEBHOOK_URL"]          # required  → https://domain/webhook
PORT = int(os.environ.get("PORT", 10000))

bot = TeleBot(BOT_TOKEN)
app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

# ───────── HANDLERS ─────────
@bot.message_handler(commands=['start', 'help'])
def start_cmd(message):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "📱 Open WebApp",
            web_app=types.WebAppInfo(url=WEBAPP_URL)
        )
    )

    bot.send_message(
        message.chat.id,
        "<b>Bot is working 👍</b>\nClick the button below 👇",
        parse_mode="HTML",
        reply_markup=kb
    )

# ───────── WEBHOOK ENDPOINT ─────────
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_data().decode("utf-8")
        bot.process_new_updates([types.Update.de_json(data)])
        return "OK", 200
    except Exception as e:
        log.error(f"Webhook error: {e}")
        return "ERROR", 500

# Optional test page
@app.route("/")
def home():
    return "Bot online 😎"

# ───────── STARTUP ─────────
if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)

    log.info(f"Webhook set → {WEBHOOK_URL}")
    log.info(f"WebApp URL → {WEBAPP_URL}")

    app.run(host="0.0.0.0", port=PORT)
