CREATE TABLE IF NOT EXISTS activity_events (
    activity_id text PRIMARY KEY,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'awaiting_feedback', 'done', 'failed')),
    summary text,
    note text,
    scheduled_at timestamptz NOT NULL DEFAULT now(),
    tg_chat_id bigint,
    tg_message_id bigint,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE activity_events ADD COLUMN IF NOT EXISTS note text;

CREATE INDEX IF NOT EXISTS activity_events_queue_idx
    ON activity_events (scheduled_at, created_at)
    WHERE status IN ('queued', 'failed');

CREATE TABLE IF NOT EXISTS pending_input (
    chat_id bigint PRIMARY KEY,
    activity_id text NOT NULL REFERENCES activity_events(activity_id) ON DELETE CASCADE,
    expecting text NOT NULL DEFAULT 'note' CHECK (expecting = 'note'),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS weekly_recaps (
    week_start date PRIMARY KEY,
    status text NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'sent', 'failed')),
    content text,
    provider_message_id text,
    attempts integer NOT NULL DEFAULT 1 CHECK (attempts > 0),
    last_error text,
    claimed_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);