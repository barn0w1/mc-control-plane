# First implementation plan

## Objective

最初の実装では、実cloudやHost通信へ進む前に、次の基盤を一つのvertical sliceとして成立させます。

```text
control
  -> JSON-RPC 2.0
control-plane
  -> SQLite
  -> Host controller
  -> fake provider
```

完成時には、`HostClaim`を作成するとcontrollerが`Host`を生成し、Claimを削除すると要求された状態へ再び収束します。

## Initial workspace

```text
Cargo.toml

crates/
  control-plane-protocol/
  control-plane/
  host-agent/
  control-cli/
```

### `control-plane-protocol`

RPC boundaryに現れる共有型だけを所有するlibrary packageです。

- JSON-RPC methodのparamsとresult
- wire上に現れるIDとerror data
- protocol/build information

controller、database entity、provider type、business ruleは置きません。
JSON-RPC parserやdispatcherは、利用可能な標準準拠libraryを優先し、独自実装しません。

### `control-plane`

`control-plane` binaryを生成します。

- RPC server
- configurationとlifecycle
- SQLite migrationsとpersistence
- HostClaimとHost
- Host controller
- fake provider

初期段階ではstorage、controller、providerを別crateへ分割せず、このpackage内のmoduleにします。

### `host-agent`

`host-agent` binaryを生成します。
最初のvertical sliceではversion表示と最小のprocess skeletonだけを持ち、Control Planeとの通信はまだ実装しません。
別Hostへ配布されるsecurity boundaryであるため、最初から独立packageにします。

### `control-cli`

`control` binaryを生成する薄いRPC clientです。

- command-line parsing
- RPC request
- human-readable output
- machine-readable JSON output
- exit status

business rule、database、provider integrationを持ちません。

## Step 1: Workspace and process skeleton

- root Cargo workspaceを作成する
- 四つのpackageを追加する
- 各binaryが`--version`と`--help`を返す
- common package metadataと最小のlint policyを設定する
- `control-plane`が起動、signal受信、正常終了できる

Acceptance:

- workspace全体をbuild、format、lint、testできる
- package間のdependencyが意図した方向だけになっている
- `host-agent`が`control-plane`内部moduleへ依存していない

## Step 2: Minimal local RPC

- JSON-RPC 2.0 request/responseを使用する
- local transportはHTTP request/responseをUnix domain socket上で運ぶ
- 独自message framingは作らない
- 最初のmethodとして`system.info`を実装する
- `control system info`からdaemonのbuildとprotocol informationを表示する

Acceptance:

- CLIはRPC以外でdaemon stateへ触れない
- malformed requestがdaemonをpanicさせない
- JSON-RPC errorがCLIのexit statusと表示へ一貫して変換される
- daemon再起動後にCLIが再接続できる

このlocal transportの選択はHost transportを決定するものではありません。

## Step 3: Persistence and minimal resources

- SQLiteを`control-plane`だけが開く
- development中は破棄可能な初期migrationを作る
- `HostClaim`と`Host`の最小spec/statusを定義する
- Claimのcreate、get、list、delete RPCを追加する
- Hostのget、list RPCを追加する
- spec変更とstatus観測を区別する

Acceptance:

- daemon再起動後もClaimとHostを読み戻せる
- CLIや`host-agent`がdatabase fileを開かない
- database constraintで明確な不変条件を保護する
- schema migrationの後方互換性は提供しない

最小fieldはこのstepの実装直前に決めます。将来を予測してresourceを増やしません。

## Step 4: Host reconciliation with a fake provider

- controllerが保存済みHostClaimを継続的に観測する
- 未充足Claimに対してHostを割り当てる
- fake providerが外部resourceの作成、観測、削除を模擬する
- fake providerの状態はControl Planeのresource tableとは独立して観測できるようにする
- Claim削除時は最初の実装では即時削除へ収束する
- idle保持と再利用は後続stepで追加する

Acceptance scenario:

1. `control host claim create`を二回実行する
2. 二つのHostClaimと二つのHostがReadyになる
3. `control-plane`を再起動する
4. duplicate Hostを作らず同じ状態へ収束する
5. 一つのClaimを削除する
6. 対応するHostが削除状態へ収束する
7. 残るClaimとHostには影響しない

追加tests:

- reconcileを何度実行しても余分なHostを作らない
- create結果不明を模擬した後に再観測して重複を避ける
- delete結果不明を模擬した後に不存在を確認する
- daemonを主要なstate transition間で再起動できる
- 複数Claimを決定的に扱える

## First implementation complete

次を満たした時点で、最初の実装を完了とします。

- 四packageの境界が機能している
- `control`と`control-plane`の最小RPCが通る
- SQLiteからstateを復旧できる
- HostClaimからHostへのlevel-triggered reconciliationが動く
- fake providerでrestartとoutcome-unknownをtestできる
- Linode、Host通信、mTLS、idle reuseを追加できる明確なextension pointがある

## Explicitly deferred

- Linode API integration
- `host-agent` enrollmentと通信
- mTLSとprivate PKI
- durable Host command delivery
- idle Host reuseとbilling-aware retention
- restic、object storage、Minecraft workload
- OpenRPC生成
- remote operator API
