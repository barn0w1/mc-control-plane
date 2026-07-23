# Roadmap

## Phase 0: Foundation documents

Status: **Complete**

- 新しいsystem boundaryと用語を定義する。
- ADRを作り直す。
- Python prototypeをGit historyへ移す。
- Host control checkpointのacceptance criteriaを固定する。
- 未確定事項をopen questionとして分離する。

## Phase 1: Rust workspace and process skeleton

- Cargo workspaceを作成する。
- `mccp-core`、`mccp-rpc`、`mccpd`、`mccp-hostd`、`mccpctl`を用意する。
- build、fmt、clippy、unit test、dependency auditのCI方針を決める。
- `mccpd`のgraceful startup/shutdown、configuration、structured loggingを実装する。
- Unix socket上の最小RPCで`mccpctl system get-info`を通す。

## Phase 2: Resource and persistence foundation

- typed ID、metadata、generation、conditionを実装する。
- SQLite migrationとtransaction boundaryを実装する。
- HostClass、HostClaim、Host、HostAllocation、ProviderResource、Activityを保存する。
- controller scheduling、lease、retryを実装する。
- process restart scenario testを作る。

## Phase 3: Linode Host provisioning

- Linode preflight、create、discover、observe、deleteを実装する。
- ownership identityとsafe deletionを実装する。
- OutcomeUnknown recoveryをscenario testする。
- HostClaimから必要数のHostが確保されることを確認する。

## Phase 4: Host identity and communication

- `mccp-hostd` bootstrapを実装する。
- enrollment、private key生成、certificate発行、mTLSを実装する。
- Host observationとdurable command exchangeを実装する。
- command journal、replay protection、fencingを実装する。

## Phase 5: Allocation, reuse, and billing-aware retention

- compatible Host selectionを実装する。
- release、sanitization、Idle、reallocationを実装する。
- billing-aligned retentionとsafe deletionを実装する。
- 複数HostClaimによる複数Host確保を検証する。

## Phase 6: Host control checkpoint acceptance

- `mccpctl`だけでend-to-end acceptanceを実行する。
- `mccpd`、`mccp-hostd`、Host reboot、network interruptionを挟む。
- SSHなしで観測・回復・削除できることを確認する。
- 実account結果をdocumentとtestへ戻す。

## Later layers

Host checkpoint完了後に、次を独立layerとして設計します。

1. Data and snapshot management
2. Workload management
3. Minecraft server and session management
4. User-facing automation interfaces

後段機能の都合でHost subsystemのownershipを崩さないことを優先します。
