-- ============================================================
-- 02_load_data.sql — Bulk-load DeviceLogonEvents.csv via COPY
-- Runs automatically on first `docker compose up` (initdb).
--
-- Strategy:
--   1. Create a temporary staging table (all TEXT columns)
--   2. COPY the raw CSV into the staging table
--   3. INSERT INTO the typed table with proper casts
--   4. Drop the staging table
-- ============================================================

-- ── 1. Staging table (column names must match CSV headers exactly) ──

CREATE TEMP TABLE _staging (
    "TimeGenerated [UTC]"                                   TEXT,
    "TenantId"                                              TEXT,
    "AccountDomain"                                         TEXT,
    "AccountName"                                           TEXT,
    "AccountSid"                                            TEXT,
    "ActionType"                                            TEXT,
    "AdditionalFields"                                      TEXT,
    "AppGuardContainerId"                                   TEXT,
    "DeviceId"                                              TEXT,
    "DeviceName"                                            TEXT,
    "FailureReason"                                         TEXT,
    "InitiatingProcessAccountDomain"                        TEXT,
    "InitiatingProcessAccountName"                          TEXT,
    "InitiatingProcessAccountObjectId"                      TEXT,
    "InitiatingProcessAccountSid"                           TEXT,
    "InitiatingProcessAccountUpn"                           TEXT,
    "InitiatingProcessCommandLine"                          TEXT,
    "InitiatingProcessFileName"                             TEXT,
    "InitiatingProcessFolderPath"                           TEXT,
    "InitiatingProcessId"                                   TEXT,
    "InitiatingProcessIntegrityLevel"                       TEXT,
    "InitiatingProcessMD5"                                  TEXT,
    "InitiatingProcessParentFileName"                       TEXT,
    "InitiatingProcessParentId"                             TEXT,
    "InitiatingProcessSHA1"                                 TEXT,
    "InitiatingProcessSHA256"                               TEXT,
    "InitiatingProcessTokenElevation"                       TEXT,
    "IsLocalAdmin"                                          TEXT,
    "LogonId"                                               TEXT,
    "LogonType"                                             TEXT,
    "MachineGroup"                                          TEXT,
    "Protocol"                                              TEXT,
    "RemoteDeviceName"                                      TEXT,
    "RemoteIP"                                              TEXT,
    "RemoteIPType"                                          TEXT,
    "RemotePort"                                            TEXT,
    "ReportId"                                              TEXT,
    "Timestamp [UTC]"                                       TEXT,
    "InitiatingProcessParentCreationTime [UTC]"             TEXT,
    "InitiatingProcessCreationTime [UTC]"                   TEXT,
    "InitiatingProcessFileSize"                             TEXT,
    "InitiatingProcessVersionInfoCompanyName"               TEXT,
    "InitiatingProcessVersionInfoFileDescription"           TEXT,
    "InitiatingProcessVersionInfoInternalFileName"          TEXT,
    "InitiatingProcessVersionInfoOriginalFileName"          TEXT,
    "InitiatingProcessVersionInfoProductName"               TEXT,
    "InitiatingProcessVersionInfoProductVersion"            TEXT,
    "InitiatingProcessSessionId"                            TEXT,
    "IsInitiatingProcessRemoteSession"                      TEXT,
    "InitiatingProcessRemoteSessionDeviceName"              TEXT,
    "InitiatingProcessRemoteSessionIP"                      TEXT,
    "InitiatingProcessUniqueId"                             TEXT,
    "SourceSystem"                                          TEXT,
    "Type"                                                  TEXT
);

-- ── 2. COPY the CSV (mounted at /data/ in docker-compose) ──

COPY _staging
FROM '/data/DeviceLogonEvents.csv'
WITH (FORMAT csv, HEADER true);

-- ── 3. INSERT into typed table with casts ──
-- Helper: NULLIF(trim(x), '') turns empty/whitespace-only → NULL

INSERT INTO device_logon_events (
    time_generated,
    "timestamp",
    tenant_id,
    device_id,
    device_name,
    machine_group,
    account_domain,
    account_name,
    account_sid,
    action_type,
    logon_type,
    logon_id,
    is_local_admin,
    failure_reason,
    protocol,
    remote_device_name,
    remote_ip,
    remote_ip_type,
    remote_port,
    initiating_process_account_domain,
    initiating_process_account_name,
    initiating_process_account_object_id,
    initiating_process_account_sid,
    initiating_process_account_upn,
    initiating_process_command_line,
    initiating_process_file_name,
    initiating_process_folder_path,
    initiating_process_id,
    initiating_process_integrity_level,
    initiating_process_md5,
    initiating_process_sha1,
    initiating_process_sha256,
    initiating_process_token_elevation,
    initiating_process_file_size,
    initiating_process_creation_time,
    initiating_process_session_id,
    initiating_process_unique_id,
    is_initiating_process_remote_session,
    initiating_process_remote_session_device,
    initiating_process_remote_session_ip,
    initiating_process_parent_file_name,
    initiating_process_parent_id,
    initiating_process_parent_creation_time,
    initiating_process_version_company,
    initiating_process_version_file_desc,
    initiating_process_version_internal_name,
    initiating_process_version_original_name,
    initiating_process_version_product_name,
    initiating_process_version_product_ver,
    additional_fields,
    app_guard_container_id,
    report_id,
    source_system,
    event_type
)
SELECT
    -- Timestamps (format: "M/D/YYYY, H:MM:SS AM")
    NULLIF(trim("TimeGenerated [UTC]"), '')::TIMESTAMPTZ,
    NULLIF(trim("Timestamp [UTC]"), '')::TIMESTAMPTZ,

    -- Tenant / Device
    NULLIF(trim("TenantId"), ''),
    NULLIF(trim("DeviceId"), ''),
    NULLIF(trim("DeviceName"), ''),
    NULLIF(trim("MachineGroup"), ''),

    -- Account
    NULLIF(trim("AccountDomain"), ''),
    NULLIF(trim("AccountName"), ''),
    NULLIF(trim("AccountSid"), ''),

    -- Logon details
    NULLIF(trim("ActionType"), ''),
    NULLIF(trim("LogonType"), ''),
    NULLIF(trim("LogonId"), '')::NUMERIC::BIGINT,
    CASE upper(trim("IsLocalAdmin"))
        WHEN 'TRUE'  THEN true
        WHEN 'FALSE' THEN false
        ELSE NULL
    END,
    NULLIF(trim("FailureReason"), ''),
    NULLIF(trim("Protocol"), ''),

    -- Remote endpoint
    NULLIF(trim("RemoteDeviceName"), ''),
    NULLIF(NULLIF(trim("RemoteIP"), ''), '-')::INET,
    NULLIF(trim("RemoteIPType"), ''),
    NULLIF(NULLIF(trim("RemotePort"), ''), '-')::INTEGER,

    -- Initiating process
    NULLIF(trim("InitiatingProcessAccountDomain"), ''),
    NULLIF(trim("InitiatingProcessAccountName"), ''),
    NULLIF(trim("InitiatingProcessAccountObjectId"), ''),
    NULLIF(trim("InitiatingProcessAccountSid"), ''),
    NULLIF(trim("InitiatingProcessAccountUpn"), ''),
    NULLIF(trim("InitiatingProcessCommandLine"), ''),
    NULLIF(trim("InitiatingProcessFileName"), ''),
    NULLIF(trim("InitiatingProcessFolderPath"), ''),
    NULLIF(trim("InitiatingProcessId"), '')::NUMERIC::BIGINT,
    NULLIF(trim("InitiatingProcessIntegrityLevel"), ''),
    NULLIF(trim("InitiatingProcessMD5"), ''),
    NULLIF(trim("InitiatingProcessSHA1"), ''),
    NULLIF(trim("InitiatingProcessSHA256"), ''),
    NULLIF(trim("InitiatingProcessTokenElevation"), ''),
    NULLIF(trim("InitiatingProcessFileSize"), '')::NUMERIC::BIGINT,
    NULLIF(trim("InitiatingProcessCreationTime [UTC]"), '')::TIMESTAMPTZ,
    NULLIF(trim("InitiatingProcessSessionId"), '')::NUMERIC::BIGINT,
    NULLIF(trim("InitiatingProcessUniqueId"), '')::NUMERIC::BIGINT,
    CASE upper(trim("IsInitiatingProcessRemoteSession"))
        WHEN 'TRUE'  THEN true
        WHEN 'FALSE' THEN false
        ELSE NULL
    END,
    NULLIF(trim("InitiatingProcessRemoteSessionDeviceName"), ''),
    NULLIF(trim("InitiatingProcessRemoteSessionIP"), ''),

    -- Parent process
    NULLIF(trim("InitiatingProcessParentFileName"), ''),
    NULLIF(trim("InitiatingProcessParentId"), '')::NUMERIC::BIGINT,
    NULLIF(trim("InitiatingProcessParentCreationTime [UTC]"), '')::TIMESTAMPTZ,

    -- Version info
    NULLIF(trim("InitiatingProcessVersionInfoCompanyName"), ''),
    NULLIF(trim("InitiatingProcessVersionInfoFileDescription"), ''),
    NULLIF(trim("InitiatingProcessVersionInfoInternalFileName"), ''),
    NULLIF(trim("InitiatingProcessVersionInfoOriginalFileName"), ''),
    NULLIF(trim("InitiatingProcessVersionInfoProductName"), ''),
    NULLIF(trim("InitiatingProcessVersionInfoProductVersion"), ''),

    -- Additional
    CASE
        WHEN NULLIF(trim("AdditionalFields"), '') IS NOT NULL
        THEN trim("AdditionalFields")::JSONB
        ELSE NULL
    END,
    NULLIF(trim("AppGuardContainerId"), ''),
    NULLIF(trim("ReportId"), '')::NUMERIC::BIGINT,
    NULLIF(trim("SourceSystem"), ''),
    NULLIF(trim("Type"), '')

FROM _staging;

-- ── 4. Clean up ──

DROP TABLE _staging;

-- ── 5. Quick sanity check (logged to container stdout) ──

DO $$
DECLARE
    row_count BIGINT;
BEGIN
    SELECT COUNT(*) INTO row_count FROM device_logon_events;
    RAISE NOTICE '✅ device_logon_events loaded: % rows', row_count;
END $$;
