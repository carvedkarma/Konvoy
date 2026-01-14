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
- **ML Training Data System (Demand Proxy Score)**:
  - Collects per-zone per-15-min window features for machine learning
  - Database model: `ZoneWindowFeature` stores training rows
  - Features: driver_count, inflow_rate, outflow_rate, net_flow, avg_dwell_sec, avg_speed_ms, confidence_avg, anomaly_score
  - Demand Proxy Score formula: `0.45 * outflow_rate_norm + 0.35 * (1 - dwell_norm) + 0.20 * drop_norm`
  - Activity classification: HOT (>=0.6), WARM (>=0.35), COLD (<0.35)
  - Zone flow tracking: Tracks inflow/outflow transitions and dwell times per zone
  - API endpoints: `/api/intelligence/training-data` (JSON/CSV export), `/api/intelligence/training-stats`
  - Produces ~15 training rows per 15-min window (~1,440 samples/day)
- **Dashboard**: Premium dark-themed design with:
  - Larger responsive map (450px mobile, 600px tablet, 700px desktop)
  - CSS custom properties for consistent dark theme styling
  - Compact stat cards with integrated window timer
  - Glassmorphic sidebar cards for recommendations, scanning status, hotspots, system health, and driver flow
  - Dark-themed charts with violet accent grid lines
  - Activity reports with color-coded levels and trend indicators
  - Predictions and patterns sections with gradient styling

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