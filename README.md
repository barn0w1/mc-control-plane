# mc-control-plane

Hostを起点としたresource automationのControl Planeを、Rustで新しく構築するprojectです。
repository名には歴史的にMinecraftが含まれていますが、systemとbinaryの名称には用途を無理に含めません。

現在は、既存のPython prototypeを移植する段階ではありません。prototypeから得たfailure caseと有効だった設計を参考にしつつ、
新しいresource modelとcontrol loopを小さく実装して検証します。

## Names

| Role | Name |
| --- | --- |
| System | **Control Plane** |
| Central daemon | `control-plane` |
| Host-resident daemon | `host-agent` |
| Operator CLI | `control` |
| Persistent Host demand | `HostClaim` |

repository名は引き続き`mc-control-plane`を使用します。

## Current goal

中期checkpointは、**Host Control System v1**を完成させることです。

これはMinecraft機能のためだけの内部部品ではなく、Akamai Cloud上のGNU/Linux実行環境を、
HostClaimから確保、認証、観測、解放、再利用、削除、cost controlまで管理する独立したsubsystemです。

一つのHostClaimは一台の排他的Hostを要求します。複数Claimを一台へpackingしません。
Controllerはdaemon稼働中に自律的に状態を観測し、operatorによる手動retryを前提とせず収束を続けます。
解決不能な外部不整合はCriticalとして停止し、異常な課金resourceの処分は別責務のCostControllerがpolicyに従って行います。

完成条件と非目標は[Host Control System v1 checkpoint](docs/checkpoints/host-control-v1/README.md)を参照してください。

## Current implementation

最初のlocal vertical sliceはRust codeとして実装され、ユーザーのRust 1.97.1環境でtestが通過しています。
現在はfake provider上で`HostClaim`から`Host`の作成・観測・削除へdurableに収束できます。

実装済みの主な境界:

- 四packageのCargo workspace
- `control`から`control-plane`へのtyped JSON-RPC
- HTTP/2 prior knowledge over Unix domain socket
- SQLx/SQLiteによる`HostClaim`と`Host`の永続化
- 単一workerのHost controller
- 独立SQLiteを使用するfake provider
- create/delete結果不明、temporary observation failure、restartのtest candidate
- databaseの単一application owner lock
- active Unix socketを誤ってunlinkしないstartup/shutdown処理

実装状況と次の計画は[Host Control System implementation](docs/checkpoints/host-control-v1/implementation.md)、完成形の設計方向は[Architecture](docs/checkpoints/host-control-v1/architecture.md)を参照してください。

## Documentation

設計文書の入口は[docs/README.md](docs/README.md)です。

## Python prototype

旧Python実装はGit履歴と`python-prototype-reference-2026-07-23` tagから参照できます。
後方互換性や移行経路は提供しません。
