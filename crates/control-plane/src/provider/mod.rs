mod fake;

use control_plane_protocol::{HostId, HostResources};
use thiserror::Error;

pub use fake::FakeProvider;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Plan {
    pub id: String,
    pub resources: HostResources,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProviderLifecycle {
    Provisioning,
    Ready,
    Deleting,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProviderResource {
    pub id: String,
    pub host_id: HostId,
    pub lifecycle: ProviderLifecycle,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ProviderObservation {
    Absent,
    Present(ProviderResource),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CreateOutcome {
    Created(ProviderResource),
    OutcomeUnknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DeleteOutcome {
    Deleted,
    OutcomeUnknown,
}

#[derive(Debug, Error)]
pub enum ProviderError {
    #[error("provider rejected the operation: {message}")]
    Definitive { message: String },

    #[error("provider operation failed temporarily: {message}")]
    Transient { message: String },
}

#[allow(async_fn_in_trait)]
pub trait HostProvider: Clone + Send + Sync + 'static {
    async fn select_plan(
        &self,
        requirements: &HostResources,
    ) -> Result<Option<Plan>, ProviderError>;

    async fn observe(&self, host_id: HostId) -> Result<ProviderObservation, ProviderError>;

    async fn create(
        &self,
        host_id: HostId,
        plan_id: &str,
    ) -> Result<CreateOutcome, ProviderError>;

    async fn delete(&self, host_id: HostId) -> Result<DeleteOutcome, ProviderError>;
}
