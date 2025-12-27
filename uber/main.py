import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from datetime import datetime, timedelta
from objects.uberDev import vehicleDetails, appLaunch, driverLocation
import config
from models import db, User, Role, create_default_roles, encrypt_data, decrypt_data
from forms import LoginForm, RegisterForm, RoleForm, ProfileForm, ChangePasswordForm, ForgotPasswordForm, ResetPasswordForm, UberConnectForm, UberDisconnectForm
import secrets

app = Flask(__name__)

flask_secret = os.environ.get("FLASK_SECRET_KEY")
if not flask_secret:
    import secrets
    flask_secret = secrets.token_hex(32)
    print("WARNING: FLASK_SECRET_KEY not set. Using generated key for this session.")
    
app.secret_key = flask_secret

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

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


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
    
    if current_user.uber_connected:
        try:
            cookies, headers, refresh_token = current_user.get_uber_credentials()
        except Exception as e:
            print(f"Error getting credentials: {e}")
            return jsonify(success=True, vehicles=[], driver_info=None)
        
        try:
            vehicles = vehicleDetails(cookies, headers, refresh_token)
        except Exception as e:
            print(f"Error fetching vehicles: {e}")
            vehicles = []
        
        try:
            from objects.uberDev import driverInfo
            driver_data = driverInfo(cookies, headers, refresh_token)
            driver_info = {
                'name': driver_data[0],
                'photo': driver_data[1]
            }
        except Exception as e:
            print(f"Error fetching driver info: {e}")
            driver_info = None
    
    return jsonify(success=True, vehicles=vehicles, driver_info=driver_info)


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
            login_user(user)
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
            vehicles = vehicleDetails(cookies, headers, refresh_token)
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
        vehicles = vehicleDetails(cookies, headers, refresh_token)
        for v in vehicles:
            if v.get('isDefault'):
                default_vehicle = v
                break
    except Exception as e:
        print(f"Error fetching vehicle: {e}")
    
    try:
        cookies, headers, refresh_token = current_user.get_uber_credentials()
        ride_data = appLaunch(cookies, headers, refresh_token)
        if ride_data and isinstance(ride_data, dict):
            return jsonify(success=True, ride_data=ride_data, default_vehicle=default_vehicle)
        else:
            return jsonify(success=True, ride_data=None, default_vehicle=default_vehicle)
    except Exception as e:
        print(f"Error fetching ride data: {e}")
        return jsonify(success=True, ride_data=None, default_vehicle=default_vehicle)


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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
