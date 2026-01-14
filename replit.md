# RizTar - Uber Driver Management System

## Overview

RizTar is a premium Flask-based web application designed for comprehensive management of Uber driver operations. It provides robust user authentication with role-based access, integrates with Uber's internal APIs for real-time vehicle and driver data, and features a luxury glassmorphism-styled dashboard for ride management. The system aims to enhance operational efficiency, provide insightful data for drivers, and offer advanced intelligence for market analysis, particularly within the Perth metro area.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Backend Framework
- **Flask**: Core web framework with Flask-Login for authentication and Flask-WTF for form validation.

### Database Layer
- **PostgreSQL**: Utilizes SQLAlchemy ORM with Flask-SQLAlchemy for data persistence. Key tables include `users`, `roles`, `user_roles`, and `chat_messages`.

### Authentication & Authorization
- Password hashing via Werkzeug.
- Role-based access control with built-in `User`, `Moderator`, `Owner` roles and support for custom roles.
- Permissions are aggregated from all assigned roles.
- UI elements are locked for users without necessary permissions.

### Uber API Integration
- Custom API client in `objects/uberDev.py` for interacting with Uber's internal endpoints.
- **Per-user credentials**: Encrypted storage of Uber driver credentials (cookies, headers, refresh tokens) using Fernet.
- Features include vehicle details, driver location tracking, token refresh, and fare pricing via GraphQL.
- **Live Driver Accumulation System**: Counts unique drivers near user's location using Uber's GetStatus GraphQL API. Employs coordinate-based deduplication, bearing checks, and velocity-based trajectory tracking across 5 sample points, polling every 3 seconds within a 3-minute rolling window.
- **Homepage Drivers Nearby Widget**: Provides background scanning of driver counts every 5 minutes, displaying UberX, XL, and Black types with change indicators.

### Frontend Architecture
- Server-side rendered templates using Jinja2.
- **Tailwind CSS**: For styling, with a premium glassmorphism design featuring frosted panels, custom logo, and dark/light themes.
- **Responsive design**: Mobile-first approach.
- **Async Loading**: Pages load with skeleton animations, fetching data via API endpoints.
- **User Feedback**: Status messages during loading processes.
- **Security**: XSS protection via `escapeHtml()` function.

### Real-Time Communication
- **Flask-SocketIO**: Powers a real-time chat lobby with instant messaging, @mentions, reply-to functionality, and online user display.

### Web Push Notifications
- VAPID authenticated push notifications using `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY`.
- Service worker (`/static/service-worker.js`) handles background push notifications.
- API endpoints for subscription management and testing.

### Intelligence Engine (Owner-Only)
- A self-learning 24/7 driver monitoring system for Perth metro.
- **Architecture**: Includes database models for `DriverObservation`, `DriverFingerprint`, `ScanBatch`, `HourlySnapshot`, `DailyPattern`, `CorrelationModel`, and `PredictionModel`.
- **Perth Grid**: 45+ zones with varying coordinate spacing.
- **Deduplication Engine**: Multi-factor fingerprinting with zone-adaptive thresholds and speed limits.
- **Trajectory Analyzer**: Tracks driver movements, predicts destinations, and records zone-to-zone transitions.
- **Background Daemon**: Continuous 24/7 scanning with crash recovery, retry mechanisms, and a watchdog timer.
- **Learning Engine**: Performs hourly analysis, discovers daily patterns, detects correlations, and generates/validates predictions.
- **15-Minute Window System**: 
  - Generates activity reports every 15 minutes aligned to clock time (00:00, 00:15, 00:30, 00:45)
  - Clears map and resets all in-memory state after each report
  - Preserves last window summary for display during new window collection
  - Zone activity levels: HOT (short dwell + high outflow + moderate drivers), WARM (balanced flow), COLD, NO_DATA
  - MOVE/STAY recommendations based on zone comparison with confidence scoring
  - UI shows countdown timer, best zone, and actionable recommendations
- **Dashboard**: Premium glassmorphism design displaying real-time stats, system health, live coverage map with movement trails, zone flow, hotspots, learned patterns, and predictions.

## External Dependencies

### Database
- **PostgreSQL**: Configured via `DATABASE_URL` environment variable.

### Third-Party APIs
- **Uber Internal APIs**: For vehicle details, driver status, location updates, and fare pricing.
- **OpenStreetMap Nominatim**: For address geocoding.
- **Ticketmaster Discovery API**: For Perth event calendar integration.
- **Perth Airport Web Scraping**: For live flight arrival data.

### Python Dependencies
- Flask, Flask-Login, Flask-SQLAlchemy, Flask-WTF
- Requests
- WTForms
- Werkzeug
- psycopg2-binary
- Gunicorn
- cryptography
- Flask-SocketIO, eventlet
- pywebpush, py-vapid