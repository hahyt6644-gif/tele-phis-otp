import os
import telebot
from telebot import types
from flask import Flask, render_template, request, jsonify
import logging
from datetime import datetime
import threading
import time
import json
import random

# ==================== SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# BOT TOKEN - USE YOUR ACTUAL TOKEN
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://itz-me-545-telegram.onrender.com")

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# Get bot info
try:
    bot_info = bot.get_me()
    BOT_USERNAME = bot_info.username
    logger.info(f"🤖 Bot: @{BOT_USERNAME}")
except:
    BOT_USERNAME = "your_bot"

# Simple session storage
user_sessions = {}

# ==================== SESSION FUNCTIONS ====================
def create_session(user_id):
    """Create a new session"""
    user_sessions[user_id] = {
        'user_id': user_id,
        'created': datetime.now(),
        'phone': None,
        'otp_code': None,
        'otp_sent': False,
        'verified': False
    }
    return user_sessions[user_id]

def get_session(user_id):
    """Get session if exists"""
    if user_id in user_sessions:
        # Check if expired (10 minutes)
        session = user_sessions[user_id]
        time_diff = (datetime.now() - session['created']).total_seconds()
        if time_diff < 600:  # 10 minutes
            return session
        else:
            del user_sessions[user_id]
    return None

def get_or_create_session(user_id):
    """Get or create session"""
    session = get_session(user_id)
    if not session:
        session = create_session(user_id)
    return session

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    """Main page"""
    user_id = request.args.get('user_id', '')
    return render_template('index.html', 
                          user_id=user_id, 
                          bot_username=BOT_USERNAME)

@app.route('/otp')
def otp_page():
    """OTP page"""
    user_id = request.args.get('user_id', '')
    
    if not user_id:
        return render_template('error.html', 
                             error="No user ID", 
                             message="Open from bot",
                             bot_username=BOT_USERNAME)
    
    session = get_session(user_id)
    phone = session.get('phone', 'Unknown') if session else 'Unknown'
    
    return render_template('otp.html',
                         user_id=user_id,
                         phone=phone,
                         bot_username=BOT_USERNAME)

@app.route('/api/start-verification', methods=['POST'])
def start_verification():
    """Start verification API"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'No user ID'})
        
        # Create session if doesn't exist
        session = get_or_create_session(user_id)
        
        return jsonify({
            'success': True,
            'message': '✅ Verification started!',
            'instructions': 'Go back to Telegram and share your contact'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    """Verify OTP"""
    try:
        data = request.json
        user_id = data.get('user_id')
        otp = data.get('otp', '')
        
        if not user_id or not otp:
            return jsonify({'success': False, 'error': 'Missing data'})
        
        session = get_session(user_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        # Check OTP
        if session.get('otp_code') == otp:
            session['verified'] = True
            return jsonify({'success': True, 'message': '✅ Verified!'})
        else:
            return jsonify({'success': False, 'error': 'Wrong OTP'})
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    """Handle /start - SIMPLE AND RELIABLE"""
    try:
        user_id = str(message.from_user.id)
        chat_id = message.chat.id
        first_name = message.from_user.first_name or "User"
        
        logger.info(f"✅ /start from {user_id}")
        
        # Create session
        session = create_session(user_id)
        
        # WebApp URL
        webapp_url = f"{WEBAPP_URL}/?user_id={user_id}"
        
        # Create button
        keyboard = types.InlineKeyboardMarkup()
        button = types.InlineKeyboardButton(
            text="📱 Open WebApp",
            web_app=types.WebAppInfo(url=webapp_url)
        )
        keyboard.add(button)
        
        # Simple message
        bot.send_message(
            chat_id,
            f"Hello {first_name}! 👋\n\nClick below to verify:",
            reply_markup=keyboard
        )
        
        logger.info(f"📱 WebApp sent to {user_id}")
        
    except Exception as e:
        logger.error(f"Start error: {e}")
        try:
            bot.reply_to(message, "Error. Try again.")
        except:
            pass

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle contact sharing - SIMPLE AND RELIABLE"""
    try:
        user_id = str(message.from_user.id)
        chat_id = message.chat.id
        
        logger.info(f"📞 Contact from {user_id}")
        
        # Get contact
        contact = message.contact
        if not contact:
            bot.send_message(chat_id, "❌ No contact found")
            return
        
        phone = contact.phone_number
        first_name = contact.first_name or ""
        
        # Create/update session
        session = get_or_create_session(user_id)
        session['phone'] = phone
        
        # Generate OTP
        otp_code = str(random.randint(10000, 99999))
        session['otp_code'] = otp_code
        session['otp_sent'] = True
        
        logger.info(f"✅ OTP {otp_code} for {user_id}")
        
        # Send OTP message
        webapp_url = f"{WEBAPP_URL}/otp?user_id={user_id}"
        
        bot.send_message(
            chat_id,
            f"""✅ Contact received!

📱 Phone: {phone}
👤 Name: {first_name}

🔢 **OTP Code:** `{otp_code}`

Click to verify: {webapp_url}

Or send /start to restart.""",
            parse_mode='Markdown'
        )
        
        # Try to delete contact message
        try:
            bot.delete_message(chat_id, message.message_id)
        except:
            pass
            
    except Exception as e:
        logger.error(f"Contact error: {e}")
        try:
            bot.send_message(message.chat.id, "❌ Error. Try again.")
        except:
            pass

@bot.message_handler(func=lambda message: True)
def handle_all(message):
    """Handle other messages"""
    user_id = str(message.from_user.id)
    logger.info(f"💬 Message from {user_id}")
    
    bot.send_message(
        message.chat.id,
        "Send /start to begin verification\nOr share your contact to continue.",
        reply_markup=types.ReplyKeyboardRemove()
    )

# ==================== SIMPLE BOT POLLING ====================
def run_bot():
    """Simple bot polling that works"""
    logger.info("🚀 Starting bot...")
    
    while True:
        try:
            logger.info("🔄 Bot polling started")
            bot.polling(none_stop=True, interval=2, timeout=30)
        except Exception as e:
            logger.error(f"❌ Bot error: {e}")
            logger.info("🔄 Restarting in 3 seconds...")
            time.sleep(3)

# ==================== MAIN ====================
if __name__ == '__main__':
    logger.info("="*50)
    logger.info("🚀 TELEGRAM VERIFICATION BOT")
    logger.info(f"🤖 Bot: @{BOT_USERNAME}")
    logger.info(f"🌐 Web: {WEBAPP_URL}")
    logger.info("="*50)
    
    # Start bot in thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Run Flask
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Flask on port {port}")
    
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
