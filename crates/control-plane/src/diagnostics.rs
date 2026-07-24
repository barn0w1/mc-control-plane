use anyhow::Context;
use tracing_subscriber::EnvFilter;

use crate::config::LogFormat;

pub fn init(format: LogFormat) -> anyhow::Result<()> {
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));

    match format {
        LogFormat::Json => tracing_subscriber::fmt()
            .with_env_filter(filter)
            .json()
            .try_init()
            .map_err(anyhow::Error::from_boxed)
            .context("initialize JSON tracing subscriber"),
        LogFormat::Compact => tracing_subscriber::fmt()
            .with_env_filter(filter)
            .compact()
            .try_init()
            .map_err(anyhow::Error::from_boxed)
            .context("initialize compact tracing subscriber"),
    }
}
