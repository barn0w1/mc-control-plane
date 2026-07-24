CREATE TABLE plans (
    id TEXT PRIMARY KEY NOT NULL,
    vcpus INTEGER NOT NULL CHECK (vcpus > 0),
    memory_bytes INTEGER NOT NULL CHECK (memory_bytes > 0),
    storage_bytes INTEGER NOT NULL CHECK (storage_bytes > 0),
    hourly_price_micros INTEGER NOT NULL CHECK (hourly_price_micros >= 0),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1))
) STRICT;

CREATE TABLE provider_resources (
    id TEXT PRIMARY KEY NOT NULL,
    host_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('provisioning', 'ready', 'deleting')),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE faults (
    operation TEXT PRIMARY KEY NOT NULL,
    remaining INTEGER NOT NULL CHECK (remaining >= 0)
) STRICT;
