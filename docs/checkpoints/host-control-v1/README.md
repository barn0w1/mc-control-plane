# Host Control System v1

- Status: Active checkpoint
- Scope: Akamai Cloud上の管理されたGNU/Linux Host

## Goal

中期checkpointは、**Host Control System v1**を完成させることです。

これはMinecraft機能へ進むためだけの途中成果ではありません。上位layerから独立して利用できる、Akamai Cloud上のGNU/Linux実行環境を宣言的に管理する一つの完成したsubsystemです。

```text
HostClaim
    |
    v
Host Control System
    |-- HostController
    |-- Host Agent control
    `-- Akamai Cloud integration
    |
    v
Akamai Cloud Linode + managed GNU/Linux environment
```

Host Control System v1が完成するまでは、Minecraft、Workload、Data、Snapshotなどの上位layerを主要実装対象にしません。

## What a Host is

`Host`は、workloadを実行する土台となる、一つの管理されたGNU/Linux実行環境です。
Host Control System v1では、その実体を一つのAkamai Cloud Linodeに固定します。

```text
HostClaim 1 -- 1 Host -- 1 Linode
```

- 一つのHostClaimは一台の排他的Hostを要求する
- 複数Claimを一台へpackingしない
- 一台のHostを複数workloadへ分割するschedulerはHost layerの責務ではない
- Host ID、Linode ID、Host Agent identityは別のidentityとして管理する
- 他cloudやphysical machineへ共通化する抽象化は作らない

## External contract

主要な入力は`HostClaim`です。

```text
HostClaim.spec.type
  exact Akamai Cloud Linode Type ID
```

上位layerはLinode APIの操作手順、Linode ID、Firewall、VPC、image、cloud-initを指定しません。
正確なLinode Type IDだけを要求し、その他の共通設定はdeployment-levelのtyped configurationが所有します。

Host Control Systemは、少なくとも次を外部へ提示します。

- HostClaimの作成、取得、一覧、削除
- Hostの取得、一覧
- raw Linode observation
- 抽象的なHost readinessとlifecycle condition
- Fatal Incidentのidentity、scope、reason、operator向け説明

すべてのoperator interfaceは`control-plane`へのRPC clientとして動作します。databaseやAkamai APIを直接操作しません。

## Responsibilities

### Demand and provisioning

- HostClaimを永続化する
- Claimごとに一つのHost identityを割り当てる
- 指定されたLinode Type IDを検証する
- fixed region、network、Firewall、image、metadataなどのdeployment policyを適用する
- ownership metadataを持つLinodeを作成する
- 複数Claimが存在する場合は同数のHostを管理する

### Host identity and readiness

- Linodeのraw statusを継続的に観測する
- GNU/Linux bootstrapとHost Agentの起動を確認する
- Host AgentをHost identityへ安全にbindingする
- Host Agentの認証、version、boot identity、healthを観測する
- Linodeが`running`であるだけでは`Ready`にしない
- 上位layerへ渡せる状態をHost conditionとして提示する

### Autonomous control

- HostControllerはdaemon稼働中に継続責任を持つlevel-triggered control loopとして動作する
- eventやnotificationが失われてもperiodic observationから状態を再構築する
- daemon、Host、networkの再起動を通常の動作として扱う
- operatorが明示的にreconcileやretryを起動しなくても正常lifecycleを管理する

### Release and normal lifecycle

- Claim解放後のHostを通常lifecycleで解放する
- policyに従って短時間Idleとして保持できる
- compatibleな新しいClaimへ安全に再利用できる
- 再利用前に必要なsanitizationを実行・検証する
- 不要なLinodeをownership確認後に削除する
- delete成功応答だけでなく、Akamai inventory上の不存在を確認する

### Fatal incidents

Akamai API failure、mutation結果不明、ownership不一致など、正常lifecycleで解決できないfailureは共通の`FatalIncident`として永続化します。

- affected HostまたはAkamai subsystemを`Critical`にする
- affected scopeへの自動mutationを停止する
- resource identity、operation、error code、最後のobservationを保存する
- operatorが原因を解消し明示的にresolveするまで自動再開しない
- 個別の無期限retry、replacement、forced deletionを行わない

共通方針は[Failure model](../../failure-model.md)を参照してください。

## Completion criteria

Host Control System v1は、次を実際のAkamai Cloud環境で確認した時点で完成とします。

1. `control`から正確なLinode Type IDを持つ複数のHostClaimを作成できる
2. 各Claimに対して一台のLinodeが作成され、別のHost identityとして追跡される
3. fixed deployment configurationがすべてのLinodeへ一貫して適用される
4. `host-agent`がbootstrapされ、認証され、Host statusを継続報告する
5. Linode statusとHost statusが別々に観測・表示される
6. Hostが上位layerへ渡せる場合だけ`Ready`になる
7. Control Plane、Host Agent、Linode、networkの再起動や一時停止後に正常lifecycleへ自律的に再収束する
8. Claim解放後にHostをIdleとして保持し、compatible Claimへ再利用できる
9. 再利用できない、または保持期限を超えたHostを正常lifecycleで削除できる
10. 削除後にAkamai inventory上の不存在を確認してからfinalizeする
11. 予測不能な外部failureをFatal Incidentとして共通形式で記録できる
12. Fatal Incident発生時にaffected scopeへのmutationを停止し、他resourceとoperator RPCは継続できる
13. Fatal IncidentをRPCから取得し、人間がacknowledgeおよびresolveできる
14. 通常経路ではSSHやCloud Managerによる手作業を必要としない
15. 主要な正常pathとFatal Incident pathをfakeと実環境のacceptance testで再現できる

## Non-goals

- Minecraft server lifecycle
- workload deploymentやcontainer orchestration
- persistent data、restic、backup、restore
- 複数Claimの一台へのpacking
- 他cloud providerへの対応
- provider plugin system
- physical machine provisioning
- 高可用な複数Control Plane node
- Fatal Incidentの自動修復
- Fatal Incident後のforced deletion
- account-wideなcost monitoringまたはcost enforcement

外部のcost management programは、このrepository、checkpoint、Control Planeの完成条件に含めません。

## Documents

- [Architecture](architecture.md)
- [Implementation status and plan](implementation.md)
- [Open questions](open-questions.md)
