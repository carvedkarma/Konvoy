from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime
import json
import base64
import os
import hashlib
from cryptography.fernet import Fernet


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


def get_encryption_key():
    secret = os.environ.get('FLASK_SECRET_KEY')
    if not secret:
        raise RuntimeError("FLASK_SECRET_KEY environment variable is required for credential encryption")
    key = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key)


def encrypt_data(data):
    if not data:
        return None
    try:
        f = Fernet(get_encryption_key())
        encrypted = f.encrypt(data.encode())
        return encrypted.decode()
    except Exception as e:
        print(f"Encryption error: {e}")
        raise ValueError(f"Failed to encrypt data: {e}")


def decrypt_data(data):
    if not data:
        return None
    try:
        f = Fernet(get_encryption_key())
        decrypted = f.decrypt(data.encode())
        return decrypted.decode()
    except Exception as e:
        print(f"Decryption error: {e}")
        return None


class Role(db.Model):
    __tablename__ = 'roles'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(20), default='gray')
    is_system = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    can_change_location = db.Column(db.Boolean, default=False)
    can_fetch_ride = db.Column(db.Boolean, default=False)
    can_access_admin = db.Column(db.Boolean, default=False)
    can_manage_roles = db.Column(db.Boolean, default=False)
    can_manage_users = db.Column(db.Boolean, default=False)
    
    users = db.relationship('User', backref='role_obj', lazy=True)
    
    def __repr__(self):
        return f'<Role {self.name}>'


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='user', nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    uber_cookies_encrypted = db.Column(db.Text, nullable=True)
    uber_headers_encrypted = db.Column(db.Text, nullable=True)
    uber_refresh_token_encrypted = db.Column(db.Text, nullable=True)
    uber_connected_at = db.Column(db.DateTime, nullable=True)
    
    ROLE_USER = 'user'
    ROLE_MODERATOR = 'moderator'
    ROLE_OWNER = 'owner'
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def is_owner(self):
        return self.role == self.ROLE_OWNER
    
    def is_moderator(self):
        return self.role in [self.ROLE_MODERATOR, self.ROLE_OWNER]
    
    def can_manage_users(self):
        return self.role == self.ROLE_OWNER
    
    def has_permission(self, permission):
        if self.role == self.ROLE_OWNER:
            return True
        if self.role_obj:
            return getattr(self.role_obj, permission, False)
        if self.role == self.ROLE_MODERATOR:
            return permission in ['can_change_location', 'can_fetch_ride']
        if self.role == self.ROLE_USER:
            return permission in ['can_change_location', 'can_fetch_ride']
        return False
    
    def get_role_display(self):
        if self.role_obj:
            return self.role_obj.display_name
        return self.role.title()
    
    def get_role_color(self):
        if self.role == self.ROLE_OWNER:
            return 'yellow'
        if self.role_obj:
            return self.role_obj.color
        if self.role == self.ROLE_MODERATOR:
            return 'blue'
        return 'gray'
    
    def set_uber_credentials(self, cookies, headers, refresh_token=None):
        self.uber_cookies_encrypted = encrypt_data(json.dumps(cookies)) if cookies else None
        self.uber_headers_encrypted = encrypt_data(json.dumps(headers)) if headers else None
        self.uber_refresh_token_encrypted = encrypt_data(refresh_token) if refresh_token else None
        self.uber_connected_at = datetime.utcnow()
    
    def get_uber_credentials(self):
        cookies = None
        headers = None
        refresh_token = None
        
        if self.uber_cookies_encrypted:
            decrypted = decrypt_data(self.uber_cookies_encrypted)
            if decrypted:
                cookies = json.loads(decrypted)
        
        if self.uber_headers_encrypted:
            decrypted = decrypt_data(self.uber_headers_encrypted)
            if decrypted:
                headers = json.loads(decrypted)
        
        if self.uber_refresh_token_encrypted:
            refresh_token = decrypt_data(self.uber_refresh_token_encrypted)
        
        return cookies, headers, refresh_token
    
    def has_uber_credentials(self):
        return self.uber_cookies_encrypted is not None and self.uber_headers_encrypted is not None
    
    def clear_uber_credentials(self):
        self.uber_cookies_encrypted = None
        self.uber_headers_encrypted = None
        self.uber_refresh_token_encrypted = None
        self.uber_connected_at = None
    
    def __repr__(self):
        return f'<User {self.username}>'


def create_default_roles():
    default_roles = [
        {
            'name': 'owner',
            'display_name': 'Owner',
            'color': 'yellow',
            'is_system': True,
            'can_change_location': True,
            'can_fetch_ride': True,
            'can_access_admin': True,
            'can_manage_roles': True,
            'can_manage_users': True
        },
        {
            'name': 'moderator',
            'display_name': 'Moderator',
            'color': 'blue',
            'is_system': True,
            'can_change_location': True,
            'can_fetch_ride': True,
            'can_access_admin': True,
            'can_manage_roles': False,
            'can_manage_users': False
        },
        {
            'name': 'user',
            'display_name': 'User',
            'color': 'gray',
            'is_system': True,
            'can_change_location': True,
            'can_fetch_ride': True,
            'can_access_admin': False,
            'can_manage_roles': False,
            'can_manage_users': False
        }
    ]
    
    for role_data in default_roles:
        existing = Role.query.filter_by(name=role_data['name']).first()
        if not existing:
            role = Role(**role_data)
            db.session.add(role)
    
    db.session.commit()
