"""
Ingest DeviceLogonEvents CSV into PostgreSQL.

Usage (from the demo root):
    python scripts/ingest.py

Requires:
    - PostgreSQL running (docker compose up -d postgres)
    - agent-data-toolkit[postgresql] installed
    - CSV at data/DeviceLogonEvents.csv

The script uses agent-data-toolkit's PostgresClient with psycopg COPY
for fast bulk loading (~4,700 rows).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEMO_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = DEMO_DIR / "data" / "DeviceLogonEvents.csv"

# Load .env from the demo root (does not override existing env vars)
load_dotenv(DEMO_DIR / ".env")

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "postgres")
PG_DATABASE = os.getenv("PG_DATABASE", "devicelogon")
PG_SSLMODE = os.getenv("PG_SSLMODE", "disable")

DSN = (
    f"postgresql://{PG_USER}:{PG_PASSWORD}"
    f"@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    f"?sslmode={PG_SSLMODE}"
)

TABLE_NAME = "device_logon_events"

# ---------------------------------------------------------------------------
# CSV column → PostgreSQL column mapping
# ---------------------------------------------------------------------------
# The CSV has human-friendly headers with spaces and "[UTC]" suffixes.
# We map them to the snake_case columns created by 01_init.sql.

COLUMN_MAP: dict[str, str] = {
    "TimeGenerated [UTC]":                          "time_generated",
    "Timestamp [UTC]":                              "timestamp",
    "TenantId":                                     "tenant_id",
    "DeviceId":                                     "device_id",
    "DeviceName":                                   "device_name",
    "MachineGroup":                                 "machine_group",
    "AccountDomain":                                "account_domain",
    "AccountName":                                  "account_name",
    "AccountSid":                                   "account_sid",
    "ActionType":                                   "action_type",
    "LogonType":                                    "logon_type",
    "LogonId":                                      "logon_id",
    "IsLocalAdmin":                                 "is_local_admin",
    "FailureReason":                                "failure_reason",
    "Protocol":                                     "protocol",
    "RemoteDeviceName":                             "remote_device_name",
    "RemoteIP":                                     "remote_ip",
    "RemoteIPType":                                 "remote_ip_type",
    "RemotePort":                                   "remote_port",
    "InitiatingProcessAccountDomain":               "initiating_process_account_domain",
    "InitiatingProcessAccountName":                 "initiating_process_account_name",
    "InitiatingProcessAccountObjectId":             "initiating_process_account_object_id",
    "InitiatingProcessAccountSid":                  "initiating_process_account_sid",
    "InitiatingProcessAccountUpn":                  "initiating_process_account_upn",
    "InitiatingProcessCommandLine":                 "initiating_process_command_line",
    "InitiatingProcessFileName":                    "initiating_process_file_name",
    "InitiatingProcessFolderPath":                  "initiating_process_folder_path",
    "InitiatingProcessId":                          "initiating_process_id",
    "InitiatingProcessIntegrityLevel":              "initiating_process_integrity_level",
    "InitiatingProcessMD5":                         "initiating_process_md5",
    "InitiatingProcessSHA1":                        "initiating_process_sha1",
    "InitiatingProcessSHA256":                      "initiating_process_sha256",
    "InitiatingProcessTokenElevation":              "initiating_process_token_elevation",
    "InitiatingProcessFileSize":                    "initiating_process_file_size",
    "InitiatingProcessCreationTime [UTC]":          "initiating_process_creation_time",
    "InitiatingProcessSessionId":                   "initiating_process_session_id",
    "InitiatingProcessUniqueId":                    "initiating_process_unique_id",
    "IsInitiatingProcessRemoteSession":             "is_initiating_process_remote_session",
    "InitiatingProcessRemoteSessionDeviceName":     "initiating_process_remote_session_device",
    "InitiatingProcessRemoteSessionIP":             "initiating_process_remote_session_ip",
    "InitiatingProcessParentFileName":              "initiating_process_parent_file_name",
    "InitiatingProcessParentId":                    "initiating_process_parent_id",
    "InitiatingProcessParentCreationTime [UTC]":    "initiating_process_parent_creation_time",
    "InitiatingProcessVersionInfoCompanyName":      "initiating_process_version_company",
    "InitiatingProcessVersionInfoFileDescription":  "initiating_process_version_file_desc",
    "InitiatingProcessVersionInfoInternalFileName":  "initiating_process_version_internal_name",
    "InitiatingProcessVersionInfoOriginalFileName":  "initiating_process_version_original_name",
    "InitiatingProcessVersionInfoProductName":       "initiating_process_version_product_name",
    "InitiatingProcessVersionInfoProductVersion":    "initiating_process_version_product_ver",
    "AdditionalFields":                             "additional_fields",
    "AppGuardContainerId":                          "app_guard_container_id",
    "ReportId":                                     "report_id",
    "SourceSystem":                                 "source_system",
    "Type":                                         "event_type",
}


# ---------------------------------------------------------------------------
# Data cleaning
# ---------------------------------------------------------------------------

def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean and coerce the raw CSV DataFrame so it matches the PG schema.
    """
    # Strip whitespace from column names (CSV has trailing spaces)
    df.columns = df.columns.str.strip()

    # Rename to snake_case PG column names
    df = df.rename(columns=COLUMN_MAP)

    # Keep only mapped columns (drop anything we didn't map)
    keep = [c for c in COLUMN_MAP.values() if c in df.columns]
    df = df[keep]

    # --- Timestamps ---
    ts_cols = [
        "time_generated", "timestamp",
        "initiating_process_creation_time",
        "initiating_process_parent_creation_time",
    ]
    for col in ts_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

    # --- Booleans ---
    bool_cols = ["is_local_admin", "is_initiating_process_remote_session"]
    for col in bool_cols:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.strip()
                .str.upper()
                .map({"TRUE": True, "FALSE": False})
            )

    # --- Integers ---
    int_cols = [
        "logon_id", "remote_port", "report_id",
        "initiating_process_id", "initiating_process_file_size",
        "initiating_process_session_id", "initiating_process_unique_id",
        "initiating_process_parent_id",
    ]
    for col in int_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    # --- JSONB (additional_fields) ---
    if "additional_fields" in df.columns:
        def _normalize_json(val):
            if pd.isna(val) or not str(val).strip():
                return None
            s = str(val).strip()
            try:
                return json.dumps(json.loads(s))
            except (json.JSONDecodeError, ValueError):
                return None
        df["additional_fields"] = df["additional_fields"].apply(_normalize_json)

    # --- INET (remote_ip) — keep as text, PG will cast ---
    # Just make sure empty strings become None
    if "remote_ip" in df.columns:
        df["remote_ip"] = df["remote_ip"].replace({"": None, " ": None})

    # --- Strip NUL bytes from text columns ---
    for col in df.select_dtypes(include=["object", "string"]).columns:
        df[col] = (
            df[col]
            .astype("string")
            .str.replace("\x00", "", regex=False)
            .str.strip()
        )

    # Replace empty strings / whitespace-only with None
    df = df.replace({"": None, " ": None})

    # Convert extension dtypes to object so pd.NA → None for psycopg
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].astype(object)
        elif hasattr(df[col].dtype, "na_value"):
            df[col] = df[col].astype(object)
    df = df.where(df.notna(), None)

    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not CSV_PATH.exists():
        print(f"❌ CSV not found: {CSV_PATH}")
        sys.exit(1)

    print(f"📄 Reading {CSV_PATH.name} …")
    df_raw = pd.read_csv(CSV_PATH, low_memory=False)
    print(f"   {len(df_raw):,} rows, {len(df_raw.columns)} columns")

    print("🧹 Cleaning …")
    df = clean_dataframe(df_raw)
    print(f"   {len(df):,} rows, {len(df.columns)} columns after cleaning")

    print(f"🔌 Connecting to PostgreSQL ({PG_HOST}:{PG_PORT}/{PG_DATABASE}) …")

    from agent_data_toolkit.postgresql import PostgresClient

    pg = PostgresClient.from_dsn(DSN)

    # Verify connection
    rows = pg.query_rows("SELECT 1 AS ok")
    assert rows[0]["ok"] == 1, "Connection test failed"
    print("   ✅ Connected")

    # Check table exists
    tables = pg.list_tables("public")
    table_names = [t["table_name"] for t in tables]
    if TABLE_NAME not in table_names:
        print(f"❌ Table '{TABLE_NAME}' not found. Did docker-entrypoint-initdb run?")
        sys.exit(1)

    # Bulk load
    print(f"📥 Loading {len(df):,} rows into {TABLE_NAME} …")
    pg.copy_df_in(TABLE_NAME, df)

    # Verify
    count = pg.query_rows(f"SELECT COUNT(*) AS n FROM {TABLE_NAME}")[0]["n"]
    print(f"   ✅ {count:,} rows in {TABLE_NAME}")

    # Quick stats
    stats = pg.query_rows(f"""
        SELECT
            action_type,
            COUNT(*) AS cnt
        FROM {TABLE_NAME}
        GROUP BY action_type
        ORDER BY cnt DESC
    """)
    print("\n📊 Rows by action_type:")
    for row in stats:
        print(f"   {row['action_type']:<20} {row['cnt']:>6,}")

    pg.close()
    print("\n✅ Ingestion complete!")


if __name__ == "__main__":
    main()
