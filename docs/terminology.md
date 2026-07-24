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
| Controller | desired stateとobserved stateを継続的に収束させるcomponent |
| Reconciliation | Controllerが差分を観測し必要な変更を行う一回の処理 |
| HostClaim | 一台のHostに対する永続的かつ排他的な需要 |
| Host | Control Planeがidentityとlifecycleを所有する論理実行環境 |
| Host ID | Control Planeが発行するHostの内部identity |
| Provider resource ID | Linodeなど外部providerが発行するidentity |
| Observation | providerまたはHostから取得した時刻付き状態 |
| Idle Host | Claimへ割り当てられておらず、一時保持されているHost |

## Naming rules

- repository名`mc-control-plane`をsystem名やbinary prefixとして強制しない
- binary名に慣習だけを理由として`d`や`ctl`を付けない
- RPC request型とpersistent resourceを区別する
  - example: `CreateHostClaimParams` and `HostClaim`
- provider固有の名称をHostより上位のlayerへ漏らさない
- lifecycleが独立していない概念を先回りしてResource化しない
