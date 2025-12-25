import os
from flask import Flask, request
from telebot import TeleBot, types
import logging

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBAPP_URL = os.environ.get("WEBAPP_URL")  # e.g. https://your-domain/app
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # e.g. https://your-domain/webhook
PORT = int(os.environ.get("PORT", 10000))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN env variable is required")

if not WEBAPP_URL:
    raise ValueError("WEBAPP_URL env variable is required")

if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL env variable is required")

# ================= INIT =================
bot = TeleBot(BOT_TOKEN)
app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")


# ================= HANDLERS =================
@bot.message_handler(commands=['start', 'help'])
def start_handler(message):
    try:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                text="📱 Open WebApp",
                web_app=types.WebAppInfo(url=WEBAPP_URL)
            )
        )

        text = (
            "<b>🔐 Telegram Service</b>\n\n"
            "Welcome! Click the button below to open the WebApp.\n\n"
            "<b>How it works:</b>\n"
            "1️⃣ Open the WebApp\n"
            "2️⃣ Follow the instructions\n\n"
            "Thank you 😊"
        )

        bot.send_message(
            message.chat.id,
            text,
            parse_mode='HTML',
            reply_markup=keyboard
        )

        logger.info(f"/start sent to {message.from_user.id}")

    except Exception as e:
        logger.error(f"/start error: {e}")
        bot.send_message(message.chat.id, "⚠️ Something went wrong. Please try again later.")


# ================= WEBHOOK ENDPOINT =================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_data().decode('utf-8')
        bot.process_new_updates([types.Update.de_json(data)])
        return 'OK', 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return 'ERROR', 500


# Optional: simple home page
@app.route('/')
def home():
    return "Bot is running"


# ================= MAIN =================
if __name__ == '__main__':
    # Remove existing webhook & set new one
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)

    logger.info("Webhook set to: " + WEBHOOK_URL)
    logger.info("WebApp URL: " + WEBAPP_URL)

    app.run(host='0.0.0.0', port=PORT)
