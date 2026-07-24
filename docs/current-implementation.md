# Current implementation

この文書は、現在のRust codebaseで**実際に実装されている範囲**をまとめます。
将来の構想ではなく、`main`のcodeとtestを基準にしています。

## Summary

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

これはHost control milestone全体の完成ではありません。Linode、Host Agent通信、Host再利用はまだ含まれません。

## Workspace

```text
crates/
  control-plane-protocol/
  control-plane/
  host-agent/
  control-cli/
```

### `control-plane-protocol`

RPC境界で共有する型だけを所有します。

- UUIDv7の`HostClaimId`と`HostId`
- `HostClaim`、`Host`、resource quantity、Condition
- RPC method名、params、result、error data
- protocol version

persistence、controller、providerの内部型は公開しません。

### `control-plane`

現在の実装の中心です。

- configurationとstructured logging
- process lifecycleとgraceful shutdown
- Unix socket RPC server
- SQLite persistence
- Host controller
- fake provider

### `control-cli`

`control` binaryです。Control Plane databaseやproviderには直接触れず、すべてlocal RPC経由で操作します。

### `host-agent`

現時点ではprocess skeletonだけです。

- CLI parsing
- structured logging
- protocol version表示
- SIGINT/SIGTERMによるgraceful shutdown

Control Planeとの通信、enrollment、mTLS、Host操作は未実装です。

## Available operator interface

現在のCLIは次を提供します。

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

human-readable outputとJSON outputを持ちます。
MemoryとstorageのCLI inputは`GiB`などを受け付け、wire上ではbytesになります。

## Local RPC

現在のlocal operator RPC profile:

- JSON-RPC 2.0
- HTTP/2 prior-knowledge connection
- Unix domain socket
- request/responseのみ
- batch disabled
- notification rejected
- request body最大1 MiB
- response body最大4 MiB
- connection上限64
- request timeout 30秒
- Unix socket modeによるlocal access control

起動時には既存socketを検査します。

- active socketは削除せず、二重起動を拒否する
- connection refusedとなるstale socketだけを削除する
- startup中にpathが置換された場合は削除しない
- shutdownでは自分がbindしたinodeのsocketだけを削除する

## Persistent resources

### HostClaim

上位layerから提示される、一台の排他的Hostへの永続需要です。

```text
spec.resources
  vcpus
  memory_bytes
  storage_bytes
```

createはcaller-generated UUIDv7を使用します。

- 同じID・同じspecはidempotent
- 同じID・異なるspecはconflict
- deleteは即時row削除ではなく`deletion_timestamp`を設定

statusは`Accepted`、`Bound`、`Ready` Conditionと割当Host IDを持ちます。

### Host

Control Planeが所有する論理Hostです。

- Host IDはprovider resource IDと別
- 一つのClaimに最大一つ
- provider create前にplan IDとallocatable resourcesを保存
- phaseは`pending`、`provisioning`、`ready`、`deleting`、`failed`
- provider observation、retry schedule、分類済みerrorを永続化

provider plan IDは内部stateであり、public Host resourceには公開していません。

## SQLite behavior

Control Plane stateとfake provider stateは別SQLite fileに保存します。

Control Plane database:

- bundled SQLite
- WAL
- `synchronous = FULL`
- foreign keys enabled
- busy timeout 5秒
- pool size 1
- embedded migration
- STRICT tables
- process lifetimeのexclusive file lock

主なdatabase保証:

- HostClaim IDの一意性
- 一つのClaimにHostは最大一つ
- provider resource IDの一意性
- Host削除はClaim削除へcascade
- resource quantityとgenerationの基本的なCHECK constraint

RPCやcontrollerは同じ`Storage` abstractionを使用し、CLIはdatabaseを開きません。

## Reconciliation model

Host controllerは一つのworkerで動きます。現在の`Notify`、wake limit、retry scheduleは最初のvertical sliceを成立させるための内部実装です。完成形ではControllerを常時責任を持つcontrol loopとして説明し、これらのscheduler detailをpublic resource modelへ露出させません。

現在の実装mechanism:

- RPC mutation時の`Notify`
- periodic safety scan
- 一回のwakeで最大64 step
- stable ID orderでresourceを処理
- deletionを通常reconciliationより優先
- external provider I/OはSQLite transaction外
- retry attemptと次回時刻をSQLiteへ保存
- retry delayは指数的に増え、最大60秒

### Claim creation flow

1. Claimを永続化する
2. fake provider catalogから要求を満たすplanをdeterministicに選ぶ
3. Host ID、plan ID、allocatable capacityを永続化する
4. provider上でHost IDをownership keyとしてresourceを観測する
5. resourceがなければcreateする
6. provider resourceを観測し、HostとClaim statusへ反映する

plan選択結果をcreate前に保存するため、再起動後に別planへ選び直しません。

### Claim deletion flow

1. Claimへ`deletion_timestamp`を設定する
2. assigned Hostをprovider上で観測する
3. resourceが存在すればdeleteを要求する
4. delete成功応答だけではfinalizeしない
5. 後続のobservationで不存在を確認する
6. HostとClaimを削除する

### Unknown outcomes

provider mutationの結果が不明な場合、同じmutationを直ちに繰り返しません。

- create response loss後はHost IDで再発見する
- delete response loss後はHost IDで不存在を再確認する
- observationの一時障害は永続retryへ移す
- definitive failureはHostを`failed`にする
- provider ownershipが矛盾した場合は破壊操作を止める

## Fake provider

fake providerはControl Plane databaseとは独立したSQLite databaseです。

実装済み:

- deterministic plan selection
- provider resourceのHost ID ownership
- idempotent create
- delete
- observation
- test-only fault injection

現在のfake plan catalog:

- 2 vCPU / 4 GiB / 40 GiB
- 4 vCPU / 8 GiB / 80 GiB
- 8 vCPU / 16 GiB / 160 GiB

fake providerではcreateされたresourceは即座に`Ready`になります。実cloudの非同期provisioningはまだ再現していません。

## Tests and verification

source treeには20個のRust testがあります。
主に次を検証します。

- generic JSON-RPC response decoding
- Claim createのidempotencyとconflict
- resource validation
- databaseの単一application owner
- deletion requestとstale status writeの競合
- provider plan/capacityの固定
- active/stale/replaced Unix socketの安全性
- 複数Claimのreconciliation
- definitive create failure
- transient observation failure
- create/delete response loss
- daemon restart後のduplicate防止

ユーザーのRust 1.97.1環境で`cargo test --workspace --all-features`は成功しています。
`cargo fmt`も適用済みです。strict Clippyで報告された警告はcode上で修正済みですが、最新commitに対する再実行結果はまだ共有されていません。

## Important limitations

現在は次を実装していません。

- Linode/Akamai Cloud adapter
- Host Agentとの通信
- Host enrollment、mTLS、certificate lifecycle
- Host Agent command protocolとlocal journal
- 実Hostのhealth、capability、boot identity
- idle Host pool、再利用、sanitization
- billing-aware retention
- operator retryやFailed Host recovery API
- mutable HostClaim spec
- paginationやwatch/stream API
- remote operator RPC
- OpenRPC document生成
- SQLx checked query/offline metadata
- restic、object storage、Minecraft workload

したがって、現在達成しているのは、**Host control architectureのlocal simulationとdurable reconciliation foundation**です。

Host packingは現在も将来もscope外です。一つのHostClaimは一つの排他的Hostを要求します。Host managementの完成形に関する方向は[Host management direction](host-management-direction.md)を参照してください。
実際のHostを完全にcontrolする中期milestoneは未完了です。

## Next implementation boundary

次の大きな段階はLinode adapterまたはHost Agent通信ですが、同時には進めません。
先に現在のsliceを締める作業として、次を完了させます。

1. 最新commitでstrict Clippyを再実行する
2. `Cargo.lock`を生成してcommitする
3. CLIからdaemonを起動して行うRPC end-to-end testを追加する
4. malformed HTTP/JSON-RPCとsocket permissionのtransport testを追加する
5. Failed Hostの回復方針を決定する

その後、Linode adapterを追加し、fake providerで証明したHost lifecycleを実resourceへ接続します。
