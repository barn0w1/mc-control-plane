use std::{
    ffi::OsString,
    fs::{File, OpenOptions, TryLockError},
    path::{Path, PathBuf},
    str::FromStr,
    sync::Arc,
    time::Duration,
};

use anyhow::{Context, anyhow};
use control_plane_protocol::{
    Condition, ConditionStatus, Host, HostClaim, HostClaimId, HostClaimSpec, HostClaimStatus,
    HostId, HostPhase, HostResources, HostStatus,
};
use jiff::Timestamp;
use sqlx::{
    Row, Sqlite, SqlitePool, Transaction,
    sqlite::{
        SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions, SqliteRow, SqliteSynchronous,
    },
};

use crate::{
    domain::{
        HostClaimRecord, HostRecord, RetryState, initial_claim, initial_host, new_condition,
        now_timestamp, set_condition,
    },
    error::AppError,
};

#[derive(Clone, Debug)]
pub struct Storage {
    pool: SqlitePool,
    _ownership_lock: Arc<File>,
}

impl Storage {
    pub async fn connect(path: &Path) -> anyhow::Result<Self> {
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("create database directory {}", parent.display()))?;
        }
        let ownership_lock = acquire_database_ownership(path)?;

        let options = SqliteConnectOptions::new()
            .filename(path)
            .create_if_missing(true)
            .foreign_keys(true)
            .journal_mode(SqliteJournalMode::Wal)
            .synchronous(SqliteSynchronous::Full)
            .busy_timeout(Duration::from_secs(5));

        let pool = SqlitePoolOptions::new()
            .max_connections(1)
            .connect_with(options)
            .await
            .with_context(|| format!("open Control Plane database {}", path.display()))?;

        sqlx::migrate!("./migrations/control-plane")
            .run(&pool)
            .await
            .context("apply Control Plane database migrations")?;

        Ok(Self {
            pool,
            _ownership_lock: Arc::new(ownership_lock),
        })
    }

    pub async fn close(&self) {
        self.pool.close().await;
    }

    pub async fn create_claim(
        &self,
        id: HostClaimId,
        spec: HostClaimSpec,
    ) -> Result<HostClaim, AppError> {
        validate_resources(&spec.resources)?;

        let mut transaction = self.pool.begin().await.map_err(internal)?;
        if let Some(existing) = fetch_claim_row(&mut transaction, id)
            .await
            .map_err(internal)?
        {
            let existing = claim_record_from_row(&existing).map_err(internal)?;
            if existing.resource.spec == spec {
                transaction.commit().await.map_err(internal)?;
                return Ok(existing.resource);
            }

            return Err(AppError::Conflict {
                resource_type: "HostClaim",
                resource_id: id.to_string(),
                message: "the existing claim has a different spec".to_owned(),
            });
        }

        let claim = initial_claim(id, spec, now_timestamp());
        sqlx::query(
            r#"
            INSERT INTO host_claims (
                id, generation, created_at, deletion_timestamp,
                vcpus, memory_bytes, storage_bytes,
                observed_generation, conditions_json,
                retry_attempt, next_reconcile_at_unix_ms,
                last_error_kind, last_error_message
            ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL)
            "#,
        )
        .bind(claim.id.to_string())
        .bind(to_i64(claim.generation, "generation")?)
        .bind(claim.created_at.to_string())
        .bind(i64::from(claim.spec.resources.vcpus))
        .bind(to_i64(claim.spec.resources.memory_bytes, "memory_bytes")?)
        .bind(to_i64(claim.spec.resources.storage_bytes, "storage_bytes")?)
        .bind(to_i64(
            claim.status.observed_generation,
            "observed_generation",
        )?)
        .bind(serde_json::to_string(&claim.status.conditions).map_err(internal)?)
        .execute(&mut *transaction)
        .await
        .map_err(internal)?;

        transaction.commit().await.map_err(internal)?;
        Ok(claim)
    }

    pub async fn get_claim(&self, id: HostClaimId) -> Result<HostClaimRecord, AppError> {
        let row = sqlx::query(
            r#"
            SELECT c.*, h.id AS host_id
            FROM host_claims AS c
            LEFT JOIN hosts AS h ON h.claim_id = c.id
            WHERE c.id = ?
            "#,
        )
        .bind(id.to_string())
        .fetch_optional(&self.pool)
        .await
        .map_err(internal)?
        .ok_or_else(|| AppError::NotFound {
            resource_type: "HostClaim",
            resource_id: id.to_string(),
        })?;

        claim_record_from_row(&row).map_err(internal)
    }

    pub async fn list_claims(&self) -> Result<Vec<HostClaimRecord>, AppError> {
        let rows = sqlx::query(
            r#"
            SELECT c.*, h.id AS host_id
            FROM host_claims AS c
            LEFT JOIN hosts AS h ON h.claim_id = c.id
            ORDER BY c.created_at ASC, c.id ASC
            "#,
        )
        .fetch_all(&self.pool)
        .await
        .map_err(internal)?;

        rows.iter()
            .map(claim_record_from_row)
            .collect::<anyhow::Result<Vec<_>>>()
            .map_err(internal)
    }

    pub async fn mark_claim_deleting(&self, id: HostClaimId) -> Result<HostClaim, AppError> {
        let mut transaction = self.pool.begin().await.map_err(internal)?;
        let row = fetch_claim_row(&mut transaction, id)
            .await
            .map_err(internal)?
            .ok_or_else(|| AppError::NotFound {
                resource_type: "HostClaim",
                resource_id: id.to_string(),
            })?;
        let mut record = claim_record_from_row(&row).map_err(internal)?;

        if record.resource.deletion_timestamp.is_none() {
            let now = now_timestamp();
            record.resource.deletion_timestamp = Some(now);
            record.resource.status.observed_generation = record.resource.generation;
            set_condition(
                &mut record.resource.status.conditions,
                new_condition(
                    "Ready",
                    ConditionStatus::False,
                    "DeletionRequested",
                    "The HostClaim is being released.",
                    record.resource.generation,
                    now,
                ),
            );
            record.retry = RetryState::default();

            let update = sqlx::query(
                r#"
                UPDATE host_claims
                SET deletion_timestamp = ?,
                    observed_generation = ?,
                    conditions_json = ?,
                    retry_attempt = 0,
                    next_reconcile_at_unix_ms = NULL,
                    last_error_kind = NULL,
                    last_error_message = NULL
                WHERE id = ?
                "#,
            )
            .bind(
                record
                    .resource
                    .deletion_timestamp
                    .as_ref()
                    .map(ToString::to_string),
            )
            .bind(to_i64(
                record.resource.status.observed_generation,
                "observed_generation",
            )?)
            .bind(serde_json::to_string(&record.resource.status.conditions).map_err(internal)?)
            .bind(id.to_string())
            .execute(&mut *transaction)
            .await
            .map_err(internal)?;

            if update.rows_affected() != 1 {
                return Err(AppError::Internal(anyhow!(
                    "HostClaim disappeared while marking deletion"
                )));
            }
        }

        transaction.commit().await.map_err(internal)?;
        Ok(record.resource)
    }

    /// Save controller-owned HostClaim status only when the deletion intent has
    /// not changed since the record was observed.
    ///
    /// `false` means a concurrent mutation superseded this observation. The
    /// controller should discard the stale status and reconcile again.
    pub async fn save_claim_status(&self, record: &HostClaimRecord) -> Result<bool, AppError> {
        let deletion_timestamp = record
            .resource
            .deletion_timestamp
            .as_ref()
            .map(ToString::to_string);
        let result = sqlx::query(
            r#"
            UPDATE host_claims
            SET observed_generation = ?,
                conditions_json = ?,
                retry_attempt = ?,
                next_reconcile_at_unix_ms = ?,
                last_error_kind = ?,
                last_error_message = ?
            WHERE id = ?
              AND deletion_timestamp IS ?
            "#,
        )
        .bind(to_i64(
            record.resource.status.observed_generation,
            "observed_generation",
        )?)
        .bind(serde_json::to_string(&record.resource.status.conditions).map_err(internal)?)
        .bind(i64::from(record.retry.attempt))
        .bind(record.retry.next_reconcile_at_unix_ms)
        .bind(&record.retry.last_error_kind)
        .bind(&record.retry.last_error_message)
        .bind(record.resource.id.to_string())
        .bind(deletion_timestamp)
        .execute(&self.pool)
        .await
        .map_err(internal)?;

        Ok(result.rows_affected() == 1)
    }

    pub async fn ensure_host(
        &self,
        claim_id: HostClaimId,
        provider_plan_id: &str,
        resources: HostResources,
    ) -> Result<HostRecord, AppError> {
        validate_resources(&resources)?;

        let mut transaction = self.pool.begin().await.map_err(internal)?;

        let row = fetch_claim_row(&mut transaction, claim_id)
            .await
            .map_err(internal)?
            .ok_or_else(|| AppError::NotFound {
                resource_type: "HostClaim",
                resource_id: claim_id.to_string(),
            })?;
        let mut claim = claim_record_from_row(&row).map_err(internal)?;
        if claim.resource.deletion_timestamp.is_some() {
            return Err(AppError::Conflict {
                resource_type: "HostClaim",
                resource_id: claim_id.to_string(),
                message: "the claim is being deleted".to_owned(),
            });
        }

        if let Some(row) = fetch_host_for_claim_row(&mut transaction, claim_id)
            .await
            .map_err(internal)?
        {
            let host = host_record_from_row(&row).map_err(internal)?;
            validate_host_assignment(&host, provider_plan_id, &resources)?;
            transaction.commit().await.map_err(internal)?;
            return Ok(host);
        }

        let now = now_timestamp();
        let host = initial_host(HostId::new(), claim_id, resources.clone(), now);
        let insert = sqlx::query(
            r#"
            INSERT INTO hosts (
                id, claim_id, created_at,
                vcpus, memory_bytes, storage_bytes, provider_plan_id,
                phase, provider_resource_id, observed_at, conditions_json,
                retry_attempt, next_reconcile_at_unix_ms,
                last_error_kind, last_error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, 0, NULL, NULL, NULL)
            ON CONFLICT(claim_id) DO NOTHING
            "#,
        )
        .bind(host.id.to_string())
        .bind(host.claim_id.to_string())
        .bind(host.created_at.to_string())
        .bind(i64::from(host.allocatable_resources.vcpus))
        .bind(to_i64(
            host.allocatable_resources.memory_bytes,
            "memory_bytes",
        )?)
        .bind(to_i64(
            host.allocatable_resources.storage_bytes,
            "storage_bytes",
        )?)
        .bind(provider_plan_id)
        .bind(serde_json::to_string(&host.status.conditions).map_err(internal)?)
        .execute(&mut *transaction)
        .await
        .map_err(internal)?;

        if insert.rows_affected() == 1 {
            claim.resource.status.host_id = Some(host.id);
            claim.resource.status.observed_generation = claim.resource.generation;
            set_condition(
                &mut claim.resource.status.conditions,
                new_condition(
                    "Accepted",
                    ConditionStatus::True,
                    "RequirementsValid",
                    "The current provider policy can satisfy this claim.",
                    claim.resource.generation,
                    now,
                ),
            );
            set_condition(
                &mut claim.resource.status.conditions,
                new_condition(
                    "Bound",
                    ConditionStatus::True,
                    "HostAssigned",
                    format!("Host {} is assigned.", host.id),
                    claim.resource.generation,
                    now,
                ),
            );
            set_condition(
                &mut claim.resource.status.conditions,
                new_condition(
                    "Ready",
                    ConditionStatus::False,
                    "HostNotReady",
                    "The assigned Host is not Ready yet.",
                    claim.resource.generation,
                    now,
                ),
            );

            let claim_update = sqlx::query(
                r#"
                UPDATE host_claims
                SET observed_generation = ?, conditions_json = ?,
                    retry_attempt = 0, next_reconcile_at_unix_ms = NULL,
                    last_error_kind = NULL, last_error_message = NULL
                WHERE id = ?
                  AND deletion_timestamp IS NULL
                "#,
            )
            .bind(to_i64(
                claim.resource.status.observed_generation,
                "observed_generation",
            )?)
            .bind(serde_json::to_string(&claim.resource.status.conditions).map_err(internal)?)
            .bind(claim_id.to_string())
            .execute(&mut *transaction)
            .await
            .map_err(internal)?;

            if claim_update.rows_affected() != 1 {
                return Err(AppError::Conflict {
                    resource_type: "HostClaim",
                    resource_id: claim_id.to_string(),
                    message: "the claim changed while assigning a Host".to_owned(),
                });
            }
        }

        let row = fetch_host_for_claim_row(&mut transaction, claim_id)
            .await
            .map_err(internal)?
            .ok_or_else(|| {
                AppError::Internal(anyhow!("Host insert completed without a Host row"))
            })?;
        let host = host_record_from_row(&row).map_err(internal)?;
        validate_host_assignment(&host, provider_plan_id, &resources)?;
        transaction.commit().await.map_err(internal)?;
        Ok(host)
    }

    pub async fn get_host(&self, id: HostId) -> Result<HostRecord, AppError> {
        let row = sqlx::query("SELECT * FROM hosts WHERE id = ?")
            .bind(id.to_string())
            .fetch_optional(&self.pool)
            .await
            .map_err(internal)?
            .ok_or_else(|| AppError::NotFound {
                resource_type: "Host",
                resource_id: id.to_string(),
            })?;

        host_record_from_row(&row).map_err(internal)
    }

    #[cfg(test)]
    pub async fn get_host_for_claim(
        &self,
        claim_id: HostClaimId,
    ) -> Result<Option<HostRecord>, AppError> {
        let row = sqlx::query("SELECT * FROM hosts WHERE claim_id = ?")
            .bind(claim_id.to_string())
            .fetch_optional(&self.pool)
            .await
            .map_err(internal)?;

        row.as_ref()
            .map(host_record_from_row)
            .transpose()
            .map_err(internal)
    }

    pub async fn list_hosts(&self) -> Result<Vec<HostRecord>, AppError> {
        let rows = sqlx::query("SELECT * FROM hosts ORDER BY created_at ASC, id ASC")
            .fetch_all(&self.pool)
            .await
            .map_err(internal)?;

        rows.iter()
            .map(host_record_from_row)
            .collect::<anyhow::Result<Vec<_>>>()
            .map_err(internal)
    }

    pub async fn save_host(&self, record: &HostRecord) -> Result<(), AppError> {
        let result = sqlx::query(
            r#"
            UPDATE hosts
            SET phase = ?,
                provider_resource_id = ?,
                observed_at = ?,
                conditions_json = ?,
                retry_attempt = ?,
                next_reconcile_at_unix_ms = ?,
                last_error_kind = ?,
                last_error_message = ?
            WHERE id = ?
            "#,
        )
        .bind(host_phase_to_db(record.resource.status.phase))
        .bind(&record.resource.status.provider_resource_id)
        .bind(
            record
                .resource
                .status
                .observed_at
                .as_ref()
                .map(ToString::to_string),
        )
        .bind(serde_json::to_string(&record.resource.status.conditions).map_err(internal)?)
        .bind(i64::from(record.retry.attempt))
        .bind(record.retry.next_reconcile_at_unix_ms)
        .bind(&record.retry.last_error_kind)
        .bind(&record.retry.last_error_message)
        .bind(record.resource.id.to_string())
        .execute(&self.pool)
        .await
        .map_err(internal)?;

        if result.rows_affected() == 0 {
            return Err(AppError::NotFound {
                resource_type: "Host",
                resource_id: record.resource.id.to_string(),
            });
        }
        Ok(())
    }

    pub async fn finalize_claim(&self, claim_id: HostClaimId) -> Result<(), AppError> {
        let result = sqlx::query("DELETE FROM host_claims WHERE id = ?")
            .bind(claim_id.to_string())
            .execute(&self.pool)
            .await
            .map_err(internal)?;

        if result.rows_affected() == 0 {
            return Err(AppError::NotFound {
                resource_type: "HostClaim",
                resource_id: claim_id.to_string(),
            });
        }
        Ok(())
    }
}

async fn fetch_claim_row(
    transaction: &mut Transaction<'_, Sqlite>,
    id: HostClaimId,
) -> Result<Option<SqliteRow>, sqlx::Error> {
    sqlx::query(
        r#"
        SELECT c.*, h.id AS host_id
        FROM host_claims AS c
        LEFT JOIN hosts AS h ON h.claim_id = c.id
        WHERE c.id = ?
        "#,
    )
    .bind(id.to_string())
    .fetch_optional(&mut **transaction)
    .await
}

async fn fetch_host_for_claim_row(
    transaction: &mut Transaction<'_, Sqlite>,
    claim_id: HostClaimId,
) -> Result<Option<SqliteRow>, sqlx::Error> {
    sqlx::query("SELECT * FROM hosts WHERE claim_id = ?")
        .bind(claim_id.to_string())
        .fetch_optional(&mut **transaction)
        .await
}

fn claim_record_from_row(row: &SqliteRow) -> anyhow::Result<HostClaimRecord> {
    let id = HostClaimId::from_str(&row.try_get::<String, _>("id")?)?;
    let host_id = row
        .try_get::<Option<String>, _>("host_id")?
        .as_deref()
        .map(HostId::from_str)
        .transpose()?;
    let conditions = serde_json::from_str::<Vec<Condition>>(row.try_get("conditions_json")?)?;

    Ok(HostClaimRecord {
        resource: HostClaim {
            id,
            generation: to_u64(row.try_get("generation")?, "generation")?,
            created_at: parse_timestamp(row.try_get("created_at")?)?,
            deletion_timestamp: row
                .try_get::<Option<String>, _>("deletion_timestamp")?
                .as_deref()
                .map(parse_timestamp)
                .transpose()?,
            spec: HostClaimSpec {
                resources: HostResources {
                    vcpus: to_u32(row.try_get("vcpus")?, "vcpus")?,
                    memory_bytes: to_u64(row.try_get("memory_bytes")?, "memory_bytes")?,
                    storage_bytes: to_u64(row.try_get("storage_bytes")?, "storage_bytes")?,
                },
            },
            status: HostClaimStatus {
                observed_generation: to_u64(
                    row.try_get("observed_generation")?,
                    "observed_generation",
                )?,
                host_id,
                conditions,
            },
        },
        retry: RetryState {
            attempt: to_u32(row.try_get("retry_attempt")?, "retry_attempt")?,
            next_reconcile_at_unix_ms: row.try_get("next_reconcile_at_unix_ms")?,
            last_error_kind: row.try_get("last_error_kind")?,
            last_error_message: row.try_get("last_error_message")?,
        },
    })
}

fn host_record_from_row(row: &SqliteRow) -> anyhow::Result<HostRecord> {
    Ok(HostRecord {
        resource: Host {
            id: HostId::from_str(&row.try_get::<String, _>("id")?)?,
            claim_id: HostClaimId::from_str(&row.try_get::<String, _>("claim_id")?)?,
            created_at: parse_timestamp(row.try_get("created_at")?)?,
            allocatable_resources: HostResources {
                vcpus: to_u32(row.try_get("vcpus")?, "vcpus")?,
                memory_bytes: to_u64(row.try_get("memory_bytes")?, "memory_bytes")?,
                storage_bytes: to_u64(row.try_get("storage_bytes")?, "storage_bytes")?,
            },
            status: HostStatus {
                phase: host_phase_from_db(row.try_get("phase")?)?,
                provider_resource_id: row.try_get("provider_resource_id")?,
                observed_at: row
                    .try_get::<Option<String>, _>("observed_at")?
                    .as_deref()
                    .map(parse_timestamp)
                    .transpose()?,
                conditions: serde_json::from_str(row.try_get("conditions_json")?)?,
            },
        },
        provider_plan_id: row.try_get("provider_plan_id")?,
        retry: RetryState {
            attempt: to_u32(row.try_get("retry_attempt")?, "retry_attempt")?,
            next_reconcile_at_unix_ms: row.try_get("next_reconcile_at_unix_ms")?,
            last_error_kind: row.try_get("last_error_kind")?,
            last_error_message: row.try_get("last_error_message")?,
        },
    })
}

fn acquire_database_ownership(database_path: &Path) -> anyhow::Result<File> {
    let lock_path = database_lock_path(database_path);
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(&lock_path)
        .with_context(|| format!("open database ownership lock {}", lock_path.display()))?;

    match lock.try_lock() {
        Ok(()) => Ok(lock),
        Err(TryLockError::WouldBlock) => Err(anyhow!(
            "Control Plane database {} is already owned by another process",
            database_path.display()
        )),
        Err(TryLockError::Error(error)) => Err(error)
            .with_context(|| format!("lock Control Plane database {}", database_path.display())),
    }
}

fn database_lock_path(database_path: &Path) -> PathBuf {
    let mut value = OsString::from(database_path.as_os_str());
    value.push(".lock");
    PathBuf::from(value)
}

fn validate_resources(resources: &HostResources) -> Result<(), AppError> {
    if resources.vcpus == 0 {
        return Err(AppError::InvalidArgument {
            message: "vcpus must be greater than zero".to_owned(),
        });
    }
    if resources.memory_bytes == 0 {
        return Err(AppError::InvalidArgument {
            message: "memory_bytes must be greater than zero".to_owned(),
        });
    }
    if resources.storage_bytes == 0 {
        return Err(AppError::InvalidArgument {
            message: "storage_bytes must be greater than zero".to_owned(),
        });
    }
    to_i64(resources.memory_bytes, "memory_bytes")?;
    to_i64(resources.storage_bytes, "storage_bytes")?;
    Ok(())
}

fn validate_host_assignment(
    host: &HostRecord,
    provider_plan_id: &str,
    resources: &HostResources,
) -> Result<(), AppError> {
    if host.provider_plan_id != provider_plan_id
        || host.resource.allocatable_resources != *resources
    {
        return Err(AppError::Conflict {
            resource_type: "HostClaim",
            resource_id: host.resource.claim_id.to_string(),
            message: format!(
                "the existing Host assignment differs from the resolved provider plan or resources (Host {})",
                host.resource.id
            ),
        });
    }

    Ok(())
}

fn to_i64(value: u64, field: &str) -> Result<i64, AppError> {
    i64::try_from(value).map_err(|_| AppError::InvalidArgument {
        message: format!("{field} must not exceed {}", i64::MAX),
    })
}

fn to_u64(value: i64, field: &str) -> anyhow::Result<u64> {
    u64::try_from(value).with_context(|| format!("invalid negative {field}: {value}"))
}

fn to_u32(value: i64, field: &str) -> anyhow::Result<u32> {
    u32::try_from(value).with_context(|| format!("invalid {field}: {value}"))
}

fn parse_timestamp(value: &str) -> anyhow::Result<Timestamp> {
    Timestamp::from_str(value).with_context(|| format!("parse timestamp {value:?}"))
}

fn host_phase_to_db(phase: HostPhase) -> &'static str {
    match phase {
        HostPhase::Pending => "pending",
        HostPhase::Provisioning => "provisioning",
        HostPhase::Ready => "ready",
        HostPhase::Deleting => "deleting",
        HostPhase::Failed => "failed",
    }
}

fn host_phase_from_db(value: &str) -> anyhow::Result<HostPhase> {
    match value {
        "pending" => Ok(HostPhase::Pending),
        "provisioning" => Ok(HostPhase::Provisioning),
        "ready" => Ok(HostPhase::Ready),
        "deleting" => Ok(HostPhase::Deleting),
        "failed" => Ok(HostPhase::Failed),
        _ => Err(anyhow!("unknown Host phase {value:?}")),
    }
}

fn internal(error: impl Into<anyhow::Error>) -> AppError {
    AppError::Internal(error.into())
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use control_plane_protocol::{HostClaimSpec, HostResources};
    use uuid::Uuid;

    use super::*;

    #[tokio::test]
    async fn create_claim_is_idempotent_for_the_same_id_and_spec() -> anyhow::Result<()> {
        let path = TestDatabase::new("claim-idempotency");
        let storage = Storage::connect(&path.database).await?;
        let id = HostClaimId::new();
        let spec = small_spec();

        let first = storage.create_claim(id, spec.clone()).await?;
        let second = storage.create_claim(id, spec).await?;

        assert_eq!(first, second);
        assert_eq!(storage.list_claims().await?.len(), 1);

        storage.close().await;
        path.remove();
        Ok(())
    }

    #[tokio::test]
    async fn create_claim_conflicts_when_the_existing_spec_differs() -> anyhow::Result<()> {
        let path = TestDatabase::new("claim-conflict");
        let storage = Storage::connect(&path.database).await?;
        let id = HostClaimId::new();
        storage.create_claim(id, small_spec()).await?;

        let error = storage
            .create_claim(
                id,
                HostClaimSpec {
                    resources: HostResources {
                        vcpus: 2,
                        memory_bytes: 2 * 1024 * 1024 * 1024,
                        storage_bytes: 20 * 1024 * 1024 * 1024,
                    },
                },
            )
            .await
            .expect_err("different spec must conflict");

        assert!(matches!(error, AppError::Conflict { .. }));
        assert_eq!(storage.list_claims().await?.len(), 1);

        storage.close().await;
        path.remove();
        Ok(())
    }

    #[tokio::test]
    async fn invalid_resources_are_rejected_before_persistence() -> anyhow::Result<()> {
        let path = TestDatabase::new("invalid-resources");
        let storage = Storage::connect(&path.database).await?;

        let error = storage
            .create_claim(
                HostClaimId::new(),
                HostClaimSpec {
                    resources: HostResources {
                        vcpus: 0,
                        memory_bytes: 1,
                        storage_bytes: 1,
                    },
                },
            )
            .await
            .expect_err("zero vcpus must be rejected");

        assert!(matches!(error, AppError::InvalidArgument { .. }));
        assert!(storage.list_claims().await?.is_empty());

        storage.close().await;
        path.remove();
        Ok(())
    }

    #[tokio::test]
    async fn a_second_storage_cannot_own_the_same_database() -> anyhow::Result<()> {
        let path = TestDatabase::new("exclusive-owner");
        let first = Storage::connect(&path.database).await?;

        let error = Storage::connect(&path.database)
            .await
            .expect_err("the database must have a single application owner");
        assert!(error.to_string().contains("already owned"));

        first.close().await;
        drop(first);
        let reopened = Storage::connect(&path.database).await?;
        reopened.close().await;
        drop(reopened);
        path.remove();
        Ok(())
    }

    #[tokio::test]
    async fn stale_status_cannot_clear_a_deletion_request() -> anyhow::Result<()> {
        let path = TestDatabase::new("stale-status");
        let storage = Storage::connect(&path.database).await?;
        let id = HostClaimId::new();
        storage.create_claim(id, small_spec()).await?;

        let mut stale = storage.get_claim(id).await?;
        storage.mark_claim_deleting(id).await?;
        stale.resource.status.observed_generation = stale.resource.generation;
        stale.retry.last_error_kind = Some("StaleObservation".to_owned());

        assert!(!storage.save_claim_status(&stale).await?);
        let current = storage.get_claim(id).await?;
        assert!(current.resource.deletion_timestamp.is_some());
        assert_ne!(
            current.retry.last_error_kind.as_deref(),
            Some("StaleObservation")
        );

        storage.close().await;
        path.remove();
        Ok(())
    }

    #[tokio::test]
    async fn existing_host_assignment_must_match_the_resolved_plan_and_resources()
    -> anyhow::Result<()> {
        let path = TestDatabase::new("host-assignment-conflict");
        let storage = Storage::connect(&path.database).await?;
        let claim_id = HostClaimId::new();
        let spec = small_spec();
        storage.create_claim(claim_id, spec.clone()).await?;

        let first = storage
            .ensure_host(claim_id, "plan-a", spec.resources.clone())
            .await?;
        let error = storage
            .ensure_host(claim_id, "plan-b", spec.resources.clone())
            .await
            .expect_err("a different provider plan must conflict");
        let resources_error = storage
            .ensure_host(
                claim_id,
                "plan-a",
                HostResources {
                    vcpus: 2,
                    ..spec.resources.clone()
                },
            )
            .await
            .expect_err("different allocatable resources must conflict");

        assert!(matches!(error, AppError::Conflict { .. }));
        assert!(matches!(resources_error, AppError::Conflict { .. }));
        let current = storage.get_host_for_claim(claim_id).await?.unwrap();
        assert_eq!(current.resource.id, first.resource.id);
        assert_eq!(current.provider_plan_id, "plan-a");
        assert_eq!(storage.list_hosts().await?.len(), 1);

        storage.close().await;
        path.remove();
        Ok(())
    }

    fn small_spec() -> HostClaimSpec {
        HostClaimSpec {
            resources: HostResources {
                vcpus: 1,
                memory_bytes: 1024 * 1024 * 1024,
                storage_bytes: 10 * 1024 * 1024 * 1024,
            },
        }
    }

    struct TestDatabase {
        database: PathBuf,
    }

    impl TestDatabase {
        fn new(name: &str) -> Self {
            let root = std::env::temp_dir()
                .join(format!("control-plane-storage-{name}-{}", Uuid::now_v7()));
            std::fs::create_dir_all(&root).expect("create test directory");
            Self {
                database: root.join("control-plane.db"),
            }
        }

        fn remove(&self) {
            if let Some(root) = self.database.parent() {
                let _ = std::fs::remove_dir_all(root);
            }
        }
    }
}
