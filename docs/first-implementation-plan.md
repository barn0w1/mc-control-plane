# First implementation plan

> [!NOTE]
> この文書は実装済みの最初のvertical sliceの記録です。HostClaimのresource quantityとplan selectionは、ADR-0010により将来targetではなくなりました。現在の実装事実は[Current implementation](current-implementation.md)、次のtargetは[HostClaim specification](host-claim-spec.md)を参照してください。

## Objective

最初の実装では、実cloudやHost通信へ進む前に、次のvertical sliceを完成させます。

```text
control
  -> JSON-RPC 2.0
  -> HTTP/2 over Unix domain socket
control-plane
  -> SQLx / SQLite
  -> Host controller
  -> fake provider
```

完成時には、`HostClaim`を作成するとcontrollerが一台の`Host`とfake provider resourceへ収束し、Claimを削除すると安全に解放・削除へ収束します。

詳細なlibraryとtransportの選択は[Implementation foundation](implementation-foundation.md)、resource shapeは[HostClaim specification](host-claim-spec.md)を正本とします。

## Implementation status

この計画のvertical sliceは実装済みです。
ユーザーのRust 1.97.1環境でcompile可能となり、workspace testはすべて成功しています。`cargo fmt`も適用済みです。
strict Clippyで報告された警告は修正済みですが、最新commitに対する再実行結果はまだ共有されていません。

現在実装されている範囲:

- Step 1のworkspace、binary skeleton、shutdown、diagnostics
- Step 2のtyped `system.info`、HTTP/2 Unix socket client/server、request size、timeout、batch/notification拒否
- Step 3のembedded migration、single-connection SQLite、HostClaim/Host persistence、独立fake provider database
- databaseごとのexclusive application-owner lock
- Step 4のHostClaim/Host RPCとCLI
- Step 5のsingle-worker reconciliationと主要fault test
- selected provider planのcreate前永続化
- Claim deletionとcontroller status writeの競合防止
- controller開始前のRPC socket bind、active/stale socketの安全な判定、owned socketだけのcleanup

現在残る仕上げ作業:

- 最新commitでstrict Clippyを再実行する
- `Cargo.lock`を生成してcommitする
- SQLx checked queryへ段階的に移行し、必要なoffline metadataをcommitする
- `POST /rpc`以外がtransport layerで確実に拒否されることをconformance testで確認し、必要ならHTTP middlewareを追加する
- malformed HTTP/JSON-RPCとsocket permissionのintegration testを追加する

## Initial workspace

```text
Cargo.toml

crates/
  control-plane-protocol/
  control-plane/
  host-agent/
  control-cli/
```

workspace共通設定:

```toml
[workspace]
resolver = "3"

[workspace.package]
edition = "2024"
rust-version = "1.97"
```

### Dependency direction

```text
control-plane-protocol
  ^         ^         ^
  |         |         |
control-plane  host-agent  control-cli
```

- protocol packageは他のworkspace packageへ依存しない
- CLIとHost AgentはControl Plane implementationへ依存しない
- `control-plane`内部のstorage、controller、providerは最初はmoduleで分離する

## Step 1: Workspace and process skeleton

### Work

- root Cargo workspaceを作成する
- 四つのpackageを追加する
- workspace package metadata、dependency versions、lint policyを定義する
- 各binaryが`--version`と`--help`を返す
- Tokio runtimeを初期化する
- `tracing` subscriberを初期化する
- `control-plane`がSIGINT/SIGTERMを受信し、CancellationTokenによって正常終了する
- `host-agent`はprocess skeletonだけを持ち、network通信をまだ実装しない

### Acceptance

- `cargo fmt`, `check`, `clippy`, `test`がworkspace全体で成功する
- own codeに`unsafe`がない
- package dependencyが意図した方向だけになっている
- structured logでstartupとshutdownが確認できる
- `host-agent`と`control-cli`が`control-plane` packageへ依存していない

## Step 2: Minimal local JSON-RPC

### Work

- Unix domain socket listenerを実装する
- Hyper HTTP/2 prior-knowledge connectionを実装する
- jsonrpsee Tower serviceをtransportへ接続する
- request body、timeout、concurrencyの上限を設ける
- `control-plane-protocol`でtyped RPC APIを定義する
- 最初のmethodとして`system.info`を実装する
- `control system info`を実装する
- human outputと`--output json`を実装する

`system.info`は少なくとも次を返します。

```text
system name
binary version
Rust version used to build
protocol version
process start time
```

### Protocol restrictions

- `POST /rpc`のみ
- HTTP/2のみ
- one JSON-RPC request per HTTP request
- batchなし
- notificationなし
- object paramsのみ
- UUIDv7 string request ID

### Acceptance

- CLIはRPC以外でdaemon stateへ触れない
- malformed HTTP、oversized body、invalid JSON、invalid JSON-RPCがdaemonをpanicさせない
- unknown methodとinvalid paramsがJSON-RPC errorになる
- RPC errorがCLIのexit statusとhuman/JSON outputへ一貫して変換される
- daemon再起動後にCLIが再接続できる
- Unix socket permissionで許可されていないlocal userを拒否できる構造になっている

## Step 3: SQLite foundation

### Work

- SQLx SQLite bundled driverを追加する
- max connection 1のpoolを構成する
- WAL、FULL synchronous、foreign key、busy timeoutを設定する
- embedded migrationをlistener開始前に実行する
- `HostClaim`と`Host` tableを作成する
- fake provider用に別SQLite databaseを用意する
- 最初のcandidateではruntime query APIを使用し、local compile後にstable queryからchecked queryへ移行する
- database errorをapplication error境界で分類し、internal detailをRPCへ露出しない
- database pathごとのexclusive file lockをprocess lifetime中保持する

### Required constraints

- HostClaim ID primary key
- active Hostは一つのClaimに最大一つ
- provider resource ownership keyとしてHost IDを一意に扱う
- provider create前に、選択済みprovider plan IDをHost内部stateへ永続化する
- generationとobserved generationを非負整数として扱う
- resource quantityは正値に限定する

### Acceptance

- daemon再起動後にresourceを読み戻せる
- migration失敗時にRPC listenerやcontrollerを開始しない
- CLIやHost Agentはdatabase fileを開かない
- transaction中にfake provider I/Oを実行しない
- 同じClaim ID、同じspecのcreateがidempotent
- 同じClaim ID、異なるspecのcreateがconflict

## Step 4: HostClaim RPC

### Work

- `host.claim.create`
- `host.claim.get`
- `host.claim.list`
- `host.claim.delete`
- `host.get`
- `host.list`
- Claim condition model
- asynchronous deletion timestampとfinal deletion

### CLI

```text
control host claim create --vcpus 2 --memory 4GiB --storage 40GiB
control host claim create --id <claim-id> --vcpus 2 --memory 4GiB --storage 40GiB
control host claim get <claim-id>
control host claim list
control host claim delete <claim-id>
control host get <host-id>
control host list
```

### Acceptance

- resource quantityがwireではcanonical bytesになる
- create responseを失った想定で同じIDを再送してもduplicateができない
- unsatisfiable Claimを保存し、`Accepted=False`として表示できる
- delete request後もcleanup完了までClaimを観測できる
- list orderがdeterministicである

## Step 5: Host reconciliation with fake provider

### Controller loop

- persisted Claim/Host stateをlevel-triggeredに観測する
- RPC mutation時の`Notify`とperiodic scanの両方でreconcileをwakeする
- 初期実装はcontroller workerを一つにし、同じresourceの並行処理を構造的に避ける
- 一回のreconcileで長いsleepをしない
- retry attempt、next reconcile time、last classified errorを永続stateとして持つ
- 一回のreconciliationで外部mutationを最大一つにする
- external resultをtransaction外で取得し、次のtransactionで反映する

### Fake provider

fake providerは別SQLite fileに次を持ちます。

- plan catalog
- provider resources
- Host ID ownership key
- lifecycle state
- capacityとhourly price
- fault injection state

### Reconciliation flow

1. deletionされていない未充足Claimを読む
2. provider policy/catalogでplanを選択する
3. Control Plane Host ID、選択plan ID、allocatable capacityを一つのHost内部stateとして保存する
4. fake providerへ永続化済みHost IDとplan IDを渡してcreateする
5. create結果に応じて観測またはretryする
6. provider resource ReadyをHost statusへ反映する
7. Host ReadyをClaim statusへ反映する
8. deletion ClaimではHost/provider resource削除を進める
9. provider不存在確認後にHostとClaimをfinalizeする

### Required fault tests

- create前のdefinitive failure
- create成功後のresponse loss
- delete成功後のresponse loss
- temporary observation failure
- daemon stop between every persisted transition
- repeated reconciliation
- provider create前にplan選択が永続化されていること
- stale controller statusがconcurrent Claim deletionを取り消せないこと
- 同じdatabaseを二つのControl Plane processが同時所有できないこと
- active Unix socketを起動時にunlinkしないこと

### End-to-end acceptance scenario

1. `control host claim create`を二回実行する
2. 二つのClaim、Host、provider resourceがReadyになる
3. `control-plane`を再起動する
4. duplicate Host/provider resourceを作らず同じ状態へ収束する
5. 一つのClaimをdeleteする
6. 対応Hostとprovider resourceが削除され、Claimがfinalizeされる
7. 残るClaimとHostには影響しない
8. create/delete response lossを注入しても同じ最終状態へ収束する

## First implementation completion

architectureとfunctional behaviorについては、次の項目を実装済みです。

- Rust 1.97 / Edition 2024の四package workspaceが成立する
- Tokio process lifecycleとstructured diagnosticsが成立する
- HTTP/2 over Unix socket上でtyped JSON-RPCが通る
- SQLx/SQLiteからstateを復旧できる
- HostClaim specとdelete lifecycleが実装される
- HostClaimからHostへのlevel-triggered reconciliationが動く
- fake providerがControl Plane stateから独立している
- restartと`OutcomeUnknown`を自動testできる
- Linode adapterとHost Agent communicationを後から追加できる境界がある

## Explicitly deferred

- Linode API integration
- `host-agent` enrollmentと通信
- TLS/mTLSとprivate PKI
- durable Host command delivery
- idle Host reuseとbilling-aware retention
- mutable HostClaim spec
- restic、object storage、Minecraft workload
- OpenRPC document生成
- remote operator API
