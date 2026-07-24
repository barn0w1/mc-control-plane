use std::time::{SystemTime, UNIX_EPOCH};

use control_plane_protocol::{
    Condition, ConditionStatus, Host, HostClaim, HostClaimId, HostClaimSpec, HostClaimStatus,
    HostId, HostPhase, HostResources, HostStatus,
};
use jiff::Timestamp;

#[derive(Clone, Debug)]
pub struct HostClaimRecord {
    pub resource: HostClaim,
    pub retry: RetryState,
}

#[derive(Clone, Debug)]
pub struct HostRecord {
    pub resource: Host,
    pub provider_plan_id: String,
    pub retry: RetryState,
}

#[derive(Clone, Debug, Default)]
pub struct RetryState {
    pub attempt: u32,
    pub next_reconcile_at_unix_ms: Option<i64>,
    pub last_error_kind: Option<String>,
    pub last_error_message: Option<String>,
}

impl RetryState {
    #[must_use]
    pub fn is_due(&self, now_unix_ms: i64) -> bool {
        self.next_reconcile_at_unix_ms
            .is_none_or(|deadline| deadline <= now_unix_ms)
    }
}

#[must_use]
pub fn now_timestamp() -> Timestamp {
    Timestamp::now()
}

#[must_use]
pub fn now_unix_ms() -> i64 {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    i64::try_from(duration.as_millis()).unwrap_or(i64::MAX)
}

#[must_use]
pub fn retry_deadline(now_unix_ms: i64, attempt: u32) -> i64 {
    let exponent = attempt.min(6);
    let delay_ms = 1_000_i64.saturating_mul(1_i64 << exponent);
    now_unix_ms.saturating_add(delay_ms.min(60_000))
}

#[must_use]
pub fn initial_claim(id: HostClaimId, spec: HostClaimSpec, now: Timestamp) -> HostClaim {
    HostClaim {
        id,
        generation: 1,
        created_at: now,
        deletion_timestamp: None,
        spec,
        status: HostClaimStatus {
            observed_generation: 0,
            host_id: None,
            conditions: vec![
                new_condition(
                    "Accepted",
                    ConditionStatus::Unknown,
                    "PendingEvaluation",
                    "The controller has not evaluated this claim yet.",
                    0,
                    now,
                ),
                new_condition(
                    "Bound",
                    ConditionStatus::False,
                    "HostPending",
                    "No Host has been assigned.",
                    0,
                    now,
                ),
                new_condition(
                    "Ready",
                    ConditionStatus::False,
                    "HostNotReady",
                    "No Ready Host is assigned.",
                    0,
                    now,
                ),
            ],
        },
    }
}

#[must_use]
pub fn initial_host(
    id: HostId,
    claim_id: HostClaimId,
    resources: HostResources,
    now: Timestamp,
) -> Host {
    Host {
        id,
        claim_id,
        created_at: now,
        allocatable_resources: resources,
        status: HostStatus {
            phase: HostPhase::Pending,
            provider_resource_id: None,
            observed_at: None,
            conditions: vec![new_condition(
                "Ready",
                ConditionStatus::False,
                "ProviderResourcePending",
                "No provider resource is currently Ready.",
                1,
                now,
            )],
        },
    }
}

#[must_use]
pub fn new_condition(
    condition_type: impl Into<String>,
    status: ConditionStatus,
    reason: impl Into<String>,
    message: impl Into<String>,
    observed_generation: u64,
    now: Timestamp,
) -> Condition {
    Condition {
        condition_type: condition_type.into(),
        status,
        reason: reason.into(),
        message: message.into(),
        observed_generation,
        last_transition_at: now,
    }
}

pub fn set_condition(conditions: &mut Vec<Condition>, next: Condition) -> bool {
    if let Some(current) = conditions
        .iter_mut()
        .find(|condition| condition.condition_type == next.condition_type)
    {
        if current.status == next.status
            && current.reason == next.reason
            && current.message == next.message
            && current.observed_generation == next.observed_generation
        {
            return false;
        }

        if current.status == next.status && current.reason == next.reason {
            current.message = next.message;
            current.observed_generation = next.observed_generation;
        } else {
            *current = next;
        }
        true
    } else {
        conditions.push(next);
        conditions.sort_by(|left, right| left.condition_type.cmp(&right.condition_type));
        true
    }
}

#[cfg(test)]
#[must_use]
pub fn condition_is(claim: &HostClaim, condition_type: &str, status: ConditionStatus) -> bool {
    claim
        .status
        .conditions
        .iter()
        .any(|condition| condition.condition_type == condition_type && condition.status == status)
}
