CREATE TABLE IF NOT EXISTS users (
    uid             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    username        TEXT        NOT NULL,
    email           TEXT        NOT NULL UNIQUE,
    password_hash   TEXT        NOT NULL,
    is_verified     BOOLEAN     NOT NULL DEFAULT FALSE,
    is_suspended    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed   TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auth_tokens (
    token           TEXT        PRIMARY KEY,
    uid             UUID        REFERENCES users(uid) ON DELETE CASCADE,
    created_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed   TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at      TIMESTAMP   NOT NULL
);  

CREATE TABLE IF NOT EXISTS verification_requests (
    verify_token    TEXT        NOT NULL UNIQUE,
    uid             UUID        REFERENCES users(uid) ON DELETE CASCADE,
    created_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at      TIMESTAMP   NOT NULL
);

CREATE TABLE IF NOT EXISTS password_reset_requests (
    reset_token     TEXT        NOT NULL UNIQUE,
    uid             UUID        REFERENCES users(uid) ON DELETE CASCADE,
    created_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at      TIMESTAMP   NOT NULL
);

CREATE TABLE IF NOT EXISTS user_preferences (
    uid             UUID        REFERENCES users(uid) ON DELETE CASCADE,
    email_subscribe BOOLEAN     NOT NULL DEFAULT FALSE,
    profile_visible BOOLEAN     NOT NULL DEFAULT TRUE,
    display_name    TEXT        NOT NULL,
    biography       TEXT        NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS server_metrics (
    ts              BIGINT      PRIMARY KEY,
    cpu_percent     REAL        NOT NULL,
    ram_used        REAL        NOT NULL,
    disk_used       REAL        NOT NULL,
    cpu_temp        REAL
);

CREATE TABLE IF NOT EXISTS uptime (
	epoch           BIGINT      NOT NULL DEFAULT FLOOR(EXTRACT(EPOCH FROM NOW())),
    epoch_date      DATE        NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE(epoch)
);

CREATE TABLE IF NOT EXISTS uptime_reports (
    id              SERIAL      PRIMARY KEY,
    report_date     DATE        NOT NULL DEFAULT CURRENT_DATE,
    created_at      TIMESTAMP   NOT NULL,
    sent_at         TIMESTAMP,
    uptime          FLOAT       NOT NULL,
    emoji_sparkline TEXT        NOT NULL,
    UNIQUE(report_date)
);
