# Terminology

用語はcode、RPC、database、CLI、documentで同じ意味に使用します。
独自用語を増やさず、一般的なcontrol planeとresource managementの用語を優先します。

| Term | Meaning |
| --- | --- |
| Control Plane | system全体の名称 |
| Host Control System v1 | Akamai Cloud上のGNU/Linux Hostを、Claimから確保、認証、観測、解放、再利用、通常削除まで管理する中期checkpoint |
| `control-plane` | stateとcontrollerを所有する中央daemon |
| `host-agent` | 管理対象Hostに常駐する将来のdaemon |
| `control` | operator向けRPC CLI client |
| Resource | identityとlifecycleを持つ永続的な管理対象 |
| Spec | Resourceに要求された状態 |
| Status | Controllerが観測・計算した状態 |
| Claim | 上位componentが下位subsystemへ提示するresource需要 |
| Controller | daemon稼働中、担当resourceのdesired stateとobserved stateを継続的に収束させるnon-terminating control loop |
| Reconciliation | Controllerが差分を観測し必要な変更を行う一回の処理 |
| HostClaim | 一台のHostに対する永続的かつ排他的な需要 |
| Linode Type ID | Akamai Cloudが公開するcompute SKUの正確なID。HostClaimの`spec.type`で指定する |
| Deployment configuration | region、network、image、bootstrapなど全Hostへ共通適用するAkamai設定 |
| Host | workloadの土台となる一つの管理されたGNU/Linux実行環境。このprojectでは一つのAkamai Cloud Linodeに対応する |
| Host ID | Control Planeが発行するHostの内部identity |
| Linode ID | Akamai Cloudが発行するLinode instance identity |
| Observation | Akamai APIまたはHost Agentから取得した時刻付き状態 |
| Idle Host | Claimへ割り当てられておらず、正常policyにより再利用のため一時保持されているHost |
| Incident | 人間による確認や対応を必要とする、永続化されたsystem-wide error record |
| Fatal Incident | Control Planeが正常lifecycleでは解決しないと判断し、affected scopeへの自動mutationを停止するCritical Incident |
| Critical | Fatal Incidentにより自動mutationが停止し、人間の対応が必要なresourceまたはsubsystem state |
| Normal deletion | Claim解放やIdle policy終了に伴う、Host Controllerが所有する通常lifecycleのLinode削除 |
| Forced cleanup | Fatal Incident後の強制削除やcost enforcement。Control Planeのscope外 |

## Naming rules

- repository名`mc-control-plane`をsystem名やbinary prefixとして強制しない
- binary名に慣習だけを理由として`d`や`ctl`を付けない
- RPC request型とpersistent resourceを区別する
  - example: `CreateHostClaimParams` and `HostClaim`
- Host layerではAkamaiの正式なType ID、Linode ID、status語彙を使用する
- lifecycleが独立していない概念を先回りしてResource化しない
- operationalなFatal IncidentとRustの`panic!`を同一視しない
