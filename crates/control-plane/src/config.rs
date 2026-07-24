use std::{path::PathBuf, time::Duration};

use clap::{Parser, ValueEnum};

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
pub enum LogFormat {
    Json,
    Compact,
}

#[derive(Clone, Debug, Parser)]
#[command(version, about = "Central Control Plane daemon")]
pub struct Config {
    /// Unix domain socket used by local RPC clients.
    #[arg(long, env = "CONTROL_PLANE_SOCKET", default_value = "/tmp/control-plane.sock")]
    pub socket_path: PathBuf,

    /// Control Plane SQLite database.
    #[arg(long, env = "CONTROL_PLANE_DATABASE", default_value = "/tmp/control-plane.db")]
    pub database_path: PathBuf,

    /// Independent fake provider SQLite database.
    #[arg(
        long,
        env = "CONTROL_PLANE_FAKE_PROVIDER_DATABASE",
        default_value = "/tmp/control-plane-fake-provider.db"
    )]
    pub fake_provider_database_path: PathBuf,

    /// Unix socket mode, written as an octal value.
    #[arg(long, env = "CONTROL_PLANE_SOCKET_MODE", default_value = "0660", value_parser = parse_mode)]
    pub socket_mode: u32,

    /// Periodic safety scan interval in milliseconds.
    #[arg(
        long,
        env = "CONTROL_PLANE_RECONCILE_INTERVAL_MS",
        default_value_t = 1000,
        value_parser = parse_positive_u64
    )]
    pub reconcile_interval_ms: u64,

    /// Structured JSON is the service default; compact output is convenient in a terminal.
    #[arg(long, env = "CONTROL_PLANE_LOG_FORMAT", value_enum, default_value_t = LogFormat::Json)]
    pub log_format: LogFormat,
}

impl Config {
    #[must_use]
    pub fn reconcile_interval(&self) -> Duration {
        Duration::from_millis(self.reconcile_interval_ms)
    }
}

fn parse_mode(value: &str) -> Result<u32, String> {
    u32::from_str_radix(value.trim_start_matches("0o"), 8)
        .map_err(|error| format!("invalid octal mode {value:?}: {error}"))
        .and_then(|mode| {
            if mode <= 0o7777 {
                Ok(mode)
            } else {
                Err(format!("socket mode {value:?} is outside 0000..7777"))
            }
        })
}

fn parse_positive_u64(value: &str) -> Result<u64, String> {
    let parsed = value
        .parse::<u64>()
        .map_err(|error| format!("invalid positive integer {value:?}: {error}"))?;
    if parsed == 0 {
        Err("value must be greater than zero".to_owned())
    } else {
        Ok(parsed)
    }
}
