import os
from flask import Flask, request
from telebot import TeleBot, types
import logging

BOT_TOKEN = os.environ.get("BOT_TOKEN", "<YOUR_BOT_TOKEN>")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://your-domain/app")

bot = TeleBot(BOT_TOKEN)
app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@bot.message_handler(commands=['start', 'help'])
def handle_start_help(message):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(
            text="📱 Open WebApp",
            web_app=types.WebAppInfo(url=WEBAPP_URL)
        )
    )

    text = (
        "<b>🔐 Telegram Service</b>\n\n"
        "Click the button below to open the WebApp.\n\n"
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


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        json_str = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return 'OK', 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return 'ERROR', 500


@app.route('/')
def home():
    return "Bot is running"


if __name__ == '__main__':
    bot.remove_webhook()
    bot.set_webhook(url="https://your-domain/webhook")
    app.run(host='0.0.0.0', port=8000)
