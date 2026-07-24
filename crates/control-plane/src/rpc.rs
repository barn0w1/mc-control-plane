use std::{
    fs::Permissions,
    future::Future,
    os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt},
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
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
    MethodResponse, Methods, RpcModule,
    core::middleware::{Batch, Notification, Request, RpcServiceBuilder, RpcServiceT},
    server::{BatchRequestConfig, Server, ServerConfig, stop_channel},
};
use tokio::{
    net::{UnixListener, UnixStream},
    sync::Semaphore,
    task::JoinSet,
};
use tokio_util::sync::CancellationToken;
use tower::ServiceBuilder;

use crate::{controller::ReconcileHandle, error::AppError, storage::Storage};

const MAX_REQUEST_BYTES: u32 = 1024 * 1024;
const MAX_RESPONSE_BYTES: u32 = 4 * 1024 * 1024;
const MAX_CONNECTIONS: usize = 64;
const EXISTING_SOCKET_PROBE_TIMEOUT: Duration = Duration::from_millis(250);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct SocketIdentity {
    device: u64,
    inode: u64,
}

#[derive(Debug)]
struct SocketPathGuard {
    path: PathBuf,
    identity: SocketIdentity,
}

impl SocketPathGuard {
    fn new(path: &Path) -> anyhow::Result<Self> {
        Ok(Self {
            path: path.to_owned(),
            identity: socket_identity(path)?,
        })
    }
}

impl Drop for SocketPathGuard {
    fn drop(&mut self) {
        let Ok(identity) = socket_identity(&self.path) else {
            return;
        };
        if identity != self.identity {
            return;
        }
        if let Err(error) = std::fs::remove_file(&self.path) {
            if error.kind() != std::io::ErrorKind::NotFound {
                tracing::warn!(
                    socket = %self.path.display(),
                    error = ?error,
                    "failed to remove owned local RPC socket"
                );
            }
        }
    }
}

#[derive(Debug)]
pub struct BoundRpcSocket {
    listener: UnixListener,
    socket_guard: SocketPathGuard,
    socket_path: PathBuf,
    socket_mode: u32,
}

impl BoundRpcSocket {
    pub async fn serve(
        self,
        context: RpcContext,
        cancellation: CancellationToken,
    ) -> anyhow::Result<()> {
        let Self {
            listener,
            socket_guard,
            socket_path,
            socket_mode,
        } = self;
        serve_bound(listener, &socket_path, socket_mode, context, cancellation).await?;
        drop(socket_guard);
        tracing::info!(socket = %socket_path.display(), "local RPC server stopped");
        Ok(())
    }
}

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

pub async fn bind(socket_path: &Path, socket_mode: u32) -> anyhow::Result<BoundRpcSocket> {
    prepare_socket_path(socket_path).await?;
    let listener = UnixListener::bind(socket_path)
        .with_context(|| format!("bind local RPC socket {}", socket_path.display()))?;
    let socket_guard = SocketPathGuard::new(socket_path)?;
    tokio::fs::set_permissions(socket_path, Permissions::from_mode(socket_mode))
        .await
        .with_context(|| format!("set mode on local RPC socket {}", socket_path.display()))?;

    Ok(BoundRpcSocket {
        listener,
        socket_guard,
        socket_path: socket_path.to_owned(),
        socket_mode,
    })
}

async fn serve_bound(
    listener: UnixListener,
    socket_path: &Path,
    socket_mode: u32,
    context: RpcContext,
    cancellation: CancellationToken,
) -> anyhow::Result<()> {
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

    if let Err(error) = server_handle.stop() {
        tracing::debug!(?error, "local RPC server was already stopped");
    }
    while let Some(joined) = connections.join_next().await {
        if let Err(error) = joined {
            tracing::warn!(error = ?error, "local RPC connection task failed during shutdown");
        }
    }
    drop(listener);
    Ok(())
}

#[derive(Clone, Debug)]
struct RejectNotifications<S> {
    service: S,
}

impl<S> RpcServiceT for RejectNotifications<S>
where
    S: RpcServiceT<NotificationResponse = MethodResponse> + Clone + Send + Sync + 'static,
{
    type MethodResponse = S::MethodResponse;
    type NotificationResponse = MethodResponse;
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
        std::future::ready(MethodResponse::notification())
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
            started_at: context.started_at,
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
    if let Some(parent) = path.parent().filter(|parent| !parent.as_os_str().is_empty()) {
        tokio::fs::create_dir_all(parent)
            .await
            .with_context(|| format!("create socket directory {}", parent.display()))?;
    }

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
            "refusing to replace non-socket path {}",
            path.display()
        ));
    }
    let existing_identity = SocketIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    };

    match tokio::time::timeout(EXISTING_SOCKET_PROBE_TIMEOUT, UnixStream::connect(path)).await {
        Ok(Ok(_stream)) => Err(anyhow!(
            "local RPC socket {} is already served by another process",
            path.display()
        )),
        Err(_elapsed) => Err(anyhow!(
            "timed out while probing existing local RPC socket {}; refusing to remove it",
            path.display()
        )),
        Ok(Err(error)) if error.kind() == std::io::ErrorKind::ConnectionRefused => {
            remove_socket_if_owned(path, existing_identity).await
        }
        Ok(Err(error)) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Ok(Err(error)) => Err(error).with_context(|| {
            format!(
                "probe existing local RPC socket {}; refusing to remove it",
                path.display()
            )
        }),
    }
}

async fn remove_socket_if_owned(
    path: &Path,
    expected: SocketIdentity,
) -> anyhow::Result<()> {
    let metadata = match tokio::fs::symlink_metadata(path).await {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => {
            return Err(error)
                .with_context(|| format!("reinspect stale local RPC socket {}", path.display()));
        }
    };
    let current = SocketIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    };
    if !metadata.file_type().is_socket() || current != expected {
        return Err(anyhow!(
            "local RPC socket {} changed while it was being probed; refusing to remove it",
            path.display()
        ));
    }

    tokio::fs::remove_file(path)
        .await
        .with_context(|| format!("remove stale local RPC socket {}", path.display()))
}

fn socket_identity(path: &Path) -> anyhow::Result<SocketIdentity> {
    let metadata = std::fs::symlink_metadata(path)
        .with_context(|| format!("inspect local RPC socket {}", path.display()))?;
    if !metadata.file_type().is_socket() {
        return Err(anyhow!("path {} is not a Unix socket", path.display()));
    }
    Ok(SocketIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    })
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use tokio::net::UnixListener;
    use uuid::Uuid;

    use super::*;

    #[tokio::test]
    async fn refuses_to_replace_an_active_socket() -> anyhow::Result<()> {
        let path = test_socket_path("active");
        let listener = UnixListener::bind(&path)?;

        let error = prepare_socket_path(&path)
            .await
            .expect_err("an active socket must not be removed");
        assert!(error.to_string().contains("already served"));
        assert!(path.exists());

        drop(listener);
        let _ = std::fs::remove_file(path);
        Ok(())
    }

    #[tokio::test]
    async fn removes_a_stale_socket() -> anyhow::Result<()> {
        let path = test_socket_path("stale");
        let listener = UnixListener::bind(&path)?;
        drop(listener);

        prepare_socket_path(&path).await?;
        assert!(!path.exists());
        Ok(())
    }

    #[tokio::test]
    async fn socket_guard_does_not_remove_a_replacement_socket() -> anyhow::Result<()> {
        let path = test_socket_path("replacement");
        let first = UnixListener::bind(&path)?;
        let guard = SocketPathGuard::new(&path)?;

        std::fs::remove_file(&path)?;
        let replacement = UnixListener::bind(&path)?;
        drop(guard);
        assert!(path.exists());

        drop(replacement);
        drop(first);
        let _ = std::fs::remove_file(path);
        Ok(())
    }

    fn test_socket_path(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "control-plane-rpc-{name}-{}.sock",
            Uuid::now_v7()
        ))
    }
}
