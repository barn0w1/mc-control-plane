mod config;
mod controller;
mod diagnostics;
mod domain;
mod error;
mod provider;
mod rpc;
mod storage;

use anyhow::Context;
use clap::Parser;
use controller::{HostController, ReconcileHandle};
use jiff::Timestamp;
use provider::FakeProvider;
use tokio_util::sync::CancellationToken;

use crate::{config::Config, rpc::RpcContext, storage::Storage};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let config = Config::parse();
    diagnostics::init(config.log_format)?;

    let storage = Storage::connect(&config.database_path).await?;
    let provider = FakeProvider::connect(&config.fake_provider_database_path).await?;
    let reconcile = ReconcileHandle::new();
    let cancellation = CancellationToken::new();
    let started_at = Timestamp::now();

    let controller = HostController::new(
        storage.clone(),
        provider.clone(),
        reconcile.clone(),
        config.reconcile_interval(),
    );
    let rpc_context = RpcContext::new(storage.clone(), reconcile, started_at);

    let mut controller_task = tokio::spawn(controller.run(cancellation.child_token()));
    let mut rpc_task = tokio::spawn(rpc::serve(
        &config.socket_path,
        config.socket_mode,
        rpc_context,
        cancellation.child_token(),
    ));

    tracing::info!(
        version = env!("CARGO_PKG_VERSION"),
        protocol_version = control_plane_protocol::PROTOCOL_VERSION,
        "Control Plane started"
    );

    enum Exit {
        Signal(anyhow::Result<()>),
        Controller(Result<anyhow::Result<()>, tokio::task::JoinError>),
        Rpc(Result<anyhow::Result<()>, tokio::task::JoinError>),
    }

    let exit = tokio::select! {
        result = shutdown_signal() => Exit::Signal(result),
        result = &mut controller_task => Exit::Controller(result),
        result = &mut rpc_task => Exit::Rpc(result),
    };

    cancellation.cancel();

    let primary_result = match exit {
        Exit::Signal(result) => {
            result?;
            controller_task.await.context("join Host controller task")??;
            rpc_task.await.context("join local RPC task")??;
            Ok(())
        }
        Exit::Controller(result) => {
            let result = result.context("Host controller task panicked")?;
            rpc_task.await.context("join local RPC task")??;
            result
        }
        Exit::Rpc(result) => {
            let result = result.context("local RPC task panicked")?;
            controller_task.await.context("join Host controller task")??;
            result
        }
    };

    storage.close().await;
    provider.close().await;
    tracing::info!("Control Plane stopped");
    primary_result
}

async fn shutdown_signal() -> anyhow::Result<()> {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{SignalKind, signal};

        let mut terminate = signal(SignalKind::terminate()).context("listen for SIGTERM")?;
        tokio::select! {
            result = tokio::signal::ctrl_c() => result.context("listen for Ctrl-C"),
            _ = terminate.recv() => Ok(()),
        }
    }

    #[cfg(not(unix))]
    {
        tokio::signal::ctrl_c()
            .await
            .context("listen for Ctrl-C")
    }
}
