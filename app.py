from telebot import TeleBot, types
import os

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ["WEBAPP_URL"]

bot = TeleBot(BOT_TOKEN)


@bot.message_handler(commands=['start'])
def start(message):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "📱 Open WebApp",
            web_app=types.WebAppInfo(url=WEBAPP_URL)
        )
    )

    bot.send_message(
        message.chat.id,
        "<b>Bot is working 👍</b>",
        parse_mode="HTML",
        reply_markup=kb
    )


print("🤖 Bot running…")
bot.infinity_polling(timeout=60, long_polling_timeout=60)
