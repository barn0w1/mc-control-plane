# Implementation foundation

> [!NOTE]
> library、transport、SQLiteの選択は継続します。後半のHostClaim quantityとplan-selection例は最初のvertical sliceの実装記録であり、ADR-0010のAkamai-native `spec.type`へ置き換える予定です。

この文書は、最初のRust workspaceを実装するために使用するtoolchain、library、transport、persistence、diagnosticsの具体的な選択を定めます。
長期的なproduct仕様ではなく、最初のvertical sliceを一貫した基盤上で実装するための決定です。

## Rust toolchain

- Rust **1.97 series**を開発基準とする
- `Cargo.toml`の`rust-version`は`1.97`とする
- Editionは`2024`とする
- patch versionは固定せず、利用可能な最新の`1.97.x`を使用する
- Edition 2024が既定とするCargo resolver 3を使用する
- repositoryには、少なくとも最初のworkspace作成時点では特定patchへ固定する`rust-toolchain.toml`を置かない

Rust 1.97.0には後に修正されたcompiler miscompilationがあったため、実際のbuildには1.97 seriesの最新patchを使用します。

## Initial dependency set

最初のworkspaceでは、次のlibrary familyを使用します。patch versionはworkspace作成時の互換する最新版をCargo.lockへ記録します。

| Concern | Selection | Initial version line | Reason |
| --- | --- | --- | --- |
| Async runtime | `tokio` | `1` | Rust async network serviceの事実上の標準で、I/O、task、timer、signalを一つのruntimeで扱える |
| Async utilities | `tokio-util` | `0.7` | `CancellationToken`とtask lifecycle管理に使用する |
| JSON-RPC | `jsonrpsee` | `0.26` | JSON-RPC 2.0 parser、dispatcher、typed API macro、Tower serviceを提供する |
| HTTP | `hyper` | `1` | HTTP/2 connectionをarbitrary async I/O上で直接駆動できる |
| HTTP runtime adapter | `hyper-util` | `0.1` | Tokio I/OとexecutorをHyperへ接続する |
| HTTP types/body | `http`, `http-body-util`, `bytes` | `1`, `0.1`, `1` | HTTP message型、bounded body処理、byte bufferを既存実装へ任せる |
| Service abstraction | `tower` | `0.5` | transport middlewareとJSON-RPC serviceを標準的な`Service`境界で構成する |
| Structured diagnostics | `tracing` | `0.1` | async taskをspanとstructured eventで関連付ける |
| Diagnostics output | `tracing-subscriber` | `0.3` | filtering、human-readable output、JSON outputを提供する |
| CLI parsing | `clap` | `4` | derive APIによってtyped subcommandを定義する |
| Byte quantity parsing | `parse-size` | `1` | SI/IEC byte quantityをallocation-freeで`u64`へparseし、Clapと直接統合できる |
| SQLite | `sqlx` | `0.9` | Tokio対応のasync API、SQLite worker thread、migration、checked queryを提供する |
| Serialization | `serde`, `serde_json` | `1` | RPCとresource型のJSON表現 |
| Resource IDs | `uuid` | `1` | RFC 9562 UUIDv7をresource identityとrequest identityに使用する |
| Time | `jiff` | `0.2` | UTC timestampのparse、format、arithmeticに使用する |
| Typed errors | `thiserror` | `2` | library/module boundaryのerror enum |
| Application errors | `anyhow` | `1` | binary entry pointでのstartup/shutdown contextに限定して使用する |

### Deliberately not selected initially

- `axum`: 最初は単一JSON-RPC endpointだけであり、`jsonrpsee`のTower serviceとHyperで十分なため追加しない
- WebSocket: subscriptionやserver pushを必要としていない
- HTTP/3/QUIC: local Unix socket transportに適さず、最初のRPCには不要
- ORM: resource lifecycleとtransactionをSQLとして明示したいため使用しない
- generic dependency-injection framework: Rustのconstructorとmodule ownershipで十分
- OpenTelemetry: 最初はlocal structured logsで十分。外部collectorが必要になった時点で追加する

## Async runtime and process lifecycle

各binaryはTokioを使用します。`full` featureをworkspace全体へ無条件に有効化せず、各packageが必要なfeatureだけを追加します。

`control-plane`はmulti-thread runtimeを使い、少なくとも次のtaskを持ちます。

- local RPC listener
- Host controller scheduler
- single Host reconciliation worker
- graceful shutdown coordinator

shutdownは`tokio_util::sync::CancellationToken`をroot tokenとして伝播します。
OS signalを受けたら新規RPC受付と新規reconciliation開始を止め、進行中の短い処理を完了させてからSQLite poolをcloseします。

外部provider I/OをSQLite transaction中に待ってはいけません。
transactionはstateを読み書きする短い区間に限定します。

## Local RPC transport

### Protocol stack

```text
JSON-RPC 2.0
    over HTTP/2 request/response
    over Unix domain socket
```

- HTTP/2はprior knowledgeで開始する
- TLSは使用しない。local access controlはUnix socketのowner、group、modeで行う
- HTTP/1.1 fallbackは提供しない
- endpointは`POST /rpc`のみ
- requestとresponseのmedia typeは`application/json`
- 一つのHTTP requestには一つのJSON-RPC requestを入れる
- JSON-RPC batchとnotificationは最初の実装では拒否する
- body sizeには明示的な上限を設定する。初期値は1 MiBとする
- malformed HTTPまたはJSON-RPC inputはprocess panicではなくprotocol errorとして終了する

HTTP/2を選ぶ理由は、binary framing、connection multiplexing、標準化されたstream lifecycleを既存libraryに任せられるためです。
HTTP/3はQUICとTLSを前提とし、同一machine内のUnix socket RPCでは複雑さに対する利益がありません。

### Server implementation

- `tokio::net::UnixListener`でsocketをacceptする
- migrationとprovider initializationの後、controllerを開始する前にsocketの検証、bind、permission設定を完了する
- startup時に既存socketへ短いconnection probeを行い、active daemonが応答可能なら起動を拒否する
- stale socketを削除するのは、Unix socketであり、connectionが`ConnectionRefused`になった場合だけとする
- shutdown時はdevice/inodeが自分のbind時点と一致するsocketだけを削除する
- `hyper::server::conn::http2`で各connectionを駆動する
- `hyper-util`のTokio adapterを使用する
- `jsonrpsee`の`RpcModule`とTower serviceでJSON-RPCをparse、dispatchする
- `http-body-util`と`tower` layerでrequest size、timeout、concurrency、tracingを適用する

### Client implementation

`control`はUnix socketへ接続し、Hyper HTTP/2 client connectionを作成します。
JSON-RPC method、params、result、error dataは`control-plane-protocol`の型を使用します。

CLI invocationごとにconnectionを作り直して構いません。CLIはshort-lived processであり、connection poolingを初期要件にしません。

### Request identity

JSON-RPC `id`にはstring形式のUUIDv7を使用します。
RPCのrequest identityと、永続resourceのidentityは別のUUIDです。

## JSON-RPC API rules

- method名はlowercase dotted formを使用する
  - `system.info`
  - `host.claim.create`
  - `host.claim.get`
  - `host.claim.list`
  - `host.claim.delete`
  - `host.get`
  - `host.list`
- paramsは原則としてJSON objectを使用し、positional arrayを使用しない
- wire typeは`control-plane-protocol`だけに置く
- internal database rowやprovider responseをRPCから直接serializeしない
- error codeはJSON-RPC標準codeとapplication codeを分ける
- machine-readableなapplication error dataには安定した`kind`とresource IDを含める
- human-readable messageをclient logicの条件分岐に使用しない

最初の実装では、wire structを`control-plane-protocol`へ置き、server methodを`RpcModule`へ明示的に登録します。
Unix socket transportに必要なconnection codeはtransport moduleへ閉じ込め、JSON-RPC parserやdispatcherは再実装しません。
proc macroによるAPI生成は、method数が増えて重複削減の効果が明確になった時点で再評価します。

## SQLite and SQLx

### Selection

SQLite accessにはSQLxを使用します。
SQLite C API自体はblockingですが、SQLxはconnectionごとにbackground worker threadを使用し、Tokio taskからnon-blockingに呼び出せます。

初期featureは概ね次です。

```text
runtime-tokio
sqlite-bundled
migrate
macros
uuid
json
```

system SQLiteへ依存せず、bundled SQLiteを使用してbuildごとのSQLite version差を避けます。

### Connection policy

最初は`SqlitePool`の`max_connections`を**1**にします。

理由:

- Control Planeは単一process、低throughputである
- SQLite writerは一つである
- transaction順序を予測しやすくする
- connection間の`SQLITE_BUSY`競合を減らす
- 外部I/Oをtransaction外に置く設計を強制しやすい

read concurrencyが実際のbottleneckになった場合だけpool sizeを増やします。

connection設定:

- `foreign_keys = ON`
- `journal_mode = WAL`
- `synchronous = FULL`
- `busy_timeout = 5 seconds`
- database作成を明示的に許可する

WALはreaderとwriterの並行性とrestart recoveryを提供します。Control Planeの状態は性能よりdurabilityを優先するため、初期値は`FULL`とします。

### Migrations and queries

- migrationは`control-plane` binaryへembedし、listener開始前に適用する
- stable release前はmigrationの後方互換性を保証しない
- schemaを作り直す破壊的migrationを許容する
- 最初のimplementation candidateはruntime query APIでschemaとcontrol flowを成立させる
- Rust toolchainで最初のbuildが通った後、安定したproduction queryからSQLx checked macroへ移行する
- checked macroを導入した時点で`.sqlx` offline metadataをrepositoryへcommitする
- dynamic SQLが本当に必要な箇所以外ではunchecked queryを残さない
- migration SQLはLFへ固定する

### Storage representation

- UUIDはcanonical lowercase stringとして`TEXT`へ保存する
- timestampはUTC RFC 3339 stringとして`TEXT`へ保存し、application boundaryでJiffの`Timestamp`へ変換する
- byte quantityはSQLiteのsigned `INTEGER`へ保存するため、受理時に`i64::MAX`以下であることを検証する
- enumとcondition reasonはstable stringとして保存し、未知値を黙って別値へ変換しない
- JSON columnはcondition detailsなど構造が本当に可変な箇所に限定し、identity、ownership、schedulingに必要なfieldをJSONへ隠さない

UUIDv7のcanonical stringはtime fieldが先頭にあるため、同じ表現同士のlexicographic orderを作成順の安定したtie-breakとして利用できます。ただし、意味のある並び順には必ず明示的なtimestampとIDの両方を使用します。

### State ownership

SQLite databaseを開くのは`control-plane`だけです。
`control`と`host-agent`はdatabase pathを知りません。

`control-plane`はdatabase fileに対応する`<database>.lock`を開き、process lifetime中はexclusive file lockを保持します。
同じdatabaseを使う二つ目のControl Plane processはlistenerやcontrollerを開始する前に失敗します。lock file自体が残っていることではなく、保持中のOS file lockをownershipの根拠にします。

controllerはSQLx typeへ直接依存せず、`storage` moduleがdomain/resource型との変換を所有します。
ただし、まだ独立crateにはしません。

## Diagnostics

全binaryで`tracing`を使用します。

- daemonのservice modeではnewline-delimited JSONをdefaultとする
- foreground developmentではcompact human-readable outputを選択できる
- filterには`RUST_LOG`互換の`EnvFilter`を使用する
- RPC request、reconciliation、provider operationにはspanを作る
- span fieldには`rpc_id`、`host_claim_id`、`host_id`、`provider_resource_id`を必要に応じて入れる
- secret、credential、cloud-init本文、certificate private keyをlogしない

`println!`と`eprintln!`はCLIのuser-facing output以外では使用しません。

## CLI

`control`は`clap` derive APIを使用します。

初期command:

```text
control system info
control host claim create --vcpus 2 --memory 4GiB --storage 40GiB
control host claim create --id <claim-id> --vcpus 2 --memory 4GiB --storage 40GiB
control host claim get <claim-id>
control host claim list
control host claim delete <claim-id>
control host get <host-id>
control host list
```

CLIはhuman-readable outputをdefaultとし、すべてのquery commandで`--output json`を提供します。
`parse-size`をClap value parserとして使用し、SIとIECのsuffixを明確に区別します。documentとexampleでは`MiB`、`GiB`などIEC単位を標準とし、RPC wireではcanonical integer bytesへ変換します。

CLIはbusiness ruleを実装しません。入力形式として検出できるerrorだけをclient側で拒否し、resourceの受理可否はControl Planeが判断します。

## IDs and timestamps

- resource ID: UUIDv7
- JSON-RPC request ID: UUIDv7 string
- provider resource ID: providerから得たopaque string
- timestamp wire format: UTC RFC 3339 string
- duration wire format: unitをfield名で明示したinteger、または必要になった時点で別途定義する

UUIDv7はtime-orderedでdatabase keyとlog correlationに適していますが、IDからsecurity情報を推測できるものとして扱いません。

## Error handling

- module boundaryのexpected errorは`thiserror` enumで表現する
- `anyhow`はbinary startup、configuration、top-level shutdownのcontextに限定する
- controllerがretry判断をstring matchingで行わない
- provider errorは少なくとも`Transient`、`Permanent`、`OutcomeUnknown`へ分類する
- RPC handlerはinternal errorをそのままclientへ露出しない
- panicはprogramming bugとして扱い、request errorの表現に使用しない

## Initial quality gates

最初のworkspace作成後は、少なくとも次を通すことを目標とします。

```text
cargo fmt --all -- --check
cargo check --workspace --all-targets
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
```

workspace lint policy:

- own codeの`unsafe_code`は禁止
- Clippy `all`を有効にする
- `pedantic`は一括有効化せず、価値のあるlintを個別に追加する
- public APIのmissing documentationはprotocol libraryから段階的に有効化する

## References

- Rust 1.97 release: <https://blog.rust-lang.org/2026/07/09/Rust-1.97.0/>
- Rust 1.97.1 correction: <https://blog.rust-lang.org/2026/07/16/Rust-1.97.1/>
- Rust 2024 Edition: <https://doc.rust-lang.org/edition-guide/rust-2024/>
- Tokio: <https://docs.rs/tokio/latest/tokio/>
- jsonrpsee: <https://docs.rs/jsonrpsee/latest/jsonrpsee/>
- Hyper HTTP/2: <https://docs.rs/hyper/latest/hyper/server/conn/http2/>
- HTTP/2: <https://www.rfc-editor.org/rfc/rfc9113.html>
- HTTP/3: <https://www.rfc-editor.org/rfc/rfc9114.html>
- tracing: <https://docs.rs/tracing/latest/tracing/>
- clap derive: <https://docs.rs/clap/latest/clap/_derive/>
- parse-size: <https://docs.rs/parse-size/latest/parse_size/>
- SQLx SQLite: <https://docs.rs/sqlx/latest/sqlx/sqlite/>
- SQLite WAL: <https://sqlite.org/wal.html>
- UUIDs: <https://www.rfc-editor.org/rfc/rfc9562.html>
