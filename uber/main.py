import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from datetime import datetime
from objects.uberDev import vehicleDetails, appLaunch, driverLocation
import config
from models import db, User
from forms import LoginForm, RegisterForm, RoleForm

app = Flask(__name__)

flask_secret = os.environ.get("FLASK_SECRET_KEY")
if not flask_secret:
    import secrets
    flask_secret = secrets.token_hex(32)
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


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


with app.app_context():
    db.create_all()
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
            login_user(user)
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
    return render_template('admin.html', users=users)


@app.route('/admin/update-role/<int:user_id>', methods=['POST'])
@login_required
def update_role(user_id):
    if not current_user.can_manage_users():
        return jsonify(status="error", message="Access denied"), 403
    
    user = User.query.get_or_404(user_id)
    new_role = request.form.get('role')
    
    if new_role in ['user', 'moderator', 'owner']:
        user.role = new_role
        db.session.commit()
        flash(f'Role updated for {user.username}.', 'success')
    
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


@app.route('/change-location')
@login_required
def home():
    return render_template('index.html')


@app.route('/fetch-ride')
@login_required
def fetch_ride():
    if config.ride_signal == 1:
        ride_data = appLaunch()
        return render_template('ride_details.html', ride_data=ride_data)
    else:
        return render_template('ride_details.html', ride_data=None)


@app.route('/submit', methods=['POST'])
@login_required
def submit():
    config.stored_destination = request.form.get('destination')
    response = driverLocation(config.stored_destination)
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
