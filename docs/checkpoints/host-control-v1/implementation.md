# Host Control System v1 implementation

この文書は、現在のRust codebaseで実装済みの範囲と、checkpoint完了までの次の実装境界をまとめます。

## Current status

最初のlocal vertical sliceは実装済みです。

```text
control
  -> JSON-RPC 2.0
  -> HTTP/2 prior knowledge
  -> Unix domain socket
control-plane
  -> SQLx / SQLite
  -> single-worker Host controller
  -> independent fake provider
```

`HostClaim`を作成すると、Control Planeがprovider planを選択し、論理`Host`とfake provider resourceを作成して`Ready`へ収束させます。
Claimを削除すると、provider resourceの不存在を確認した後にHostとClaimをfinalizeします。

これはcheckpointのfoundationであり、Host Control System v1の完成ではありません。

## Engineering foundation

### Rust

- Rust 1.97 series
- Edition 2024
- `rust-version = "1.97"`
- latest available 1.97.x patchを使用

### Workspace

```text
crates/
  control-plane-protocol/
  control-plane/
  host-agent/
  control-cli/
```

- `control-plane-protocol`: RPC wire contractだけを共有
- `control-plane`: daemon、RPC、SQLite、controller、fake provider
- `host-agent`: 現在はprocess skeleton
- `control-cli`: thin local RPC client

初期段階で`core`、`domain`、`storage`などのcrateを先回りして増やしません。

### Initial libraries

- Tokio / tokio-util
- Hyper / hyper-util / Tower
- jsonrpsee
- tracing / tracing-subscriber
- clap
- SQLx with bundled SQLite
- serde / serde_json
- uuid UUIDv7
- Jiff
- thiserror / anyhow

### Local RPC

```text
JSON-RPC 2.0
  over HTTP/2 prior knowledge
  over Unix domain socket
```

- request/responseのみ
- batch disabled
- notification rejected
- request body最大1 MiB
- response body最大4 MiB
- connection上限64
- request timeout 30秒
- Unix socket modeによるlocal access control

起動時にはactive socketを削除せず、connection refusedとなるstale socketだけを削除します。
shutdown時も自分がbindしたinodeのsocketだけを削除します。

### SQLite

- bundled SQLite
- WAL
- `synchronous = FULL`
- foreign keys enabled
- busy timeout 5秒
- pool size 1
- embedded migrations
- STRICT tables
- process lifetimeのexclusive file lock

External I/O中にSQLite transactionを保持しません。

## Current operator interface

```text
control system info

control host claim create --vcpus <n> --memory <size> --storage <size>
control host claim create --id <uuid> --vcpus <n> --memory <size> --storage <size>
control host claim get <claim-id>
control host claim list
control host claim delete <claim-id>

control host get <host-id>
control host list
```

現在のCLIとresource modelは、Akamai-native targetへ移行する前の検証用modelです。

## Current resources

### HostClaim

現在は次のresource quantityを持ちます。

```text
spec.resources
  vcpus
  memory_bytes
  storage_bytes
```

- caller-generated UUIDv7
- 同じID・同じspecはidempotent
- 同じID・異なるspecはconflict
- deleteは`deletion_timestamp`を設定

### Host

- Host IDはprovider resource IDと別
- 一つのClaimに最大一つ
- provider create前にplan IDとcapacityを保存
- phaseは`pending`、`provisioning`、`ready`、`deleting`、`failed`
- provider observation、現在のtest用retry schedule、分類済みerrorを永続化

## Current reconciliation behavior

現在のcontrollerは単一workerです。

- RPC mutation時の`Notify`
- periodic safety scan
- 一回のwakeで最大64 step
- stable ID order
- deletion優先
- provider I/Oはtransaction外
- 現在の検証用実装ではretry attemptと次回時刻を永続化

これらは現在のscheduler mechanismであり、target architectureのpublic conceptではありません。
将来もControllerは常時責任を持つcontrol loopとして動作し、notification lossからperiodic observationで回復します。
一方、予測不能なAkamai API errorを自動retryするtargetにはしません。共通のFatal Incidentへ昇格し、affected scopeのmutationを停止します。

### Create flow

1. Claimを永続化
2. fake catalogからplanを選択
3. Host ID、plan ID、capacityを永続化
4. Host IDでprovider resourceを観測
5. 存在しなければcreate
6. observationをHostとClaim statusへ反映

### Delete flow

1. Claimへ`deletion_timestamp`を設定
2. assigned Hostをprovider上で観測
3. resourceが存在すればdeleteを要求
4. 後続のobservationで不存在を確認
5. HostとClaimを削除

### Unknown outcomes

- create response loss後はHost IDで再発見
- delete response loss後は不存在を再確認
- observation failureは現在のtest用retry pathへ移す
- definitive failureは現在のHostを`failed`にする
- ownership矛盾時は破壊操作を停止

このbehaviorは初期reconciliation foundationの検証用です。Akamai-native modelへ移行するとき、予測不能なexternal failureは共通のFatal Incidentへ置き換えます。

## Fake provider

fake providerはControl Plane databaseとは独立したSQLite databaseです。
現在はdeterministic plan selection、resource create/observe/delete、failure injectionを実装しています。

これはAkamai integration前に、Akamai Type ID、raw Linode status、ownership、account inventoryを再現するfakeへ置き換えます。

## Verification

ユーザーのRust 1.97.1環境で、workspace testはすべて通過しています。
`cargo fmt`も適用済みです。strict Clippyで報告された警告も修正されています。

現在のtestは主に次を検証します。

- generic JSON-RPC response
- Claim createのidempotencyとconflict
- invalid resource rejection
- SQLite single-owner lock
- stale status update防止
- provider plan固定
- active/stale Unix socket処理
- 複数Claim
- definitive create failure
- transient observation failure
- create/delete response loss
- daemon restart後のduplicate防止

## Target model gap

Akamai integration前に、現在の検証用domain modelを破壊的に置き換えます。

- `HostResources` -> `LinodeTypeId`
- automatic plan selection -> removed
- CLI -> `control host claim create --type <type-id>`
- generic provider wording -> Akamai/Linode-specific boundary
- fake catalog -> fake Akamai Type/instance/status model
- raw Linode observationを追加
- current retry/error fieldsをcommon Incident modelへ置換
- forced cleanupをControl Planeのscopeから削除

Compatibility migrationは提供しません。

## Next implementation sequence

### Slice 1: Akamai-native local model and Incident foundation

- HostClaimをexact Linode Type IDへ変更
- database migrationを作り直す
- CLIとRPCを`--type`へ変更
- raw Linode statusとidentity modelを追加
- common Incident tableとtyped Fatal Incidentを追加
- Incident list/get/acknowledge/resolve RPCの最小contractを追加
- fakeをAkamai-specific contractへ変更
- existing failure testsを書き換える

### Slice 2: Real Akamai observation

- typed deployment configuration
- Akamai API token loading
- Linode Type lookup
- owned inventory listing
- instance observation
- read-only integration tests

この段階では実Linodeを作成しません。

### Slice 3: Linode provisioning lifecycle

- create intent persistence
- ownership label/tag
- Linode create
- mutation outcome unknownをFatal Incidentとして停止
- normal deleteと不存在確認
- Akamai API failureをFatal Incidentへ変換
- real-account acceptance

### Slice 4: Host Agent identity and readiness

- Host transport
- enrollment
- mTLS
- identity binding
- health/capability observation
- `running`と`Ready`の分離

### Slice 5: Idle reuse

- release/idle state
- compatibility rule
- sanitization
- retention policy
- Fatal Incidentが存在するHostを再利用しないrule

各sliceの詳細仕様は、実装を開始する直前に[Open questions](open-questions.md)から必要な項目だけ決定します。

## Deferred beyond this checkpoint

- Minecraft lifecycle
- workload deployment
- restic、backup、restore
- persistent data
- external cost monitoringまたはforced cleanup program
- remote user interface
- multi-node Control Plane
