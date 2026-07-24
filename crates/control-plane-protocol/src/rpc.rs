use jiff::Timestamp;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{Host, HostClaim, HostClaimId, HostClaimSpec, HostId};

pub mod method {
    pub const SYSTEM_INFO: &str = "system.info";
    pub const HOST_CLAIM_CREATE: &str = "host.claim.create";
    pub const HOST_CLAIM_GET: &str = "host.claim.get";
    pub const HOST_CLAIM_LIST: &str = "host.claim.list";
    pub const HOST_CLAIM_DELETE: &str = "host.claim.delete";
    pub const HOST_GET: &str = "host.get";
    pub const HOST_LIST: &str = "host.list";
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EmptyParams {}

pub type SystemInfoParams = EmptyParams;
pub type ListHostClaimsParams = EmptyParams;
pub type ListHostsParams = EmptyParams;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SystemInfoResult {
    pub system_name: String,
    pub binary_version: String,
    pub rust_version: String,
    pub protocol_version: String,
    pub started_at: Timestamp,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateHostClaimParams {
    pub id: HostClaimId,
    pub spec: HostClaimSpec,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GetHostClaimParams {
    pub id: HostClaimId,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeleteHostClaimParams {
    pub id: HostClaimId,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GetHostParams {
    pub id: HostId,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostClaimList {
    pub items: Vec<HostClaim>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostList {
    pub items: Vec<Host>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RpcErrorKind {
    InvalidArgument,
    NotFound,
    Conflict,
    Unavailable,
    Internal,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RpcErrorData {
    pub kind: RpcErrorKind,
    pub resource_id: Option<String>,
    pub details: Option<Value>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(deny_unknown_fields)]
pub struct JsonRpcRequest<P> {
    pub jsonrpc: &'static str,
    pub id: String,
    pub method: &'static str,
    pub params: P,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JsonRpcResponse<R> {
    pub jsonrpc: String,
    pub id: String,
    #[serde(default)]
    pub result: Option<R>,
    #[serde(default)]
    pub error: Option<JsonRpcErrorObject>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JsonRpcErrorObject {
    pub code: i32,
    pub message: String,
    #[serde(default)]
    pub data: Option<Value>,
}
