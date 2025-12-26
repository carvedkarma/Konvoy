from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime
import json
import base64
import os
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def get_encryption_key():
    secret = os.environ.get("FLASK_SECRET_KEY", "fallback-secret-key")
    salt = b'uber_credentials_salt'
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    return Fernet(key)


def encrypt_data(data):
    if not data:
        return None
    fernet = get_encryption_key()
    return fernet.encrypt(data.encode()).decode()


def decrypt_data(encrypted_data):
    if not encrypted_data:
        return None
    try:
        fernet = get_encryption_key()
        return fernet.decrypt(encrypted_data.encode()).decode()
    except:
        return None


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


user_roles = db.Table('user_roles',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), primary_key=True),
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id'), primary_key=True)
)


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
    
    users = db.relationship('User', secondary=user_roles, back_populates='roles')
    
    def get_badge_classes(self):
        color_map = {
            'gray': 'bg-zinc-100 text-zinc-600 border-zinc-200',
            'red': 'bg-red-100 text-red-600 border-red-200',
            'orange': 'bg-orange-100 text-orange-600 border-orange-200',
            'yellow': 'bg-yellow-100 text-yellow-700 border-yellow-200',
            'green': 'bg-green-100 text-green-600 border-green-200',
            'teal': 'bg-teal-100 text-teal-600 border-teal-200',
            'blue': 'bg-blue-100 text-blue-600 border-blue-200',
            'indigo': 'bg-indigo-100 text-indigo-600 border-indigo-200',
            'purple': 'bg-purple-100 text-purple-600 border-purple-200',
            'pink': 'bg-pink-100 text-pink-600 border-pink-200',
        }
        return color_map.get(self.color, color_map['gray'])
    
    def __repr__(self):
        return f'<Role {self.name}>'


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    first_name = db.Column(db.String(80), nullable=True)
    last_name = db.Column(db.String(80), nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='user', nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    reset_token = db.Column(db.String(100), nullable=True)
    reset_token_expiry = db.Column(db.DateTime, nullable=True)
    
    uber_cookies = db.Column(db.Text, nullable=True)
    uber_headers = db.Column(db.Text, nullable=True)
    uber_refresh_token = db.Column(db.Text, nullable=True)
    uber_connected = db.Column(db.Boolean, default=False)
    
    roles = db.relationship('Role', secondary=user_roles, back_populates='users')
    
    ROLE_USER = 'user'
    ROLE_MODERATOR = 'moderator'
    ROLE_OWNER = 'owner'
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def is_owner(self):
        for role in self.roles:
            if role.name == self.ROLE_OWNER:
                return True
        return self.role == self.ROLE_OWNER
    
    def is_moderator(self):
        for role in self.roles:
            if role.name in [self.ROLE_MODERATOR, self.ROLE_OWNER]:
                return True
        return self.role in [self.ROLE_MODERATOR, self.ROLE_OWNER]
    
    def can_manage_users(self):
        return self.is_owner()
    
    def has_permission(self, permission):
        for role in self.roles:
            if getattr(role, permission, False):
                return True
        
        if not self.roles:
            role_record = Role.query.filter_by(name=self.role).first()
            if role_record:
                return getattr(role_record, permission, False)
        
        return False
    
    def get_primary_role(self):
        priority = ['owner', 'moderator', 'user']
        for p in priority:
            for role in self.roles:
                if role.name == p:
                    return role
        if self.roles:
            return self.roles[0]
        return Role.query.filter_by(name=self.role).first()
    
    def get_role_display(self):
        if len(self.roles) > 1:
            return ', '.join([r.display_name for r in self.roles])
        primary = self.get_primary_role()
        if primary:
            return primary.display_name
        return self.role.title()
    
    def get_role_color(self):
        primary = self.get_primary_role()
        if primary:
            return primary.color
        return 'gray'
    
    def get_role_badge_classes(self):
        color_map = {
            'gray': 'bg-zinc-100 text-zinc-600 border-zinc-200',
            'red': 'bg-red-100 text-red-600 border-red-200',
            'orange': 'bg-orange-100 text-orange-600 border-orange-200',
            'yellow': 'bg-yellow-100 text-yellow-700 border-yellow-200',
            'green': 'bg-green-100 text-green-600 border-green-200',
            'teal': 'bg-teal-100 text-teal-600 border-teal-200',
            'blue': 'bg-blue-100 text-blue-600 border-blue-200',
            'indigo': 'bg-indigo-100 text-indigo-600 border-indigo-200',
            'purple': 'bg-purple-100 text-purple-600 border-purple-200',
            'pink': 'bg-pink-100 text-pink-600 border-pink-200',
        }
        color = self.get_role_color()
        return color_map.get(color, color_map['gray'])
    
    def get_all_roles(self):
        return self.roles
    
    def add_role(self, role):
        if role not in self.roles:
            self.roles.append(role)
    
    def remove_role(self, role):
        if role in self.roles:
            self.roles.remove(role)
    
    def set_roles(self, role_list):
        self.roles = role_list
    
    def get_display_name(self):
        if self.first_name and self.last_name:
            return f'{self.first_name} {self.last_name}'
        elif self.first_name:
            return self.first_name
        return self.username
    
    def get_initials(self):
        if self.first_name and self.last_name:
            return f'{self.first_name[0]}{self.last_name[0]}'.upper()
        elif self.first_name:
            return self.first_name[0].upper()
        return self.username[0].upper()
    
    def get_uber_credentials(self):
        import json
        cookies_str = decrypt_data(self.uber_cookies)
        headers_str = decrypt_data(self.uber_headers)
        refresh_token = decrypt_data(self.uber_refresh_token)
        
        cookies = json.loads(cookies_str) if cookies_str else {}
        headers = json.loads(headers_str) if headers_str else {}
        
        return cookies, headers, refresh_token
    
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
