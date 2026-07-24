# Terminology

用語はcode、RPC、database、CLI、documentで同じ意味に使用します。
独自用語を増やさず、一般的なcontrol planeとresource managementの用語を優先します。

| Term | Meaning |
| --- | --- |
| Control Plane | system全体の名称 |
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
| Allocatable capacity | OSやHost Agent用reserveを除き、workloadへ提供可能なCPU、memory、storage |
| Provisioning policy | region、plan許可範囲、network、image、bootstrapなどControl Plane deploymentが所有する固定方針 |
| Host | workloadの土台となる一つの管理されたGNU/Linux実行環境。実装上はAkamai CloudのLinodeだが、概念上はvirtual/physicalを問わない |
| Host ID | Control Planeが発行するHostの内部identity |
| Provider resource ID | Linodeなど外部providerが発行するidentity |
| Observation | providerまたはHostから取得した時刻付き状態 |
| Idle Host | Claimへ割り当てられておらず、再利用のため一時保持されているHost |

## Naming rules

- repository名`mc-control-plane`をsystem名やbinary prefixとして強制しない
- binary名に慣習だけを理由として`d`や`ctl`を付けない
- RPC request型とpersistent resourceを区別する
  - example: `CreateHostClaimParams` and `HostClaim`
- provider固有の名称をHostより上位のlayerへ漏らさない
- lifecycleが独立していない概念を先回りしてResource化しない
