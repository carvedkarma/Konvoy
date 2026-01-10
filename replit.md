# RizTar - Uber Driver Management System

## Overview

RizTar is a premium Flask-based web application designed for managing Uber driver operations. The system provides user authentication with role-based access control (user, moderator, owner), integrates with Uber's internal APIs to fetch vehicle details and driver location data, and offers a luxury glassmorphism-styled dashboard interface for ride management.

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
- Tables: `users` (authentication), `roles` (custom permissions), `user_roles` (many-to-many association), `chat_messages` (real-time chat)
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
- **Multi-Role Support**: Users can have multiple roles assigned via `user_roles` association table
- **Permission Aggregation**: Permissions are combined from all assigned roles (if any role grants a permission, user has it)
- **Permissions**:
  - `can_change_location`: Access to location change feature
  - `can_fetch_ride`: Access to fetch ride feature
  - `can_access_admin`: Access to admin panel
  - `can_manage_users`: Ability to manage user accounts
  - `can_manage_roles`: Ability to create/delete custom roles
- **Owner**: Full control including role management page
- **Locked UI States**: Users see all features but without permission see lock icons and locked panels

### Uber API Integration
- Custom API client in `objects/uberDev.py` interacts with Uber's internal endpoints
- **Per-user credentials**: Each user can connect their own Uber driver account
- Credentials (cookies, headers, refresh tokens) are encrypted with Fernet using PBKDF2HMAC key derivation
- Features include: vehicle details, driver location tracking, token refresh
- Location geocoding via OpenStreetMap's Nominatim API
- Ride signal system to detect active rides
- **Fare Pricing API**: GraphQL integration with Uber's pricing endpoint (`m.uber.com/go/graphql`)
  - Uses `fare_cookies` and `fare_headers` from `source/cred.py` for authentication
  - Fetches fare estimates by ride type (UberX, Comfort, etc.)
  - Displays estimated driver earnings (73% of fare after Uber's cut)
  - Shows trip distance from API (`unmodifiedDistance`) and ETA calculation
- **Live Driver Accumulation System**: Counts unique drivers near user's location
  - Uses Uber's GetStatus GraphQL API (`m.uber.com/go/graphql`)
  - **Coordinate-based deduplication**: Drivers within 100m are counted as one
  - **Bearing check**: Drivers at same location facing opposite directions (>90°) counted separately
  - **Velocity-based trajectory tracking**: Moving drivers are tracked using:
    - Cross-track tolerance: 100m lateral deviation allowed
    - Speed-based distance: max 30m/s (108km/h) × elapsed time
    - Bearing alignment: driver must be moving in same direction (within 30°)
    - Movement alignment: new position must be along expected trajectory (within 45°)
  - **5 sample points**: Polls center + N/S/E/W within 1km radius of user location
  - **3-second polling**: Rotates through sample points for broader coverage
  - **3-minute rolling window**: Accumulates unique drivers, older entries expire
  - **Per-user cache**: Each user has separate driver cache, cleared on logout
  - **Geolocation support**: Uses browser location or defaults to Perth CBD
  - Displays product type breakdown (UberX, Comfort, XL, Black) and sample counter

### Uber Account Connection
- Users can connect their Uber driver accounts via `/uber-connect`
- Credentials are captured from Uber mobile app API requests and stored encrypted
- CSRF-protected forms prevent unauthorized credential changes
- Users can disconnect their accounts from profile settings
- All Uber API functions accept per-user credentials as parameters
- **Owner Credential Management**: Owners can view/edit/disconnect any user's Uber credentials via `/admin/uber-credentials/<user_id>`

### Frontend Architecture
- Server-side rendered templates using Jinja2
- Tailwind CSS loaded via CDN for styling
- Premium glassmorphism design with:
  - Frosted glass panels with blur effects
  - Custom RizTar logo (`static/images/logo.png`) - used site-wide with invert filter on dark backgrounds
  - Dark/light contrast themes
  - Responsive mobile-first design
- Pages: login, register, home hub, location change, ride details, admin panel
- **Async Loading**: Pages load instantly with skeleton animations, then fetch data via API endpoints:
  - Home page (`/`): Shows skeleton loading for driver profile and vehicles
  - Location page (`/change-location`): Shows skeleton loading for default vehicle
  - Fetch Ride page (`/fetch-ride`): Shows spinner with rotating status messages
- **User Feedback**: Status messages cycle during loading ("Connecting to Uber...", "Authenticating session...", etc.)
- **Security**: All dynamic content properly escaped using `escapeHtml()` function to prevent XSS attacks

### Real-Time Chat Lobby
- **Flask-SocketIO** with eventlet async mode for real-time communication
- **ChatMessage model**: Stores messages with user relationships, reply-to references, and timestamps
- **Online Users Display**: Shows connected users in horizontal boxes with role-based gradient colors
- **Features**:
  - Real-time messaging with instant updates
  - @mention system with user search dropdown
  - Reply-to-message functionality with visual threading
  - XSS protection via `|tojson` filter and `escapeHtml()` function
- **Events**: `connect`, `disconnect`, `send_message`, `get_online_users`

### Web Push Notifications
- **VAPID Authentication**: Uses VAPID keys stored in environment variables (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`)
- **PushSubscription Model**: Stores user subscriptions with endpoint, p256dh key, and auth key
- **Service Worker**: Located at `/static/service-worker.js` for background push handling
- **API Endpoints**:
  - `/api/push/vapid-public-key`: Returns public VAPID key for client registration
  - `/api/push/subscribe`: Registers a push subscription for the current user
  - `/api/push/unsubscribe`: Removes push subscription
  - `/api/push/status`: Checks if current user has active subscription
  - `/api/push/test`: Sends a test notification to current user
- **UI**: Notification bell icon in header with green indicator when subscribed
- **Browser Support**: Works with Chrome, Firefox, Edge; iOS Safari requires PWA installation

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
│   ├── uber_connect.html # Uber account connection page
│   ├── chat_lobby.html  # Real-time chat with online users
│   ├── demand_intel.html # Unified Demand Intelligence (Hotspots, Surge Map, Events tabs)
│   ├── live_drivers.html # Live driver density map with product type breakdown
│   ├── smart_route.html  # Smart Route Planner
│   └── flight_center.html # Live flight arrivals
└── static/
    ├── images/          # Static assets
    └── service-worker.js # Push notification handler
```

## External Dependencies

### Database
- PostgreSQL (configured via `DATABASE_URL` environment variable)

### Third-Party APIs
- **Uber Internal APIs** (`cn-geo1.uber.com`): Vehicle data, driver status, location updates
- **OpenStreetMap Nominatim**: Address geocoding for location tracking
- **Ticketmaster Discovery API**: Perth event calendar integration
- **Perth Airport Web Scraping**: Live flight arrival data

### Required Environment Variables
- `DATABASE_URL`: PostgreSQL connection string (auto-configured)
- `FLASK_SECRET_KEY`: Session encryption key
- `RIZTAR_OWNER_EMAIL`: Auto-create owner account email
- `RIZTAR_OWNER_PASSWORD`: Auto-create owner account password
- `VAPID_PUBLIC_KEY`: Web push notification public key
- `VAPID_PRIVATE_KEY`: Web push notification private key

### Python Dependencies
- Flask, Flask-Login, Flask-SQLAlchemy, Flask-WTF
- Requests (HTTP client for Uber API)
- WTForms with email-validator
- Werkzeug (password hashing)
- psycopg2-binary (PostgreSQL adapter)
- Gunicorn (production server)
- cryptography (Fernet encryption for Uber credentials)
- Flask-SocketIO, eventlet (real-time chat)
- pywebpush, py-vapid (web push notifications)