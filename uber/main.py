import eventlet
eventlet.monkey_patch()

import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime, timedelta
from objects.uberDev import vehicleDetails, appLaunch, driverLocation, updateLocationOnce, flightArrivals, parseFlightsByHour
import config
import cache
from models import db, User, Role, ChatMessage, create_default_roles, encrypt_data, decrypt_data
from forms import LoginForm, RegisterForm, RoleForm, ProfileForm, ChangePasswordForm, ForgotPasswordForm, ResetPasswordForm, UberConnectForm, UberDisconnectForm, EmptyForm
import secrets
import json

app = Flask(__name__)

flask_secret = os.environ.get("FLASK_SECRET_KEY")
if not flask_secret:
    import secrets
    flask_secret = secrets.token_hex(32)
    print("WARNING: FLASK_SECRET_KEY not set. Using generated key for this session.")
    
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
    print("ERROR: DATABASE_URL is not set. Please configure your database.")
else:
    print(f"Database configured: {'production (neon)' if 'neon' in database_url else 'development'}")

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
    "connect_args": {"connect_timeout": 10}
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
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


with app.app_context():
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
        print(f"Migrated {len(users_without_roles)} users to multi-role system.")
    
    owner_email = os.environ.get("KONVOY_OWNER_EMAIL")
    owner_password = os.environ.get("KONVOY_OWNER_PASSWORD")
    
    if owner_email and owner_password:
        owner = User.query.filter_by(email=owner_email).first()
        if not owner:
            owner_role = Role.query.filter_by(name='owner').first()
            owner = User(
                email=owner_email,
                username=owner_email.split('@')[0],
                role='owner'
            )
            owner.set_password(owner_password)
            if owner_role:
                owner.roles.append(owner_role)
            db.session.add(owner)
            db.session.commit()
            print("Owner account configured from environment variables.")


stop_signal = 0
stored_destination = None


@app.route('/')
def root():
    if current_user.is_authenticated:
        return render_template('home.html', loading=current_user.uber_connected, vehicles=[], driver_info=None)
    return redirect(url_for('login'))


@app.route('/api/home-data')
@login_required
def home_data():
    vehicles = []
    driver_info = None
    default_vehicle = None
    active_ride = None
    
    if current_user.uber_connected:
        try:
            cookies, headers, refresh_token = current_user.get_uber_credentials()
        except Exception as e:
            print(f"Error getting credentials: {e}")
            return jsonify(success=True, vehicles=[], driver_info=None, default_vehicle=None, active_ride=None)
        
        cached_vehicles = cache.get_cached(current_user.id, 'vehicles')
        cached_driver = cache.get_cached(current_user.id, 'driver_info')
        cached_ride = cache.get_cached(current_user.id, 'active_ride')
        
        def fetch_vehicles():
            try:
                return vehicleDetails(cookies, headers, refresh_token)
            except Exception as e:
                print(f"Error fetching vehicles: {e}")
                return []
        
        def fetch_driver_info():
            try:
                from objects.uberDev import driverInfo
                data = driverInfo(cookies, headers, refresh_token)
                return {'name': data[0], 'photo': data[1]}
            except Exception as e:
                print(f"Error fetching driver info: {e}")
                return None
        
        def fetch_ride():
            try:
                ride_data = appLaunch(cookies, headers, refresh_token)
                if ride_data and isinstance(ride_data, dict):
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
                cache.set_cached(current_user.id, 'active_ride', full_ride_data)
            else:
                full_ride_data = cached_ride
        
        if full_ride_data and isinstance(full_ride_data, dict):
            active_ride = {
                'full_name': full_ride_data.get('full_name', 'Rider'),
                'rating': full_ride_data.get('rating', '--'),
                'trip_distance': full_ride_data.get('trip_distance'),
                'ride_type': full_ride_data.get('ride_type', 'UberX')
            }
        else:
            active_ride = None
        
        for v in vehicles:
            if v.get('isDefault'):
                default_vehicle = v
                break
    
    return jsonify(success=True, vehicles=vehicles, driver_info=driver_info, default_vehicle=default_vehicle, active_ride=active_ride)


@app.route('/api/active-ride')
@login_required
def get_active_ride():
    if not current_user.uber_connected:
        return jsonify(success=True, active_ride=None)
    
    cached_ride = cache.get_cached(current_user.id, 'active_ride')
    if cached_ride is not None:
        if isinstance(cached_ride, dict):
            return jsonify(success=True, active_ride={
                'full_name': cached_ride.get('full_name', 'Rider'),
                'rating': cached_ride.get('rating', '--'),
                'trip_distance': cached_ride.get('trip_distance'),
                'ride_type': cached_ride.get('ride_type', 'UberX')
            })
        return jsonify(success=True, active_ride=None)
    
    try:
        cookies, headers, refresh_token = current_user.get_uber_credentials()
    except Exception as e:
        return jsonify(success=True, active_ride=None)
    
    try:
        ride_data = appLaunch(cookies, headers, refresh_token)
        cache.set_cached(current_user.id, 'active_ride', ride_data)
        if ride_data and isinstance(ride_data, dict):
            return jsonify(success=True, active_ride={
                'full_name': ride_data.get('full_name', 'Rider'),
                'rating': ride_data.get('rating', '--'),
                'trip_distance': ride_data.get('trip_distance'),
                'ride_type': ride_data.get('ride_type', 'UberX')
            })
    except Exception as e:
        print(f"Error fetching active ride: {e}")
    
    return jsonify(success=True, active_ride=None)


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
        user = User(
            email=form.email.data,
            username=form.username.data,
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            role='user'
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
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
                return render_template('edit_user.html', user=user, roles=all_roles)
        
        if username != user.username:
            existing = User.query.filter_by(username=username).first()
            if existing:
                flash('Username already taken.', 'error')
                return render_template('edit_user.html', user=user, roles=all_roles)
        
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


@app.route('/admin/uber-credentials/<int:user_id>/disconnect', methods=['POST'])
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
    
    all_roles = Role.query.order_by(Role.is_system.desc(), Role.created_at.asc()).all()
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
        can_manage_roles=request.form.get('can_manage_roles') == '1'
    )
    
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
        role.can_change_location = request.form.get('can_change_location') == '1'
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
    
    User.query.filter_by(role_id=role_id).update({'role_id': None, 'role': 'user'})
    
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
                return render_template('profile.html', form=form, disconnect_form=disconnect_form)
        
        if form.username.data != current_user.username:
            existing = User.query.filter_by(username=form.username.data).first()
            if existing:
                flash('Username already taken.', 'error')
                return render_template('profile.html', form=form, disconnect_form=disconnect_form)
        
        current_user.first_name = form.first_name.data
        current_user.last_name = form.last_name.data
        current_user.username = form.username.data
        current_user.email = form.email.data
        db.session.commit()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('profile'))
    
    form.first_name.data = current_user.first_name
    form.last_name.data = current_user.last_name
    form.username.data = current_user.username
    form.email.data = current_user.email
    return render_template('profile.html', form=form, disconnect_form=disconnect_form)


@app.route('/uber-connect', methods=['GET', 'POST'])
@login_required
def uber_connect():
    form = UberConnectForm()
    disconnect_form = UberDisconnectForm()
    
    if form.validate_on_submit():
        cookies_json = request.form.get('cookies', '').strip()
        headers_json = request.form.get('headers', '').strip()
        refresh_token = request.form.get('refresh_token', '').strip()
        
        if not cookies_json or not headers_json or not refresh_token:
            flash('All fields are required.', 'error')
            callback_url = url_for('uber_callback', _external=True)
            return render_template('uber_connect.html', form=form, disconnect_form=disconnect_form, callback_url=callback_url)
        
        try:
            import json
            json.loads(cookies_json)
            json.loads(headers_json)
        except json.JSONDecodeError:
            flash('Invalid JSON format for cookies or headers.', 'error')
            callback_url = url_for('uber_callback', _external=True)
            return render_template('uber_connect.html', form=form, disconnect_form=disconnect_form, callback_url=callback_url)
        
        current_user.uber_cookies = encrypt_data(cookies_json)
        current_user.uber_headers = encrypt_data(headers_json)
        current_user.uber_refresh_token = encrypt_data(refresh_token)
        current_user.uber_connected = True
        db.session.commit()
        cache.invalidate_cache(current_user.id)
        flash('Uber account connected successfully!', 'success')
        return redirect(url_for('root'))
    
    callback_url = url_for('uber_callback', _external=True)
    return render_template('uber_connect.html', form=form, disconnect_form=disconnect_form, callback_url=callback_url)


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
                return jsonify({'success': True, 'message': 'Credentials valid'})
            else:
                return jsonify({'success': False, 'error': 'Could not refresh token'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
    except json.JSONDecodeError:
        return jsonify({'success': False, 'error': 'Invalid JSON format'})
    except Exception as e:
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
            flash(f'Password reset link: /reset-password/{token} (valid for 1 hour)', 'success')
        else:
            flash('If an account with that email exists, a reset link has been generated.', 'info')
        return redirect(url_for('forgot_password'))
    
    return render_template('forgot_password.html', form=form)


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('root'))
    
    user = User.query.filter_by(reset_token=token).first()
    
    if not user or not user.reset_token_expiry or user.reset_token_expiry < datetime.utcnow():
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
    return render_template('index.html', has_permission=has_permission, default_vehicle=None, loading=loading)


@app.route('/api/location-data')
@login_required
def location_data():
    default_vehicle = None
    if current_user.uber_connected:
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
            print(f"Error fetching default vehicle: {e}")
    return jsonify(success=True, default_vehicle=default_vehicle)


@app.route('/fetch-ride')
@login_required
def fetch_ride():
    has_permission = current_user.has_permission('can_fetch_ride')
    
    if not has_permission:
        return render_template('ride_details.html', has_permission=False, ride_data=None, default_vehicle=None, loading=False)
    
    if not current_user.uber_connected:
        flash('Please connect your Uber account first.', 'error')
        return redirect(url_for('uber_connect'))
    
    return render_template('ride_details.html', has_permission=True, ride_data=None, default_vehicle=None, loading=True)


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
            return jsonify(success=True, ride_data=ride_data, default_vehicle=default_vehicle)
        else:
            return jsonify(success=True, ride_data=None, default_vehicle=default_vehicle)
    except Exception as e:
        print(f"Error fetching ride data: {e}")
        return jsonify(success=True, ride_data=None, default_vehicle=default_vehicle)


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
            result = updateLocationOnce(pickup_lat, pickup_lng, cookies, headers, refresh_token)
            return jsonify(success=True, step='pickup', message='Moved to pickup location')
        
        elif step == 'intermediate':
            lat = data.get('lat')
            lng = data.get('lng')
            point_num = data.get('point_num', 1)
            result = updateLocationOnce(lat, lng, cookies, headers, refresh_token)
            return jsonify(success=True, step='intermediate', message=f'Route point {point_num}')
        
        elif step == 'dropoff':
            result = updateLocationOnce(dropoff_lat, dropoff_lng, cookies, headers, refresh_token)
            return jsonify(success=True, step='dropoff', message='Arrived at dropoff')
        
        elif step == 'hold_dropoff':
            result = updateLocationOnce(dropoff_lat, dropoff_lng, cookies, headers, refresh_token)
            return jsonify(success=True, step='hold_dropoff', message='Holding at dropoff')
        
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
    response = driverLocation(config.stored_destination, cookies, headers, refresh_token)
    print(f"Destination Saved: {config.stored_destination}")
    return jsonify(status="success")


@app.route('/stop', methods=['POST'])
@login_required
def stop():
    config.stop_signal = 1
    print(f"Stop signal received. Variable 'stop_signal' set to: {config.stop_signal}")
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
            return jsonify({
                'success': False,
                'message': 'API unavailable'
            })
        
        data = response.json()
        flights = data.get('flights', [])
        terminals = data.get('terminals', [])
        
        perth_tz = timezone(timedelta(hours=8))
        perth_now = datetime.now(perth_tz)
        current_time = perth_now.strftime('%H:%M')
        
        flights_by_terminal = defaultdict(list)
        next_arrival = None
        
        for flight in flights:
            term = flight.get('terminal', 'Unknown')
            flight_time = flight.get('time', '')
            
            if flight_time >= current_time or flight_time < '06:00':
                flights_by_terminal[term].append(flight)
                
                if next_arrival is None and flight_time >= current_time:
                    next_arrival = flight_time
        
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
        return jsonify({
            'success': False,
            'message': str(e)
        })


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
        print(f"Perth time: {perth_now.strftime('%H:%M')}, current_hour: {current_hour}")
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
        'id': current_user.id,
        'username': current_user.username,
        'display_name': current_user.get_display_name(),
        'initials': current_user.get_initials(),
        'roles': [{'name': r.display_name, 'color': r.color} for r in current_user.roles],
        'last_seen': datetime.utcnow().isoformat(),
        'current_page': request.json.get('page', 'unknown') if request.json else 'unknown'
    }
    active_users[current_user.id] = user_data
    
    now = datetime.utcnow()
    expired_ids = [uid for uid, data in active_users.items() 
                   if datetime.fromisoformat(data['last_seen']) < now - timedelta(seconds=60)]
    for uid in expired_ids:
        active_users.pop(uid, None)
    
    return jsonify({'success': True, 'active_count': len(active_users)})


@app.route('/api/active-users')
@login_required
def get_active_users():
    now = datetime.utcnow()
    expired_ids = [uid for uid, data in active_users.items() 
                   if datetime.fromisoformat(data['last_seen']) < now - timedelta(seconds=60)]
    for uid in expired_ids:
        active_users.pop(uid, None)
    return jsonify({'success': True, 'users': list(active_users.values())})


@app.route('/chat-lobby')
@login_required
def chat_lobby():
    return render_template('chat_lobby.html')


@app.route('/api/chat-messages')
@login_required
def get_chat_messages():
    messages = ChatMessage.query.order_by(ChatMessage.created_at.desc()).limit(100).all()
    return jsonify({
        'success': True,
        'messages': [m.to_dict() for m in reversed(messages)]
    })


@app.route('/api/chat-users')
@login_required
def get_chat_users():
    users = User.query.all()
    return jsonify({
        'success': True,
        'users': [{'id': u.id, 'username': u.username, 'display_name': u.get_display_name()} for u in users]
    })


@socketio.on('connect')
def handle_connect():
    print(f"Socket connect - authenticated: {current_user.is_authenticated}", flush=True)
    if current_user.is_authenticated:
        user_data = {
            'id': current_user.id,
            'username': current_user.username,
            'display_name': current_user.get_display_name(),
            'initials': current_user.get_initials(),
            'roles': [{'name': r.display_name, 'color': r.color} for r in current_user.roles]
        }
        online_users[current_user.id] = user_data
        print(f"User connected: {current_user.username}, online users: {len(online_users)}", flush=True)
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
    print(f"Received send_message event, authenticated: {current_user.is_authenticated}", flush=True)
    if not current_user.is_authenticated:
        print("User not authenticated, ignoring message", flush=True)
        return
    
    message_text = data.get('message', '').strip()
    reply_to_id = data.get('reply_to_id')
    mentioned_username = data.get('mentioned_user')
    
    if not message_text:
        print("Empty message, ignoring", flush=True)
        return
    
    print(f"Processing message from {current_user.username}: {message_text[:50]}", flush=True)
    
    mentioned_user_id = None
    if mentioned_username:
        mentioned_user = User.query.filter_by(username=mentioned_username).first()
        if mentioned_user:
            mentioned_user_id = mentioned_user.id
    
    chat_msg = ChatMessage(
        user_id=current_user.id,
        message=message_text,
        reply_to_id=reply_to_id if reply_to_id else None,
        mentioned_user_id=mentioned_user_id
    )
    db.session.add(chat_msg)
    db.session.commit()
    
    print(f"Message saved with id {chat_msg.id}, broadcasting...", flush=True)
    emit('new_message', chat_msg.to_dict(), broadcast=True)
    print("Broadcast complete", flush=True)


@socketio.on('get_online_users')
def handle_get_online_users():
    emit('online_users', list(online_users.values()))


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
