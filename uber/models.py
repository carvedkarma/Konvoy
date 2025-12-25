from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


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
