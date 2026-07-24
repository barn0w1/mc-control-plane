use control_plane_protocol::{RpcErrorData, RpcErrorKind};
use jsonrpsee::types::ErrorObjectOwned;
use thiserror::Error;

pub const INVALID_ARGUMENT_CODE: i32 = -32100;
pub const NOT_FOUND_CODE: i32 = -32101;
pub const CONFLICT_CODE: i32 = -32102;

#[derive(Debug, Error)]
pub enum AppError {
    #[error("invalid argument: {message}")]
    InvalidArgument { message: String },

    #[error("{resource_type} {resource_id} was not found")]
    NotFound {
        resource_type: &'static str,
        resource_id: String,
    },

    #[error("conflict for {resource_type} {resource_id}: {message}")]
    Conflict {
        resource_type: &'static str,
        resource_id: String,
        message: String,
    },


    #[error(transparent)]
    Internal(#[from] anyhow::Error),
}

impl AppError {
    #[must_use]
    pub fn into_rpc_error(self) -> ErrorObjectOwned {
        let (code, kind, resource_id, message) = match self {
            Self::InvalidArgument { message } => {
                (INVALID_ARGUMENT_CODE, RpcErrorKind::InvalidArgument, None, message)
            }
            Self::NotFound {
                resource_type,
                resource_id,
            } => (
                NOT_FOUND_CODE,
                RpcErrorKind::NotFound,
                Some(resource_id.clone()),
                format!("{resource_type} {resource_id} was not found"),
            ),
            Self::Conflict {
                resource_type,
                resource_id,
                message,
            } => (
                CONFLICT_CODE,
                RpcErrorKind::Conflict,
                Some(resource_id.clone()),
                format!("conflict for {resource_type} {resource_id}: {message}"),
            ),
            Self::Internal(error) => {
                tracing::error!(error = ?error, "internal RPC error");
                (
                    jsonrpsee::types::error::INTERNAL_ERROR_CODE,
                    RpcErrorKind::Internal,
                    None,
                    "Internal error".to_owned(),
                )
            }
        };

        ErrorObjectOwned::owned(
            code,
            message,
            Some(RpcErrorData {
                kind,
                resource_id,
                details: None,
            }),
        )
    }
}
