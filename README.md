This project is a Flask-based web app that powers a personal site with account management, admin tooling, and integrations for services like Discord webhooks, Audiobookshelf registrations, and Minecraft whitelist requests. It uses PostgreSQL for core app data, a separate metrics database for server telemetry, and a small HTML/JS/CSS framework for rendering pages.

The website is hosted at [zubekanov.com](https://zubekanov.com), which you may be using to view this README at this very moment. If you are, I encourage you to register an account and click around the site. Otherwise, please do visit the website through the link.

### What the app does

- **Public site + account flows**: Landing page, login, registration, email verification, password reset (stub), and profile pages.
- **Session-based auth**: Secure session cookies backed by hashed tokens stored in `user_sessions`, with server-side validation and cache.
- **Email verification & messaging**: Registration creates a pending user, emails a verification link, and activates the account on success.
- **Integrations**
  - **Discord webhooks**: Users can register webhooks and subscribe them to event keys; moderation events are emitted to Discord.
  - **Audiobookshelf**: Users can submit access requests that are reviewed by admins.
  - **Minecraft**: Users can request whitelist access; approvals manage whitelist entries and audit status.
- **Admin dashboards & approvals**: Admin-only views for handling Audiobookshelf, Discord webhook, and Minecraft approval queues.
- **DB admin interface**: An internal “psql interface” page supports viewing/updating table data via API endpoints.
- **Server metrics dashboard**: A metrics page renders live graphs (CPU/RAM/Disk/Network) using data from a dedicated metrics DB.
- **Static resources**: Includes CSS/JS assets, a resume endpoint, and a light HTML builder for consistent page layouts.

### Core building blocks

- **Flask app** (`src/run.py`, `src/app/*`): Blueprints for page routes, JSON API endpoints, and static resources.
- **HTML builder** (`src/util/webpage_builder/*`): Generates page layouts, forms, banners, and metric charts.
- **Postgres interface** (`src/sql/psql_client.py`, `src/sql/psql_interface.py`): Connection pooling, CRUD helpers, auth/session management, and schema verification.
- **Schema configs** (`src/sql/tables/*.json`): JSON definitions used to create/verify tables on startup. In non-safe mode, unknown tables in configured schemas can be dropped.
- **Integrations** (`src/util/integrations/*`): Discord webhook emitter, event key registry, and email delivery via Gmail OAuth.
- **Metrics** (`src/util/webpage_builder/metrics_builder.py`): Reads from a metrics DB configured in `src/config/metrics_db.conf`.
