from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, PasswordField, SubmitField, SelectField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError, Optional, Regexp
from models import User
import re


class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Sign In')


class RegisterForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(min=1, max=80)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(min=1, max=80)])
    username = StringField('Username', validators=[
        DataRequired(), 
        Length(min=3, max=80),
        Regexp('^[a-z0-9]+$', message='Username must contain only lowercase letters and numbers, no spaces or symbols')
    ])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=8, message='Password must be at least 8 characters')])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password', message='Passwords must match')])
    profile_image = FileField('Profile Image', validators=[FileAllowed(['jpg', 'jpeg', 'png', 'gif'], 'Images only!')])
    submit = SubmitField('Create Account')
    
    def validate_email(self, email):
        user = User.query.filter_by(email=email.data.lower()).first()
        if user:
            raise ValidationError('Email already registered. Please use a different email.')
    
    def validate_username(self, username):
        if username.data != username.data.lower():
            raise ValidationError('Username must be lowercase only.')
        if ' ' in username.data:
            raise ValidationError('Username cannot contain spaces.')
        if not re.match('^[a-z0-9]+$', username.data):
            raise ValidationError('Username can only contain lowercase letters and numbers.')
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('Username already taken. Please choose a different one.')


class RoleForm(FlaskForm):
    role = SelectField('Role', choices=[
        ('user', 'User'),
        ('moderator', 'Moderator'),
        ('owner', 'Owner')
    ])
    submit = SubmitField('Update Role')


class UberConnectForm(FlaskForm):
    cookies = StringField('Cookies', validators=[DataRequired()])
    headers = StringField('Headers', validators=[DataRequired()])
    refresh_token = StringField('Refresh Token', validators=[DataRequired()])
    submit = SubmitField('Connect Account')


class UberDisconnectForm(FlaskForm):
    submit = SubmitField('Disconnect')


class ForgotPasswordForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Send Reset Link')


class ResetPasswordForm(FlaskForm):
    password = PasswordField('New Password', validators=[DataRequired(), Length(min=8, message='Password must be at least 8 characters')])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password', message='Passwords must match')])
    submit = SubmitField('Reset Password')


class ProfileForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(min=1, max=80)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(min=1, max=80)])
    profile_image = FileField('Profile Image', validators=[FileAllowed(['jpg', 'jpeg', 'png', 'gif'], 'Images only!')])
    submit = SubmitField('Update Profile')


class ContactForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    subject = StringField('Subject', validators=[DataRequired(), Length(min=5, max=200)])
    message = StringField('Message', validators=[DataRequired(), Length(min=10, max=2000)])
    submit = SubmitField('Send Message')


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=8, message='Password must be at least 8 characters')])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('new_password', message='Passwords must match')])
    submit = SubmitField('Change Password')


class EmptyForm(FlaskForm):
    submit = SubmitField('Submit')
