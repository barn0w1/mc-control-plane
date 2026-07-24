use std::path::PathBuf;

use anyhow::{Context, bail};
use bytes::Bytes;
use clap::{Args, Parser, Subcommand, ValueEnum};
use control_plane_protocol::{
    CreateHostClaimParams, DeleteHostClaimParams, EmptyParams, GetHostClaimParams, GetHostParams,
    Host, HostClaim, HostClaimId, HostClaimList, HostClaimSpec, HostId, HostList, HostResources,
    JsonRpcRequest, JsonRpcResponse, SystemInfoResult, method,
};
use http::{Method, Request, header};
use http_body_util::{BodyExt, Full, Limited};
use hyper::client::conn::http2;
use hyper_util::rt::{TokioExecutor, TokioIo};
use serde::{Serialize, de::DeserializeOwned};
use tokio::net::UnixStream;
use uuid::Uuid;

const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;

#[derive(Debug, Parser)]
#[command(version, about = "Operator client for Control Plane")]
struct Cli {
    /// Unix domain socket exposed by the Control Plane daemon.
    #[arg(
        long,
        env = "CONTROL_PLANE_SOCKET",
        default_value = "/tmp/control-plane.sock"
    )]
    socket_path: PathBuf,

    /// Output format.
    #[arg(long, value_enum, default_value_t = OutputFormat::Human)]
    output: OutputFormat,

    #[command(subcommand)]
    command: Command,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum OutputFormat {
    Human,
    Json,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Inspect the Control Plane process.
    System(SystemArgs),

    /// Manage HostClaims and inspect Hosts.
    Host(HostArgs),
}

#[derive(Debug, Args)]
struct SystemArgs {
    #[command(subcommand)]
    command: SystemCommand,
}

#[derive(Debug, Subcommand)]
enum SystemCommand {
    /// Show process and protocol information.
    Info,
}

#[derive(Debug, Args)]
struct HostArgs {
    #[command(subcommand)]
    command: HostCommand,
}

#[derive(Debug, Subcommand)]
enum HostCommand {
    /// Manage persistent Host demand.
    Claim(HostClaimArgs),

    /// Get a Host by ID.
    Get { id: HostId },

    /// List managed Hosts.
    List,
}

#[derive(Debug, Args)]
struct HostClaimArgs {
    #[command(subcommand)]
    command: HostClaimCommand,
}

#[derive(Debug, Subcommand)]
enum HostClaimCommand {
    /// Create a HostClaim. Repeating the same ID and spec is idempotent.
    Create {
        /// Caller-controlled UUID. A UUIDv7 is generated when omitted.
        #[arg(long)]
        id: Option<HostClaimId>,

        /// Minimum allocatable virtual CPUs.
        #[arg(long)]
        vcpus: u32,

        /// Minimum allocatable memory, for example 4GiB.
        #[arg(long, value_parser = parse_byte_quantity)]
        memory: u64,

        /// Minimum allocatable local working storage, for example 40GiB.
        #[arg(long, value_parser = parse_byte_quantity)]
        storage: u64,
    },

    /// Get a HostClaim by ID.
    Get { id: HostClaimId },

    /// List HostClaims.
    List,

    /// Request asynchronous deletion of a HostClaim.
    Delete { id: HostClaimId },
}

#[tokio::main]
async fn main() {
    let cli = Cli::parse();
    let output = cli.output;
    if let Err(error) = run(cli).await {
        report_error(output, &error);
        std::process::exit(1);
    }
}

async fn run(cli: Cli) -> anyhow::Result<()> {
    let client = RpcClient::new(cli.socket_path);

    match cli.command {
        Command::System(SystemArgs {
            command: SystemCommand::Info,
        }) => {
            let result: SystemInfoResult = client
                .call(method::SYSTEM_INFO, EmptyParams::default())
                .await?;
            print_value(cli.output, &result, print_system_info)?;
        }
        Command::Host(HostArgs { command }) => match command {
            HostCommand::Claim(HostClaimArgs { command }) => match command {
                HostClaimCommand::Create {
                    id,
                    vcpus,
                    memory,
                    storage,
                } => {
                    let claim_id = id.unwrap_or_else(HostClaimId::new);
                    let result: HostClaim = client
                        .call(
                            method::HOST_CLAIM_CREATE,
                            CreateHostClaimParams {
                                id: claim_id,
                                spec: HostClaimSpec {
                                    resources: HostResources {
                                        vcpus,
                                        memory_bytes: memory,
                                        storage_bytes: storage,
                                    },
                                },
                            },
                        )
                        .await
                        .with_context(|| {
                            format!(
                                "create HostClaim {claim_id}; retry safely with --id {claim_id}"
                            )
                        })?;
                    print_value(cli.output, &result, print_host_claim)?;
                }
                HostClaimCommand::Get { id } => {
                    let result: HostClaim = client
                        .call(method::HOST_CLAIM_GET, GetHostClaimParams { id })
                        .await?;
                    print_value(cli.output, &result, print_host_claim)?;
                }
                HostClaimCommand::List => {
                    let result: HostClaimList = client
                        .call(method::HOST_CLAIM_LIST, EmptyParams::default())
                        .await?;
                    print_value(cli.output, &result, print_host_claim_list)?;
                }
                HostClaimCommand::Delete { id } => {
                    let result: HostClaim = client
                        .call(method::HOST_CLAIM_DELETE, DeleteHostClaimParams { id })
                        .await?;
                    print_value(cli.output, &result, print_host_claim)?;
                }
            },
            HostCommand::Get { id } => {
                let result: Host = client.call(method::HOST_GET, GetHostParams { id }).await?;
                print_value(cli.output, &result, print_host)?;
            }
            HostCommand::List => {
                let result: HostList = client
                    .call(method::HOST_LIST, EmptyParams::default())
                    .await?;
                print_value(cli.output, &result, print_host_list)?;
            }
        },
    }

    Ok(())
}

#[derive(Clone, Debug)]
struct RpcClient {
    socket_path: PathBuf,
}

impl RpcClient {
    fn new(socket_path: PathBuf) -> Self {
        Self { socket_path }
    }

    async fn call<P, R>(&self, method: &'static str, params: P) -> anyhow::Result<R>
    where
        P: Serialize,
        R: DeserializeOwned,
    {
        let request_id = Uuid::now_v7().to_string();
        let payload = serde_json::to_vec(&JsonRpcRequest {
            jsonrpc: "2.0",
            id: request_id.clone(),
            method,
            params,
        })
        .context("serialize JSON-RPC request")?;

        let stream = UnixStream::connect(&self.socket_path)
            .await
            .with_context(|| format!("connect to {}", self.socket_path.display()))?;
        let (mut sender, connection) = http2::handshake(TokioExecutor::new(), TokioIo::new(stream))
            .await
            .context("establish HTTP/2 connection over Unix socket")?;

        tokio::spawn(async move {
            if let Err(error) = connection.await {
                tracing::debug!(error = ?error, "local RPC HTTP/2 connection ended with an error");
            }
        });

        let request = Request::builder()
            .method(Method::POST)
            .uri("http://localhost/rpc")
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::ACCEPT, "application/json")
            .body(Full::new(Bytes::from(payload)))
            .context("build local RPC HTTP request")?;
        let response = sender
            .send_request(request)
            .await
            .context("send local RPC request")?;
        let status = response.status();
        let body = Limited::new(response.into_body(), MAX_RESPONSE_BYTES)
            .collect()
            .await
            .map_err(anyhow::Error::from_boxed)
            .context("read bounded local RPC response")?
            .to_bytes();
        if !status.is_success() {
            bail!(
                "local RPC returned HTTP {status}: {}",
                String::from_utf8_lossy(&body)
            );
        }

        let response: JsonRpcResponse<R> =
            serde_json::from_slice(&body).context("decode JSON-RPC response")?;
        if response.jsonrpc != "2.0" {
            bail!("unsupported JSON-RPC version {:?}", response.jsonrpc);
        }
        if response.id != request_id {
            bail!(
                "JSON-RPC response ID mismatch: expected {request_id}, received {}",
                response.id
            );
        }
        match (response.result, response.error) {
            (Some(result), None) => Ok(result),
            (None, Some(error)) => Err(RpcCallError {
                code: error.code,
                message: error.message,
                data: error.data,
            }
            .into()),
            (Some(_), Some(_)) => {
                bail!("JSON-RPC response contains both result and error")
            }
            (None, None) => {
                bail!("JSON-RPC response contains neither result nor error")
            }
        }
    }
}

#[derive(Debug, thiserror::Error)]
#[error("RPC error {code}: {message}")]
struct RpcCallError {
    code: i32,
    message: String,
    data: Option<serde_json::Value>,
}

fn report_error(output: OutputFormat, error: &anyhow::Error) {
    match output {
        OutputFormat::Human => eprintln!("error: {error:#}"),
        OutputFormat::Json => {
            let payload = if let Some(rpc) = error.downcast_ref::<RpcCallError>() {
                serde_json::json!({
                    "error": {
                        "source": "rpc",
                        "code": rpc.code,
                        "message": &rpc.message,
                        "context": format!("{error:#}"),
                        "data": &rpc.data,
                    }
                })
            } else {
                serde_json::json!({
                    "error": {
                        "source": "client",
                        "message": format!("{error:#}"),
                    }
                })
            };
            eprintln!(
                "{}",
                serde_json::to_string_pretty(&payload).unwrap_or_else(|_| {
                    r#"{"error":{"source":"client","message":"failed to serialize error"}}"#
                        .to_owned()
                })
            );
        }
    }
}

fn parse_byte_quantity(value: &str) -> Result<u64, String> {
    parse_size::parse_size(value).map_err(|error| error.to_string())
}

fn print_value<T>(format: OutputFormat, value: &T, human: fn(&T)) -> anyhow::Result<()>
where
    T: Serialize,
{
    match format {
        OutputFormat::Human => human(value),
        OutputFormat::Json => println!("{}", serde_json::to_string_pretty(value)?),
    }
    Ok(())
}

fn print_system_info(info: &SystemInfoResult) {
    println!("system: {}", info.system_name);
    println!("version: {}", info.binary_version);
    println!("rust: {}", info.rust_version);
    println!("protocol: {}", info.protocol_version);
    println!("started: {}", info.started_at);
}

fn print_host_claim(claim: &HostClaim) {
    println!("HostClaim {}", claim.id);
    println!("  generation: {}", claim.generation);
    println!("  created: {}", claim.created_at);
    if let Some(timestamp) = &claim.deletion_timestamp {
        println!("  deletion requested: {timestamp}");
    }
    print_resources("  requested", &claim.spec.resources);
    println!(
        "  host: {}",
        claim
            .status
            .host_id
            .map_or_else(|| "-".to_owned(), |id| id.to_string())
    );
    for condition in &claim.status.conditions {
        println!(
            "  condition {}={:?} ({}) {}",
            condition.condition_type, condition.status, condition.reason, condition.message
        );
    }
}

fn print_host_claim_list(list: &HostClaimList) {
    if list.items.is_empty() {
        println!("No HostClaims.");
        return;
    }
    for (index, claim) in list.items.iter().enumerate() {
        if index > 0 {
            println!();
        }
        print_host_claim(claim);
    }
}

fn print_host(host: &Host) {
    println!("Host {}", host.id);
    println!("  claim: {}", host.claim_id);
    println!("  created: {}", host.created_at);
    println!("  phase: {:?}", host.status.phase);
    println!(
        "  provider resource: {}",
        host.status.provider_resource_id.as_deref().unwrap_or("-")
    );
    print_resources("  allocatable", &host.allocatable_resources);
    for condition in &host.status.conditions {
        println!(
            "  condition {}={:?} ({}) {}",
            condition.condition_type, condition.status, condition.reason, condition.message
        );
    }
}

fn print_host_list(list: &HostList) {
    if list.items.is_empty() {
        println!("No Hosts.");
        return;
    }
    for (index, host) in list.items.iter().enumerate() {
        if index > 0 {
            println!();
        }
        print_host(host);
    }
}

fn print_resources(prefix: &str, resources: &HostResources) {
    println!("{prefix} vcpus: {}", resources.vcpus);
    println!("{prefix} memory bytes: {}", resources.memory_bytes);
    println!("{prefix} storage bytes: {}", resources.storage_bytes);
}
