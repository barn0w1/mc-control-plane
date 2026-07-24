PRAGMA foreign_keys = ON;

CREATE TABLE host_claims (
    id TEXT PRIMARY KEY NOT NULL,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    created_at TEXT NOT NULL,
    deletion_timestamp TEXT,
    vcpus INTEGER NOT NULL CHECK (vcpus > 0),
    memory_bytes INTEGER NOT NULL CHECK (memory_bytes > 0),
    storage_bytes INTEGER NOT NULL CHECK (storage_bytes > 0),
    observed_generation INTEGER NOT NULL DEFAULT 0 CHECK (observed_generation >= 0),
    conditions_json TEXT NOT NULL,
    retry_attempt INTEGER NOT NULL DEFAULT 0 CHECK (retry_attempt >= 0),
    next_reconcile_at_unix_ms INTEGER,
    last_error_kind TEXT,
    last_error_message TEXT
) STRICT;

CREATE TABLE hosts (
    id TEXT PRIMARY KEY NOT NULL,
    claim_id TEXT NOT NULL UNIQUE REFERENCES host_claims(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    vcpus INTEGER NOT NULL CHECK (vcpus > 0),
    memory_bytes INTEGER NOT NULL CHECK (memory_bytes > 0),
    storage_bytes INTEGER NOT NULL CHECK (storage_bytes > 0),
    phase TEXT NOT NULL CHECK (phase IN ('pending', 'provisioning', 'ready', 'deleting', 'failed')),
    provider_resource_id TEXT UNIQUE,
    observed_at TEXT,
    conditions_json TEXT NOT NULL,
    retry_attempt INTEGER NOT NULL DEFAULT 0 CHECK (retry_attempt >= 0),
    next_reconcile_at_unix_ms INTEGER,
    last_error_kind TEXT,
    last_error_message TEXT
) STRICT;

CREATE INDEX host_claims_reconcile_order
    ON host_claims(deletion_timestamp, next_reconcile_at_unix_ms, created_at, id);

CREATE INDEX hosts_reconcile_order
    ON hosts(next_reconcile_at_unix_ms, created_at, id);
