use std::{sync::Arc, time::Duration};

use control_plane_protocol::{ConditionStatus, HostPhase};
use tokio::sync::Notify;
use tokio_util::sync::CancellationToken;
use tracing::{Instrument, info_span};

use crate::{
    domain::{
        HostClaimRecord, HostRecord, RetryState, new_condition, now_timestamp, now_unix_ms,
        retry_deadline, set_condition,
    },
    error::AppError,
    provider::{
        CreateOutcome, DeleteOutcome, HostProvider, ProviderError, ProviderLifecycle,
        ProviderObservation,
    },
    storage::Storage,
};

const MAX_STEPS_PER_WAKE: usize = 64;

#[derive(Clone, Debug, Default)]
pub struct ReconcileHandle {
    notify: Arc<Notify>,
}

impl ReconcileHandle {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub fn wake(&self) {
        self.notify.notify_one();
    }
}

#[derive(Debug)]
pub struct HostController<P> {
    storage: Storage,
    provider: P,
    handle: ReconcileHandle,
    scan_interval: Duration,
}

impl<P> HostController<P>
where
    P: HostProvider,
{
    #[must_use]
    pub fn new(
        storage: Storage,
        provider: P,
        handle: ReconcileHandle,
        scan_interval: Duration,
    ) -> Self {
        Self {
            storage,
            provider,
            handle,
            scan_interval,
        }
    }

    pub async fn run(self, cancellation: CancellationToken) -> anyhow::Result<()> {
        let mut interval = tokio::time::interval(self.scan_interval);
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        self.handle.wake();

        loop {
            tokio::select! {
                () = cancellation.cancelled() => break,
                () = self.handle.notify.notified() => {},
                _ = interval.tick() => {},
            }

            if cancellation.is_cancelled() {
                break;
            }

            let mut reached_step_limit = true;
            for _ in 0..MAX_STEPS_PER_WAKE {
                match self.reconcile_once().await {
                    Ok(ReconcileResult::Progress) => {}
                    Ok(ReconcileResult::Idle) => {
                        reached_step_limit = false;
                        break;
                    }
                    Err(error) => {
                        reached_step_limit = false;
                        tracing::error!(error = ?error, "Host reconciliation failed unexpectedly");
                        break;
                    }
                }
            }
            if reached_step_limit {
                self.handle.wake();
            }
        }

        tracing::info!("Host controller stopped");
        Ok(())
    }

    async fn reconcile_once(&self) -> Result<ReconcileResult, AppError> {
        let now_ms = now_unix_ms();
        let claims = self.storage.list_claims().await?;

        for claim in claims.iter().filter(|claim| {
            claim.resource.deletion_timestamp.is_some() && claim.retry.is_due(now_ms)
        }) {
            let span = info_span!(
                "reconcile_host_claim_deletion",
                host_claim_id = %claim.resource.id
            );
            let result = self
                .reconcile_deleting_claim(claim.clone(), now_ms)
                .instrument(span)
                .await?;
            if result == ReconcileResult::Progress {
                return Ok(result);
            }
        }

        for claim in claims
            .iter()
            .filter(|claim| claim.resource.deletion_timestamp.is_none())
        {
            if let Some(host_id) = claim.resource.status.host_id {
                let host = self.storage.get_host(host_id).await?;
                if !host.retry.is_due(now_ms) || host.resource.status.phase == HostPhase::Failed {
                    continue;
                }
                let span = info_span!(
                    "reconcile_host",
                    host_claim_id = %claim.resource.id,
                    host_id = %host_id
                );
                let result = self
                    .reconcile_host(claim.clone(), host, now_ms)
                    .instrument(span)
                    .await?;
                if result == ReconcileResult::Progress {
                    return Ok(result);
                }
                continue;
            }

            if !claim.retry.is_due(now_ms) {
                continue;
            }

            let span = info_span!("reconcile_host_claim", host_claim_id = %claim.resource.id);
            let result = self
                .reconcile_unbound_claim(claim.clone(), now_ms)
                .instrument(span)
                .await?;
            if result == ReconcileResult::Progress {
                return Ok(result);
            }
        }

        Ok(ReconcileResult::Idle)
    }

    async fn reconcile_unbound_claim(
        &self,
        mut claim: HostClaimRecord,
        now_ms: i64,
    ) -> Result<ReconcileResult, AppError> {
        match self
            .provider
            .select_plan(&claim.resource.spec.resources)
            .await
        {
            Ok(Some(plan)) => match self
                .storage
                .ensure_host(claim.resource.id, &plan.id, plan.resources)
                .await
            {
                Ok(_host) => {
                    tracing::info!(plan_id = %plan.id, "assigned a Host to HostClaim");
                    Ok(ReconcileResult::Progress)
                }
                Err(AppError::Conflict { .. } | AppError::NotFound { .. }) => {
                    tracing::debug!(
                        "HostClaim changed while assigning a Host; discarding stale reconciliation"
                    );
                    self.handle.wake();
                    Ok(ReconcileResult::Idle)
                }
                Err(error) => Err(error),
            },
            Ok(None) => {
                let now = now_timestamp();
                let mut changed = false;
                claim.resource.status.observed_generation = claim.resource.generation;
                changed |= set_condition(
                    &mut claim.resource.status.conditions,
                    new_condition(
                        "Accepted",
                        ConditionStatus::False,
                        "Unsatisfiable",
                        "No provider plan can satisfy the requested allocatable resources.",
                        claim.resource.generation,
                        now,
                    ),
                );
                changed |= set_condition(
                    &mut claim.resource.status.conditions,
                    new_condition(
                        "Bound",
                        ConditionStatus::False,
                        "HostPending",
                        "No Host has been assigned.",
                        claim.resource.generation,
                        now,
                    ),
                );
                changed |= set_condition(
                    &mut claim.resource.status.conditions,
                    new_condition(
                        "Ready",
                        ConditionStatus::False,
                        "HostNotReady",
                        "No Ready Host is assigned.",
                        claim.resource.generation,
                        now,
                    ),
                );
                claim.retry = RetryState::default();
                if changed {
                    self.persist_claim_status(&claim).await?;
                    tracing::warn!("HostClaim is unsatisfiable by the current provider catalog");
                    Ok(ReconcileResult::Progress)
                } else {
                    Ok(ReconcileResult::Idle)
                }
            }
            Err(error) => {
                schedule_claim_provider_error(&mut claim, error, now_ms);
                self.persist_claim_status(&claim).await?;
                Ok(ReconcileResult::Progress)
            }
        }
    }

    async fn reconcile_host(
        &self,
        mut claim: HostClaimRecord,
        mut host: HostRecord,
        now_ms: i64,
    ) -> Result<ReconcileResult, AppError> {
        match self.provider.observe(host.resource.id).await {
            Ok(ProviderObservation::Present(resource)) => {
                if let Some(expected) = host.resource.status.provider_resource_id.clone() {
                    if expected != resource.id {
                        mark_host_failed(
                            &mut claim,
                            &mut host,
                            "ProviderOwnershipConflict",
                            format!(
                                "Host recorded provider resource {expected}, but ownership lookup returned {}.",
                                resource.id
                            ),
                        );
                        self.storage.save_host(&host).await?;
                        self.persist_claim_status(&claim).await?;
                        return Ok(ReconcileResult::Progress);
                    }
                }

                let unchanged = provider_observation_is_stable(&host, &resource);
                apply_provider_observation(&mut claim, &mut host, resource);
                self.storage.save_host(&host).await?;
                self.persist_claim_status(&claim).await?;
                Ok(if unchanged {
                    ReconcileResult::Idle
                } else {
                    ReconcileResult::Progress
                })
            }
            Ok(ProviderObservation::Absent) => {
                if host.resource.status.provider_resource_id.is_some()
                    || host.resource.status.phase != HostPhase::Pending
                {
                    mark_provider_absent(&mut claim, &mut host);
                    self.storage.save_host(&host).await?;
                    self.persist_claim_status(&claim).await?;
                    return Ok(ReconcileResult::Progress);
                }

                host.resource.status.phase = HostPhase::Provisioning;
                host.retry = RetryState::default();
                self.storage.save_host(&host).await?;

                match self
                    .provider
                    .create(host.resource.id, &host.provider_plan_id)
                    .await
                {
                    Ok(CreateOutcome::Created(resource)) => {
                        apply_provider_observation(&mut claim, &mut host, resource);
                    }
                    Ok(CreateOutcome::OutcomeUnknown) => {
                        mark_outcome_unknown(
                            &mut claim,
                            &mut host,
                            "CreateOutcomeUnknown",
                            "The provider may have created the Host. The next step will rediscover it by Host ID.",
                            now_ms,
                        );
                    }
                    Err(error) => {
                        schedule_host_provider_error(&mut claim, &mut host, error, now_ms);
                    }
                }
                self.storage.save_host(&host).await?;
                self.persist_claim_status(&claim).await?;
                Ok(ReconcileResult::Progress)
            }
            Err(error) => {
                schedule_host_provider_error(&mut claim, &mut host, error, now_ms);
                self.storage.save_host(&host).await?;
                self.persist_claim_status(&claim).await?;
                Ok(ReconcileResult::Progress)
            }
        }
    }

    async fn persist_claim_status(&self, claim: &HostClaimRecord) -> Result<(), AppError> {
        if !self.storage.save_claim_status(claim).await? {
            tracing::debug!(
                host_claim_id = %claim.resource.id,
                "discarded stale HostClaim status after a concurrent mutation"
            );
            self.handle.wake();
        }
        Ok(())
    }

    async fn reconcile_deleting_claim(
        &self,
        mut claim: HostClaimRecord,
        now_ms: i64,
    ) -> Result<ReconcileResult, AppError> {
        let Some(host_id) = claim.resource.status.host_id else {
            self.storage.finalize_claim(claim.resource.id).await?;
            tracing::info!("finalized HostClaim without an assigned Host");
            return Ok(ReconcileResult::Progress);
        };

        let mut host = self.storage.get_host(host_id).await?;
        if !host.retry.is_due(now_ms) {
            return Ok(ReconcileResult::Idle);
        }

        match self.provider.observe(host.resource.id).await {
            Ok(ProviderObservation::Absent) => {
                self.storage.finalize_claim(claim.resource.id).await?;
                tracing::info!("provider resource is absent; finalized HostClaim and Host");
                Ok(ReconcileResult::Progress)
            }
            Ok(ProviderObservation::Present(resource)) => {
                if let Some(expected) = host.resource.status.provider_resource_id.clone() {
                    if expected != resource.id {
                        mark_host_failed(
                            &mut claim,
                            &mut host,
                            "ProviderOwnershipConflict",
                            format!(
                                "Host recorded provider resource {expected}, but ownership lookup returned {}.",
                                resource.id
                            ),
                        );
                        host.retry.next_reconcile_at_unix_ms = Some(i64::MAX);
                        self.storage.save_host(&host).await?;
                        self.persist_claim_status(&claim).await?;
                        return Ok(ReconcileResult::Progress);
                    }
                }

                host.resource.status.phase = HostPhase::Deleting;
                host.resource.status.provider_resource_id = Some(resource.id);
                host.resource.status.observed_at = Some(now_timestamp());
                host.retry = RetryState::default();
                self.storage.save_host(&host).await?;

                match self.provider.delete(host.resource.id).await {
                    Ok(DeleteOutcome::Deleted) => {
                        // A successful mutation is not the deletion proof. Reconcile again
                        // immediately and finalize only after an observation returns Absent.
                        host.retry = RetryState::default();
                    }
                    Ok(DeleteOutcome::OutcomeUnknown) => {
                        host.retry.next_reconcile_at_unix_ms = Some(now_ms.saturating_add(1_000));
                        host.retry.last_error_kind = Some("DeleteOutcomeUnknown".to_owned());
                        host.retry.last_error_message = Some(
                            "The provider may have deleted the Host; rediscovery is required."
                                .to_owned(),
                        );
                    }
                    Err(error) => schedule_host_retry_only(&mut host, error, now_ms),
                }
                self.storage.save_host(&host).await?;
                Ok(ReconcileResult::Progress)
            }
            Err(error) => {
                schedule_host_retry_only(&mut host, error, now_ms);
                self.storage.save_host(&host).await?;
                Ok(ReconcileResult::Progress)
            }
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ReconcileResult {
    Progress,
    Idle,
}

fn provider_observation_is_stable(
    host: &HostRecord,
    resource: &crate::provider::ProviderResource,
) -> bool {
    let expected_phase = match resource.lifecycle {
        ProviderLifecycle::Provisioning => HostPhase::Provisioning,
        ProviderLifecycle::Ready => HostPhase::Ready,
        ProviderLifecycle::Deleting => HostPhase::Deleting,
    };

    host.resource.status.phase == expected_phase
        && host.resource.status.provider_resource_id.as_deref() == Some(resource.id.as_str())
        && host.retry.attempt == 0
        && host.retry.next_reconcile_at_unix_ms.is_none()
        && host.retry.last_error_kind.is_none()
        && host.retry.last_error_message.is_none()
}

fn apply_provider_observation(
    claim: &mut HostClaimRecord,
    host: &mut HostRecord,
    resource: crate::provider::ProviderResource,
) {
    let now = now_timestamp();
    host.resource.status.provider_resource_id = Some(resource.id);
    host.resource.status.observed_at = Some(now);
    host.retry = RetryState::default();

    let (phase, status, reason, message) = match resource.lifecycle {
        ProviderLifecycle::Provisioning => (
            HostPhase::Provisioning,
            ConditionStatus::False,
            "ProviderResourceProvisioning",
            "The provider resource is still provisioning.",
        ),
        ProviderLifecycle::Ready => (
            HostPhase::Ready,
            ConditionStatus::True,
            "ProviderResourceReady",
            "The provider resource is Ready.",
        ),
        ProviderLifecycle::Deleting => (
            HostPhase::Deleting,
            ConditionStatus::False,
            "ProviderResourceDeleting",
            "The provider resource is being deleted.",
        ),
    };
    host.resource.status.phase = phase;
    set_condition(
        &mut host.resource.status.conditions,
        new_condition("Ready", status, reason, message, 1, now),
    );

    claim.resource.status.observed_generation = claim.resource.generation;
    claim.retry = RetryState::default();
    let claim_ready = phase == HostPhase::Ready;
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
            format!("Host {} is assigned.", host.resource.id),
            claim.resource.generation,
            now,
        ),
    );
    set_condition(
        &mut claim.resource.status.conditions,
        new_condition(
            "Ready",
            if claim_ready {
                ConditionStatus::True
            } else {
                ConditionStatus::False
            },
            if claim_ready {
                "HostReady"
            } else {
                "HostNotReady"
            },
            if claim_ready {
                "The assigned Host is Ready."
            } else {
                "The assigned Host is not Ready."
            },
            claim.resource.generation,
            now,
        ),
    );
}

fn mark_provider_absent(claim: &mut HostClaimRecord, host: &mut HostRecord) {
    let now = now_timestamp();
    host.resource.status.phase = HostPhase::Pending;
    host.resource.status.provider_resource_id = None;
    host.resource.status.observed_at = Some(now);
    host.retry = RetryState::default();
    set_condition(
        &mut host.resource.status.conditions,
        new_condition(
            "Ready",
            ConditionStatus::False,
            "ProviderResourceAbsent",
            "No provider resource exists for this Host.",
            1,
            now,
        ),
    );
    set_condition(
        &mut claim.resource.status.conditions,
        new_condition(
            "Ready",
            ConditionStatus::False,
            "HostNotReady",
            "The assigned Host has no provider resource.",
            claim.resource.generation,
            now,
        ),
    );
}

fn mark_outcome_unknown(
    claim: &mut HostClaimRecord,
    host: &mut HostRecord,
    kind: &str,
    message: &str,
    now_ms: i64,
) {
    let now = now_timestamp();
    host.resource.status.phase = HostPhase::Provisioning;
    host.resource.status.observed_at = Some(now);
    host.retry.attempt = host.retry.attempt.saturating_add(1);
    host.retry.next_reconcile_at_unix_ms = Some(now_ms.saturating_add(1_000));
    host.retry.last_error_kind = Some(kind.to_owned());
    host.retry.last_error_message = Some(message.to_owned());
    set_condition(
        &mut host.resource.status.conditions,
        new_condition("Ready", ConditionStatus::Unknown, kind, message, 1, now),
    );
    set_condition(
        &mut claim.resource.status.conditions,
        new_condition(
            "Ready",
            ConditionStatus::Unknown,
            kind,
            message,
            claim.resource.generation,
            now,
        ),
    );
}

fn mark_host_failed(
    claim: &mut HostClaimRecord,
    host: &mut HostRecord,
    reason: &str,
    message: String,
) {
    let now = now_timestamp();
    host.resource.status.phase = HostPhase::Failed;
    host.retry.next_reconcile_at_unix_ms = None;
    host.retry.last_error_kind = Some(reason.to_owned());
    host.retry.last_error_message = Some(message.clone());
    set_condition(
        &mut host.resource.status.conditions,
        new_condition(
            "Ready",
            ConditionStatus::False,
            reason,
            message.clone(),
            1,
            now,
        ),
    );
    set_condition(
        &mut claim.resource.status.conditions,
        new_condition(
            "Ready",
            ConditionStatus::False,
            reason,
            message,
            claim.resource.generation,
            now,
        ),
    );
}

fn schedule_claim_provider_error(claim: &mut HostClaimRecord, error: ProviderError, now_ms: i64) {
    let now = now_timestamp();
    claim.resource.status.observed_generation = claim.resource.generation;

    match error {
        ProviderError::Definitive { message } => {
            claim.retry = RetryState {
                attempt: claim.retry.attempt,
                next_reconcile_at_unix_ms: Some(i64::MAX),
                last_error_kind: Some("ProviderRejected".to_owned()),
                last_error_message: Some(message.clone()),
            };
            set_condition(
                &mut claim.resource.status.conditions,
                new_condition(
                    "Accepted",
                    ConditionStatus::False,
                    "ProviderRejected",
                    message,
                    claim.resource.generation,
                    now,
                ),
            );
        }
        ProviderError::Transient { message } => {
            claim.retry.attempt = claim.retry.attempt.saturating_add(1);
            claim.retry.next_reconcile_at_unix_ms =
                Some(retry_deadline(now_ms, claim.retry.attempt));
            claim.retry.last_error_kind = Some("ProviderUnavailable".to_owned());
            claim.retry.last_error_message = Some(message.clone());
            set_condition(
                &mut claim.resource.status.conditions,
                new_condition(
                    "Accepted",
                    ConditionStatus::Unknown,
                    "ProviderUnavailable",
                    message,
                    claim.resource.generation,
                    now,
                ),
            );
        }
    }
}

fn schedule_host_provider_error(
    claim: &mut HostClaimRecord,
    host: &mut HostRecord,
    error: ProviderError,
    now_ms: i64,
) {
    match error {
        ProviderError::Definitive { message } => {
            mark_host_failed(claim, host, "ProviderRejected", message);
        }
        ProviderError::Transient { message } => {
            let now = now_timestamp();
            host.retry.attempt = host.retry.attempt.saturating_add(1);
            host.retry.next_reconcile_at_unix_ms = Some(retry_deadline(now_ms, host.retry.attempt));
            host.retry.last_error_kind = Some("ProviderUnavailable".to_owned());
            host.retry.last_error_message = Some(message.clone());
            set_condition(
                &mut host.resource.status.conditions,
                new_condition(
                    "Ready",
                    ConditionStatus::Unknown,
                    "ProviderUnavailable",
                    message.clone(),
                    1,
                    now,
                ),
            );
            set_condition(
                &mut claim.resource.status.conditions,
                new_condition(
                    "Ready",
                    ConditionStatus::Unknown,
                    "ProviderUnavailable",
                    message,
                    claim.resource.generation,
                    now,
                ),
            );
        }
    }
}

fn schedule_host_retry_only(host: &mut HostRecord, error: ProviderError, now_ms: i64) {
    let (kind, message, terminal) = match error {
        ProviderError::Definitive { message } => ("ProviderRejected", message, true),
        ProviderError::Transient { message } => ("ProviderUnavailable", message, false),
    };
    host.retry.attempt = host.retry.attempt.saturating_add(1);
    host.retry.next_reconcile_at_unix_ms = if terminal {
        Some(i64::MAX)
    } else {
        Some(retry_deadline(now_ms, host.retry.attempt))
    };
    host.retry.last_error_kind = Some(kind.to_owned());
    host.retry.last_error_message = Some(message);
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::*;
    use control_plane_protocol::{HostClaimId, HostClaimSpec, HostResources};
    use uuid::Uuid;

    #[test]
    fn retry_backoff_is_bounded() {
        assert!(crate::domain::retry_deadline(1_000, 1) > 1_000);
        assert_eq!(crate::domain::retry_deadline(1_000, 99), 61_000);
    }

    #[test]
    fn condition_helper_detects_status() {
        let claim = crate::domain::initial_claim(
            HostClaimId::new(),
            HostClaimSpec {
                resources: HostResources {
                    vcpus: 1,
                    memory_bytes: 1,
                    storage_bytes: 1,
                },
            },
            now_timestamp(),
        );
        assert!(crate::domain::condition_is(
            &claim,
            "Accepted",
            ConditionStatus::Unknown
        ));
    }

    #[tokio::test]
    async fn reconciles_multiple_claims_and_deletes_one() -> anyhow::Result<()> {
        let paths = TestPaths::new("multiple-claims");
        let storage = Storage::connect(&paths.control_plane).await?;
        let provider = crate::provider::FakeProvider::connect(&paths.provider).await?;
        let controller = HostController::new(
            storage.clone(),
            provider.clone(),
            ReconcileHandle::new(),
            Duration::from_millis(10),
        );

        let first = HostClaimId::new();
        let second = HostClaimId::new();
        storage.create_claim(first, small_spec()).await?;
        storage.create_claim(second, small_spec()).await?;
        reconcile_to_idle(&controller, 32).await?;

        let claims = storage.list_claims().await?;
        assert_eq!(claims.len(), 2);
        assert!(claims.iter().all(|claim| crate::domain::condition_is(
            &claim.resource,
            "Ready",
            ConditionStatus::True
        )));
        assert_eq!(storage.list_hosts().await?.len(), 2);

        storage.mark_claim_deleting(first).await?;
        reconcile_to_idle(&controller, 32).await?;
        assert!(matches!(
            storage.get_claim(first).await,
            Err(AppError::NotFound { .. })
        ));
        assert_eq!(storage.list_hosts().await?.len(), 1);

        storage.close().await;
        provider.close().await;
        paths.remove();
        Ok(())
    }

    #[tokio::test]
    async fn persists_selected_provider_plan_before_external_create() -> anyhow::Result<()> {
        let paths = TestPaths::new("persisted-provider-plan");
        let storage = Storage::connect(&paths.control_plane).await?;
        let provider = crate::provider::FakeProvider::connect(&paths.provider).await?;
        let controller = HostController::new(
            storage.clone(),
            provider.clone(),
            ReconcileHandle::new(),
            Duration::from_millis(10),
        );

        let claim_id = HostClaimId::new();
        storage.create_claim(claim_id, small_spec()).await?;
        assert_eq!(
            controller.reconcile_once().await?,
            ReconcileResult::Progress
        );

        let host = storage
            .get_host_for_claim(claim_id)
            .await?
            .expect("Host must be assigned");
        assert_eq!(host.resource.status.phase, HostPhase::Pending);
        assert_eq!(host.provider_plan_id, "fake-2c-4g-40g");
        assert_eq!(provider.resource_count().await?, 0);

        storage.close().await;
        provider.close().await;
        paths.remove();
        Ok(())
    }

    #[tokio::test]
    async fn marks_definitive_create_failure_as_failed() -> anyhow::Result<()> {
        let paths = TestPaths::new("definitive-create-failure");
        let storage = Storage::connect(&paths.control_plane).await?;
        let provider = crate::provider::FakeProvider::connect(&paths.provider).await?;
        let controller = HostController::new(
            storage.clone(),
            provider.clone(),
            ReconcileHandle::new(),
            Duration::from_millis(10),
        );

        let claim_id = HostClaimId::new();
        storage.create_claim(claim_id, small_spec()).await?;
        assert_eq!(
            controller.reconcile_once().await?,
            ReconcileResult::Progress
        );
        provider
            .inject_fault("create_definitive_failure", 1)
            .await?;
        assert_eq!(
            controller.reconcile_once().await?,
            ReconcileResult::Progress
        );

        let host = storage
            .get_host_for_claim(claim_id)
            .await?
            .expect("Host must be assigned");
        assert_eq!(host.resource.status.phase, HostPhase::Failed);
        assert_eq!(
            host.retry.last_error_kind.as_deref(),
            Some("ProviderRejected")
        );
        assert_eq!(provider.resource_count().await?, 0);

        storage.close().await;
        provider.close().await;
        paths.remove();
        Ok(())
    }

    #[tokio::test]
    async fn retries_transient_observation_failure() -> anyhow::Result<()> {
        let paths = TestPaths::new("transient-observation");
        let storage = Storage::connect(&paths.control_plane).await?;
        let provider = crate::provider::FakeProvider::connect(&paths.provider).await?;
        let controller = HostController::new(
            storage.clone(),
            provider.clone(),
            ReconcileHandle::new(),
            Duration::from_millis(10),
        );

        let claim_id = HostClaimId::new();
        storage.create_claim(claim_id, small_spec()).await?;
        assert_eq!(
            controller.reconcile_once().await?,
            ReconcileResult::Progress
        );
        provider
            .inject_fault("observe_transient_failure", 1)
            .await?;
        assert_eq!(
            controller.reconcile_once().await?,
            ReconcileResult::Progress
        );

        let mut host = storage
            .get_host_for_claim(claim_id)
            .await?
            .expect("Host must be assigned");
        assert_eq!(host.retry.attempt, 1);
        assert_eq!(
            host.retry.last_error_kind.as_deref(),
            Some("ProviderUnavailable")
        );

        host.retry.next_reconcile_at_unix_ms = None;
        storage.save_host(&host).await?;
        reconcile_to_idle(&controller, 8).await?;
        assert!(crate::domain::condition_is(
            &storage.get_claim(claim_id).await?.resource,
            "Ready",
            ConditionStatus::True
        ));

        storage.close().await;
        provider.close().await;
        paths.remove();
        Ok(())
    }

    #[tokio::test]
    async fn rediscovers_delete_after_response_loss() -> anyhow::Result<()> {
        let paths = TestPaths::new("delete-response-loss");
        let storage = Storage::connect(&paths.control_plane).await?;
        let provider = crate::provider::FakeProvider::connect(&paths.provider).await?;
        let controller = HostController::new(
            storage.clone(),
            provider.clone(),
            ReconcileHandle::new(),
            Duration::from_millis(10),
        );

        let claim_id = HostClaimId::new();
        storage.create_claim(claim_id, small_spec()).await?;
        reconcile_to_idle(&controller, 16).await?;
        assert_eq!(provider.resource_count().await?, 1);

        provider.inject_fault("delete_response_loss", 1).await?;
        storage.mark_claim_deleting(claim_id).await?;
        assert_eq!(
            controller.reconcile_once().await?,
            ReconcileResult::Progress
        );
        assert_eq!(provider.resource_count().await?, 0);
        assert!(storage.get_claim(claim_id).await.is_ok());

        tokio::time::sleep(Duration::from_millis(1_050)).await;
        reconcile_to_idle(&controller, 8).await?;
        assert!(matches!(
            storage.get_claim(claim_id).await,
            Err(AppError::NotFound { .. })
        ));

        storage.close().await;
        provider.close().await;
        paths.remove();
        Ok(())
    }

    #[tokio::test]
    async fn rediscovers_create_after_response_loss() -> anyhow::Result<()> {
        let paths = TestPaths::new("create-response-loss");
        let storage = Storage::connect(&paths.control_plane).await?;
        let provider = crate::provider::FakeProvider::connect(&paths.provider).await?;
        provider.inject_fault("create_response_loss", 1).await?;
        let controller = HostController::new(
            storage.clone(),
            provider.clone(),
            ReconcileHandle::new(),
            Duration::from_millis(10),
        );

        let claim_id = HostClaimId::new();
        storage.create_claim(claim_id, small_spec()).await?;
        assert_eq!(
            controller.reconcile_once().await?,
            ReconcileResult::Progress
        );
        assert_eq!(
            controller.reconcile_once().await?,
            ReconcileResult::Progress
        );

        let host = storage
            .get_host_for_claim(claim_id)
            .await?
            .expect("Host must be assigned");
        assert_eq!(host.resource.status.phase, HostPhase::Provisioning);
        assert_eq!(
            host.retry.last_error_kind.as_deref(),
            Some("CreateOutcomeUnknown")
        );

        tokio::time::sleep(Duration::from_millis(1_050)).await;
        reconcile_to_idle(&controller, 8).await?;
        let claim = storage.get_claim(claim_id).await?;
        assert!(crate::domain::condition_is(
            &claim.resource,
            "Ready",
            ConditionStatus::True
        ));
        assert_eq!(storage.list_hosts().await?.len(), 1);

        storage.close().await;
        provider.close().await;
        paths.remove();
        Ok(())
    }

    #[tokio::test]
    async fn restart_reconciliation_does_not_duplicate_resources() -> anyhow::Result<()> {
        let paths = TestPaths::new("restart");
        let claim_id = HostClaimId::new();

        {
            let storage = Storage::connect(&paths.control_plane).await?;
            let provider = crate::provider::FakeProvider::connect(&paths.provider).await?;
            let controller = HostController::new(
                storage.clone(),
                provider.clone(),
                ReconcileHandle::new(),
                Duration::from_millis(10),
            );
            storage.create_claim(claim_id, small_spec()).await?;
            reconcile_to_idle(&controller, 16).await?;
            assert_eq!(storage.list_hosts().await?.len(), 1);
            assert_eq!(provider.resource_count().await?, 1);
            storage.close().await;
            provider.close().await;
        }

        {
            let storage = Storage::connect(&paths.control_plane).await?;
            let provider = crate::provider::FakeProvider::connect(&paths.provider).await?;
            let controller = HostController::new(
                storage.clone(),
                provider.clone(),
                ReconcileHandle::new(),
                Duration::from_millis(10),
            );
            reconcile_to_idle(&controller, 8).await?;
            assert_eq!(storage.list_hosts().await?.len(), 1);
            assert_eq!(provider.resource_count().await?, 1);
            assert!(crate::domain::condition_is(
                &storage.get_claim(claim_id).await?.resource,
                "Ready",
                ConditionStatus::True
            ));
            storage.close().await;
            provider.close().await;
        }

        paths.remove();
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

    async fn reconcile_to_idle<P>(
        controller: &HostController<P>,
        max_steps: usize,
    ) -> Result<(), AppError>
    where
        P: HostProvider,
    {
        for _ in 0..max_steps {
            if controller.reconcile_once().await? == ReconcileResult::Idle {
                return Ok(());
            }
        }
        panic!("controller did not become idle within {max_steps} steps");
    }

    struct TestPaths {
        control_plane: PathBuf,
        provider: PathBuf,
    }

    impl TestPaths {
        fn new(name: &str) -> Self {
            let root =
                std::env::temp_dir().join(format!("control-plane-{name}-{}", Uuid::now_v7()));
            std::fs::create_dir_all(&root).expect("create test directory");
            Self {
                control_plane: root.join("control-plane.db"),
                provider: root.join("provider.db"),
            }
        }

        fn remove(&self) {
            if let Some(root) = self.control_plane.parent() {
                let _ = std::fs::remove_dir_all(root);
            }
        }
    }
}
