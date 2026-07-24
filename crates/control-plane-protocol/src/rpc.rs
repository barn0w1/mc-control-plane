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
#[serde(deny_unknown_fields, bound(deserialize = "R: Deserialize<'de>"))]
pub struct JsonRpcResponse<R> {
    pub jsonrpc: String,
    pub id: String,
    pub result: Option<R>,
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

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Debug, Eq, PartialEq, serde::Deserialize)]
    #[serde(deny_unknown_fields)]
    struct TestResult {
        value: String,
    }

    #[test]
    fn deserializes_success_without_requiring_default_on_result() {
        let response: JsonRpcResponse<TestResult> = serde_json::from_str(
            r#"{"jsonrpc":"2.0","id":"request-1","result":{"value":"ok"}}"#,
        )
        .expect("deserialize JSON-RPC success response");

        assert_eq!(
            response.result,
            Some(TestResult {
                value: "ok".to_owned(),
            })
        );
        assert!(response.error.is_none());
    }

    #[test]
    fn deserializes_error_without_a_result_field() {
        let response: JsonRpcResponse<TestResult> = serde_json::from_str(
            r#"{"jsonrpc":"2.0","id":"request-1","error":{"code":-32603,"message":"Internal error"}}"#,
        )
        .expect("deserialize JSON-RPC error response");

        assert!(response.result.is_none());
        let error = response.error.expect("error response");
        assert_eq!(error.code, -32603);
        assert_eq!(error.message, "Internal error");
        assert!(error.data.is_none());
    }
}
