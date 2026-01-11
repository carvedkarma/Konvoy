import eventlet

eventlet.monkey_patch()

import os
import sys

try:
    from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_from_directory
    from flask_login import LoginManager, login_user, logout_user, login_required, current_user
    from flask_socketio import SocketIO, emit, join_room, leave_room
    from datetime import datetime, timedelta
    from werkzeug.utils import secure_filename
    from objects.uberDev import vehicleDetails, appLaunch, driverLocation, updateLocationOnce, flightArrivals, parseFlightsByHour, uberRidersNearby, fetch_all_perth_drivers
    import config
    import cache
    from models import db, User, Role, ChatMessage, PushSubscription, PageVisit, create_default_roles, encrypt_data, decrypt_data
    from forms import LoginForm, RegisterForm, RoleForm, ProfileForm, ChangePasswordForm, ForgotPasswordForm, ResetPasswordForm, UberConnectForm, UberDisconnectForm, EmptyForm
    from pywebpush import webpush, WebPushException
    import secrets
    import json
    import os
    print("All imports successful", flush=True)
except Exception as e:
    print(f"Import error: {e}", flush=True)
    sys.exit(1)

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
UPLOAD_FOLDER = os.path.join('static', 'uploads', 'profile_images')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024  # 2MB max-limit


flask_secret = os.environ.get("FLASK_SECRET_KEY") or os.environ.get(
    "SESSION_SECRET")
if not flask_secret:
    import secrets
    flask_secret = secrets.token_hex(32)
    print(
        "WARNING: FLASK_SECRET_KEY not set. Using generated key for this session.",
        flush=True)

app.secret_key = flask_secret
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
app.config['REMEMBER_COOKIE_SECURE'] = False
app.config['REMEMBER_COOKIE_HTTPONLY'] = True


def get_database_url():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url and os.path.exists('/tmp/replitdb'):
        with open('/tmp/replitdb', 'r') as f:
            db_url = f.read().strip()
    if db_url:
        if db_url.startswith('https://'):
            db_url = db_url.replace('https://', 'postgresql://', 1)
        elif db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
    return db_url


database_url = get_database_url()
if not database_url:
    print("ERROR: DATABASE_URL is not set. Please configure your database.",
          flush=True)
    sys.exit(1)
else:
    print(
        f"Database configured: {'production (neon)' if 'neon' in database_url else 'development'}",
        flush=True)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
    "connect_args": {
        "connect_timeout": 10
    }
}

db.init_app(app)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
online_users = {}
active_users = {}  # Track all users active on any page

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith('/api/'):
        return jsonify(success=False, message='Authentication required'), 401
    flash('Please log in to access this page.', 'info')
    return redirect(url_for('login'))


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


try:
    with app.app_context():
        print("Initializing database...", flush=True)
        db.create_all()
        create_default_roles()

        users_without_roles = User.query.filter(~User.roles.any()).all()
        for user in users_without_roles:
            if user.role_id:
                custom_role = Role.query.get(user.role_id)
                if custom_role:
                    user.roles.append(custom_role)
                    user.role_id = None

            system_role = Role.query.filter_by(name=user.role).first()
            if system_role and system_role not in user.roles:
                user.roles.append(system_role)

        if users_without_roles:
            db.session.commit()
            print(
                f"Migrated {len(users_without_roles)} users to multi-role system.",
                flush=True)

        owner_email = os.environ.get("RIZTAR_OWNER_EMAIL")
        owner_password = os.environ.get("RIZTAR_OWNER_PASSWORD")

        if owner_email and owner_password:
            owner = User.query.filter_by(email=owner_email).first()
            if not owner:
                owner_role = Role.query.filter_by(name='owner').first()
                owner = User(email=owner_email,
                             username=owner_email.split('@')[0],
                             role='owner')
                owner.set_password(owner_password)
                if owner_role:
                    owner.roles.append(owner_role)
                db.session.add(owner)
                db.session.commit()
                print("Owner account configured from environment variables.",
                      flush=True)

        print("Database initialization complete.", flush=True)
except Exception as e:
    print(f"Database initialization error: {e}", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)

stop_signal = 0
stored_destination = None


@app.route('/')
def root():
    if current_user.is_authenticated:
        disconnect_form = UberDisconnectForm(
        ) if current_user.uber_connected else None
        return render_template('home.html',
                               loading=current_user.uber_connected,
                               vehicles=[],
                               driver_info=None,
                               disconnect_form=disconnect_form)
    return redirect(url_for('landing'))

@app.route('/welcome')
def landing():
    if current_user.is_authenticated:
        return redirect(url_for('root'))
    
    # Track page visit
    try:
        visit = PageVisit(
            page='welcome',
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string[:500] if request.user_agent.string else None,
            referrer=request.referrer[:500] if request.referrer else None
        )
        db.session.add(visit)
        db.session.commit()
    except Exception as e:
        pass
    
    return render_template('landing.html')


@app.route('/api/home-data')
@login_required
def home_data():
    vehicles = []
    driver_info = None
    default_vehicle = None
    active_ride = None
    driver_status = None

    if current_user.uber_connected:
        try:
            cookies, headers, refresh_token = current_user.get_uber_credentials(
            )
        except Exception as e:
            print(f"Error getting credentials: {e}")
            return jsonify(success=True,
                           vehicles=[],
                           driver_info=None,
                           default_vehicle=None,
                           active_ride=None,
                           driver_status=None)

        cached_vehicles = cache.get_cached(current_user.id, 'vehicles')
        cached_driver = cache.get_cached(current_user.id, 'driver_info')
        cached_ride = cache.get_cached(current_user.id, 'active_ride')

        user_display_name = current_user.get_display_name()

        def fetch_vehicles():
            try:
                return vehicleDetails(cookies, headers, refresh_token)
            except Exception as e:
                print(f"Error fetching vehicles: {e}")
                return []

        def fetch_driver_info():
            return {'name': user_display_name, 'photo': None}

        def fetch_ride():
            try:
                ride_data = appLaunch(cookies, headers, refresh_token)
                if ride_data:
                    # appLaunch returns [0, data] when no ride, or dict when ride exists
                    if isinstance(ride_data, list) and len(ride_data) >= 2:
                        return ride_data[1]  # Return the data dict
                    elif isinstance(ride_data, dict):
                        return ride_data
                return None
            except Exception as e:
                print(f"Error fetching ride: {e}")
                return None

        if cached_vehicles is not None and cached_driver is not None and cached_ride is not None:
            vehicles = cached_vehicles
            driver_info = cached_driver
            full_ride_data = cached_ride
        else:
            tasks = {}
            if cached_vehicles is None:
                tasks['vehicles'] = eventlet.spawn(fetch_vehicles)
            if cached_driver is None:
                tasks['driver'] = eventlet.spawn(fetch_driver_info)
            if cached_ride is None:
                tasks['ride'] = eventlet.spawn(fetch_ride)

            if 'vehicles' in tasks:
                vehicles = tasks['vehicles'].wait()
                cache.set_cached(current_user.id, 'vehicles', vehicles)
            else:
                vehicles = cached_vehicles

            if 'driver' in tasks:
                driver_info = tasks['driver'].wait()
                cache.set_cached(current_user.id, 'driver_info', driver_info)
            else:
                driver_info = cached_driver

            if 'ride' in tasks:
                full_ride_data = tasks['ride'].wait()
                cache.set_cached(current_user.id, 'active_ride',
                                 full_ride_data)
            else:
                full_ride_data = cached_ride

        driver_status = None
        active_ride = None
        
        if full_ride_data and isinstance(full_ride_data, dict):
            # Check if this is already processed ride data (has full_name key)
            if 'full_name' in full_ride_data:
                # This is already a processed ride - use it directly
                active_ride = full_ride_data
            elif 'driverTasks' in full_ride_data:
                # This is raw API response - extract driver status
                driver_tasks = full_ride_data.get('driverTasks', {})
                driver_state = driver_tasks.get('driverState', {})
                task_scopes = driver_tasks.get('taskScopes', [])
                
                driver_status = {
                    'online': bool(driver_state.get('online', False)),
                    'available': bool(driver_state.get('available', False)),
                    'dispatchable': bool(driver_state.get('dispatchable', False)),
                    'onboarding_status': full_ride_data.get('driverOnboardingStatus', 'UNKNOWN')
                }
                
                # No active ride in raw response (taskScopes would be empty if no ride)
                active_ride = None

        for v in vehicles:
            if v.get('isDefault'):
                default_vehicle = v
                break

        nearby_data = 0
        cached_nearby = cache.get_cached(current_user.id, 'nearby_vehicles')
        if cached_nearby is not None:
            nearby_data = cached_nearby
        else:
            try:
                # Extract user's current location from their Uber headers
                user_lat = headers.get('x-uber-device-location-latitude')
                user_lng = headers.get('x-uber-device-location-longitude')
                
                if user_lat and user_lng:
                    nearby_result = uberRidersNearby(cookies, headers, refresh_token, 
                                                     lat=float(user_lat), lng=float(user_lng))
                else:
                    nearby_result = uberRidersNearby(cookies, headers, refresh_token)
                    
                if nearby_result:
                    nearby_data = nearby_result.get('nearby_vehicles', 0)
                    if isinstance(nearby_data, int):
                        cache.set_cached(current_user.id, 'nearby_vehicles', nearby_data)
                    else:
                        nearby_data = 0
            except Exception as e:
                print(f"Error fetching nearby vehicles: {e}", flush=True)

    # Unread messages count - handle NULL last_chat_read_at
    if current_user.last_chat_read_at:
        unread_chat_count = ChatMessage.query.filter(
            ChatMessage.created_at > current_user.last_chat_read_at,
            ChatMessage.user_id != current_user.id
        ).count()
    else:
        # If never read, count all messages from others
        unread_chat_count = ChatMessage.query.filter(
            ChatMessage.user_id != current_user.id
        ).count()

    return jsonify(success=True,
                   vehicles=vehicles,
                   driver_info=driver_info,
                   default_vehicle=default_vehicle,
                   active_ride=active_ride,
                   driver_status=driver_status,
                   nearby_drivers=nearby_data,
                   unread_chat_count=unread_chat_count)


@app.route('/api/driver-status')
@login_required
def get_driver_status():
    """Get fresh driver status - bypasses cache for real-time updates"""
    if not current_user.uber_connected:
        return jsonify(success=False, error='Not connected')
    
    try:
        cookies, headers, refresh_token = current_user.get_uber_credentials()
    except Exception as e:
        return jsonify(success=False, error='Credentials error')
    
    try:
        ride_data = appLaunch(cookies, headers, refresh_token)
        driver_status = None
        active_ride = None
        
        if ride_data:
            if isinstance(ride_data, list) and len(ride_data) >= 2:
                full_data = ride_data[1]
            elif isinstance(ride_data, dict):
                full_data = ride_data
            else:
                full_data = None
            
            if full_data and isinstance(full_data, dict):
                driver_tasks = full_data.get('driverTasks', {})
                driver_state = driver_tasks.get('driverState', {})
                task_scopes = driver_tasks.get('taskScopes', [])
                
                driver_status = {
                    'online': bool(driver_state.get('online', False)),
                    'available': bool(driver_state.get('available', False)),
                    'dispatchable': bool(driver_state.get('dispatchable', False))
                }
                
                if task_scopes and len(task_scopes) > 0:
                    first_task = task_scopes[0]
                    active_ride = {
                        'full_name': first_task.get('rider', {}).get('firstName', 'Rider'),
                        'rating': first_task.get('rider', {}).get('rating', '--'),
                        'trip_distance': first_task.get('tripDistance'),
                        'ride_type': first_task.get('vehicleViewName', 'UberX')
                    }
                
                cache.set_cached(current_user.id, 'active_ride', full_data)
        
        return jsonify(success=True, driver_status=driver_status, active_ride=active_ride)
    except Exception as e:
        print(f"Error fetching driver status: {e}", flush=True)
        return jsonify(success=False, error=str(e))


@app.route('/api/nearby-drivers')
@login_required
def get_nearby_drivers():
    """Get nearby drivers count using browser geolocation coordinates"""
    if not current_user.uber_connected:
        return jsonify(success=True, nearby_drivers=0)
    
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    
    if not lat or not lng:
        return jsonify(success=False, error='Location required', nearby_drivers=0)
    
    try:
        cookies, headers, refresh_token = current_user.get_uber_credentials()
    except Exception as e:
        return jsonify(success=False, error='Credentials error', nearby_drivers=0)
    
    try:
        nearby_result = uberRidersNearby(cookies, headers, refresh_token, lat=lat, lng=lng)
        if nearby_result:
            nearby_count = nearby_result.get('nearby_vehicles', 0)
            if isinstance(nearby_count, int):
                cache.set_cached(current_user.id, 'nearby_vehicles', nearby_count)
                return jsonify(success=True, nearby_drivers=nearby_count)
        return jsonify(success=True, nearby_drivers=0)
    except Exception as e:
        print(f"Error fetching nearby drivers: {e}", flush=True)
        return jsonify(success=False, error=str(e), nearby_drivers=0)


# VAPID keys for push notifications - loaded from environment
VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_CLAIMS = {"sub": "mailto:admin@riztar.com"}


@app.route('/api/push/vapid-public-key')
@login_required
def get_vapid_public_key():
    """Return the VAPID public key for client-side push subscription"""
    if not VAPID_PUBLIC_KEY:
        return jsonify(success=False, error='Push notifications not configured')
    return jsonify(success=True, publicKey=VAPID_PUBLIC_KEY)


@app.route('/api/push/subscribe', methods=['POST'])
@login_required
def push_subscribe():
    """Subscribe user to push notifications"""
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        return jsonify(success=False, error='Push notifications not configured')
    
    data = request.json
    if not data:
        return jsonify(success=False, error='No data provided')
    
    endpoint = data.get('endpoint')
    keys = data.get('keys', {})
    p256dh = keys.get('p256dh')
    auth = keys.get('auth')
    
    if not all([endpoint, p256dh, auth]):
        return jsonify(success=False, error='Invalid subscription data')
    
    existing = PushSubscription.query.filter_by(
        user_id=current_user.id,
        endpoint=endpoint
    ).first()
    
    if existing:
        existing.p256dh_key = p256dh
        existing.auth_key = auth
        existing.is_active = True
    else:
        subscription = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh_key=p256dh,
            auth_key=auth
        )
        db.session.add(subscription)
    
    db.session.commit()
    return jsonify(success=True, message='Subscribed to push notifications')


@app.route('/api/push/unsubscribe', methods=['POST'])
@login_required
def push_unsubscribe():
    """Unsubscribe user from push notifications"""
    data = request.json
    endpoint = data.get('endpoint') if data else None
    
    if endpoint:
        PushSubscription.query.filter_by(
            user_id=current_user.id,
            endpoint=endpoint
        ).delete()
    else:
        PushSubscription.query.filter_by(user_id=current_user.id).delete()
    
    db.session.commit()
    return jsonify(success=True, message='Unsubscribed from push notifications')


@app.route('/api/push/status')
@login_required
def push_status():
    """Check if user has active push subscriptions"""
    has_subscription = PushSubscription.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).first() is not None
    configured = bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)
    return jsonify(success=True, subscribed=has_subscription, configured=configured)


def send_push_notification(user_id, title, body, url='/', tag='default', require_interaction=False):
    """Send push notification to a specific user"""
    if not VAPID_PRIVATE_KEY:
        print("No VAPID private key configured")
        return 0
        
    subscriptions = PushSubscription.query.filter_by(user_id=user_id, is_active=True).all()
    print(f"Found {len(subscriptions)} active subscriptions for user {user_id}")
    
    payload = json.dumps({
        'title': title,
        'body': body,
        'url': url,
        'tag': tag,
        'requireInteraction': require_interaction
    })
    
    sent = 0
    for sub in subscriptions:
        subscription_info = sub.to_subscription_info()
        print(f"Sending to endpoint: {subscription_info['endpoint'][:80]}...")
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS,
                content_encoding="aes128gcm"
            )
            print("Push notification sent successfully!")
            sent += 1
        except WebPushException as e:
            if e.response and e.response.status_code in [404, 410]:
                sub.is_active = False
                db.session.commit()
            print(f"Push notification failed: {e}")
            print(f"Subscription info: endpoint={subscription_info['endpoint'][:50]}, keys present: {bool(subscription_info.get('keys'))}")
            if e.response:
                print(f"Response status: {e.response.status_code}")
                print(f"Response body: {e.response.text}")
    
    return sent


@app.route('/api/push/test', methods=['POST'])
@login_required
def test_push_notification():
    """Send a test push notification to the current user"""
    sent = send_push_notification(
        current_user.id,
        'Test Notification',
        'Push notifications are working!',
        url='/',
        tag='test'
    )
    return jsonify(success=True, sent=sent)


@app.route('/api/ride-data')
@login_required
def ride_data_api():
    if not current_user.uber_connected:
        return jsonify(success=True, active_ride=None)

    def extract_active_ride(data):
        """Extract active ride from appLaunch response if one exists"""
        if not data or not isinstance(data, dict):
            return None
        # Check if this is already processed ride data (has full_name key)
        if 'full_name' in data:
            return data
        # Otherwise it's raw API response with no active ride
        return None

    cached_ride = cache.get_cached(current_user.id, 'active_ride')
    if cached_ride is not None:
        if isinstance(cached_ride, dict):
            active_ride = extract_active_ride(cached_ride)
            return jsonify(success=True, active_ride=active_ride)
        return jsonify(success=True, active_ride=None)

    try:
        cookies, headers, refresh_token = current_user.get_uber_credentials()
    except Exception as e:
        return jsonify(success=True, active_ride=None)

    try:
        ride_data = appLaunch(cookies, headers, refresh_token)
        if ride_data:
            if isinstance(ride_data, list) and len(ride_data) >= 2:
                ride_data = ride_data[1]
            cache.set_cached(current_user.id, 'active_ride', ride_data)
            active_ride = extract_active_ride(ride_data)
            return jsonify(success=True, active_ride=active_ride)
    except Exception as e:
        print(f"Error fetching active ride: {e}")

    return jsonify(success=True, active_ride=None)


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/contact')
def contact():
    return render_template('contact.html')


@app.route('/privacy')
def privacy():
    return render_template('privacy.html')


@app.route('/terms')
def terms():
    return render_template('terms.html')


@app.route('/subscription')
def subscription():
    return render_template('subscription.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('root'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user and user.check_password(form.password.data):
            user.last_login = datetime.utcnow()
            db.session.commit()
            login_user(user, remember=True)
            next_page = request.args.get('next')
            return redirect(next_page if next_page else url_for('root'))
        else:
            flash('Invalid email or password.', 'error')

    return render_template('login.html', form=form)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('root'))

    form = RegisterForm()
    if form.validate_on_submit():
        profile_image_path = None
        if form.profile_image.data:
            import uuid
            from werkzeug.utils import secure_filename
            file = form.profile_image.data
            filename = secure_filename(file.filename)
            ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'jpg'
            unique_filename = f"{uuid.uuid4().hex}.{ext}"
            upload_folder = os.path.join(app.root_path, 'static', 'uploads', 'profiles')
            os.makedirs(upload_folder, exist_ok=True)
            file_path = os.path.join(upload_folder, unique_filename)
            file.save(file_path)
            profile_image_path = f"/static/uploads/profiles/{unique_filename}"
        
        user = User(email=form.email.data.lower().strip(),
                    username=form.username.data.lower().strip(),
                    first_name=form.first_name.data.strip(),
                    last_name=form.last_name.data.strip(),
                    profile_image=profile_image_path,
                    role='user')
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        
        # Assign default "user" role to new user
        user_role = Role.query.filter_by(name='user').first()
        if user_role:
            user.roles.append(user_role)
            db.session.commit()
        
        # Send welcome email using Replit Mail service
        try:
            from replitmail import send_email as replit_send_email
            replit_send_email(
                to=user.email,
                subject=f"Welcome to RizTar, {user.first_name}!",
                body=f"Hi {user.first_name},\n\nWelcome to RizTar, the premium driver management system.\n\nWe're excited to have you on board! With RizTar, you'll have access to powerful tools designed to help you maximize your earnings and streamline your driving experience.\n\nIf you have any questions, feel free to reach out to our support team.\n\nBest regards,\nThe RizTar Team\ninfo@riztar.com"
            )
            print(f"Welcome email sent to {user.email}", flush=True)
        except Exception as e:
            print(f"Failed to send welcome email: {e}", flush=True)

        flash('Account created successfully! Please sign in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html', form=form)


@app.route('/logout')
@login_required
def logout():
    clear_driver_cache_for_user(current_user.id)
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/admin')
@login_required
def admin():
    if not current_user.can_manage_users():
        flash('Access denied. Owner privileges required.', 'error')
        return redirect(url_for('root'))

    users = User.query.all()
    roles = Role.query.all()
    return render_template('admin.html', users=users, roles=roles)


@app.route('/admin/statistics')
@login_required
def admin_statistics():
    if not current_user.can_manage_users():
        flash('Access denied. Owner privileges required.', 'error')
        return redirect(url_for('root'))
    return render_template('statistics.html')


@app.route('/api/statistics')
@login_required
def api_statistics():
    if not current_user.can_manage_users():
        return jsonify(error='Access denied'), 403
    
    from sqlalchemy import func
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)
    
    # Page visits
    visits_today = PageVisit.query.filter(PageVisit.visited_at >= today_start).count()
    visits_week = PageVisit.query.filter(PageVisit.visited_at >= week_start).count()
    visits_month = PageVisit.query.filter(PageVisit.visited_at >= month_start).count()
    visits_total = PageVisit.query.count()
    
    # User stats
    total_users = User.query.count()
    users_today = User.query.filter(User.created_at >= today_start).count()
    users_week = User.query.filter(User.created_at >= week_start).count()
    users_month = User.query.filter(User.created_at >= month_start).count()
    
    # Uber connected
    uber_connected = User.query.filter(User.uber_connected == True).count()
    
    # Daily visits for last 30 days (for chart)
    daily_visits = []
    daily_signups = []
    for i in range(29, -1, -1):
        day_start = (today_start - timedelta(days=i))
        day_end = day_start + timedelta(days=1)
        count = PageVisit.query.filter(
            PageVisit.visited_at >= day_start,
            PageVisit.visited_at < day_end
        ).count()
        signup_count = User.query.filter(
            User.created_at >= day_start,
            User.created_at < day_end
        ).count()
        daily_visits.append({
            'date': day_start.strftime('%b %d'),
            'count': count
        })
        daily_signups.append({
            'date': day_start.strftime('%b %d'),
            'count': signup_count
        })
    
    return jsonify(
        visits={
            'today': visits_today,
            'week': visits_week,
            'month': visits_month,
            'total': visits_total
        },
        users={
            'total': total_users,
            'today': users_today,
            'week': users_week,
            'month': users_month
        },
        uber_connected=uber_connected,
        daily_visits=daily_visits,
        daily_signups=daily_signups
    )


@app.route('/admin/update-roles/<int:user_id>', methods=['POST'])
@login_required
def update_roles(user_id):
    if not current_user.can_manage_users():
        return jsonify(status="error", message="Access denied"), 403

    user = User.query.get_or_404(user_id)
    role_ids = request.form.getlist('role_ids')

    new_roles = []
    for role_id in role_ids:
        try:
            role = Role.query.get(int(role_id))
            if role:
                new_roles.append(role)
        except (ValueError, TypeError):
            pass

    if not new_roles:
        default_role = Role.query.filter_by(name='user').first()
        if default_role:
            new_roles = [default_role]

    user.roles = new_roles
    user.role_id = None

    if new_roles:
        primary = user.get_primary_role()
        if primary:
            user.role = primary.name
    else:
        user.role = 'user'

    db.session.commit()
    role_names = ', '.join([r.display_name for r in new_roles])
    flash(f'Roles updated for {user.username}: {role_names}', 'success')

    return redirect(url_for('admin'))


@app.route('/admin/delete-user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if not current_user.can_manage_users():
        return jsonify(status="error", message="Access denied"), 403

    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin'))

    db.session.delete(user)
    db.session.commit()
    flash(f'User {user.username} has been deleted.', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/edit-user/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    if not current_user.can_manage_users():
        flash('Access denied.', 'error')
        return redirect(url_for('root'))

    user = User.query.get_or_404(user_id)
    all_roles = Role.query.all()

    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()

        if email != user.email:
            existing = User.query.filter_by(email=email).first()
            if existing:
                flash('Email already in use.', 'error')
                return render_template('edit_user.html',
                                       user=user,
                                       roles=all_roles)

        if username != user.username:
            existing = User.query.filter_by(username=username).first()
            if existing:
                flash('Username already taken.', 'error')
                return render_template('edit_user.html',
                                       user=user,
                                       roles=all_roles)

        user.first_name = first_name
        user.last_name = last_name
        user.username = username
        user.email = email

        new_password = request.form.get('new_password', '').strip()
        if new_password:
            user.set_password(new_password)

        role_ids = request.form.getlist('role_ids')
        new_roles = []
        for role_id in role_ids:
            try:
                role = Role.query.get(int(role_id))
                if role:
                    new_roles.append(role)
            except (ValueError, TypeError):
                pass

        if not new_roles:
            default_role = Role.query.filter_by(name='user').first()
            if default_role:
                new_roles = [default_role]

        user.roles = new_roles
        user.role_id = None
        if new_roles:
            primary = user.get_primary_role()
            if primary:
                user.role = primary.name
        else:
            user.role = 'user'

        db.session.commit()
        flash(f'User {user.username} updated successfully.', 'success')
        return redirect(url_for('admin'))

    return render_template('edit_user.html', user=user, roles=all_roles)


@app.route('/admin/uber-credentials/<int:user_id>')
@login_required
def admin_uber_credentials(user_id):
    if not current_user.is_owner():
        flash('Access denied. Owner privileges required.', 'error')
        return redirect(url_for('root'))

    user = User.query.get_or_404(user_id)

    current_cookies = None
    current_headers = None
    current_refresh_token = None

    if user.uber_connected:
        try:
            current_cookies = decrypt_data(user.uber_cookies)
            current_headers = decrypt_data(user.uber_headers)
            current_refresh_token = decrypt_data(user.uber_refresh_token)
        except:
            pass

    form = EmptyForm()
    return render_template('uber_credentials.html',
                           user=user,
                           form=form,
                           current_cookies=current_cookies,
                           current_headers=current_headers,
                           current_refresh_token=current_refresh_token)


@app.route('/admin/uber-credentials/<int:user_id>/set', methods=['POST'])
@login_required
def admin_set_uber_credentials(user_id):
    if not current_user.is_owner():
        flash('Access denied. Owner privileges required.', 'error')
        return redirect(url_for('root'))

    user = User.query.get_or_404(user_id)

    cookies = request.form.get('cookies', '').strip()
    headers = request.form.get('headers', '').strip()
    refresh_token = request.form.get('refresh_token', '').strip()

    if not cookies or not headers or not refresh_token:
        flash('All credential fields are required.', 'error')
        return redirect(url_for('admin_uber_credentials', user_id=user_id))

    import json
    try:
        json.loads(cookies)
        json.loads(headers)
    except json.JSONDecodeError:
        flash('Cookies and Headers must be valid JSON.', 'error')
        return redirect(url_for('admin_uber_credentials', user_id=user_id))

    user.uber_cookies = encrypt_data(cookies)
    user.uber_headers = encrypt_data(headers)
    user.uber_refresh_token = encrypt_data(refresh_token)
    user.uber_connected = True

    db.session.commit()
    cache.invalidate_cache(user.id)

    flash(f'Uber credentials saved for {user.username}.', 'success')
    return redirect(url_for('admin_uber_credentials', user_id=user_id))


@app.route('/admin/uber-credentials/<int:user_id>/disconnect',
           methods=['POST'])
@login_required
def admin_disconnect_uber(user_id):
    if not current_user.is_owner():
        flash('Access denied. Owner privileges required.', 'error')
        return redirect(url_for('root'))

    user = User.query.get_or_404(user_id)

    user.uber_cookies = None
    user.uber_headers = None
    user.uber_refresh_token = None
    user.uber_connected = False

    db.session.commit()
    cache.invalidate_cache(user.id)

    flash(f'Uber account disconnected for {user.username}.', 'success')
    return redirect(url_for('admin_uber_credentials', user_id=user_id))


@app.route('/roles')
@login_required
def roles():
    if not current_user.is_owner():
        flash('Access denied. Owner privileges required.', 'error')
        return redirect(url_for('root'))

    all_roles = Role.query.order_by(Role.is_system.desc(),
                                    Role.created_at.asc()).all()
    return render_template('roles.html', roles=all_roles)


@app.route('/roles/create', methods=['POST'])
@login_required
def create_role():
    if not current_user.is_owner():
        flash('Access denied. Owner privileges required.', 'error')
        return redirect(url_for('root'))

    name = request.form.get('name', '').lower().strip().replace(' ', '_')
    display_name = request.form.get('display_name', '').strip()
    color = request.form.get('color', 'gray')

    if not name or not display_name:
        flash('Role name and display name are required.', 'error')
        return redirect(url_for('roles'))

    existing = Role.query.filter_by(name=name).first()
    if existing:
        flash(f'A role with name "{name}" already exists.', 'error')
        return redirect(url_for('roles'))

    role = Role(
        name=name,
        display_name=display_name,
        color=color,
        is_system=False,
        can_change_location=request.form.get('can_change_location') == '1',
        can_fetch_ride=request.form.get('can_fetch_ride') == '1',
        can_access_admin=request.form.get('can_access_admin') == '1',
        can_manage_users=request.form.get('can_manage_users') == '1',
        can_manage_roles=request.form.get('can_manage_roles') == '1')

    db.session.add(role)
    db.session.commit()
    flash(f'Role "{display_name}" created successfully!', 'success')
    return redirect(url_for('roles'))


@app.route('/roles/edit/<int:role_id>', methods=['GET', 'POST'])
@login_required
def edit_role(role_id):
    if not current_user.is_owner():
        flash('Access denied. Owner privileges required.', 'error')
        return redirect(url_for('root'))

    role = Role.query.get_or_404(role_id)

    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        color = request.form.get('color', 'gray')

        if not display_name:
            flash('Display name is required.', 'error')
            return redirect(url_for('edit_role', role_id=role_id))

        role.display_name = display_name
        role.color = color
        role.can_change_location = request.form.get(
            'can_change_location') == '1'
        role.can_fetch_ride = request.form.get('can_fetch_ride') == '1'
        role.can_access_admin = request.form.get('can_access_admin') == '1'
        role.can_manage_users = request.form.get('can_manage_users') == '1'
        role.can_manage_roles = request.form.get('can_manage_roles') == '1'

        db.session.commit()
        flash(f'Role "{display_name}" updated successfully!', 'success')
        return redirect(url_for('roles'))

    return render_template('edit_role.html', role=role)


@app.route('/roles/delete/<int:role_id>', methods=['POST'])
@login_required
def delete_role(role_id):
    if not current_user.is_owner():
        flash('Access denied. Owner privileges required.', 'error')
        return redirect(url_for('root'))

    role = Role.query.get_or_404(role_id)

    if role.is_system:
        flash('System roles cannot be deleted.', 'error')
        return redirect(url_for('roles'))

    User.query.filter_by(role_id=role_id).update({
        'role_id': None,
        'role': 'user'
    })

    db.session.delete(role)
    db.session.commit()
    flash(f'Role "{role.display_name}" has been deleted.', 'success')
    return redirect(url_for('roles'))


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    form = ProfileForm()
    disconnect_form = UberDisconnectForm()

    if form.validate_on_submit():
        if form.email.data != current_user.email:
            existing = User.query.filter_by(email=form.email.data).first()
            if existing:
                flash('Email already in use.', 'error')
                return render_template('profile.html',
                                       form=form,
                                       disconnect_form=disconnect_form)

        if form.profile_image.data:
            file = form.profile_image.data
            filename = secure_filename(f"user_{current_user.id}_{secrets.token_hex(4)}_{file.filename}")
            file_path = os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename)
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            file.save(file_path)
            
            # Delete old image if it exists and is local
            if current_user.profile_image and current_user.profile_image.startswith('/static/uploads/'):
                old_path = os.path.join(app.root_path, current_user.profile_image.lstrip('/'))
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except:
                        pass
            
            current_user.profile_image = f"/static/uploads/profile_images/{filename}"

        current_user.first_name = form.first_name.data
        current_user.last_name = form.last_name.data
        current_user.email = form.email.data
        db.session.commit()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('profile'))

    form.first_name.data = current_user.first_name
    form.last_name.data = current_user.last_name
    form.username.data = current_user.username
    form.email.data = current_user.email
    return render_template('profile.html',
                           form=form,
                           disconnect_form=disconnect_form)


@app.route('/uber-connect', methods=['GET'])
@login_required
def uber_connect():
    return redirect(url_for('uber_phone_connect'))


@app.route('/uber-callback', methods=['GET', 'POST'])
@login_required
def uber_callback():
    """Callback page for bookmarklet - receives cookies and displays confirmation form"""
    form = UberConnectForm()
    disconnect_form = UberDisconnectForm()

    if request.method == 'POST':
        cookies_json = request.form.get('cookies', '').strip()
        headers_json = request.form.get('headers', '').strip()
        refresh_token = request.form.get('refresh_token', '').strip()

        if cookies_json:
            try:
                import json
                json.loads(cookies_json)
                if headers_json:
                    json.loads(headers_json)
            except json.JSONDecodeError:
                flash('Invalid JSON format.', 'error')
                return redirect(url_for('uber_connect'))

            current_user.uber_cookies = encrypt_data(cookies_json)
            if headers_json:
                current_user.uber_headers = encrypt_data(headers_json)
            if refresh_token:
                current_user.uber_refresh_token = encrypt_data(refresh_token)
            current_user.uber_connected = True
            db.session.commit()
            cache.invalidate_cache(current_user.id)
            flash('Uber cookies captured successfully!', 'success')
            return redirect(url_for('root'))

    cookies_from_url = request.args.get('cookies', '')

    return render_template('uber_callback.html',
                           form=form,
                           cookies=cookies_from_url,
                           disconnect_form=disconnect_form)


@app.route('/api/test-uber-credentials', methods=['POST'])
@login_required
def test_uber_credentials():
    """Test if the provided Uber credentials are valid"""
    try:
        data = request.get_json()
        headers_json = data.get('headers', '')
        cookies_json = data.get('cookies', '')
        refresh_token = data.get('refresh_token', '')

        if not all([headers_json, cookies_json, refresh_token]):
            return jsonify({'success': False, 'error': 'All fields required'})

        headers = json.loads(headers_json)
        cookies = json.loads(cookies_json)

        from objects.uberDev import refreshToken
        try:
            new_token = refreshToken(cookies, headers, refresh_token)
            if new_token:
                return jsonify({
                    'success': True,
                    'message': 'Credentials valid'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Could not refresh token'
                })
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
    except json.JSONDecodeError:
        return jsonify({'success': False, 'error': 'Invalid JSON format'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/test-driver-navigation', methods=['GET'])
@login_required
def test_driver_navigation():
    """Test the cookie grabber and driver navigation functions"""
    if not current_user.uber_connected:
        return jsonify({'success': False, 'error': 'Uber not connected'})
    
    try:
        cookies, headers, refresh_token = current_user.get_uber_credentials()
        
        from objects.uberDev import uberCookieGrabber, driverNavigation
        
        cookie_result = uberCookieGrabber(headers, refresh_token)
        
        if not cookie_result:
            return jsonify({
                'success': False, 
                'error': 'Failed to grab cookies',
                'step': 'uberCookieGrabber'
            })
        
        web_cookies = cookie_result.get('cookies', {})
        access_token = cookie_result.get('access_token')
        
        nav_result = driverNavigation(web_cookies, access_token)
        
        return jsonify({
            'success': True,
            'cookie_grabber': {
                'cookies': web_cookies,
                'has_access_token': bool(access_token),
                'response_keys': list(cookie_result.get('response_data', {}).keys()) if cookie_result.get('response_data') else []
            },
            'navigation': nav_result
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/uber-phone-connect')
@login_required
def uber_phone_connect():
    """Phone-based Uber connection page"""
    return render_template('uber_phone_connect.html')


@app.route('/api/uber-send-code', methods=['POST'])
@login_required
def uber_send_code():
    """Send verification code to phone number"""
    try:
        data = request.get_json()
        country_code = data.get('country_code', '+61')
        phone_number = data.get('phone_number', '')

        if not phone_number:
            return jsonify({
                'success': False,
                'error': 'Phone number required'
            })

        from objects.uberDev import uberAuth
        result = uberAuth(country_code, phone_number)

        if result.get('success'):
            return jsonify({
                'success': True,
                'session_id': result.get('session_id'),
                'needs_captcha': result.get('needs_captcha', False),
                'captcha_url': result.get('captcha_url')
            })
        else:
            return jsonify({
                'success':
                False,
                'error':
                result.get('error', 'Failed to send code'),
                'needs_captcha':
                result.get('needs_captcha', False),
                'captcha_url':
                result.get('captcha_url'),
                'can_request_voice':
                result.get('can_request_voice', True),
                'session_id':
                result.get('session_id', '')
            })
    except Exception as e:
        print(f"Error in uber_send_code: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'can_request_voice': True
        })


@app.route('/api/uber-request-voice-otp', methods=['POST'])
@login_required
def uber_request_voice_otp():
    """Request voice call OTP as fallback"""
    try:
        data = request.get_json()
        session_id = data.get('session_id', '')
        country_code = data.get('country_code', '+61')
        phone_number = data.get('phone_number', '')

        if not phone_number:
            return jsonify({
                'success': False,
                'error': 'Phone number required'
            })

        from objects.uberDev import uberVoiceOTP
        result = uberVoiceOTP(session_id, country_code, phone_number)

        if result.get('success'):
            return jsonify({
                'success':
                True,
                'session_id':
                result.get('session_id'),
                'message':
                'Voice call initiated - you will receive a call shortly'
            })
        else:
            return jsonify({
                'success':
                False,
                'error':
                result.get('error', 'Failed to initiate voice call')
            })
    except Exception as e:
        print(f"Error in uber_request_voice_otp: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/uber-verify-code', methods=['POST'])
@login_required
def uber_verify_code():
    """Verify the SMS code - may require email OTP next"""
    try:
        data = request.get_json()
        session_id = data.get('session_id', '')
        code = data.get('code', '')

        if not session_id or not code:
            return jsonify({
                'success': False,
                'error': 'Session ID and code required'
            })

        from objects.uberDev import uberVerifyCode
        result = uberVerifyCode(session_id, code)

        if result.get('success'):
            if result.get('needs_email_otp'):
                return jsonify({
                    'success': True,
                    'needs_email_otp': True,
                    'session_id': result.get('session_id'),
                    'email_hint': result.get('email_hint', '')
                })

            cookies = result.get('cookies', {})
            headers = result.get('headers', {})
            refresh_token = result.get('refresh_token', '')

            current_user.uber_cookies = encrypt_data(json.dumps(cookies))
            current_user.uber_headers = encrypt_data(json.dumps(headers))
            current_user.uber_refresh_token = encrypt_data(refresh_token)
            current_user.uber_connected = True
            db.session.commit()
            cache.invalidate_cache(current_user.id)
            print(
                f"Uber credentials saved for user {current_user.id} ({current_user.email})"
            )

            return jsonify({'success': True, 'needs_email_otp': False})
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Invalid code')
            })
    except Exception as e:
        print(f"Error in uber_verify_code: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/uber-verify-email', methods=['POST'])
@login_required
def uber_verify_email():
    """Verify the email OTP code and complete connection"""
    try:
        data = request.get_json()
        session_id = data.get('session_id', '')
        code = data.get('code', '')

        if not session_id or not code:
            return jsonify({
                'success': False,
                'error': 'Session ID and code required'
            })

        from objects.uberDev import uberEmailVerify, uberAuthention
        result = uberEmailVerify(session_id, code)

        if result.get('success'):
            if result.get('needs_authentication'):
                auth_code = result.get('auth_code')
                new_session_id = result.get('session_id', session_id)
                cookies = result.get('cookies', {})
                headers = result.get('headers', {})
                auth_result = uberAuthention(headers, cookies, new_session_id,
                                             auth_code)

                if auth_result.get('success'):
                    final_cookies = auth_result.get('cookies', {})
                    final_headers = auth_result.get('headers', {})
                    refresh_token = auth_result.get('refresh_token', '')

                    current_user.uber_cookies = encrypt_data(
                        json.dumps(final_cookies))
                    current_user.uber_headers = encrypt_data(
                        json.dumps(final_headers))
                    current_user.uber_refresh_token = encrypt_data(
                        refresh_token)
                    current_user.uber_connected = True
                    db.session.commit()
                    cache.invalidate_cache(current_user.id)
                    print(
                        f"Uber credentials saved (email+auth) for user {current_user.id} ({current_user.email})"
                    )

                    return jsonify({'success': True})
                else:
                    return jsonify({
                        'success':
                        False,
                        'error':
                        auth_result.get('error', 'Authentication failed')
                    })
            else:
                cookies = result.get('cookies', {})
                headers = result.get('headers', {})
                refresh_token = result.get('refresh_token', '')

                current_user.uber_cookies = encrypt_data(json.dumps(cookies))
                current_user.uber_headers = encrypt_data(json.dumps(headers))
                current_user.uber_refresh_token = encrypt_data(refresh_token)
                current_user.uber_connected = True
                db.session.commit()
                cache.invalidate_cache(current_user.id)
                print(
                    f"Uber credentials saved (email only) for user {current_user.id} ({current_user.email})"
                )

                return jsonify({'success': True})
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Invalid code')
            })
    except Exception as e:
        print(f"Error in uber_verify_email: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/uber-disconnect', methods=['POST'])
@login_required
def uber_disconnect():
    form = UberDisconnectForm()
    if form.validate_on_submit():
        current_user.uber_cookies = None
        current_user.uber_headers = None
        current_user.uber_refresh_token = None
        current_user.uber_connected = False
        db.session.commit()
        cache.invalidate_cache(current_user.id)
        flash('Uber account disconnected.', 'success')
    return redirect(url_for('profile'))


@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()

    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash('Current password is incorrect.', 'error')
            return render_template('change_password.html', form=form)

        current_user.set_password(form.new_password.data)
        db.session.commit()
        flash('Password updated successfully.', 'success')
        return redirect(url_for('profile'))

    return render_template('change_password.html', form=form)


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('root'))

    form = ForgotPasswordForm()

    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            token = secrets.token_urlsafe(32)
            user.reset_token = token
            user.reset_token_expiry = datetime.utcnow() + timedelta(hours=1)
            db.session.commit()
            flash(
                f'Password reset link: /reset-password/{token} (valid for 1 hour)',
                'success')
        else:
            flash(
                'If an account with that email exists, a reset link has been generated.',
                'info')
        return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html', form=form)


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('root'))

    user = User.query.filter_by(reset_token=token).first()

    if not user or not user.reset_token_expiry or user.reset_token_expiry < datetime.utcnow(
    ):
        flash('Invalid or expired reset link.', 'error')
        return redirect(url_for('login'))

    form = ResetPasswordForm()

    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.reset_token = None
        user.reset_token_expiry = None
        db.session.commit()
        flash('Password has been reset. Please sign in.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', form=form)


@app.route('/change-location')
@login_required
def home():
    has_permission = current_user.has_permission('can_change_location')
    loading = current_user.uber_connected and has_permission
    disconnect_form = UberDisconnectForm(
    ) if current_user.uber_connected else None
    return render_template('index.html',
                           has_permission=has_permission,
                           default_vehicle=None,
                           loading=loading,
                           uber_connected=current_user.uber_connected,
                           username=current_user.get_display_name(),
                           disconnect_form=disconnect_form)


@app.route('/api/location-data')
@login_required
def location_data():
    default_vehicle = None
    driver_info = None
    driver_status = None
    if current_user.uber_connected:
        try:
            cookies, headers, refresh_token = current_user.get_uber_credentials(
            )
            user_display_name = current_user.get_display_name()

            def fetch_vehicles():
                return vehicleDetails(cookies, headers, refresh_token)

            def fetch_driver():
                return {'name': user_display_name, 'photo': None}

            def fetch_ride():
                try:
                    return appLaunch(cookies, headers, refresh_token)
                except Exception as e:
                    print(f"Error fetching appLaunch: {e}")
                    return None

            vehicles = cache.get_vehicles(current_user.id, fetch_vehicles)
            for v in vehicles:
                if v.get('isDefault'):
                    default_vehicle = v
                    break

            cached_driver = cache.get_cached(current_user.id, 'driver_info')
            if cached_driver:
                driver_info = cached_driver
            else:
                driver_info = fetch_driver()
                cache.set_cached(current_user.id, 'driver_info', driver_info)

            cached_ride = cache.get_cached(current_user.id, 'active_ride')
            if cached_ride is None:
                cached_ride = fetch_ride()
                cache.set_cached(current_user.id, 'active_ride', cached_ride)
            
            if cached_ride and isinstance(cached_ride, dict):
                driver_tasks = cached_ride.get('driverTasks', {})
                driver_state = driver_tasks.get('driverState', {})
                driver_status = {
                    'online': bool(driver_state.get('online', False)),
                    'available': bool(driver_state.get('available', False)),
                    'dispatchable': bool(driver_state.get('dispatchable', False)),
                    'onboarding_status': cached_ride.get('driverOnboardingStatus', 'UNKNOWN')
                }
        except Exception as e:
            print(f"Error fetching location data: {e}")
    return jsonify(success=True,
                   default_vehicle=default_vehicle,
                   driver_info=driver_info,
                   driver_status=driver_status)


@app.route('/hotspots')
@app.route('/demand')
@login_required
def demand_intel():
    return render_template('demand_intel.html')


@app.route('/live-drivers')
@login_required
def live_drivers_page():
    return render_template('live_drivers.html')


driver_cache = {}
driver_cache_lock = {}

def calculate_distance_meters(lat1, lng1, lat2, lng2):
    """Calculate distance between two coordinates in meters using Haversine formula."""
    import math
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)
    a = math.sin(delta_phi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def bearing_difference(b1, b2):
    """Calculate the absolute difference between two bearings (0-180)."""
    if b1 is None or b2 is None:
        return 0
    diff = abs(b1 - b2) % 360
    return min(diff, 360 - diff)

def calculate_bearing_to_point(lat1, lng1, lat2, lng2):
    """Calculate bearing from point 1 to point 2 in degrees (0-360)."""
    import math
    delta_lng = math.radians(lng2 - lng1)
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    x = math.sin(delta_lng) * math.cos(lat2_rad)
    y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lng)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360

def is_moving_same_driver(new_driver, existing_driver, max_cross_track=100, max_elapsed_seconds=60):
    """
    Check if new_driver is the same as existing_driver based on motion trajectory.
    Uses velocity-based projection: if driver is moving forward at consistent bearing,
    allow unlimited along-track distance but restrict cross-track deviation.
    
    A driver moving at 60km/h (city speed) travels ~1000m per minute.
    We check if the new position is along the expected trajectory.
    """
    import math
    from datetime import datetime
    
    new_lat = new_driver.get('lat')
    new_lng = new_driver.get('lng')
    new_bearing = new_driver.get('bearing')
    
    ex_lat = existing_driver.get('lat')
    ex_lng = existing_driver.get('lng')
    ex_bearing = existing_driver.get('bearing')
    ex_timestamp = existing_driver.get('timestamp')
    
    if None in (new_lat, new_lng, ex_lat, ex_lng):
        return False
    
    dist = calculate_distance_meters(new_lat, new_lng, ex_lat, ex_lng)
    
    if dist <= 100:
        b_diff = bearing_difference(new_bearing, ex_bearing)
        return b_diff < 90
    
    if new_bearing is None or ex_bearing is None:
        return False
    
    b_diff = bearing_difference(new_bearing, ex_bearing)
    if b_diff >= 30:
        return False
    
    if ex_timestamp:
        now = datetime.now()
        elapsed = (now - ex_timestamp).total_seconds()
        if elapsed > max_elapsed_seconds:
            return False
        
        max_speed_mps = 30
        max_travel = max_speed_mps * elapsed
        
        if dist > max_travel + 100:
            return False
    
    bearing_to_new = calculate_bearing_to_point(ex_lat, ex_lng, new_lat, new_lng)
    movement_alignment = bearing_difference(ex_bearing, bearing_to_new)
    
    if movement_alignment <= 45:
        cross_track = dist * math.sin(math.radians(movement_alignment))
        if cross_track <= max_cross_track:
            return True
    
    return False

def find_matching_driver_index(new_driver, existing_drivers):
    """Find index of existing driver that matches new_driver, or -1 if none."""
    for i, existing in enumerate(existing_drivers):
        if is_moving_same_driver(new_driver, existing):
            return i
    return -1

@app.route('/api/live-drivers')
@login_required
def api_live_drivers():
    """
    Fetch drivers near user's location using coordinate-based deduplication.
    Accumulates unique drivers over 3-minute rolling window.
    """
    from datetime import datetime, timedelta
    from objects.uberDev import fetch_drivers_at_location
    
    user_id = current_user.id
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    
    if lat is None or lng is None:
        lat, lng = -31.9505, 115.8605
    
    if user_id not in driver_cache:
        driver_cache[user_id] = {'drivers': [], 'last_cleanup': datetime.now(), 'sample_count': 0}
    
    cache = driver_cache[user_id]
    now = datetime.now()
    
    cutoff = now - timedelta(minutes=3)
    cache['drivers'] = [d for d in cache['drivers'] if d.get('timestamp', now) > cutoff]
    
    try:
        new_drivers = fetch_drivers_at_location(lat, lng)
        cache['sample_count'] += 1
        
        for driver in new_drivers:
            driver['timestamp'] = now
            match_idx = find_matching_driver_index(driver, cache['drivers'])
            if match_idx >= 0:
                cache['drivers'][match_idx]['lat'] = driver['lat']
                cache['drivers'][match_idx]['lng'] = driver['lng']
                cache['drivers'][match_idx]['bearing'] = driver.get('bearing')
                cache['drivers'][match_idx]['timestamp'] = now
            else:
                cache['drivers'].append(driver)
        
        counts_by_type = {'UberX': 0, 'Comfort': 0, 'XL': 0, 'Black': 0}
        type_mapping = {
            'UBERX': 'UberX', 'COMFORT': 'Comfort', 'XL': 'XL', 'BLACK': 'Black',
            'UberX': 'UberX', 'Comfort': 'Comfort', 'Black': 'Black'
        }
        for driver in cache['drivers']:
            raw_type = driver.get('product_type', 'UberX')
            ptype = type_mapping.get(raw_type, 'UberX')
            counts_by_type[ptype] += 1
        
        counts = {
            'total': len(cache['drivers']),
            'uberx': counts_by_type['UberX'],
            'comfort': counts_by_type['Comfort'],
            'xl': counts_by_type['XL'],
            'black': counts_by_type['Black'],
        }
        
        drivers_for_response = []
        for d in cache['drivers']:
            driver_copy = {k: v for k, v in d.items() if k != 'timestamp'}
            age_seconds = (now - d.get('timestamp', now)).total_seconds()
            driver_copy['opacity'] = max(0.4, 1 - (age_seconds / 180))
            drivers_for_response.append(driver_copy)
        
        return jsonify({
            'success': True,
            'drivers': drivers_for_response,
            'counts': counts,
            'sampleCount': cache['sample_count'],
            'updated': now.strftime('%H:%M:%S'),
            'userLocation': {'lat': lat, 'lng': lng}
        })
    except Exception as e:
        print(f"Live drivers API error: {e}")
        return jsonify(success=False, message=str(e)), 500

@app.route('/api/live-drivers/reset')
@login_required
def api_live_drivers_reset():
    """Reset the driver cache for current user."""
    user_id = current_user.id
    if user_id in driver_cache:
        del driver_cache[user_id]
    return jsonify(success=True)


@app.route('/api/location-drivers')
@login_required
def api_location_drivers():
    """
    Search for drivers at a named location.
    Geocodes the location, then samples 5 points (center + 4 offsets in 1km radius).
    Collects 25 samples total (5 points x 5 rounds).
    """
    import time
    from objects.uberDev import fetch_drivers_at_location
    
    location = request.args.get('location', '').strip()
    if not location:
        return jsonify(success=False, message='Location parameter required')
    
    try:
        geo_response = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={
                'q': f"{location}, Perth, Western Australia",
                'format': 'json',
                'limit': 1
            },
            headers={'User-Agent': 'RizTar/1.0'}
        )
        geo_data = geo_response.json()
        
        if not geo_data:
            return jsonify(success=False, message=f'Location "{location}" not found')
        
        lat = float(geo_data[0]['lat'])
        lng = float(geo_data[0]['lon'])
        location_name = geo_data[0].get('display_name', location)
        
    except Exception as e:
        return jsonify(success=False, message=f'Geocoding failed: {str(e)}')
    
    offset_km = 1.0
    offset_deg = offset_km / 111.0
    sample_points = [
        (lat, lng),
        (lat + offset_deg, lng),
        (lat - offset_deg, lng),
        (lat, lng + offset_deg),
        (lat, lng - offset_deg)
    ]
    
    unique_drivers = []
    samples_collected = 0
    
    try:
        for round_num in range(5):
            for point_lat, point_lng in sample_points:
                try:
                    new_drivers = fetch_drivers_at_location(point_lat, point_lng)
                    samples_collected += 1
                    
                    for driver in new_drivers:
                        driver['timestamp'] = datetime.now()
                        match_idx = find_matching_driver_index(driver, unique_drivers)
                        if match_idx >= 0:
                            unique_drivers[match_idx]['lat'] = driver['lat']
                            unique_drivers[match_idx]['lng'] = driver['lng']
                            unique_drivers[match_idx]['bearing'] = driver.get('bearing')
                            unique_drivers[match_idx]['timestamp'] = datetime.now()
                        else:
                            unique_drivers.append(driver)
                    
                    time.sleep(0.5)
                except Exception as e:
                    print(f"Location driver fetch error: {e}")
                    time.sleep(3)
        
        counts_by_type = {'UberX': 0, 'Comfort': 0, 'XL': 0, 'Black': 0}
        type_mapping = {
            'UBERX': 'UberX', 'COMFORT': 'Comfort', 'XL': 'XL', 'BLACK': 'Black',
            'UberX': 'UberX', 'Comfort': 'Comfort', 'Black': 'Black'
        }
        for driver in unique_drivers:
            raw_type = driver.get('product_type', 'UberX')
            ptype = type_mapping.get(raw_type, 'UberX')
            counts_by_type[ptype] += 1
        
        counts = {
            'total': len(unique_drivers),
            'uberx': counts_by_type['UberX'],
            'comfort': counts_by_type['Comfort'],
            'xl': counts_by_type['XL'],
            'black': counts_by_type['Black'],
        }
        
        return jsonify({
            'success': True,
            'location_name': location_name,
            'lat': lat,
            'lng': lng,
            'counts': counts,
            'samples': samples_collected
        })
    except Exception as e:
        return jsonify(success=False, message=str(e))


@app.route('/api/drivers-nearby')
@login_required
def api_drivers_nearby():
    """
    Driver scan for home page - uses the SAME cache and logic as live-drivers.
    This ensures both endpoints always return identical counts.
    """
    from datetime import datetime, timedelta
    from objects.uberDev import fetch_drivers_at_location
    
    user_id = current_user.id
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    reset = request.args.get('reset', type=str) == 'true'
    
    if lat is None or lng is None:
        lat, lng = -31.9505, 115.8605
    
    if reset:
        if user_id in driver_cache:
            del driver_cache[user_id]
    
    if user_id not in driver_cache:
        driver_cache[user_id] = {'drivers': [], 'last_cleanup': datetime.now(), 'sample_count': 0}
    
    cache = driver_cache[user_id]
    now = datetime.now()
    
    cutoff = now - timedelta(minutes=3)
    cache['drivers'] = [d for d in cache['drivers'] if d.get('timestamp', now) > cutoff]
    
    try:
        new_drivers = fetch_drivers_at_location(lat, lng)
        cache['sample_count'] += 1
        
        for driver in new_drivers:
            driver['timestamp'] = now
            match_idx = find_matching_driver_index(driver, cache['drivers'])
            if match_idx >= 0:
                cache['drivers'][match_idx]['lat'] = driver['lat']
                cache['drivers'][match_idx]['lng'] = driver['lng']
                cache['drivers'][match_idx]['bearing'] = driver.get('bearing')
                cache['drivers'][match_idx]['timestamp'] = now
            else:
                cache['drivers'].append(driver)
        
        counts_by_type = {'UberX': 0, 'Comfort': 0, 'XL': 0, 'Black': 0}
        type_mapping = {
            'UBERX': 'UberX', 'COMFORT': 'Comfort', 'XL': 'XL', 'BLACK': 'Black',
            'UberX': 'UberX', 'Comfort': 'Comfort', 'Black': 'Black'
        }
        for driver in cache['drivers']:
            raw_type = driver.get('product_type', 'UberX')
            ptype = type_mapping.get(raw_type, 'UberX')
            counts_by_type[ptype] += 1
        
        counts = {
            'total': len(cache['drivers']),
            'uberx': counts_by_type['UberX'],
            'comfort': counts_by_type['Comfort'],
            'xl': counts_by_type['XL'],
            'black': counts_by_type['Black'],
        }
        
        return jsonify({
            'success': True,
            'counts': counts,
            'sampleCount': cache['sample_count'],
            'updated': now.strftime('%H:%M:%S')
        })
    except Exception as e:
        print(f"Drivers nearby API error: {e}")
        return jsonify(success=False, message=str(e)), 500


def clear_driver_cache_for_user(user_id):
    """Clear driver cache when user logs out."""
    if user_id in driver_cache:
        del driver_cache[user_id]


@app.route('/api/hotspots')
@login_required
def api_hotspots():
    """
    Advanced hotspot prediction API with:
    - 30-minute time bins
    - Location-type specific demand curves
    - Weather-based multipliers
    - Day-specific patterns
    """
    import requests
    from datetime import datetime
    
    # Get user location if provided
    user_lat = request.args.get('lat', type=float)
    user_lng = request.args.get('lng', type=float)
    max_distance = request.args.get('max_distance', 200, type=float)
    
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    day = now.weekday()  # 0=Monday, 6=Sunday
    
    # 30-minute time slot (0-47)
    time_slot = hour * 2 + (1 if minute >= 30 else 0)
    
    # Enhanced hotspot data with more precise patterns
    # Perth, Australia Hotspots
    HOTSPOTS = [
        {
            "id": 1,
            "name": "Perth Airport (T1/T2)",
            "lat": -31.9430,
            "lng": 115.9669,
            "type": "airport",
            "baseMultiplier": 1.5,
            # Peak: early morning (5-7), afternoon (1-3pm), evening (6-8pm), night (10-12pm)
            "peakSlots": [10, 11, 12, 13, 26, 27, 28, 29, 36, 37, 38, 39, 44, 45, 46, 47],
            "description": "International and regional arrivals"
        },
        {
            "id": 2,
            "name": "Perth Airport (T3/T4)",
            "lat": -31.9288,
            "lng": 115.9525,
            "type": "airport",
            "baseMultiplier": 1.4,
            "peakSlots": [10, 11, 12, 13, 28, 29, 30, 31, 34, 35, 36, 37, 42, 43, 44, 45],
            "description": "Qantas and domestic arrivals"
        },
        {
            "id": 3,
            "name": "Perth CBD (St Georges Tce)",
            "lat": -31.9544,
            "lng": 115.8567,
            "type": "business",
            "baseMultiplier": 1.3,
            # Peak: 8-10am, 12pm, 5-7pm
            "peakSlots": [16, 17, 18, 19, 24, 25, 34, 35, 36, 37],
            "description": "Corporate offices and banking district"
        },
        {
            "id": 4,
            "name": "Northbridge Entertainment Zone",
            "lat": -31.9472,
            "lng": 115.8576,
            "type": "entertainment",
            "baseMultiplier": 1.6,
            "peakSlots": [0, 1, 2, 3, 4, 5, 6, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47],
            "description": "Bars, clubs, and late-night dining"
        },
        {
            "id": 5,
            "name": "Elizabeth Quay",
            "lat": -31.9575,
            "lng": 115.8570,
            "type": "entertainment",
            "baseMultiplier": 1.2,
            "peakSlots": [22, 23, 24, 25, 36, 37, 38, 39, 40, 41],
            "description": "Tourist hub and riverside dining"
        },
        {
            "id": 6,
            "name": "Crown Perth (Burswood)",
            "lat": -31.9598,
            "lng": 115.8943,
            "type": "entertainment",
            "baseMultiplier": 1.5,
            "peakSlots": [0, 1, 2, 3, 4, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47],
            "description": "Casino, hotels, and theaters"
        },
        {
            "id": 7,
            "name": "Optus Stadium",
            "lat": -31.9511,
            "lng": 115.8891,
            "type": "entertainment",
            "baseMultiplier": 1.4,
            "peakSlots": [34, 35, 36, 37, 38, 39, 40, 41, 42, 43],
            "description": "Events and stadium traffic"
        },
        {
            "id": 8,
            "name": "Karrinyup Shopping Centre",
            "lat": -31.8767,
            "lng": 115.7839,
            "type": "shopping",
            "baseMultiplier": 1.3,
            "peakSlots": [20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37],
            "description": "Major shopping hub"
        },
        {
            "id": 9,
            "name": "Westfield Carousel",
            "lat": -32.0201,
            "lng": 115.9397,
            "type": "shopping",
            "baseMultiplier": 1.2,
            "peakSlots": [20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37],
            "description": "South-east shopping and cinema"
        },
        {
            "id": 10,
            "name": "Fremantle Markets",
            "lat": -32.0569,
            "lng": 115.7483,
            "type": "shopping",
            "baseMultiplier": 1.4,
            "peakSlots": [20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33],
            "description": "Weekend tourist and market traffic"
        },
        {
            "id": 11,
            "name": "Subiaco (Rokeby Road)",
            "lat": -31.9478,
            "lng": 115.8239,
            "type": "business",
            "baseMultiplier": 1.2,
            "peakSlots": [16, 17, 18, 19, 24, 25, 36, 37, 38, 39, 40, 41],
            "description": "Boutique offices and dining"
        },
        {
            "id": 12,
            "name": "Scarborough Beach",
            "lat": -31.8943,
            "lng": 115.7565,
            "type": "entertainment",
            "baseMultiplier": 1.3,
            "peakSlots": [30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41],
            "description": "Beachfront dining and sunset traffic"
        },
        {
            "id": 13,
            "name": "Joondalup City Centre",
            "lat": -31.7456,
            "lng": 115.7667,
            "type": "business",
            "baseMultiplier": 1.1,
            "peakSlots": [16, 17, 18, 19, 24, 25, 34, 35, 36, 37],
            "description": "Northern hub, university, and hospital"
        },
        {
            "id": 14,
            "name": "Curtin University",
            "lat": -32.0038,
            "lng": 115.8944,
            "type": "education",
            "baseMultiplier": 1.0,
            "peakSlots": [16, 17, 18, 19, 24, 25, 32, 33, 34, 35, 36, 37],
            "description": "Student commute peaks"
        },
        {
            "id": 15,
            "name": "QEII Medical Centre",
            "lat": -31.9667,
            "lng": 115.8167,
            "type": "business",
            "baseMultiplier": 1.1,
            "peakSlots": [14, 15, 16, 17, 24, 25, 30, 31, 32, 33, 34, 35, 36, 37],
            "description": "Hospitals and medical offices"
        }
    ]
    
    # Time period analysis (more granular 30-min slots)
    def get_time_period_info(slot):
        if slot < 10:  # 0:00-5:00
            return {"name": "Late Night", "multiplier": 0.5, "emoji": ""}
        elif slot < 14:  # 5:00-7:00
            return {"name": "Early Morning", "multiplier": 0.9, "emoji": ""}
        elif slot < 18:  # 7:00-9:00
            return {"name": "Morning Rush", "multiplier": 1.5, "emoji": ""}
        elif slot < 24:  # 9:00-12:00
            return {"name": "Late Morning", "multiplier": 0.85, "emoji": ""}
        elif slot < 28:  # 12:00-14:00
            return {"name": "Lunch Time", "multiplier": 1.25, "emoji": ""}
        elif slot < 34:  # 14:00-17:00
            return {"name": "Afternoon", "multiplier": 0.75, "emoji": ""}
        elif slot < 40:  # 17:00-20:00
            return {"name": "Evening Rush", "multiplier": 1.6, "emoji": ""}
        elif slot < 46:  # 20:00-23:00
            return {"name": "Night Life", "multiplier": 1.3, "emoji": ""}
        else:  # 23:00-24:00
            return {"name": "Late Night", "multiplier": 0.7, "emoji": ""}
    
    def get_day_info(d):
        days = {
            0: {"name": "Monday", "multiplier": 1.0, "type": "weekday"},
            1: {"name": "Tuesday", "multiplier": 1.0, "type": "weekday"},
            2: {"name": "Wednesday", "multiplier": 1.0, "type": "weekday"},
            3: {"name": "Thursday", "multiplier": 1.05, "type": "weekday"},
            4: {"name": "Friday", "multiplier": 1.3, "type": "weekend"},
            5: {"name": "Saturday", "multiplier": 1.2, "type": "weekend"},
            6: {"name": "Sunday", "multiplier": 0.9, "type": "weekend"}
        }
        return days.get(d, {"name": "Unknown", "multiplier": 1.0, "type": "weekday"})
    
    # Weather integration (cached for 30 minutes)
    weather_data = None
    weather_multiplier = 1.0
    weather_description = None
    
    try:
        # Check cache first
        weather_cache_key = f"weather_{now.strftime('%Y%m%d%H')}"
        cached_weather = cache.get_cached(0, weather_cache_key)
        
        if cached_weather:
            weather_data = cached_weather
        else:
            # Fetch from OpenWeather (free tier)
            api_key = os.environ.get('OPENWEATHER_API_KEY')
            if api_key:
                resp = requests.get(
                    f"https://api.openweathermap.org/data/2.5/weather?lat=-31.9505&lon=115.8605&appid={api_key}&units=metric",
                    timeout=5
                )
                if resp.status_code == 200:
                    weather_data = resp.json()
                    cache.set_cached(0, weather_cache_key, weather_data)
    except Exception as e:
        print(f"Weather API error: {e}")
    
    if weather_data:
        main = weather_data.get('main', {})
        weather = weather_data.get('weather', [{}])[0]
        weather_id = weather.get('id', 800)
        temp = main.get('temp', 25)
        
        weather_description = weather.get('description', '').title()
        
        # Rain increases demand significantly
        if 200 <= weather_id < 600:  # Thunderstorm or Rain
            weather_multiplier = 1.5
            weather_description = f"Rainy ({weather_description}) - High Demand"
        elif 600 <= weather_id < 700:  # Snow
            weather_multiplier = 1.6
            weather_description = f"Snow - Very High Demand"
        elif 700 <= weather_id < 800:  # Fog/Mist
            weather_multiplier = 1.2
            weather_description = f"Low Visibility - Moderate Boost"
        elif temp > 38:  # Extreme heat
            weather_multiplier = 1.3
            weather_description = f"Extreme Heat ({temp:.0f}°C) - High Demand"
        elif temp < 10:  # Cold
            weather_multiplier = 1.2
            weather_description = f"Cold ({temp:.0f}°C) - Moderate Boost"
        else:
            weather_description = f"Clear ({temp:.0f}°C)"
    
    time_info = get_time_period_info(time_slot)
    day_info = get_day_info(day)
    is_weekend = day_info['type'] == 'weekend'
    
    # Live flight integration - boost airport demand based on actual arrivals
    flight_boost = 1.0
    flights_next_hour = 0
    next_flight_info = None
    
    try:
        from datetime import timezone, timedelta
        perth_tz = timezone(timedelta(hours=8))
        perth_now = datetime.now(perth_tz)
        current_time = perth_now.strftime('%H:%M')
        current_hour = perth_now.hour
        current_minute = perth_now.minute
        
        # Get flight data from cache or API
        flight_cache_key = f"flights_all"
        flight_data = cache.get_cached('global', flight_cache_key)
        
        if flight_data is None:
            try:
                response = flightArrivals(None)
                if response and response.status_code == 200:
                    flight_data = response.json()
                    cache.set_cached('global', flight_cache_key, flight_data)
            except Exception as fe:
                print(f"Flight API error: {fe}")
        
        if flight_data:
            flights = flight_data.get('flights', [])
            
            # Count flights arriving in next 60 minutes
            for flight in flights:
                if flight.get('landed', False):
                    continue
                    
                flight_time = flight.get('time', '')
                if not flight_time:
                    continue
                
                try:
                    fh, fm = map(int, flight_time.split(':'))
                    # Calculate minutes until arrival
                    flight_mins = fh * 60 + fm
                    now_mins = current_hour * 60 + current_minute
                    mins_until = flight_mins - now_mins
                    
                    # Handle midnight wrap
                    if mins_until < -60:
                        mins_until += 1440
                    
                    # Count flights arriving in 0-60 minutes
                    if 0 <= mins_until <= 60:
                        flights_next_hour += 1
                        
                        # Track the next flight
                        if next_flight_info is None or mins_until < next_flight_info['mins']:
                            next_flight_info = {
                                'time': flight_time,
                                'mins': mins_until,
                                'origin': flight.get('origin', 'Unknown'),
                                'airline': flight.get('airline', ''),
                                'terminal': flight.get('terminal', '')
                            }
                except Exception:
                    continue
            
            # Calculate flight-based boost (more flights = higher demand)
            if flights_next_hour >= 10:
                flight_boost = 1.8  # Very busy
            elif flights_next_hour >= 6:
                flight_boost = 1.5  # Busy
            elif flights_next_hour >= 3:
                flight_boost = 1.3  # Moderate
            elif flights_next_hour >= 1:
                flight_boost = 1.15  # Light activity
            
            print(f"Flight boost: {flight_boost}x ({flights_next_hour} flights in next hour)")
    except Exception as e:
        print(f"Flight integration error: {e}")
    
    # Event integration - boost entertainment venues when events are happening today
    event_boost = 1.0
    today_events = []
    event_venues_boost = {}  # venue name -> boost multiplier
    
    try:
        event_cache_key = f"events_{now.strftime('%Y%m%d%H')}"
        cached_events = cache.get_cached('global', event_cache_key)
        
        if cached_events:
            today_str = now.strftime('%Y-%m-%d')
            for event in cached_events:
                event_date = event.get('date', '')
                if event_date == today_str:
                    today_events.append(event)
                    
                    # Calculate time-based boost for this event
                    event_time = event.get('time', '')
                    if event_time:
                        try:
                            eh, em = map(int, event_time.split(':')[:2])
                            event_mins = eh * 60 + em
                            now_mins = hour * 60 + minute
                            mins_until = event_mins - now_mins
                            
                            venue_name = event.get('venue', '').lower()
                            expected_crowd = event.get('expectedCrowd', 5000)
                            
                            # Boost calculation based on proximity to event time
                            boost = 1.0
                            if -90 <= mins_until <= 60:  # Event ending or about to start
                                if expected_crowd >= 40000:
                                    boost = 2.0  # Massive event
                                elif expected_crowd >= 15000:
                                    boost = 1.7  # Large event
                                elif expected_crowd >= 5000:
                                    boost = 1.4  # Medium event
                                else:
                                    boost = 1.2  # Small event
                            elif mins_until < -90 and mins_until > -180:  # Post-event surge
                                if expected_crowd >= 40000:
                                    boost = 2.5  # Massive post-event surge
                                elif expected_crowd >= 15000:
                                    boost = 1.9
                                else:
                                    boost = 1.5
                            
                            # Map venue names to hotspot names
                            if 'optus' in venue_name or 'stadium' in venue_name:
                                event_venues_boost['optus stadium'] = max(event_venues_boost.get('optus stadium', 1.0), boost)
                            if 'rac arena' in venue_name or 'perth arena' in venue_name:
                                event_venues_boost['rac arena'] = max(event_venues_boost.get('rac arena', 1.0), boost)
                            if 'crown' in venue_name:
                                event_venues_boost['crown perth'] = max(event_venues_boost.get('crown perth', 1.0), boost)
                        except:
                            pass
            
            if today_events:
                print(f"Event boost: {len(today_events)} events today, venue boosts: {event_venues_boost}")
    except Exception as e:
        print(f"Event integration error: {e}")
    
    # Calculate distance helper
    def calc_distance(lat1, lon1, lat2, lon2):
        from math import radians, sin, cos, sqrt, atan2
        R = 6371
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        return R * 2 * atan2(sqrt(a), sqrt(1-a))
    
    # Process each hotspot
    hotspots_result = []
    for h in HOTSPOTS:
        # Base calculation
        demand = h['baseMultiplier'] * time_info['multiplier'] * day_info['multiplier']
        
        # Peak slot bonus
        if time_slot in h.get('peakSlots', []):
            demand *= 1.5
        
        # Weekend adjustment
        if is_weekend:
            demand *= h.get('weekendBoost', 1.0)
        
        # Weather boost
        demand *= weather_multiplier
        
        # Apply live flight boost to airport hotspots
        is_airport = h['type'] == 'airport'
        if is_airport and flight_boost > 1.0:
            demand *= flight_boost
        
        # Apply event boost to entertainment venues
        event_boost_applied = None
        if h['type'] == 'entertainment' and event_venues_boost:
            hotspot_name_lower = h['name'].lower()
            for venue_key, boost in event_venues_boost.items():
                if venue_key in hotspot_name_lower:
                    demand *= boost
                    event_boost_applied = boost
                    break
        
        # Cap at 3.5x
        demand = min(demand, 3.5)
        
        # Determine demand level
        if demand >= 2.0:
            level = "high"
            color = "#ef4444"
        elif demand >= 1.2:
            level = "medium"
            color = "#f59e0b"
        else:
            level = "low"
            color = "#22c55e"
        
        hotspot_data = {
            "id": h['id'],
            "name": h['name'],
            "lat": h['lat'],
            "lng": h['lng'],
            "type": h['type'],
            "demand": round(demand, 2),
            "level": level,
            "color": color,
            "description": h['description'],
            "isPeak": time_slot in h.get('peakSlots', []),
            "flightBoost": flight_boost if is_airport and flight_boost > 1.0 else None,
            "flightsNearby": flights_next_hour if is_airport else None,
            "eventBoost": event_boost_applied if event_boost_applied and event_boost_applied > 1.0 else None
        }
        
        # Add distance if user location provided
        if user_lat and user_lng:
            distance = calc_distance(user_lat, user_lng, h['lat'], h['lng'])
            hotspot_data['distance'] = round(distance, 1)
            
            # Only include if within max distance
            if distance <= max_distance:
                hotspots_result.append(hotspot_data)
        else:
            hotspots_result.append(hotspot_data)
    
    # Sort by demand (highest first)
    hotspots_result.sort(key=lambda x: x['demand'], reverse=True)
    
    # If user location, sort nearby ones by distance first
    if user_lat and user_lng:
        nearby = [h for h in hotspots_result if h.get('distance', 999) <= 15]
        far = [h for h in hotspots_result if h.get('distance', 999) > 15]
        nearby.sort(key=lambda x: x['demand'], reverse=True)
        far.sort(key=lambda x: x.get('distance', 999))
        hotspots_result = nearby + far
    
    # Calculate overall demand
    if hotspots_result:
        avg_demand = sum(h['demand'] for h in hotspots_result) / len(hotspots_result)
    else:
        avg_demand = 1.0
    
    if avg_demand >= 2.0:
        overall_level = "high"
    elif avg_demand >= 1.2:
        overall_level = "medium"
    else:
        overall_level = "low"
    
    # Generate smart tips based on current conditions
    tips = []
    if time_info['name'] == "Morning Rush":
        tips.append("Morning commuters heading to work - focus on residential areas")
    elif time_info['name'] == "Evening Rush":
        tips.append("Evening rush hour - position near business districts")
    elif time_info['name'] == "Night Life":
        tips.append("Nightlife peak - entertainment areas and restaurants are hot")
    
    if weather_multiplier > 1.2:
        tips.append("Bad weather = surge pricing - stay active!")
    
    if is_weekend:
        tips.append("Weekend mode - malls and entertainment spots are busiest")
    else:
        tips.append("Weekday - business areas peak during office hours")
    
    # Airport tip based on LIVE flight data
    if flights_next_hour > 0:
        if next_flight_info:
            mins = next_flight_info['mins']
            origin = next_flight_info['origin']
            if mins <= 20:
                tips.insert(0, f"Flight from {origin} landing in {mins} mins - head to airport NOW!")
            elif mins <= 40:
                tips.insert(0, f"{flights_next_hour} flights landing soon - airport demand is HIGH")
            else:
                tips.append(f"{flights_next_hour} flights in next hour - airport will get busy")
    
    # Event tips
    if today_events:
        for event in today_events[:2]:
            event_time = event.get('time', '')
            venue = event.get('venue', '')
            if event_time and venue:
                try:
                    eh, em = map(int, event_time.split(':')[:2])
                    event_mins = eh * 60 + em
                    now_mins = hour * 60 + minute
                    mins_until = event_mins - now_mins
                    
                    if 0 <= mins_until <= 90:
                        tips.insert(0, f"Event at {venue} starting in {mins_until} mins - expect surge!")
                    elif -180 <= mins_until < -30:
                        tips.insert(0, f"Post-event surge at {venue} - high demand NOW")
                except:
                    pass
    
    return jsonify({
        "success": True,
        "hotspots": hotspots_result[:20],  # Top 20
        "analysis": {
            "timePeriod": time_info['name'],
            "timeEmoji": time_info.get('emoji', ''),
            "dayName": day_info['name'],
            "dayType": day_info['type'],
            "timeSlot": time_slot,
            "overallDemand": round(avg_demand, 2),
            "overallLevel": overall_level,
            "weather": weather_description,
            "weatherMultiplier": weather_multiplier,
            "flightsNextHour": flights_next_hour,
            "nextFlight": next_flight_info,
            "flightBoost": flight_boost if flight_boost > 1.0 else None,
            "todayEventsCount": len(today_events),
            "eventVenuesBoost": list(event_venues_boost.keys()) if event_venues_boost else None
        },
        "tips": tips[:4],
        "updated": now.strftime("%H:%M")
    })


@app.route('/api/events')
@login_required
def api_events():
    """
    Fetch Perth events from Ticketmaster API.
    Requires TICKETMASTER_API_KEY secret.
    """
    import requests
    
    api_key = os.environ.get('TICKETMASTER_API_KEY')
    
    # Perth venue IDs for major venues
    PERTH_VENUES = {
        'optus_stadium': {'id': 'ZFr9jZea7A', 'name': 'Optus Stadium', 'lat': -31.9511, 'lng': 115.8891},
        'rac_arena': {'id': 'ZFr9jZe7aA', 'name': 'RAC Arena', 'lat': -31.9448, 'lng': 115.8534},
        'crown_perth': {'id': 'ZFr9jZeaea', 'name': 'Crown Perth', 'lat': -31.9598, 'lng': 115.8943},
    }
    
    try:
        # Check cache first (cache for 1 hour)
        from datetime import datetime, timedelta
        now = datetime.now()
        cache_key = f"events_{now.strftime('%Y%m%d%H')}"
        cached_events = cache.get_cached('global', cache_key)
        
        if cached_events:
            return jsonify(success=True, events=cached_events, source='cache')
        
        if not api_key:
            # Return sample events without API key
            sample_events = [
                {
                    "name": "AFL Match - Fremantle vs West Coast",
                    "venue": "Optus Stadium",
                    "venueLat": -31.9511,
                    "venueLng": 115.8891,
                    "date": now.strftime("%Y-%m-%d"),
                    "time": "19:00",
                    "type": "sports",
                    "expectedCrowd": 55000
                },
                {
                    "name": "Perth Scorchers vs Melbourne Stars",
                    "venue": "Optus Stadium",
                    "venueLat": -31.9511,
                    "venueLng": 115.8891,
                    "date": now.strftime("%Y-%m-%d"),
                    "time": "18:30",
                    "type": "sports",
                    "expectedCrowd": 30000
                },
                {
                    "name": "Concert at RAC Arena",
                    "venue": "RAC Arena",
                    "venueLat": -31.9448,
                    "venueLng": 115.8534,
                    "date": now.strftime("%Y-%m-%d"),
                    "time": "20:00",
                    "type": "music",
                    "expectedCrowd": 15000
                }
            ]
            cache.set_cached('global', cache_key, sample_events)
            return jsonify(success=True, events=sample_events, source='sample', message='Add TICKETMASTER_API_KEY for live data')
        
        # Fetch from Ticketmaster Discovery API
        events_list = []
        response = requests.get(
            'https://app.ticketmaster.com/discovery/v2/events.json',
            params={
                'apikey': api_key,
                'city': 'Perth',
                'stateCode': 'WA',
                'countryCode': 'AU',
                'size': 50,
                'sort': 'date,asc'
            },
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            events = data.get('_embedded', {}).get('events', [])
            
            for event in events[:20]:
                venue_info = event.get('_embedded', {}).get('venues', [{}])[0]
                date_info = event.get('dates', {}).get('start', {})
                
                events_list.append({
                    "name": event.get('name', 'Unknown Event'),
                    "venue": venue_info.get('name', 'Unknown Venue'),
                    "venueLat": float(venue_info.get('location', {}).get('latitude', 0)),
                    "venueLng": float(venue_info.get('location', {}).get('longitude', 0)),
                    "date": date_info.get('localDate', ''),
                    "time": date_info.get('localTime', ''),
                    "type": event.get('classifications', [{}])[0].get('segment', {}).get('name', 'other').lower(),
                    "url": event.get('url', ''),
                    "image": event.get('images', [{}])[0].get('url', '')
                })
            
            cache.set_cached('global', cache_key, events_list)
            return jsonify(success=True, events=events_list, source='ticketmaster')
        else:
            return jsonify(success=False, message=f'API error: {response.status_code}')
            
    except Exception as e:
        print(f"Events API error: {e}")
        return jsonify(success=False, message=str(e))


@app.route('/events')
@login_required
def events_page():
    return redirect(url_for('demand_intel'))


@app.route('/surge-map')
@login_required
def surge_map_page():
    return redirect(url_for('demand_intel'))


@app.route('/smart-route')
@login_required
def smart_route_page():
    return render_template('smart_route.html')


@app.route('/api/airport-queue')
@login_required
def api_airport_queue():
    """
    Predict airport queue times based on flight arrivals and historical patterns.
    Returns estimated wait times for pickup queue at Perth Airport.
    """
    from datetime import datetime, timezone, timedelta
    
    perth_tz = timezone(timedelta(hours=8))
    now = datetime.now(perth_tz)
    hour = now.hour
    day = now.weekday()
    
    # Base queue times by time of day (minutes) - Perth historical patterns
    base_queue = {
        0: 5, 1: 5, 2: 5, 3: 5, 4: 10, 5: 15,   # Early morning
        6: 20, 7: 25, 8: 25, 9: 20, 10: 15, 11: 15,  # Morning
        12: 15, 13: 18, 14: 20, 15: 22, 16: 25, 17: 28,  # Afternoon
        18: 25, 19: 22, 20: 18, 21: 20, 22: 25, 23: 15   # Evening/Night
    }
    
    # Weekend adjustment (busier on weekends)
    weekend_mult = 1.3 if day >= 5 else 1.0
    
    # Get live flight data
    flights_next_hour = 0
    upcoming_flights = []
    
    try:
        flight_cache_key = "flights_all"
        flight_data = cache.get_cached('global', flight_cache_key)
        
        if flight_data:
            flights = flight_data.get('flights', [])
            current_mins = hour * 60 + now.minute
            
            for flight in flights:
                if flight.get('landed', False):
                    continue
                flight_time = flight.get('time', '')
                if not flight_time:
                    continue
                try:
                    fh, fm = map(int, flight_time.split(':'))
                    flight_mins = fh * 60 + fm
                    mins_until = flight_mins - current_mins
                    if mins_until < -60:
                        mins_until += 1440
                    
                    if 0 <= mins_until <= 90:
                        flights_next_hour += 1
                        upcoming_flights.append({
                            'time': flight_time,
                            'mins': mins_until,
                            'origin': flight.get('origin', 'Unknown'),
                            'airline': flight.get('airline', ''),
                            'terminal': flight.get('terminal', 'T1')
                        })
                except:
                    continue
            
            upcoming_flights.sort(key=lambda x: x['mins'])
    except Exception as e:
        print(f"Queue prediction error: {e}")
    
    # Calculate queue estimate
    base_wait = base_queue.get(hour, 15)
    
    # Flight-based adjustment
    if flights_next_hour >= 10:
        flight_mult = 2.5
        queue_status = "Very Long"
    elif flights_next_hour >= 6:
        flight_mult = 1.8
        queue_status = "Long"
    elif flights_next_hour >= 3:
        flight_mult = 1.3
        queue_status = "Moderate"
    elif flights_next_hour >= 1:
        flight_mult = 1.1
        queue_status = "Short"
    else:
        flight_mult = 0.8
        queue_status = "Very Short"
    
    estimated_wait = round(base_wait * weekend_mult * flight_mult)
    
    # Queue position estimate (based on typical drivers waiting)
    queue_position = round(estimated_wait / 3)
    
    # Best strategy recommendation
    if estimated_wait > 30:
        strategy = "Consider waiting in remote lot and timing arrival with flight landing"
    elif estimated_wait > 15:
        strategy = "Join queue 10-15 mins before next flight lands for optimal positioning"
    else:
        strategy = "Queue is short - head to airport now for quick pickup"
    
    return jsonify({
        "success": True,
        "queue": {
            "estimatedWait": estimated_wait,
            "queuePosition": queue_position,
            "status": queue_status,
            "flightsNext90": flights_next_hour,
            "upcomingFlights": upcoming_flights[:5],
            "strategy": strategy
        },
        "terminals": {
            "T1T2": {
                "name": "International/Regional (T1/T2)",
                "lat": -31.9430,
                "lng": 115.9669,
                "waitMins": estimated_wait
            },
            "T3T4": {
                "name": "Domestic (T3/T4)",
                "lat": -31.9288,
                "lng": 115.9525,
                "waitMins": round(estimated_wait * 0.8)  # Usually shorter
            }
        },
        "updated": now.strftime("%H:%M")
    })


@app.route('/api/smart-route')
@login_required
def api_smart_route():
    """
    Smart Route Planner - recommend next destination after a ride.
    Considers current position, nearby hotspots, demand levels, and distance.
    """
    from datetime import datetime
    from math import radians, sin, cos, sqrt, atan2
    
    # Get current position from query params
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    
    if not lat or not lng:
        return jsonify(success=False, message="Current location required"), 400
    
    def calc_distance(lat1, lon1, lat2, lon2):
        R = 6371
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        return R * 2 * atan2(sqrt(a), sqrt(1-a))
    
    # Get current hotspots data
    try:
        # Fetch hotspots (reuse internal logic)
        import requests as req_lib
        # Make internal request to hotspots API
        # For efficiency, we'll calculate inline
        
        now = datetime.now()
        hour = now.hour
        time_slot = hour * 2 + (1 if now.minute >= 30 else 0)
        is_weekend = now.weekday() >= 5
        
        # Perth hotspots with current multipliers
        HOTSPOTS = [
            {"name": "Perth Airport (T1/T2)", "lat": -31.9430, "lng": 115.9669, "type": "airport", "base": 1.5},
            {"name": "Perth CBD", "lat": -31.9544, "lng": 115.8567, "type": "business", "base": 1.3},
            {"name": "Northbridge", "lat": -31.9472, "lng": 115.8576, "type": "entertainment", "base": 1.6},
            {"name": "Crown Perth", "lat": -31.9598, "lng": 115.8943, "type": "entertainment", "base": 1.5},
            {"name": "Optus Stadium", "lat": -31.9511, "lng": 115.8891, "type": "entertainment", "base": 1.4},
            {"name": "Fremantle", "lat": -32.0569, "lng": 115.7439, "type": "entertainment", "base": 1.3},
            {"name": "Scarborough Beach", "lat": -31.8931, "lng": 115.7577, "type": "leisure", "base": 1.2},
            {"name": "Karrinyup Mall", "lat": -31.8767, "lng": 115.7839, "type": "shopping", "base": 1.2},
        ]
        
        # Time-based multipliers
        if hour >= 17 and hour <= 20:
            time_mult = 1.6  # Evening rush
        elif hour >= 7 and hour <= 9:
            time_mult = 1.5  # Morning rush
        elif hour >= 21 or hour <= 2:
            time_mult = 1.4  # Nightlife
        else:
            time_mult = 1.0
        
        recommendations = []
        for h in HOTSPOTS:
            distance = calc_distance(lat, lng, h['lat'], h['lng'])
            
            # Calculate demand score
            demand = h['base'] * time_mult
            if is_weekend and h['type'] in ['entertainment', 'leisure', 'shopping']:
                demand *= 1.2
            
            # Score = demand / sqrt(distance) - prioritize high demand nearby
            score = demand / max(sqrt(distance), 0.5)
            
            # Estimate drive time (rough: 30km/h average in city)
            drive_mins = round((distance / 30) * 60)
            
            recommendations.append({
                "name": h['name'],
                "lat": h['lat'],
                "lng": h['lng'],
                "type": h['type'],
                "distance": round(distance, 1),
                "driveMins": drive_mins,
                "demand": round(demand, 2),
                "score": round(score, 2)
            })
        
        # Sort by score (highest first)
        recommendations.sort(key=lambda x: x['score'], reverse=True)
        
        top_rec = recommendations[0] if recommendations else None
        
        return jsonify({
            "success": True,
            "currentLocation": {"lat": lat, "lng": lng},
            "topRecommendation": top_rec,
            "alternatives": recommendations[1:4],
            "tips": [
                f"Head to {top_rec['name']} ({top_rec['driveMins']} min drive)" if top_rec else "Stay in current area",
                "Demand peaks in 30 mins - position early" if hour in [7, 17] else None,
                "Weekend entertainment areas are busy tonight" if is_weekend and hour >= 18 else None
            ],
            "updated": now.strftime("%H:%M")
        })
        
    except Exception as e:
        print(f"Smart route error: {e}")
        return jsonify(success=False, message=str(e)), 500


@app.route('/api/surge-map')
@login_required
def api_surge_map():
    """
    Real-time surge pricing map using Uber Price Estimates.
    Returns surge multipliers for key Perth areas.
    Note: Requires Uber API credentials or uses prediction-based estimates.
    """
    from datetime import datetime
    
    now = datetime.now()
    hour = now.hour
    day = now.weekday()
    is_weekend = day >= 5
    
    # Predicted surge multipliers based on time/day patterns
    # These would be replaced with live Uber API data if credentials available
    
    def predict_surge(location_type, hour, is_weekend):
        """Predict surge multiplier based on patterns."""
        base = 1.0
        
        # Time-based patterns
        if hour >= 17 and hour <= 19:  # Evening rush
            base = 1.8
        elif hour >= 7 and hour <= 9:  # Morning rush
            base = 1.5
        elif hour >= 22 or hour <= 2:  # Late night
            base = 2.0
        elif hour >= 12 and hour <= 14:  # Lunch
            base = 1.3
        
        # Location type adjustments
        if location_type == 'airport':
            base *= 1.2
        elif location_type == 'entertainment' and (is_weekend or hour >= 18):
            base *= 1.4
        elif location_type == 'business' and not is_weekend:
            base *= 1.1
        
        # Weekend adjustment
        if is_weekend and location_type in ['entertainment', 'leisure']:
            base *= 1.3
        
        return round(min(base, 3.5), 1)  # Cap at 3.5x
    
    # Generate surge data for Perth areas
    surge_zones = [
        {"id": 1, "name": "Perth Airport", "lat": -31.9430, "lng": 115.9669, "type": "airport"},
        {"id": 2, "name": "Perth CBD", "lat": -31.9544, "lng": 115.8567, "type": "business"},
        {"id": 3, "name": "Northbridge", "lat": -31.9472, "lng": 115.8576, "type": "entertainment"},
        {"id": 4, "name": "Crown Perth", "lat": -31.9598, "lng": 115.8943, "type": "entertainment"},
        {"id": 5, "name": "Optus Stadium", "lat": -31.9511, "lng": 115.8891, "type": "entertainment"},
        {"id": 6, "name": "Elizabeth Quay", "lat": -31.9575, "lng": 115.8570, "type": "entertainment"},
        {"id": 7, "name": "Fremantle", "lat": -32.0569, "lng": 115.7439, "type": "entertainment"},
        {"id": 8, "name": "Subiaco", "lat": -31.9458, "lng": 115.8264, "type": "residential"},
        {"id": 9, "name": "Cottesloe Beach", "lat": -31.9928, "lng": 115.7526, "type": "leisure"},
        {"id": 10, "name": "Scarborough", "lat": -31.8931, "lng": 115.7577, "type": "leisure"},
    ]
    
    for zone in surge_zones:
        zone['surge'] = predict_surge(zone['type'], hour, is_weekend)
        zone['level'] = 'high' if zone['surge'] >= 2.0 else ('medium' if zone['surge'] >= 1.5 else 'low')
        zone['color'] = '#ef4444' if zone['surge'] >= 2.0 else ('#f59e0b' if zone['surge'] >= 1.5 else '#22c55e')
    
    # Sort by surge (highest first)
    surge_zones.sort(key=lambda x: x['surge'], reverse=True)
    
    # Calculate overall city surge level
    avg_surge = sum(z['surge'] for z in surge_zones) / len(surge_zones)
    
    return jsonify({
        "success": True,
        "zones": surge_zones,
        "overall": {
            "avgSurge": round(avg_surge, 1),
            "level": 'high' if avg_surge >= 1.8 else ('medium' if avg_surge >= 1.3 else 'low'),
            "peakAreas": [z['name'] for z in surge_zones[:3]]
        },
        "note": "Predictions based on historical patterns. Connect Uber account for live surge data.",
        "updated": now.strftime("%H:%M")
    })


@app.route('/fetch-ride')
@login_required
def fetch_ride():
    has_permission = current_user.has_permission('can_fetch_ride')

    if not has_permission:
        return render_template('ride_details.html',
                               has_permission=False,
                               ride_data=None,
                               default_vehicle=None,
                               loading=False)

    if not current_user.uber_connected:
        flash('Please connect your Uber account first.', 'error')
        return redirect(url_for('uber_connect'))

    return render_template('ride_details.html',
                           has_permission=True,
                           ride_data=None,
                           default_vehicle=None,
                           loading=True)


@app.route('/api/fetch-ride-data')
@login_required
def fetch_ride_data():
    if not current_user.has_permission('can_fetch_ride'):
        return jsonify(error="No permission"), 403

    if not current_user.uber_connected:
        return jsonify(error="Uber not connected"), 400

    default_vehicle = None
    try:
        cookies, headers, refresh_token = current_user.get_uber_credentials()

        def fetch_vehicles():
            return vehicleDetails(cookies, headers, refresh_token)

        vehicles = cache.get_vehicles(current_user.id, fetch_vehicles)
        for v in vehicles:
            if v.get('isDefault'):
                default_vehicle = v
                break
    except Exception as e:
        print(f"Error fetching vehicle: {e}")

    try:
        cookies, headers, refresh_token = current_user.get_uber_credentials()

        def fetch_ride():
            return appLaunch(cookies, headers, refresh_token)

        ride_data = cache.get_active_ride(current_user.id, fetch_ride)
        if ride_data and isinstance(ride_data, dict):
            return jsonify(success=True,
                           ride_data=ride_data,
                           default_vehicle=default_vehicle)
        else:
            return jsonify(success=True,
                           ride_data=None,
                           default_vehicle=default_vehicle)
    except Exception as e:
        print(f"Error fetching ride data: {e}")
        return jsonify(success=True,
                       ride_data=None,
                       default_vehicle=default_vehicle)


@app.route('/api/cancel-ride-simulation', methods=['POST'])
@login_required
def cancel_ride_simulation():
    if not current_user.has_permission('can_fetch_ride'):
        return jsonify(error="No permission"), 403

    if not current_user.uber_connected:
        return jsonify(error="Uber not connected"), 400

    data = request.get_json()
    step = data.get('step', 'start')
    pickup_lat = data.get('pickup_lat')
    pickup_lng = data.get('pickup_lng')
    dropoff_lat = data.get('dropoff_lat')
    dropoff_lng = data.get('dropoff_lng')

    try:
        cookies, headers, refresh_token = current_user.get_uber_credentials()

        if step == 'start':
            result = updateLocationOnce(pickup_lat, pickup_lng, cookies,
                                        headers, refresh_token)
            return jsonify(success=True,
                           step='pickup',
                           message='Moved to pickup location')

        elif step == 'intermediate':
            lat = data.get('lat')
            lng = data.get('lng')
            point_num = data.get('point_num', 1)
            result = updateLocationOnce(lat, lng, cookies, headers,
                                        refresh_token)
            return jsonify(success=True,
                           step='intermediate',
                           message=f'Route point {point_num}')

        elif step == 'dropoff':
            result = updateLocationOnce(dropoff_lat, dropoff_lng, cookies,
                                        headers, refresh_token)
            return jsonify(success=True,
                           step='dropoff',
                           message='Arrived at dropoff')

        elif step == 'hold_dropoff':
            result = updateLocationOnce(dropoff_lat, dropoff_lng, cookies,
                                        headers, refresh_token)
            return jsonify(success=True,
                           step='hold_dropoff',
                           message='Holding at dropoff')

        else:
            return jsonify(error="Invalid step"), 400

    except Exception as e:
        print(f"Error in cancel ride simulation: {e}")
        return jsonify(error=str(e)), 500


@app.route('/submit', methods=['POST'])
@login_required
def submit():
    if not current_user.uber_connected:
        return jsonify(status="error", message="Uber account not connected")

    import json
    cookies = json.loads(decrypt_data(current_user.uber_cookies))
    headers = json.loads(decrypt_data(current_user.uber_headers))
    refresh_token = decrypt_data(current_user.uber_refresh_token)

    config.stored_destination = request.form.get('destination')
    response = driverLocation(config.stored_destination, cookies, headers,
                              refresh_token)
    print(f"Destination Saved: {config.stored_destination}")
    return jsonify(status="success")


@app.route('/stop', methods=['POST'])
@login_required
def stop():
    config.stop_signal = 1
    print(
        f"Stop signal received. Variable 'stop_signal' set to: {config.stop_signal}"
    )
    return jsonify(status="success", value=config.stop_signal)


@app.route('/flight-center')
@login_required
def flight_center():
    return render_template('flight_center.html')


@app.route('/api/flight-details')
@login_required
def api_flight_details():
    from datetime import datetime, timezone, timedelta
    from collections import defaultdict

    try:
        terminal = request.args.get('terminal', None)
        response = flightArrivals(terminal)

        if response is None:
            return jsonify({'success': False, 'message': 'API unavailable'})

        data = response.json()
        flights = data.get('flights', [])
        terminals = data.get('terminals', [])

        perth_tz = timezone(timedelta(hours=8))
        perth_now = datetime.now(perth_tz)
        current_time = perth_now.strftime('%H:%M')

        flights_by_terminal = defaultdict(list)
        next_arrival = None
        upcoming_count = 0
        landed_count = 0

        for flight in flights:
            term = flight.get('terminal', 'Unknown')
            flight_time = flight.get('time', '')
            is_landed = flight.get('landed', False)

            if is_landed:
                landed_count += 1
                continue

            upcoming_count += 1
            flights_by_terminal[term].append(flight)

            if next_arrival is None and flight_time >= current_time:
                next_arrival = flight_time

        print(
            f"Flight filter: {upcoming_count} upcoming, {landed_count} already landed"
        )

        for term in flights_by_terminal:
            flights_by_terminal[term].sort(key=lambda x: x.get('time', ''))

        total_flights = sum(len(f) for f in flights_by_terminal.values())

        return jsonify({
            'success': True,
            'flights_by_terminal': dict(flights_by_terminal),
            'terminals': terminals,
            'total_flights': total_flights,
            'current_time': current_time,
            'next_arrival': next_arrival
        })
    except Exception as e:
        print(f"Error fetching flight details: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/flight-arrivals')
@login_required
def api_flight_arrivals():
    from datetime import datetime, timezone, timedelta

    try:
        terminal = request.args.get('terminal', None)

        def fetch_flights():
            response = flightArrivals(terminal)
            if response is None:
                return {'flights': [], 'terminals': [], 'error': True}
            try:
                return response.json()
            except:
                return {'flights': [], 'terminals': [], 'error': True}

        cache_key = f"flights_{terminal or 'all'}"
        data = cache.get_cached('global', cache_key)
        if data is None:
            data = fetch_flights()
            cache.set_cached('global', cache_key, data)
            print(f"Flight data fetched from API for terminal: {terminal}")
        else:
            print(f"Flight data served from cache for terminal: {terminal}")

        if data.get('error'):
            hourly_data = parseFlightsByHour({})
            return jsonify({
                'success': True,
                'hourly_flights': hourly_data,
                'total_flights': 0,
                'message': 'API unavailable'
            })

        hourly_data = parseFlightsByHour(data)
        terminals = data.get('terminals', [])

        perth_tz = timezone(timedelta(hours=8))
        perth_now = datetime.now(perth_tz)
        current_hour = perth_now.hour
        print(
            f"Perth time: {perth_now.strftime('%H:%M')}, current_hour: {current_hour}"
        )
        filtered_hours = [h for h in hourly_data if h['hour'] >= current_hour]
        total_flights = sum(h['count'] for h in filtered_hours)

        return jsonify({
            'success': True,
            'hourly_flights': filtered_hours,
            'total_flights': total_flights,
            'current_hour': current_hour,
            'terminals': terminals
        })
    except Exception as e:
        print(f"Error fetching flight data: {e}")
        import traceback
        traceback.print_exc()
        hourly_data = parseFlightsByHour({})
        return jsonify({
            'success': True,
            'hourly_flights': hourly_data,
            'total_flights': 0,
            'message': 'Error occurred'
        })


@app.route('/api/heartbeat', methods=['POST'])
@login_required
def heartbeat():
    user_data = {
        'id':
        current_user.id,
        'username':
        current_user.username,
        'display_name':
        current_user.get_display_name(),
        'initials':
        current_user.get_initials(),
        'roles': [{
            'name': r.display_name,
            'color': r.color
        } for r in current_user.roles],
        'last_seen':
        datetime.utcnow().isoformat(),
        'current_page':
        request.json.get('page', 'unknown') if request.json else 'unknown'
    }
    active_users[current_user.id] = user_data

    now = datetime.utcnow()
    expired_ids = [
        uid for uid, data in active_users.items()
        if datetime.fromisoformat(data['last_seen']) < now -
        timedelta(seconds=60)
    ]
    for uid in expired_ids:
        active_users.pop(uid, None)

    return jsonify({'success': True, 'active_count': len(active_users)})


@app.route('/api/active-users')
@login_required
def get_active_users():
    now = datetime.utcnow()
    expired_ids = [
        uid for uid, data in active_users.items()
        if datetime.fromisoformat(data['last_seen']) < now -
        timedelta(seconds=60)
    ]
    for uid in expired_ids:
        active_users.pop(uid, None)
    
    users = []
    for uid, data in active_users.items():
        user = db.session.get(User, int(uid))
        if user:
            users.append({
                'id': user.id,
                'username': user.username,
                'display_name': user.get_display_name(),
                'initials': user.get_initials(),
                'profile_image': user.profile_image,
                'roles': [{'name': r.display_name, 'color': r.color} for r in user.roles],
                'current_page': data.get('page', '/')
            })
            
    return jsonify({'success': True, 'users': users})


@app.route('/admin/broadcast', methods=['GET', 'POST'])
@login_required
def admin_broadcast():
    if not current_user.is_owner():
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('root'))
    
    if request.method == 'POST':
        title = request.form.get('title', 'RizTar Announcement')
        message = request.form.get('message')
        url = request.form.get('url', '/')
        
        if not message:
            flash('Message content is required.', 'danger')
        else:
            import re
            clean_text = re.sub('<[^<]+?>', '', message).strip()
            
            all_users = User.query.all()
            total_sent = 0
            
            for user in all_users:
                try:
                    sent_push_count = send_push_notification(
                        user.id, 
                        title, 
                        clean_text,
                        url=url, 
                        tag='broadcast',
                        require_interaction=True
                    )
                    if sent_push_count and sent_push_count > 0:
                        total_sent += 1
                except Exception:
                    pass
            
            flash(f'Push notification sent to {total_sent} users.', 'success')
            return redirect(url_for('admin_broadcast'))
            
    return render_template('admin_broadcast.html')

@app.route('/api/chat-mark-read', methods=['POST'])
@login_required
def chat_mark_read():
    current_user.last_chat_read_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})


@app.route('/service-worker.js')
def service_worker():
    return send_from_directory('.', 'service-worker.js')


@app.route('/chat-lobby')
@login_required
def chat_lobby():
    current_user.last_chat_read_at = datetime.utcnow()
    db.session.commit()
    return render_template('chat_lobby.html')


@app.route('/api/chat-messages')
@login_required
def get_chat_messages():
    messages = ChatMessage.query.order_by(
        ChatMessage.created_at.desc()).limit(100).all()
    return jsonify({
        'success': True,
        'messages': [m.to_dict() for m in reversed(messages)]
    })


@app.route('/api/chat-users')
@login_required
def get_chat_users():
    users = User.query.all()
    return jsonify({
        'success':
        True,
        'users': [{
            'id': u.id,
            'username': u.username,
            'display_name': u.get_display_name()
        } for u in users]
    })


@app.route('/api/chat/delete-message/<int:message_id>', methods=['DELETE'])
@login_required
def delete_chat_message(message_id):
    """Delete a specific chat message (owner only)"""
    if not current_user.is_owner():
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    message = ChatMessage.query.get(message_id)
    if not message:
        return jsonify({'success': False, 'error': 'Message not found'}), 404
    
    db.session.delete(message)
    db.session.commit()
    
    # Emit socket event to remove message for all clients
    socketio.emit('message_deleted', {'message_id': message_id}, broadcast=True)
    
    return jsonify({'success': True})


@app.route('/api/chat/clear-history', methods=['DELETE'])
@login_required
def clear_chat_history():
    """Clear all chat messages (owner only)"""
    if not current_user.is_owner():
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    ChatMessage.query.delete()
    db.session.commit()
    
    # Emit socket event to clear messages for all clients
    socketio.emit('chat_cleared', broadcast=True)
    
    return jsonify({'success': True})


@socketio.on('connect')
def handle_connect():
    print(f"Socket connect - authenticated: {current_user.is_authenticated}",
          flush=True)
    if current_user.is_authenticated:
        user_data = {
            'id':
            current_user.id,
            'username':
            current_user.username,
            'display_name':
            current_user.get_display_name(),
            'initials':
            current_user.get_initials(),
            'profile_image':
            current_user.profile_image,
            'roles': [{
                'name': r.display_name,
                'color': r.color
            } for r in current_user.roles]
        }
        online_users[current_user.id] = user_data
        print(
            f"User connected: {current_user.username}, online users: {len(online_users)}",
            flush=True)
        emit('user_joined', user_data, broadcast=True)
        emit('online_users', list(online_users.values()), broadcast=True)


@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated and current_user.id in online_users:
        user_data = online_users.pop(current_user.id, None)
        if user_data:
            emit('user_left', {'id': current_user.id}, broadcast=True)
            emit('online_users', list(online_users.values()), broadcast=True)


@socketio.on('send_message')
def handle_send_message(data):
    print(
        f"Received send_message event, authenticated: {current_user.is_authenticated}",
        flush=True)
    if not current_user.is_authenticated:
        print("User not authenticated, ignoring message", flush=True)
        return

    message_text = data.get('message', '').strip()
    reply_to_id = data.get('reply_to_id')
    mentioned_username = data.get('mentioned_user')

    if not message_text:
        print("Empty message, ignoring", flush=True)
        return

    print(
        f"Processing message from {current_user.username}: {message_text[:50]}",
        flush=True)

    mentioned_user_id = None
    if mentioned_username:
        mentioned_user = User.query.filter_by(
            username=mentioned_username).first()
        if mentioned_user:
            mentioned_user_id = mentioned_user.id

    chat_msg = ChatMessage(user_id=current_user.id,
                           message=message_text,
                           reply_to_id=reply_to_id if reply_to_id else None,
                           mentioned_user_id=mentioned_user_id)
    db.session.add(chat_msg)
    db.session.commit()

    emit('new_message', chat_msg.to_dict(), broadcast=True)


@socketio.on('get_online_users')
def handle_get_online_users():
    emit('online_users', list(online_users.values()))


from intelligence.grid import PERTH_GRID
from intelligence.dedup import DriverDeduplicator, DriverSighting
from intelligence.daemon import IntelligenceDaemon, get_daemon, start_daemon, stop_daemon
from intelligence.learning import LearningEngine
from models import DriverObservation, DriverFingerprint, ZoneConfig, HourlySnapshot, DailyPattern, CorrelationModel, PredictionModel, IntelligenceConfig, ScanBatch

_intelligence_daemon = None

def get_fetch_drivers_func():
    from objects.uberDev import fetch_drivers_at_location
    return fetch_drivers_at_location


@app.route('/intelligence')
@login_required
def intelligence_dashboard():
    if not current_user.is_owner():
        flash('Access denied. Owner privileges required.', 'error')
        return redirect(url_for('root'))
    return render_template('intelligence.html')


@app.route('/api/intelligence/status')
@login_required
def api_intelligence_status():
    if not current_user.is_owner():
        return jsonify(success=False, message='Access denied'), 403
    
    global _intelligence_daemon
    
    if _intelligence_daemon is None:
        return jsonify(success=True, status={
            'is_running': False,
            'unique_drivers': 0,
            'counts_by_type': {},
            'coordinates_scanned': 0,
            'total_observations': 0,
            'cycle_count': 0,
            'grid_stats': PERTH_GRID.get_stats()
        })
    
    return jsonify(success=True, status=_intelligence_daemon.get_status())


@app.route('/api/intelligence/start', methods=['POST'])
@login_required
def api_intelligence_start():
    if not current_user.is_owner():
        return jsonify(success=False, message='Access denied'), 403
    
    global _intelligence_daemon
    
    try:
        from objects.uberDev import fetch_drivers_at_location
        
        if _intelligence_daemon is None:
            _intelligence_daemon = IntelligenceDaemon(fetch_drivers_at_location)
            
            _active_batches = {}
            _flask_app = app
            
            def on_observation(data):
                try:
                    with _flask_app.app_context():
                        batch_id = data.get('batch_id', 'unknown')
                        
                        if batch_id not in _active_batches:
                            scan_batch = ScanBatch(
                                batch_id=batch_id,
                                started_at=datetime.now(),
                                status='running'
                            )
                            db.session.add(scan_batch)
                            db.session.flush()
                            _active_batches[batch_id] = scan_batch
                        
                        for obs in data.get('observations', []):
                            fp_id = obs['fingerprint_id']
                            existing_fp = DriverFingerprint.query.filter_by(fingerprint_id=fp_id).first()
                            
                            if existing_fp:
                                existing_fp.last_seen_lat = obs['lat']
                                existing_fp.last_seen_lng = obs['lng']
                                existing_fp.last_bearing = obs.get('bearing')
                                existing_fp.last_seen_at = obs['timestamp']
                                existing_fp.observation_count += 1
                                existing_fp.confidence_score = min(0.99, existing_fp.confidence_score + 0.02)
                                existing_fp.primary_zone = obs['zone_id']
                            elif obs.get('is_new', False):
                                new_fp = DriverFingerprint(
                                    fingerprint_id=fp_id,
                                    vehicle_type=obs['vehicle_type'],
                                    first_seen_lat=obs['lat'],
                                    first_seen_lng=obs['lng'],
                                    last_seen_lat=obs['lat'],
                                    last_seen_lng=obs['lng'],
                                    last_bearing=obs.get('bearing'),
                                    confidence_score=obs['confidence'],
                                    primary_zone=obs['zone_id'],
                                    first_seen_at=obs['timestamp'],
                                    last_seen_at=obs['timestamp']
                                )
                                db.session.add(new_fp)
                            
                            db_obs = DriverObservation(
                                scan_batch_id=batch_id,
                                lat=obs['lat'],
                                lng=obs['lng'],
                                bearing=obs.get('bearing'),
                                vehicle_type=obs['vehicle_type'],
                                zone_id=obs['zone_id'],
                                fingerprint_id=fp_id,
                                confidence=obs['confidence'],
                                observed_at=obs['timestamp']
                            )
                            db.session.add(db_obs)
                        
                        db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    print(f"DB observation error: {e}", flush=True)
            
            def on_cycle_complete(data):
                try:
                    with _flask_app.app_context():
                        for batch_id, batch in list(_active_batches.items()):
                            scan_batch = ScanBatch.query.filter_by(batch_id=batch_id).first()
                            if scan_batch:
                                scan_batch.completed_at = datetime.now()
                                scan_batch.unique_drivers = data.get('unique_drivers', 0)
                                scan_batch.status = 'completed'
                            del _active_batches[batch_id]
                        db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    print(f"DB cycle complete error: {e}", flush=True)
            
            _intelligence_daemon.register_callback('on_observation', on_observation)
            _intelligence_daemon.register_callback('on_cycle_complete', on_cycle_complete)
        
        result = _intelligence_daemon.start()
        return jsonify(success=result, message='Intelligence engine started' if result else 'Already running')
    except Exception as e:
        return jsonify(success=False, message=str(e))


@app.route('/api/intelligence/stop', methods=['POST'])
@login_required
def api_intelligence_stop():
    if not current_user.is_owner():
        return jsonify(success=False, message='Access denied'), 403
    
    global _intelligence_daemon
    
    if _intelligence_daemon is None:
        return jsonify(success=False, message='Engine not initialized')
    
    result = _intelligence_daemon.stop()
    return jsonify(success=result, message='Intelligence engine stopped' if result else 'Already stopped')


@app.route('/api/intelligence/hotspots')
@login_required
def api_intelligence_hotspots():
    if not current_user.is_owner():
        return jsonify(success=False, message='Access denied'), 403
    
    try:
        global _intelligence_daemon
        if _intelligence_daemon is None:
            return jsonify(success=True, hotspots=[])
        
        counts = _intelligence_daemon.deduplicator.get_counts_by_zone()
        hotspots = []
        for zone_id, type_counts in sorted(counts.items(), key=lambda x: sum(x[1].values()), reverse=True)[:10]:
            total = sum(type_counts.values())
            if total > 0:
                hotspots.append({
                    'zone_id': zone_id,
                    'drivers': total,
                    'direction': None,
                    'uberx': type_counts.get('UberX', 0),
                    'comfort': type_counts.get('Comfort', 0),
                    'xl': type_counts.get('XL', 0),
                    'black': type_counts.get('Black', 0)
                })
        return jsonify(success=True, hotspots=hotspots)
    except Exception as e:
        return jsonify(success=False, message=str(e))


@app.route('/api/intelligence/drivers')
@login_required
def api_intelligence_drivers():
    if not current_user.is_owner():
        return jsonify(success=False, message='Access denied'), 403
    
    try:
        global _intelligence_daemon
        minutes_ago = request.args.get('minutes', 10, type=int)
        
        if _intelligence_daemon is None:
            return jsonify(success=True, drivers=[], max_id=0, count=0)
        
        cutoff = datetime.now() - timedelta(minutes=minutes_ago)
        drivers_data = _intelligence_daemon.deduplicator.get_recent_drivers(minutes=minutes_ago)
        
        result = [{
            'id': idx,
            'fingerprint_id': d['fingerprint_id'],
            'lat': d['lat'],
            'lng': d['lng'],
            'bearing': d.get('bearing'),
            'vehicle_type': d['vehicle_type'],
            'zone': d.get('zone_id'),
            'confidence': d.get('confidence', 0.5),
            'observations': d.get('observations', 1),
            'last_seen': d['last_seen'].isoformat() if d.get('last_seen') else None
        } for idx, d in enumerate(drivers_data)]
        
        return jsonify(success=True, drivers=result, max_id=len(result), count=len(result))
    except Exception as e:
        return jsonify(success=False, message=str(e))


@app.route('/api/intelligence/patterns')
@login_required
def api_intelligence_patterns():
    if not current_user.is_owner():
        return jsonify(success=False, message='Access denied'), 403
    
    try:
        patterns = DailyPattern.query.all()
        
        by_day = {}
        for p in patterns:
            if p.day_of_week not in by_day:
                by_day[p.day_of_week] = []
            by_day[p.day_of_week].append({
                'hour': p.hour_of_day,
                'avg_drivers': p.avg_drivers,
                'zone': p.zone_id
            })
        
        return jsonify(success=True, patterns=by_day)
    except Exception as e:
        return jsonify(success=False, message=str(e))


@app.route('/api/intelligence/predictions')
@login_required
def api_intelligence_predictions():
    if not current_user.is_owner():
        return jsonify(success=False, message='Access denied'), 403
    
    try:
        now = datetime.now()
        predictions = PredictionModel.query.filter(
            PredictionModel.target_time >= now,
            PredictionModel.validated_at.is_(None)
        ).order_by(PredictionModel.target_time).limit(10).all()
        
        result = [{
            'zone_id': p.zone_id,
            'target_time': p.target_time.isoformat(),
            'predicted_drivers': p.predicted_drivers,
            'direction': p.predicted_direction,
            'confidence': p.confidence
        } for p in predictions]
        
        return jsonify(success=True, predictions=result)
    except Exception as e:
        return jsonify(success=False, message=str(e))


@app.route('/api/intelligence/run-learning', methods=['POST'])
@login_required
def api_intelligence_run_learning():
    if not current_user.is_owner():
        return jsonify(success=False, message='Access denied'), 403
    
    try:
        learning = LearningEngine(db.session)
        
        hourly = learning.run_hourly_analysis()
        daily = learning.run_daily_analysis()
        correlations = learning.learn_correlations()
        predictions = learning.generate_predictions()
        validated = learning.validate_predictions()
        
        return jsonify(success=True, results={
            'hourly_snapshots': hourly,
            'daily_patterns': daily,
            'correlations_found': correlations,
            'predictions_made': predictions,
            'predictions_validated': validated
        })
    except Exception as e:
        return jsonify(success=False, message=str(e))


@app.route('/api/intelligence/grid')
@login_required
def api_intelligence_grid():
    if not current_user.is_owner():
        return jsonify(success=False, message='Access denied'), 403
    
    return jsonify(success=True, grid=PERTH_GRID.get_stats())


@app.route('/api/intelligence/trails')
@login_required
def api_intelligence_trails():
    if not current_user.is_owner():
        return jsonify(success=False, message='Access denied'), 403
    
    try:
        from uber.intelligence.trajectory import get_trajectory_analyzer
        analyzer = get_trajectory_analyzer()
        
        minutes = request.args.get('minutes', 10, type=int)
        trails = analyzer.get_active_driver_trails(minutes=minutes)
        
        return jsonify(success=True, trails=trails, stats=analyzer.get_stats())
    except Exception as e:
        return jsonify(success=False, message=str(e))


@app.route('/api/intelligence/flows')
@login_required
def api_intelligence_flows():
    if not current_user.is_owner():
        return jsonify(success=False, message='Access denied'), 403
    
    try:
        from uber.intelligence.trajectory import get_trajectory_analyzer
        analyzer = get_trajectory_analyzer()
        
        minutes = request.args.get('minutes', 30, type=int)
        flows = analyzer.get_zone_flow_summary(minutes=minutes)
        
        return jsonify(success=True, flows=flows)
    except Exception as e:
        return jsonify(success=False, message=str(e))


@app.route('/api/intelligence/drivers-heading-to/<zone_id>')
@login_required
def api_intelligence_drivers_heading_to(zone_id):
    if not current_user.is_owner():
        return jsonify(success=False, message='Access denied'), 403
    
    try:
        from uber.intelligence.trajectory import get_trajectory_analyzer
        analyzer = get_trajectory_analyzer()
        
        drivers = analyzer.get_drivers_heading_to(zone_id)
        
        return jsonify(success=True, zone=zone_id, drivers=drivers, count=len(drivers))
    except Exception as e:
        return jsonify(success=False, message=str(e))


def auto_start_intelligence_engine():
    """Auto-start the Intelligence Engine on server startup for 24/7 operation"""
    global _intelligence_daemon
    
    try:
        from objects.uberDev import fetch_drivers_at_location
        
        if _intelligence_daemon is None:
            _intelligence_daemon = IntelligenceDaemon(fetch_drivers_at_location)
        
        if not _intelligence_daemon.is_running:
            result = _intelligence_daemon.start()
            if result:
                print("[Intelligence] Engine auto-started for 24/7 operation", flush=True)
            else:
                print("[Intelligence] Engine already running", flush=True)
    except Exception as e:
        print(f"[Intelligence] Auto-start failed: {e}", flush=True)


with app.app_context():
    auto_start_intelligence_engine()


if __name__ == '__main__':
    print("Starting RizTar server on port 5000...", flush=True)
    socketio.run(app, host='0.0.0.0', port=5000)
