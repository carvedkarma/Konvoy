# Konvoy - Uber Driver Management System

## Overview

Konvoy is a premium Flask-based web application designed for managing Uber driver operations. The system provides user authentication with role-based access control (user, moderator, owner), integrates with Uber's internal APIs to fetch vehicle details and driver location data, and offers a luxury glassmorphism-styled dashboard interface for ride management.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Backend Framework
- **Flask** serves as the core web framework
- The application uses Flask-Login for session management and user authentication
- Flask-WTF handles form validation and CSRF protection

### Database Layer
- **PostgreSQL** database accessed via SQLAlchemy ORM
- Uses Flask-SQLAlchemy with a custom DeclarativeBase for model definitions
- Tables: `users` (authentication), `roles` (custom permissions)
- Connection pooling configured with `pool_recycle` and `pool_pre_ping` for reliability

### Authentication System
- Password hashing via Werkzeug's security utilities
- System roles: user, moderator, owner + custom roles
- Owner account auto-created on startup if environment variables are set
- Session-based authentication with Flask-Login
- Protected routes with permission-based access control

### Role & Permission System
- **System Roles**: User, Moderator, Owner (built-in)
- **Custom Roles**: Created by owners with specific permissions
- **Permissions**:
  - `can_change_location`: Access to location change feature
  - `can_fetch_ride`: Access to fetch ride feature
  - `can_access_admin`: Access to admin panel
  - `can_manage_users`: Ability to manage user accounts
  - `can_manage_roles`: Ability to create/delete custom roles
- **Owner**: Full control including role management page

### Uber API Integration
- Custom API client in `objects/uberDev.py` interacts with Uber's internal endpoints
- **Per-user credentials**: Each user can connect their own Uber driver account
- Credentials (cookies, headers, refresh tokens) are encrypted with Fernet using PBKDF2HMAC key derivation
- Features include: vehicle details, driver location tracking, token refresh
- Location geocoding via OpenStreetMap's Nominatim API
- Ride signal system to detect active rides

### Uber Account Connection
- Users can connect their Uber driver accounts via `/uber-connect`
- Credentials are captured from Uber mobile app API requests and stored encrypted
- CSRF-protected forms prevent unauthorized credential changes
- Users can disconnect their accounts from profile settings
- All Uber API functions accept per-user credentials as parameters

### Frontend Architecture
- Server-side rendered templates using Jinja2
- Tailwind CSS loaded via CDN for styling
- Premium glassmorphism design with:
  - Frosted glass panels with blur effects
  - Gradient metallic logo
  - Dark/light contrast themes
  - Responsive mobile-first design
- Pages: login, register, home hub, location change, ride details, admin panel

### Configuration Management
- Environment variables for sensitive data (Flask secret key, database URL, owner credentials)
- Global state stored in `config.py` for ride/stop signals and destination tracking

## Project Structure

```
uber/
├── main.py              # Flask application with routes and auth
├── models.py            # SQLAlchemy User and Role models
├── forms.py             # WTForms for login/register
├── config.py            # Global state variables
├── objects/
│   └── uberDev.py       # Uber API integration
├── source/
│   └── cred.py          # API credentials
├── templates/
│   ├── base.html        # Shared header/layout template
│   ├── login.html       # Premium login page
│   ├── register.html    # Account creation page
│   ├── home.html        # Main hub with navigation
│   ├── index.html       # Location change interface
│   ├── ride_details.html # Ride info display
│   ├── admin.html       # User management
│   ├── roles.html       # Role & permission management
│   ├── profile.html     # User profile & Uber connection status
│   └── uber_connect.html # Uber account connection page
└── static/
    └── images/          # Static assets
```

## External Dependencies

### Database
- PostgreSQL (configured via `DATABASE_URL` environment variable)

### Third-Party APIs
- **Uber Internal APIs** (`cn-geo1.uber.com`): Vehicle data, driver status, location updates
- **OpenStreetMap Nominatim**: Address geocoding for location tracking

### Required Environment Variables
- `DATABASE_URL`: PostgreSQL connection string (auto-configured)
- `FLASK_SECRET_KEY`: Session encryption key
- `KONVOY_OWNER_EMAIL`: Auto-create owner account email
- `KONVOY_OWNER_PASSWORD`: Auto-create owner account password

### Python Dependencies
- Flask, Flask-Login, Flask-SQLAlchemy, Flask-WTF
- Requests (HTTP client for Uber API)
- WTForms with email-validator
- Werkzeug (password hashing)
- psycopg2-binary (PostgreSQL adapter)
- Gunicorn (production server)
- cryptography (Fernet encryption for Uber credentials)