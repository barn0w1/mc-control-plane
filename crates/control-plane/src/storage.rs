use std::{path::Path, str::FromStr, time::Duration};

use anyhow::{Context, anyhow};
use control_plane_protocol::{
    Condition, ConditionStatus, Host, HostClaim, HostClaimId, HostClaimSpec, HostClaimStatus,
    HostId, HostPhase, HostResources, HostStatus,
};
use jiff::Timestamp;
use sqlx::{
    Row, Sqlite, SqlitePool, Transaction,
    sqlite::{
        SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions, SqliteRow,
        SqliteSynchronous,
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
}

impl Storage {
    pub async fn connect(path: &Path) -> anyhow::Result<Self> {
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

        Ok(Self { pool })
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
        if let Some(existing) = fetch_claim_row(&mut transaction, id).await.map_err(internal)? {
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

    pub async fn mark_claim_deleting(
        &self,
        id: HostClaimId,
    ) -> Result<HostClaim, AppError> {
        let mut record = self.get_claim(id).await?;
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
            self.save_claim(&record).await?;
        }
        Ok(record.resource)
    }

    pub async fn save_claim(&self, record: &HostClaimRecord) -> Result<(), AppError> {
        let result = sqlx::query(
            r#"
            UPDATE host_claims
            SET deletion_timestamp = ?,
                observed_generation = ?,
                conditions_json = ?,
                retry_attempt = ?,
                next_reconcile_at_unix_ms = ?,
                last_error_kind = ?,
                last_error_message = ?
            WHERE id = ?
            "#,
        )
        .bind(record.resource.deletion_timestamp.as_ref().map(ToString::to_string))
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
        .execute(&self.pool)
        .await
        .map_err(internal)?;

        if result.rows_affected() == 0 {
            return Err(AppError::NotFound {
                resource_type: "HostClaim",
                resource_id: record.resource.id.to_string(),
            });
        }
        Ok(())
    }

    pub async fn ensure_host(
        &self,
        claim_id: HostClaimId,
        resources: HostResources,
    ) -> Result<HostRecord, AppError> {
        validate_resources(&resources)?;

        if let Some(host) = self.get_host_for_claim(claim_id).await? {
            return Ok(host);
        }

        let mut claim = self.get_claim(claim_id).await?;
        if claim.resource.deletion_timestamp.is_some() {
            return Err(AppError::Conflict {
                resource_type: "HostClaim",
                resource_id: claim_id.to_string(),
                message: "the claim is being deleted".to_owned(),
            });
        }

        let now = now_timestamp();
        let host = initial_host(HostId::new(), claim_id, resources, now);
        let mut transaction = self.pool.begin().await.map_err(internal)?;

        let insert = sqlx::query(
            r#"
            INSERT INTO hosts (
                id, claim_id, created_at,
                vcpus, memory_bytes, storage_bytes,
                phase, provider_resource_id, observed_at, conditions_json,
                retry_attempt, next_reconcile_at_unix_ms,
                last_error_kind, last_error_message
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, 0, NULL, NULL, NULL)
            ON CONFLICT(claim_id) DO NOTHING
            "#,
        )
        .bind(host.id.to_string())
        .bind(host.claim_id.to_string())
        .bind(host.created_at.to_string())
        .bind(i64::from(host.allocatable_resources.vcpus))
        .bind(to_i64(host.allocatable_resources.memory_bytes, "memory_bytes")?)
        .bind(to_i64(host.allocatable_resources.storage_bytes, "storage_bytes")?)
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

            sqlx::query(
                r#"
                UPDATE host_claims
                SET observed_generation = ?, conditions_json = ?,
                    retry_attempt = 0, next_reconcile_at_unix_ms = NULL,
                    last_error_kind = NULL, last_error_message = NULL
                WHERE id = ?
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
        }

        transaction.commit().await.map_err(internal)?;
        self.get_host_for_claim(claim_id)
            .await?
            .ok_or_else(|| AppError::Internal(anyhow!("Host insert completed without a Host row")))
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
        .bind(record.resource.status.observed_at.as_ref().map(ToString::to_string))
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
        retry: RetryState {
            attempt: to_u32(row.try_get("retry_attempt")?, "retry_attempt")?,
            next_reconcile_at_unix_ms: row.try_get("next_reconcile_at_unix_ms")?,
            last_error_kind: row.try_get("last_error_kind")?,
            last_error_message: row.try_get("last_error_message")?,
        },
    })
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
    let _ = to_i64(resources.memory_bytes, "memory_bytes")?;
    let _ = to_i64(resources.storage_bytes, "storage_bytes")?;
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
            let root = std::env::temp_dir().join(format!(
                "control-plane-storage-{name}-{}",
                Uuid::now_v7()
            ));
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
