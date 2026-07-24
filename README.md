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

中期目標は、Host layerをControl Planeだけで完全に管理できることです。

- clientが`HostClaim`を作成・削除できる
- controllerが必要なHost数へ自動的に収束する
- Hostのidentityとprovider resourceをControl Planeが管理する
- Claim解放後の再利用または削除をHost subsystemが判断する
- daemonやHostの再起動、通信断、provider APIの不確実な結果から安全に再開する
- 通常操作は`control`からRPCを通じて行い、databaseやproviderを直接操作しない

最初の実装では、Rust 1.97 / Edition 2024、Tokio、HTTP/2 over Unix domain socket、JSON-RPC 2.0、
SQLx/SQLiteを基盤として、`HostClaim`、`Host`、fake providerによるreconciliationを成立させます。

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

実装範囲、runtime flow、保証と未実装部分は[Current implementation](docs/current-implementation.md)を参照してください。
実装計画と残作業は[First implementation plan](docs/first-implementation-plan.md)にあります。

## Documentation

設計文書の入口は[docs/README.md](docs/README.md)です。

## Python prototype

旧Python実装はGit履歴と`python-prototype-reference-2026-07-23` tagから参照できます。
後方互換性や移行経路は提供しません。
