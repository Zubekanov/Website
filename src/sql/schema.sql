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

CREATE TABLE IF NOT EXISTS tournament_users (
	uid             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
	user_id         UUID        REFERENCES users(uid),
	display_name	TEXT        NOT NULL,
	created_at	    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tournament (
	uid					UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	display_name		TEXT        NOT NULL,
	share_code			TEXT        UNIQUE,
	created_at			TIMESTAMPTZ NOT NULL DEFAULT now(),
	created_by			UUID REFERENCES tournament_users(uid) ON DELETE SET NULL,

	status				TEXT NOT NULL DEFAULT 'draft'
		CHECK (status IN ('draft','running','completed','cancelled', 'template')),

	games_per_match		INT  NOT NULL DEFAULT 3         CHECK (games_per_match > 0),
	games_per_win		INT  NOT NULL DEFAULT 2         CHECK (games_per_win > 0 AND games_per_win <= games_per_match),
	can_draw			BOOLEAN NOT NULL DEFAULT TRUE,

	points_per_win		INT  NOT NULL DEFAULT 3         CHECK (points_per_win  >= 0),
	points_per_draw		INT  NOT NULL DEFAULT 1         CHECK (points_per_draw >= 0),
	points_per_loss		INT  NOT NULL DEFAULT 0         CHECK (points_per_loss >= 0),

	num_rounds			INT  NOT NULL DEFAULT 4        	CHECK (num_rounds > 0),
	n_losses_elim		INT                             CHECK (n_losses_elim IS NULL OR n_losses_elim >= 1),

	cut_to_top			BOOLEAN NOT NULL DEFAULT FALSE,
	num_top				INT  NOT NULL DEFAULT 8         CHECK (num_top > 1),

	round_time_minutes	INT  NOT NULL DEFAULT 50        CHECK (round_time_minutes > 0),
	time_enforcement	TEXT NOT NULL DEFAULT 'none'
		CHECK (time_enforcement IN ('none','soft','hard_auto_draw')),

	players_can_submit_results	BOOLEAN NOT NULL DEFAULT TRUE,
	require_double_confirmation	BOOLEAN NOT NULL DEFAULT TRUE,

	join_locked					BOOLEAN NOT NULL DEFAULT FALSE,  -- manual lock toggle (works before or after start)
	allow_join_after_start		BOOLEAN NOT NULL DEFAULT FALSE,  -- policy gate once status='running'
	lock_joins_at				TIMESTAMPTZ,                      -- optional scheduled lock

	CONSTRAINT cut_requires_positive_top
		CHECK ((cut_to_top = FALSE) OR (num_top IS NOT NULL AND num_top > 1))
);

CREATE TABLE IF NOT EXISTS tournament_participation (
	uid				UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	tournament		UUID NOT NULL REFERENCES tournament(uid) ON DELETE CASCADE,
	tournament_users	UUID NOT NULL REFERENCES tournament_users(uid) ON DELETE CASCADE,
	seed			INT,
	is_disqualified	BOOLEAN NOT NULL DEFAULT FALSE,
	dropped			BOOLEAN NOT NULL DEFAULT FALSE,
	created_at		TIMESTAMPTZ NOT NULL DEFAULT now(),
	UNIQUE (tournament, tournament_users)
);

CREATE TABLE IF NOT EXISTS tournament_round (
	uid			UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	tournament	UUID NOT NULL REFERENCES tournament(uid) ON DELETE CASCADE,
	round_no	INT  NOT NULL CHECK (round_no > 0),
	started_at	TIMESTAMPTZ,
	ended_at	TIMESTAMPTZ,
	UNIQUE (tournament, round_no)
);

CREATE TABLE IF NOT EXISTS match (
	uid			UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	tournament	UUID NOT NULL REFERENCES tournament(uid) ON DELETE CASCADE,
	round		UUID NOT NULL REFERENCES tournament_round(uid) ON DELETE CASCADE,
	table_no	INT,
	player_a	UUID REFERENCES tournament_participation(uid) ON DELETE CASCADE,
	player_b	UUID REFERENCES tournament_participation(uid) ON DELETE CASCADE,
	-- NULL player_b = bye
	is_bye		BOOLEAN NOT NULL DEFAULT FALSE,
	-- record points/score summary for standings
	a_game_wins	INT NOT NULL DEFAULT 0 CHECK (a_game_wins >= 0),
	b_game_wins	INT NOT NULL DEFAULT 0 CHECK (b_game_wins >= 0),
	draws		INT NOT NULL DEFAULT 0 CHECK (draws >= 0),
	winner		CHAR(1) CHECK (winner IN ('A','B','D')), -- D=draw
	reported	BOOLEAN NOT NULL DEFAULT FALSE,
	UNIQUE (round, table_no),
	CONSTRAINT no_self_match CHECK (player_a IS NULL OR player_b IS NULL OR player_a <> player_b)
);

CREATE TABLE IF NOT EXISTS match_report (
	uid			UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	match		UUID NOT NULL REFERENCES match(uid) ON DELETE CASCADE,
	reporter	UUID NOT NULL REFERENCES tournament_participation(uid) ON DELETE CASCADE,
	a_game_wins	INT NOT NULL DEFAULT 0 CHECK (a_game_wins >= 0),
	b_game_wins	INT NOT NULL DEFAULT 0 CHECK (b_game_wins >= 0),
	draws		INT NOT NULL DEFAULT 0 CHECK (draws >= 0),
	submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	UNIQUE (match, reporter)
);
