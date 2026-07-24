use anyhow::Context;
use clap::{Parser, ValueEnum};
use tracing_subscriber::EnvFilter;

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum LogFormat {
    Json,
    Compact,
}

#[derive(Debug, Parser)]
#[command(version, about = "Host-resident Control Plane agent")]
struct Config {
    /// Structured JSON is the service default; compact output is convenient in a terminal.
    #[arg(long, env = "HOST_AGENT_LOG_FORMAT", value_enum, default_value_t = LogFormat::Json)]
    log_format: LogFormat,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let config = Config::parse();
    init_tracing(config.log_format)?;

    tracing::info!(
        version = env!("CARGO_PKG_VERSION"),
        protocol_version = control_plane_protocol::PROTOCOL_VERSION,
        "Host Agent skeleton started"
    );
    shutdown_signal().await?;
    tracing::info!("Host Agent skeleton stopped");
    Ok(())
}

fn init_tracing(format: LogFormat) -> anyhow::Result<()> {
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    match format {
        LogFormat::Json => tracing_subscriber::fmt()
            .with_env_filter(filter)
            .json()
            .try_init()
            .context("initialize JSON tracing subscriber"),
        LogFormat::Compact => tracing_subscriber::fmt()
            .with_env_filter(filter)
            .compact()
            .try_init()
            .context("initialize compact tracing subscriber"),
    }
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
