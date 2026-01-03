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
    from objects.uberDev import vehicleDetails, appLaunch, driverLocation, updateLocationOnce, flightArrivals, parseFlightsByHour, uberRidersNearby
    import config
    import cache
    from models import db, User, Role, ChatMessage, PushSubscription, create_default_roles, encrypt_data, decrypt_data
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


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.after_request
def add_no_cache_headers(response):
    if request.endpoint in ['login', 'register', 'logout', 'root']:
        response.headers[
            'Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
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
    return redirect(url_for('login'))


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

        print(f"DEBUG: Starting home_data, caches: v={cached_vehicles is not None}, d={cached_driver is not None}, r={cached_ride is not None}", flush=True)
        if cached_vehicles is not None and cached_driver is not None and cached_ride is not None:
            vehicles = cached_vehicles
            driver_info = cached_driver
            full_ride_data = cached_ride
            print("DEBUG: Using all cached data", flush=True)
        else:
            print("DEBUG: Spawning API tasks", flush=True)
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
            print("DEBUG: All API tasks completed", flush=True)

        print("DEBUG: Processing driver status", flush=True)
        driver_status = None
        active_ride = None
        
        if full_ride_data and isinstance(full_ride_data, dict):
            # Check if this is already processed ride data (has full_name key)
            if 'full_name' in full_ride_data:
                # This is already a processed ride - use it directly
                active_ride = full_ride_data
                print(f"DEBUG: Found processed ride data: {full_ride_data.get('full_name')}", flush=True)
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

    # Unread messages count
    unread_chat_count = ChatMessage.query.filter(
        ChatMessage.created_at > current_user.last_chat_read_at,
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
        return 0
        
    subscriptions = PushSubscription.query.filter_by(user_id=user_id, is_active=True).all()
    
    payload = json.dumps({
        'title': title,
        'body': body,
        'url': url,
        'tag': tag,
        'requireInteraction': require_interaction
    })
    
    sent = 0
    for sub in subscriptions:
        try:
            webpush(
                subscription_info=sub.to_subscription_info(),
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS
            )
            sent += 1
        except WebPushException as e:
            if e.response and e.response.status_code in [404, 410]:
                sub.is_active = False
                db.session.commit()
            print(f"Push notification failed: {e}")
    
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
        user = User.query.filter_by(email=form.email.data).first()
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
        user = User(email=form.email.data,
                    username=form.username.data,
                    first_name=form.first_name.data,
                    last_name=form.last_name.data,
                    role='user')
        user.set_password(form.password.data)
        db.session.add(user)
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


@app.route('/api/chat-mark-read', methods=['POST'])
@login_required
def chat_mark_read():
    current_user.last_chat_read_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})


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

    print(f"Message saved with id {chat_msg.id}, broadcasting...", flush=True)
    emit('new_message', chat_msg.to_dict(), broadcast=True)
    print("Broadcast complete", flush=True)


@socketio.on('get_online_users')
def handle_get_online_users():
    emit('online_users', list(online_users.values()))


if __name__ == '__main__':
    print("Starting RizTar server on port 5000...", flush=True)
    socketio.run(app, host='0.0.0.0', port=5000)
