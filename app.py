from telebot import TeleBot, types
import logging
import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "<YOUR_BOT_TOKEN>")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://your-domain/app")

bot = TeleBot(BOT_TOKEN)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@bot.message_handler(commands=['start', 'help'])
def handle_start_help(message):
    try:
        user_id = message.from_user.id
        logger.info(f"Received /start from {user_id}")

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
            "Thanks for using our bot 😊"
        )

        bot.send_message(
            message.chat.id,
            text,
            parse_mode='HTML',
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"/start error: {e}")
        bot.send_message(message.chat.id, "⚠️ Something went wrong. Please try again later.")

print("Bot running…")
bot.infinity_polling()
