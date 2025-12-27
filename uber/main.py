import os
import json
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from datetime import datetime, timedelta
from objects.uberDev import vehicleDetails, appLaunch, driverLocation, driverInfo
import config
from models import db, User, Role, create_default_roles
from forms import LoginForm, RegisterForm, RoleForm, EmptyForm

app = Flask(__name__)

flask_secret = os.environ.get("FLASK_SECRET_KEY")
if not flask_secret:
    import secrets
    flask_secret = secrets.token_hex(32)
    os.environ["FLASK_SECRET_KEY"] = flask_secret
    print("WARNING: FLASK_SECRET_KEY not set. Using generated key for this session.")
    
app.secret_key = flask_secret
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


with app.app_context():
    db.create_all()
    create_default_roles()
    
    owner_email = os.environ.get("KONVOY_OWNER_EMAIL")
    owner_password = os.environ.get("KONVOY_OWNER_PASSWORD")
    
    if owner_email and owner_password:
        owner = User.query.filter_by(email=owner_email).first()
        if not owner:
            owner = User(
                email=owner_email,
                username=owner_email.split('@')[0],
                role='owner'
            )
            owner.set_password(owner_password)
            db.session.add(owner)
            db.session.commit()
            print("Owner account configured from environment variables.")


stop_signal = 0
stored_destination = None


@app.route('/')
def root():
    if current_user.is_authenticated:
        return render_template('home.html')
    return redirect(url_for('login'))


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
            flash('Welcome back to Konvoy!', 'success')
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


@app.route('/admin/update-role/<int:user_id>', methods=['POST'])
@login_required
def update_role(user_id):
    if not current_user.can_manage_users():
        return jsonify(status="error", message="Access denied"), 403
    
    user = User.query.get_or_404(user_id)
    role_value = request.form.get('role_id', '')
    
    if not role_value:
        flash('No role selected.', 'error')
        return redirect(url_for('admin'))
    
    if role_value.startswith('system_'):
        system_role = role_value.replace('system_', '')
        if system_role in ['user', 'moderator', 'owner']:
            user.role = system_role
            user.role_id = None
            db.session.commit()
            flash(f'Role updated for {user.username}.', 'success')
    else:
        try:
            custom_role_id = int(role_value)
            custom_role = Role.query.get(custom_role_id)
            if custom_role:
                user.role = 'user'
                user.role_id = custom_role_id
                db.session.commit()
                flash(f'Role updated for {user.username} to {custom_role.display_name}.', 'success')
        except (ValueError, TypeError):
            flash('Invalid role selection.', 'error')
    
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


@app.route('/change-location')
@login_required
def home():
    if not current_user.has_permission('can_change_location'):
        flash('You do not have permission to access this page.', 'error')
        return redirect(url_for('root'))
    return render_template('index.html')


@app.route('/fetch-ride')
@login_required
def fetch_ride():
    if not current_user.has_permission('can_fetch_ride'):
        flash('You do not have permission to access this page.', 'error')
        return redirect(url_for('root'))
    
    return render_template('ride_details.html', ride_data=None)


@app.route('/submit', methods=['POST'])
@login_required
def submit():
    cookies, headers, refresh_token = current_user.get_uber_credentials()
    if not cookies or not headers:
        return jsonify(status="error", message="No Uber credentials")
    
    config.stored_destination = request.form.get('destination')
    response = driverLocation(config.stored_destination, cookies, headers)
    print(f"Destination Saved: {config.stored_destination}")
    return jsonify(status="success")


@app.route('/stop', methods=['POST'])
@login_required
def stop():
    config.stop_signal = 1
    print(f"Stop signal received. Variable 'stop_signal' set to: {config.stop_signal}")
    return jsonify(status="success", value=config.stop_signal)


@app.route('/profile')
@login_required
def profile():
    form = EmptyForm()
    return render_template('profile.html', form=form)


@app.route('/uber-connect', methods=['GET', 'POST'])
@login_required
def uber_connect():
    if request.method == 'POST':
        har_file = request.files.get('har_file')
        
        if not har_file:
            flash('Please upload a HAR file.', 'error')
            return render_template('uber_connect.html')
        
        try:
            har_content = har_file.read().decode('utf-8')
            har_data = json.loads(har_content)
            
            cookies = {}
            headers = {}
            
            for entry in har_data.get('log', {}).get('entries', []):
                req = entry.get('request', {})
                url = req.get('url', '')
                
                if 'uber.com' in url:
                    for cookie in req.get('cookies', []):
                        cookies[cookie['name']] = cookie['value']
                    
                    for header in req.get('headers', []):
                        name = header['name'].lower()
                        if name not in ['host', 'content-length', 'connection']:
                            headers[name] = header['value']
                    
                    if cookies and headers:
                        break
            
            if not cookies or not headers:
                flash('Could not find Uber credentials in HAR file. Make sure you captured traffic from the Uber app.', 'error')
                return render_template('uber_connect.html')
            
            if 'authorization' not in headers:
                flash('HAR file missing authorization header. Please capture fresh traffic.', 'error')
                return render_template('uber_connect.html')
            
            current_user.set_uber_credentials(cookies, headers)
            db.session.commit()
            
            flash('Uber account connected successfully!', 'success')
            return redirect(url_for('profile'))
            
        except json.JSONDecodeError:
            flash('Invalid HAR file format.', 'error')
        except Exception as e:
            print(f"Error processing HAR: {e}")
            flash('Error processing HAR file.', 'error')
    
    return render_template('uber_connect.html')


@app.route('/uber-disconnect', methods=['POST'])
@login_required
def uber_disconnect():
    current_user.clear_uber_credentials()
    db.session.commit()
    flash('Uber account disconnected.', 'info')
    return redirect(url_for('profile'))


@app.route('/api/home-data')
@login_required
def home_data():
    cookies, headers, refresh_token = current_user.get_uber_credentials()
    
    data = {
        'connected': current_user.has_uber_credentials(),
        'vehicles': [],
        'driver': None,
        'ride': None
    }
    
    if cookies and headers:
        vehicles = vehicleDetails(cookies, headers)
        data['vehicles'] = vehicles if vehicles else []
        
        driver = driverInfo(cookies, headers)
        data['driver'] = driver
        
        ride_data = appLaunch(cookies, headers)
        if ride_data and ride_data[0] != 0:
            config.ride_signal = 1
            data['ride'] = {
                'type': ride_data[0],
                'first_name': ride_data[1],
                'last_name': ride_data[2],
                'rating': ride_data[3],
                'pickup': ride_data[4],
                'dropoff': ride_data[5]
            }
        else:
            config.ride_signal = 0
    
    return jsonify(data)


@app.route('/api/fetch-ride-data')
@login_required
def fetch_ride_data():
    cookies, headers, refresh_token = current_user.get_uber_credentials()
    
    if not cookies or not headers:
        return jsonify({'ride': None, 'error': 'No Uber credentials'})
    
    ride_data = appLaunch(cookies, headers)
    if ride_data and ride_data[0] != 0:
        return jsonify({
            'ride': {
                'type': ride_data[0],
                'first_name': ride_data[1],
                'last_name': ride_data[2],
                'rating': ride_data[3],
                'pickup': ride_data[4],
                'dropoff': ride_data[5]
            }
        })
    
    return jsonify({'ride': None})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
