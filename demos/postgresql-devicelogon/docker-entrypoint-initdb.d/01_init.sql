-- ============================================================
-- DeviceLogonEvents — Microsoft Defender for Endpoint schema
-- Runs once on first `docker compose up` (initdb).
-- ============================================================

-- Create the target database (POSTGRES_DB already creates "devicelogon",
-- but this keeps it explicit and idempotent for clarity).
-- Note: The POSTGRES_DB env var in docker-compose already creates the DB,
-- so this script runs *inside* that DB.

CREATE TABLE IF NOT EXISTS device_logon_events (
    id                                          SERIAL PRIMARY KEY,

    -- Timestamps
    time_generated                              TIMESTAMPTZ,
    "timestamp"                                 TIMESTAMPTZ,

    -- Tenant / Device
    tenant_id                                   TEXT,
    device_id                                   TEXT,
    device_name                                 TEXT,
    machine_group                               TEXT,

    -- Account
    account_domain                              TEXT,
    account_name                                TEXT,
    account_sid                                 TEXT,

    -- Logon details
    action_type                                 TEXT,
    logon_type                                  TEXT,
    logon_id                                    BIGINT,
    is_local_admin                              BOOLEAN,
    failure_reason                              TEXT,
    protocol                                    TEXT,

    -- Remote endpoint
    remote_device_name                          TEXT,
    remote_ip                                   INET,
    remote_ip_type                              TEXT,
    remote_port                                 INTEGER,

    -- Initiating process
    initiating_process_account_domain           TEXT,
    initiating_process_account_name             TEXT,
    initiating_process_account_object_id        TEXT,
    initiating_process_account_sid              TEXT,
    initiating_process_account_upn              TEXT,
    initiating_process_command_line              TEXT,
    initiating_process_file_name                TEXT,
    initiating_process_folder_path              TEXT,
    initiating_process_id                       BIGINT,
    initiating_process_integrity_level          TEXT,
    initiating_process_md5                      TEXT,
    initiating_process_sha1                     TEXT,
    initiating_process_sha256                   TEXT,
    initiating_process_token_elevation          TEXT,
    initiating_process_file_size                BIGINT,
    initiating_process_creation_time            TIMESTAMPTZ,
    initiating_process_session_id               BIGINT,
    initiating_process_unique_id                BIGINT,
    is_initiating_process_remote_session        BOOLEAN,
    initiating_process_remote_session_device    TEXT,
    initiating_process_remote_session_ip        TEXT,

    -- Initiating process — parent
    initiating_process_parent_file_name         TEXT,
    initiating_process_parent_id                BIGINT,
    initiating_process_parent_creation_time     TIMESTAMPTZ,

    -- Initiating process — version info
    initiating_process_version_company          TEXT,
    initiating_process_version_file_desc        TEXT,
    initiating_process_version_internal_name    TEXT,
    initiating_process_version_original_name    TEXT,
    initiating_process_version_product_name     TEXT,
    initiating_process_version_product_ver      TEXT,

    -- Additional
    additional_fields                           JSONB,
    app_guard_container_id                      TEXT,
    report_id                                   BIGINT,
    source_system                               TEXT,
    event_type                                  TEXT
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_dle_action_type   ON device_logon_events (action_type);
CREATE INDEX IF NOT EXISTS idx_dle_account_name  ON device_logon_events (account_name);
CREATE INDEX IF NOT EXISTS idx_dle_device_name   ON device_logon_events (device_name);
CREATE INDEX IF NOT EXISTS idx_dle_remote_ip     ON device_logon_events (remote_ip);
CREATE INDEX IF NOT EXISTS idx_dle_time_gen      ON device_logon_events (time_generated);
CREATE INDEX IF NOT EXISTS idx_dle_logon_type    ON device_logon_events (logon_type);
