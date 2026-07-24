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

最初の実装では、LinodeやHostとの通信へ進む前に、local RPC、SQLite、`HostClaim`、`Host`、
fake providerによるreconciliationを成立させます。

## Documentation

設計文書の入口は[docs/README.md](docs/README.md)です。

## Python prototype

旧Python実装はGit履歴と`python-prototype-reference-2026-07-23` tagから参照できます。
後方互換性や移行経路は提供しません。
