use std::{path::Path, str::FromStr, time::Duration};

use anyhow::Context;
use control_plane_protocol::{HostId, HostResources};
use jiff::Timestamp;
use sqlx::{
    Row, SqlitePool,
    sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions, SqliteSynchronous},
};
use uuid::Uuid;

use super::{
    CreateOutcome, DeleteOutcome, HostProvider, Plan, ProviderError, ProviderLifecycle,
    ProviderObservation, ProviderResource,
};

const FAULT_CREATE_DEFINITIVE: &str = "create_definitive_failure";
const FAULT_CREATE_TRANSIENT: &str = "create_transient_failure";
const FAULT_CREATE_RESPONSE_LOSS: &str = "create_response_loss";
const FAULT_DELETE_TRANSIENT: &str = "delete_transient_failure";
const FAULT_DELETE_RESPONSE_LOSS: &str = "delete_response_loss";
const FAULT_OBSERVE_TRANSIENT: &str = "observe_transient_failure";

#[derive(Clone, Debug)]
pub struct FakeProvider {
    pool: SqlitePool,
}

impl FakeProvider {
    pub async fn connect(path: &Path) -> anyhow::Result<Self> {
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("create fake provider directory {}", parent.display()))?;
        }

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
            .with_context(|| format!("open fake provider database {}", path.display()))?;

        sqlx::migrate!("./migrations/fake-provider")
            .run(&pool)
            .await
            .context("apply fake provider database migrations")?;

        seed_catalog(&pool)
            .await
            .context("seed fake provider plan catalog")?;
        Ok(Self { pool })
    }

    pub async fn close(&self) {
        self.pool.close().await;
    }

    #[cfg(test)]
    pub(crate) async fn inject_fault(&self, operation: &str, count: u32) -> anyhow::Result<()> {
        sqlx::query(
            r#"
            INSERT INTO faults(operation, remaining) VALUES(?, ?)
            ON CONFLICT(operation) DO UPDATE SET remaining = excluded.remaining
            "#,
        )
        .bind(operation)
        .bind(i64::from(count))
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    #[cfg(test)]
    pub(crate) async fn resource_count(&self) -> anyhow::Result<i64> {
        Ok(
            sqlx::query_scalar("SELECT COUNT(*) FROM provider_resources")
                .fetch_one(&self.pool)
                .await?,
        )
    }

    async fn consume_fault(&self, operation: &str) -> Result<bool, ProviderError> {
        let mut transaction = self
            .pool
            .begin()
            .await
            .map_err(|error| transient(error.to_string()))?;
        let remaining =
            sqlx::query_scalar::<_, i64>("SELECT remaining FROM faults WHERE operation = ?")
                .bind(operation)
                .fetch_optional(&mut *transaction)
                .await
                .map_err(|error| transient(error.to_string()))?
                .unwrap_or(0);

        if remaining == 0 {
            transaction
                .commit()
                .await
                .map_err(|error| transient(error.to_string()))?;
            return Ok(false);
        }

        sqlx::query("UPDATE faults SET remaining = remaining - 1 WHERE operation = ?")
            .bind(operation)
            .execute(&mut *transaction)
            .await
            .map_err(|error| transient(error.to_string()))?;
        transaction
            .commit()
            .await
            .map_err(|error| transient(error.to_string()))?;
        Ok(true)
    }

    async fn resource_by_host(
        &self,
        host_id: HostId,
    ) -> Result<Option<ProviderResource>, ProviderError> {
        let row =
            sqlx::query("SELECT id, host_id, lifecycle FROM provider_resources WHERE host_id = ?")
                .bind(host_id.to_string())
                .fetch_optional(&self.pool)
                .await
                .map_err(|error| transient(error.to_string()))?;

        row.map(|row| {
            let id: String = row
                .try_get("id")
                .map_err(|error| transient(error.to_string()))?;
            let host_id: String = row
                .try_get("host_id")
                .map_err(|error| transient(error.to_string()))?;
            let lifecycle: String = row
                .try_get("lifecycle")
                .map_err(|error| transient(error.to_string()))?;
            Ok(ProviderResource {
                id,
                host_id: HostId::from_str(&host_id)
                    .map_err(|error| transient(error.to_string()))?,
                lifecycle: lifecycle_from_db(&lifecycle)?,
            })
        })
        .transpose()
    }
}

impl HostProvider for FakeProvider {
    async fn select_plan(
        &self,
        requirements: &HostResources,
    ) -> Result<Option<Plan>, ProviderError> {
        let memory = i64::try_from(requirements.memory_bytes)
            .map_err(|_| definitive("memory requirement exceeds SQLite range"))?;
        let storage = i64::try_from(requirements.storage_bytes)
            .map_err(|_| definitive("storage requirement exceeds SQLite range"))?;

        let row = sqlx::query(
            r#"
            SELECT id, vcpus, memory_bytes, storage_bytes
            FROM plans
            WHERE enabled = 1
              AND vcpus >= ?
              AND memory_bytes >= ?
              AND storage_bytes >= ?
            ORDER BY hourly_price_micros ASC,
                     memory_bytes ASC,
                     storage_bytes ASC,
                     vcpus ASC,
                     id ASC
            LIMIT 1
            "#,
        )
        .bind(i64::from(requirements.vcpus))
        .bind(memory)
        .bind(storage)
        .fetch_optional(&self.pool)
        .await
        .map_err(|error| transient(error.to_string()))?;

        row.map(|row| {
            Ok(Plan {
                id: row
                    .try_get("id")
                    .map_err(|error| transient(error.to_string()))?,
                resources: HostResources {
                    vcpus: u32::try_from(
                        row.try_get::<i64, _>("vcpus")
                            .map_err(|error| transient(error.to_string()))?,
                    )
                    .map_err(|error| transient(error.to_string()))?,
                    memory_bytes: u64::try_from(
                        row.try_get::<i64, _>("memory_bytes")
                            .map_err(|error| transient(error.to_string()))?,
                    )
                    .map_err(|error| transient(error.to_string()))?,
                    storage_bytes: u64::try_from(
                        row.try_get::<i64, _>("storage_bytes")
                            .map_err(|error| transient(error.to_string()))?,
                    )
                    .map_err(|error| transient(error.to_string()))?,
                },
            })
        })
        .transpose()
    }

    async fn observe(&self, host_id: HostId) -> Result<ProviderObservation, ProviderError> {
        if self.consume_fault(FAULT_OBSERVE_TRANSIENT).await? {
            return Err(transient("injected observation failure"));
        }
        Ok(self
            .resource_by_host(host_id)
            .await?
            .map_or(ProviderObservation::Absent, ProviderObservation::Present))
    }

    async fn create(&self, host_id: HostId, plan_id: &str) -> Result<CreateOutcome, ProviderError> {
        if self.consume_fault(FAULT_CREATE_DEFINITIVE).await? {
            return Err(definitive("injected definitive create failure"));
        }
        if self.consume_fault(FAULT_CREATE_TRANSIENT).await? {
            return Err(transient("injected transient create failure"));
        }
        if let Some(existing) = self.resource_by_host(host_id).await? {
            let existing_plan = sqlx::query_scalar::<_, String>(
                "SELECT plan_id FROM provider_resources WHERE host_id = ?",
            )
            .bind(host_id.to_string())
            .fetch_one(&self.pool)
            .await
            .map_err(|error| transient(error.to_string()))?;
            if existing_plan != plan_id {
                return Err(definitive(format!(
                    "Host {host_id} already owns a provider resource with plan {existing_plan:?}, not {plan_id:?}"
                )));
            }
            return Ok(CreateOutcome::Created(existing));
        }

        let plan_exists =
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM plans WHERE id = ? AND enabled = 1")
                .bind(plan_id)
                .fetch_one(&self.pool)
                .await
                .map_err(|error| transient(error.to_string()))?;
        if plan_exists != 1 {
            return Err(definitive(format!(
                "unknown or disabled provider plan {plan_id:?}"
            )));
        }

        let lose_response = self.consume_fault(FAULT_CREATE_RESPONSE_LOSS).await?;
        let resource = ProviderResource {
            id: format!("fake-{}", Uuid::now_v7()),
            host_id,
            lifecycle: ProviderLifecycle::Ready,
        };

        sqlx::query(
            r#"
            INSERT INTO provider_resources(id, host_id, plan_id, lifecycle, created_at)
            VALUES(?, ?, ?, 'ready', ?)
            "#,
        )
        .bind(&resource.id)
        .bind(resource.host_id.to_string())
        .bind(plan_id)
        .bind(Timestamp::now().to_string())
        .execute(&self.pool)
        .await
        .map_err(|error| transient(error.to_string()))?;

        if lose_response {
            Ok(CreateOutcome::OutcomeUnknown)
        } else {
            Ok(CreateOutcome::Created(resource))
        }
    }

    async fn delete(&self, host_id: HostId) -> Result<DeleteOutcome, ProviderError> {
        if self.consume_fault(FAULT_DELETE_TRANSIENT).await? {
            return Err(transient("injected transient delete failure"));
        }
        let lose_response = self.consume_fault(FAULT_DELETE_RESPONSE_LOSS).await?;

        sqlx::query("DELETE FROM provider_resources WHERE host_id = ?")
            .bind(host_id.to_string())
            .execute(&self.pool)
            .await
            .map_err(|error| transient(error.to_string()))?;

        if lose_response {
            Ok(DeleteOutcome::OutcomeUnknown)
        } else {
            Ok(DeleteOutcome::Deleted)
        }
    }
}

async fn seed_catalog(pool: &SqlitePool) -> anyhow::Result<()> {
    let plans = [
        (
            "fake-2c-4g-40g",
            2_i64,
            4_i64 << 30,
            40_i64 << 30,
            10_000_i64,
        ),
        (
            "fake-4c-8g-80g",
            4_i64,
            8_i64 << 30,
            80_i64 << 30,
            20_000_i64,
        ),
        (
            "fake-8c-16g-160g",
            8_i64,
            16_i64 << 30,
            160_i64 << 30,
            40_000_i64,
        ),
    ];

    for (id, vcpus, memory, storage, price) in plans {
        sqlx::query(
            r#"
            INSERT INTO plans(id, vcpus, memory_bytes, storage_bytes, hourly_price_micros, enabled)
            VALUES(?, ?, ?, ?, ?, 1)
            ON CONFLICT(id) DO UPDATE SET
                vcpus = excluded.vcpus,
                memory_bytes = excluded.memory_bytes,
                storage_bytes = excluded.storage_bytes,
                hourly_price_micros = excluded.hourly_price_micros,
                enabled = excluded.enabled
            "#,
        )
        .bind(id)
        .bind(vcpus)
        .bind(memory)
        .bind(storage)
        .bind(price)
        .execute(pool)
        .await?;
    }
    Ok(())
}

fn lifecycle_from_db(value: &str) -> Result<ProviderLifecycle, ProviderError> {
    match value {
        "provisioning" => Ok(ProviderLifecycle::Provisioning),
        "ready" => Ok(ProviderLifecycle::Ready),
        "deleting" => Ok(ProviderLifecycle::Deleting),
        _ => Err(transient(format!(
            "unknown fake provider lifecycle {value:?}"
        ))),
    }
}

fn definitive(message: impl Into<String>) -> ProviderError {
    ProviderError::Definitive {
        message: message.into(),
    }
}

fn transient(message: impl Into<String>) -> ProviderError {
    ProviderError::Transient {
        message: message.into(),
    }
}
