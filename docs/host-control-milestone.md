# Host Control System milestone

## Decision

中期checkpointは、**Host Control System v1**を完成させることです。

これはMinecraft機能へ進むためだけの途中成果ではありません。上位layerから独立して利用できる、Akamai Cloud上のGNU/Linux実行環境を宣言的に管理する一つの完成したsubsystemです。

```text
HostClaim
    |
    v
Host Control System
    |
    +-- HostController
    +-- Host Agent control
    +-- CostController
    +-- Akamai Cloud integration
    |
    v
Akamai Cloud Linode + managed GNU/Linux environment
```

Host Control System v1が完成するまでは、Minecraft、Workload、Data、Snapshotなどの上位layerを主要実装対象にしません。

## What a Host is

`Host`は、workloadを実行する土台となる、一つの管理されたGNU/Linux実行環境です。

このprojectのHost Control System v1では、実体を一つのAkamai Cloud Linodeに固定します。
VM以外の実装や他cloudへ共通化するための抽象化は作りません。

```text
HostClaim 1 ── 1 Host ── 1 Linode
```

- 一つのHostClaimは一台の排他的Hostを要求する
- 複数Claimを一台へpackingしない
- 一台のHostを複数workloadへ分割するschedulerはHost layerの責務ではない
- Host ID、Linode ID、Host Agent identityは別のidentityとして管理する

## External contract

Host Control Systemへの主要な入力は`HostClaim`です。

```text
HostClaim.spec.type
  exact Akamai Cloud Linode Type ID
```

上位layerは、Linode APIの操作手順、Linode ID、Firewall、VPC、image、cloud-initを指定しません。
正確なLinode Type IDだけを要求し、その他の共通設定はdeployment-levelのtyped configurationが所有します。

Host Control Systemは、少なくとも次を外部へ提示します。

- HostClaimの作成、取得、一覧、削除
- Hostの取得、一覧
- raw Linode observation
- 抽象的なHost readinessとlifecycle condition
- terminalまたはCritical errorのreasonとoperator向け説明
- CostControllerによる処分状態

すべてのoperator interfaceは`control-plane`へのRPC clientとして動作します。databaseやAkamai APIを直接操作しません。

## Responsibilities

Host Control System v1は、Host lifecycle全体を所有します。

### Demand and allocation

- HostClaimを永続化する
- Claimごとに一つのHost identityを割り当てる
- 指定されたLinode Type IDを検証する
- 複数Claimが存在する場合は同数のHostを管理する

### Provisioning

- fixed region、network、Firewall、image、metadataなどのdeployment policyを適用する
- ownership metadataを持つLinodeを作成する
- Linodeのraw statusを継続的に観測する
- GNU/Linux bootstrapとHost Agentの起動を確認する

### Host identity and readiness

- Host AgentをHost identityへ安全にbindingする
- Host Agentの認証、version、boot identity、healthを観測する
- Linodeが`running`であるだけでは`Ready`にしない
- 上位layerへ渡せる状態をHost conditionとして提示する

### Autonomous control

- HostControllerはdaemon稼働中に継続責任を持つlevel-triggered control loopとして動作する
- eventやnotificationが失われてもperiodic observationから状態を再構築する
- daemon、Host、networkの再起動を通常の動作として扱う
- operatorが明示的にreconcileやretryを起動しなくても管理を継続する

### Release, reuse, and normal deletion

- Claim解放後のHostを通常lifecycleで解放する
- policyに従って短時間Idleとして保持できる
- compatibleな新しいClaimへ安全に再利用できる
- 再利用前に必要なsanitizationを実行・検証する
- 不要なLinodeをownership確認後に削除する
- delete成功応答だけでなく、Akamai inventory上の不存在を確認する

### Critical state and cost control

HostControllerは、解決不能な外部不整合に対して複雑な自己修復を行いません。
bounded observationで正常状態を確認できなければ、terminalまたはCriticalとして通常mutationを停止します。

Host Control System v1には、HostControllerとは別責務のCostControllerを含めます。

- 所有している課金対象Linodeをaccount inventoryから継続監視する
- terminal/Critical状態がpolicy上のgrace periodを超えた場合に検出する
- active Claimや保護holdがないresourceだけをforce-delete対象にする
- 削除結果を観測し、不存在を確認する
- 削除不能、ownership不明、結果不明が解決しない場合はCriticalとして人間へ提示する

「完全に管理する」とは、あらゆる故障を自動修復することではありません。
正常lifecycleを自動化し、解決不能な状態を見失わず、課金resourceを安全なpolicyの範囲で処分し、人間が対応できる情報を残すことを意味します。

## Completion criteria

Host Control System v1は、次を実際のAkamai Cloud環境で確認した時点で完成とします。

1. `control`から正確なLinode Type IDを持つ複数のHostClaimを作成できる
2. 各Claimに対して一台のLinodeが作成され、別のHost identityとして追跡される
3. fixed deployment configurationがすべてのLinodeへ一貫して適用される
4. `host-agent`がbootstrapされ、認証され、Host statusを継続報告する
5. Linode statusとHost statusが別々に観測・表示される
6. Hostが上位layerへ渡せる場合だけ`Ready`になる
7. Control Plane、Host Agent、Linode、networkの再起動や一時停止後に自律的に再収束する
8. Claim解放後にHostをIdleとして保持し、compatible Claimへ再利用できる
9. 再利用できない、または保持期限を超えたHostを正常lifecycleで削除できる
10. 削除後にAkamai inventory上の不存在を確認してからfinalizeする
11. create/deleteの不明結果やownership不一致をbounded observation後にCriticalとして停止できる
12. CostControllerがpolicy対象のstale billing resourceを検出・処分できる
13. 削除不能などのCritical状態を、resource identityと原因を含めてoperatorへ提示できる
14. 通常経路ではSSHやCloud Managerによる手作業を必要としない
15. 上記の主要failure pathをfakeと実環境のacceptance testで再現できる

## Explicit non-goals

Host Control System v1には次を含めません。

- Minecraft server lifecycle
- workload deploymentやcontainer orchestration
- persistent data、restic、backup、restore
- 複数Claimの一台へのpacking
- 他cloud providerへの対応
- provider plugin system
- physical machine provisioning
- 高可用な複数Control Plane node
- あらゆるCritical errorの自動修復

Data layerが追加された後は、CostControllerの削除条件へ`DataProtectionHold`を統合します。
backupが成功または不要と確認できない課金resourceは、costだけを理由に削除しません。

## Relationship to the current implementation

現在のRust実装は、local RPC、SQLite、HostClaim、Host、single controller、fake providerによるdurable reconciliationを検証したfoundationです。

まだHost Control System v1ではありません。特に次が未実装です。

- Akamai Cloud adapter
- Linode Type IDベースのtarget model
- Host Agent通信とidentity
- raw Linode status observation
- Idle、reuse、sanitization
- CostController
- 実環境acceptance

次の実装は、このmilestoneを小さなvertical sliceへ分解して進めます。各詳細仕様は、そのsliceを実装する直前に決定します。
