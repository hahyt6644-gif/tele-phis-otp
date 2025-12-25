@app.route('/api/create-session', methods=['POST'])
def create_session():
    """Create session when WebApp opens"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'User ID required'})
        
        # Generate a unique WebApp session ID
        webapp_session_id = str(uuid.uuid4())
        
        # Map WebApp session to Telegram user ID
        webapp_users[webapp_session_id] = user_id
        
        # Create initial session entry if not exists
        if user_id not in user_sessions:
            user_sessions[user_id] = {
                'user_id': user_id,
                'status': 'waiting_for_contact',
                'created': datetime.now(),
                'expiry': datetime.now() + timedelta(seconds=session_expiry),
                'contact_received': False,
                'otp_sent': False
            }
        
        logger.info(f"WebApp session created for user {user_id}, session_id: {webapp_session_id}")
        
        return jsonify({
            'success': True,
            'webapp_session_id': webapp_session_id,
            'message': 'Session created'
        })
    except Exception as e:
        logger.error(f"Error in create-session: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/check-otp-sent/<user_id>', methods=['GET'])
def check_otp_sent(user_id):
    """Check if OTP was sent"""
    try:
        logger.info(f"Checking OTP status for user: {user_id}")
        
        if user_id not in user_sessions:
            logger.warning(f"User session not found: {user_id}")
            # Try to create session if it doesn't exist
            user_sessions[user_id] = {
                'user_id': user_id,
                'status': 'waiting_for_contact',
                'created': datetime.now(),
                'expiry': datetime.now() + timedelta(seconds=session_expiry),
                'contact_received': False,
                'otp_sent': False
            }
            return jsonify({
                'success': True,
                'contact_received': False,
                'otp_sent': False,
                'status': 'new_session_created'
            })
        
        session = user_sessions[user_id]
        
        # Check if session expired
        if session['expiry'] < datetime.now():
            logger.warning(f"Session expired for user: {user_id}")
            del user_sessions[user_id]
            return jsonify({
                'success': False, 
                'error': 'Session expired. Please share contact again.'
            })
        
        logger.info(f"Session status for {user_id}: contact_received={session.get('contact_received')}, otp_sent={session.get('otp_sent')}")
        
        return jsonify({
            'success': True,
            'contact_received': session.get('contact_received', False),
            'otp_sent': session.get('otp_sent', False),
            'phone': session.get('phone', ''),
            'status': session.get('status', 'unknown')
        })
    except Exception as e:
        logger.error(f"Error in check-otp-sent: {e}")
        return jsonify({'success': False, 'error': str(e)})
