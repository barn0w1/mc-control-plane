use jiff::Timestamp;
use serde::{Deserialize, Serialize};

use crate::{HostClaimId, HostId};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostResources {
    pub vcpus: u32,
    pub memory_bytes: u64,
    pub storage_bytes: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostClaimSpec {
    pub resources: HostResources,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConditionStatus {
    True,
    False,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Condition {
    #[serde(rename = "type")]
    pub condition_type: String,
    pub status: ConditionStatus,
    pub reason: String,
    pub message: String,
    pub observed_generation: u64,
    pub last_transition_at: Timestamp,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostClaimStatus {
    pub observed_generation: u64,
    pub host_id: Option<HostId>,
    pub conditions: Vec<Condition>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostClaim {
    pub id: HostClaimId,
    pub generation: u64,
    pub created_at: Timestamp,
    pub deletion_timestamp: Option<Timestamp>,
    pub spec: HostClaimSpec,
    pub status: HostClaimStatus,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HostPhase {
    Pending,
    Provisioning,
    Ready,
    Deleting,
    Failed,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostStatus {
    pub phase: HostPhase,
    pub provider_resource_id: Option<String>,
    pub observed_at: Option<Timestamp>,
    pub conditions: Vec<Condition>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Host {
    pub id: HostId,
    pub claim_id: HostClaimId,
    pub created_at: Timestamp,
    pub allocatable_resources: HostResources,
    pub status: HostStatus,
}
