//! Wire contracts shared by Control Plane RPC clients and servers.
//!
//! This crate intentionally contains no persistence, controller, or provider
//! implementation details.

pub mod ids;
pub mod resources;
pub mod rpc;

pub use ids::{HostClaimId, HostId};
pub use resources::{
    Condition, ConditionStatus, Host, HostClaim, HostClaimSpec, HostClaimStatus, HostPhase,
    HostResources, HostStatus,
};
pub use rpc::*;

pub const PROTOCOL_VERSION: &str = "1";
