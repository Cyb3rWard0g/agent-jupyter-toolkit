CREATE TABLE IF NOT EXISTS demo_users (
  id SERIAL PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO demo_users (username)
VALUES ('alice'), ('bob'), ('carol')
ON CONFLICT (username) DO NOTHING;
