use std::{
    fs::Permissions,
    future::Future,
    sync::Arc,
    os::unix::fs::{FileTypeExt, PermissionsExt},
    path::Path,
};

use anyhow::{Context, anyhow};
use control_plane_protocol::{
    CreateHostClaimParams, DeleteHostClaimParams, GetHostClaimParams,
    GetHostParams, HostClaimList, HostList, ListHostClaimsParams, ListHostsParams,
    SystemInfoParams, SystemInfoResult, method,
};
use jiff::Timestamp;
use hyper::server::conn::http2;
use hyper_util::{
    rt::{TokioExecutor, TokioIo},
    service::TowerToHyperService,
};
use jsonrpsee::{
    Methods, NotifyMsg, RpcModule,
    core::middleware::{Batch, Notification, Request, RpcServiceBuilder, RpcServiceT},
    server::{BatchRequestConfig, Server, ServerConfig, stop_channel},
};
use tokio::{net::UnixListener, sync::Semaphore, task::JoinSet};
use tokio_util::sync::CancellationToken;
use tower::ServiceBuilder;

use crate::{controller::ReconcileHandle, error::AppError, storage::Storage};

const MAX_REQUEST_BYTES: u32 = 1024 * 1024;
const MAX_RESPONSE_BYTES: u32 = 4 * 1024 * 1024;
const MAX_CONNECTIONS: usize = 64;

#[derive(Clone, Debug)]
pub struct RpcContext {
    storage: Storage,
    reconcile: ReconcileHandle,
    started_at: Timestamp,
}

impl RpcContext {
    #[must_use]
    pub fn new(storage: Storage, reconcile: ReconcileHandle, started_at: Timestamp) -> Self {
        Self {
            storage,
            reconcile,
            started_at,
        }
    }
}

pub async fn serve(
    socket_path: &Path,
    socket_mode: u32,
    context: RpcContext,
    cancellation: CancellationToken,
) -> anyhow::Result<()> {
    prepare_socket_path(socket_path).await?;
    let listener = UnixListener::bind(socket_path)
        .with_context(|| format!("bind local RPC socket {}", socket_path.display()))?;
    tokio::fs::set_permissions(socket_path, Permissions::from_mode(socket_mode))
        .await
        .with_context(|| format!("set mode on local RPC socket {}", socket_path.display()))?;

    let config = ServerConfig::builder()
        .http_only()
        .max_connections(MAX_CONNECTIONS as u32)
        .max_request_body_size(MAX_REQUEST_BYTES)
        .max_response_body_size(MAX_RESPONSE_BYTES)
        .set_batch_request_config(BatchRequestConfig::Disabled)
        .build();
    let rpc_middleware = RpcServiceBuilder::new()
        .layer_fn(|service| RejectNotifications { service });
    let service_builder = Server::builder()
        .set_config(config)
        .set_rpc_middleware(rpc_middleware)
        .set_http_middleware(
            ServiceBuilder::new().timeout(std::time::Duration::from_secs(30)),
        )
        .to_service_builder();
    let methods: Methods = rpc_module(context)?.into();
    let (stop_handle, server_handle) = stop_channel();
    let mut connections = JoinSet::new();
    let connection_limit = Arc::new(Semaphore::new(MAX_CONNECTIONS));

    tracing::info!(socket = %socket_path.display(), mode = format_args!("{socket_mode:04o}"), "local RPC server started");

    loop {
        tokio::select! {
            () = cancellation.cancelled() => break,
            accepted = listener.accept() => {
                let (stream, _) = accepted.context("accept local RPC connection")?;
                let permit = match connection_limit.clone().try_acquire_owned() {
                    Ok(permit) => permit,
                    Err(_) => {
                        tracing::warn!(limit = MAX_CONNECTIONS, "rejected local RPC connection at concurrency limit");
                        continue;
                    }
                };
                let methods = methods.clone();
                let service_builder = service_builder.clone();
                let stop = stop_handle.clone();

                connections.spawn(async move {
                    let _permit = permit;
                    let stop_for_shutdown = stop.clone();
                    let service = service_builder.build(methods, stop);
                    let service = TowerToHyperService::new(service);
                    let connection = http2::Builder::new(TokioExecutor::new())
                        .serve_connection(TokioIo::new(stream), service);
                    tokio::pin!(connection);

                    tokio::select! {
                        result = connection.as_mut() => {
                            if let Err(error) = result {
                                tracing::warn!(error = ?error, "local RPC connection failed");
                            }
                        }
                        () = stop_for_shutdown.shutdown() => {
                            connection.as_mut().graceful_shutdown();
                            if let Err(error) = connection.await {
                                tracing::warn!(error = ?error, "local RPC connection failed during shutdown");
                            }
                        }
                    }
                });
            }
            Some(joined) = connections.join_next(), if !connections.is_empty() => {
                if let Err(error) = joined {
                    tracing::warn!(error = ?error, "local RPC connection task failed");
                }
            }
        }
    }

    let _ = server_handle.stop();
    while let Some(joined) = connections.join_next().await {
        if let Err(error) = joined {
            tracing::warn!(error = ?error, "local RPC connection task failed during shutdown");
        }
    }
    drop(listener);
    remove_socket_if_present(socket_path).await?;
    tracing::info!(socket = %socket_path.display(), "local RPC server stopped");
    Ok(())
}


#[derive(Clone, Debug)]
struct RejectNotifications<S> {
    service: S,
}

impl<S> RpcServiceT for RejectNotifications<S>
where
    S: RpcServiceT<NotificationResponse = NotifyMsg> + Clone + Send + Sync + 'static,
{
    type MethodResponse = S::MethodResponse;
    type NotificationResponse = NotifyMsg;
    type BatchResponse = S::BatchResponse;

    fn call<'a>(
        &self,
        request: Request<'a>,
    ) -> impl Future<Output = Self::MethodResponse> + Send + 'a {
        self.service.call(request)
    }

    fn batch<'a>(
        &self,
        requests: Batch<'a>,
    ) -> impl Future<Output = Self::BatchResponse> + Send + 'a {
        self.service.batch(requests)
    }

    fn notification<'a>(
        &self,
        notification: Notification<'a>,
    ) -> impl Future<Output = Self::NotificationResponse> + Send + 'a {
        tracing::warn!(method = %notification.method_name(), "rejected JSON-RPC notification");
        std::future::ready(NotifyMsg::Err)
    }
}

fn rpc_module(context: RpcContext) -> anyhow::Result<RpcModule<RpcContext>> {
    let mut module = RpcModule::new(context);

    module.register_async_method(method::SYSTEM_INFO, |params, context, _| async move {
        parse_params::<SystemInfoParams>(params)?;
        Ok::<_, jsonrpsee::types::ErrorObjectOwned>(SystemInfoResult {
            system_name: "Control Plane".to_owned(),
            binary_version: env!("CARGO_PKG_VERSION").to_owned(),
            rust_version: env!("CONTROL_PLANE_RUSTC_VERSION").to_owned(),
            protocol_version: control_plane_protocol::PROTOCOL_VERSION.to_owned(),
            started_at: context.started_at.clone(),
        })
    })?;

    module.register_async_method(method::HOST_CLAIM_CREATE, |params, context, _| async move {
        let params = parse_params::<CreateHostClaimParams>(params)?;
        let claim = context
            .storage
            .create_claim(params.id, params.spec)
            .await
            .map_err(AppError::into_rpc_error)?;
        context.reconcile.wake();
        Ok::<_, jsonrpsee::types::ErrorObjectOwned>(claim)
    })?;

    module.register_async_method(method::HOST_CLAIM_GET, |params, context, _| async move {
        let params = parse_params::<GetHostClaimParams>(params)?;
        context
            .storage
            .get_claim(params.id)
            .await
            .map(|record| record.resource)
            .map_err(AppError::into_rpc_error)
    })?;

    module.register_async_method(method::HOST_CLAIM_LIST, |params, context, _| async move {
        parse_params::<ListHostClaimsParams>(params)?;
        context
            .storage
            .list_claims()
            .await
            .map(|records| HostClaimList {
                items: records.into_iter().map(|record| record.resource).collect(),
            })
            .map_err(AppError::into_rpc_error)
    })?;

    module.register_async_method(method::HOST_CLAIM_DELETE, |params, context, _| async move {
        let params = parse_params::<DeleteHostClaimParams>(params)?;
        let claim = context
            .storage
            .mark_claim_deleting(params.id)
            .await
            .map_err(AppError::into_rpc_error)?;
        context.reconcile.wake();
        Ok::<_, jsonrpsee::types::ErrorObjectOwned>(claim)
    })?;

    module.register_async_method(method::HOST_GET, |params, context, _| async move {
        let params = parse_params::<GetHostParams>(params)?;
        context
            .storage
            .get_host(params.id)
            .await
            .map(|record| record.resource)
            .map_err(AppError::into_rpc_error)
    })?;

    module.register_async_method(method::HOST_LIST, |params, context, _| async move {
        parse_params::<ListHostsParams>(params)?;
        context
            .storage
            .list_hosts()
            .await
            .map(|records| HostList {
                items: records.into_iter().map(|record| record.resource).collect(),
            })
            .map_err(AppError::into_rpc_error)
    })?;

    Ok(module)
}

fn parse_params<T>(params: jsonrpsee::types::Params<'_>) -> Result<T, jsonrpsee::types::ErrorObjectOwned>
where
    T: serde::de::DeserializeOwned,
{
    params.parse::<T>().map_err(|error| {
        AppError::InvalidArgument {
            message: error.to_string(),
        }
        .into_rpc_error()
    })
}

async fn prepare_socket_path(path: &Path) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent)
            .await
            .with_context(|| format!("create socket directory {}", parent.display()))?;
    }
    remove_socket_if_present(path).await
}

async fn remove_socket_if_present(path: &Path) -> anyhow::Result<()> {
    let metadata = match tokio::fs::symlink_metadata(path).await {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => {
            return Err(error)
                .with_context(|| format!("inspect local RPC socket {}", path.display()));
        }
    };

    if !metadata.file_type().is_socket() {
        return Err(anyhow!(
            "refusing to remove non-socket path {}",
            path.display()
        ));
    }

    tokio::fs::remove_file(path)
        .await
        .with_context(|| format!("remove stale local RPC socket {}", path.display()))
}
