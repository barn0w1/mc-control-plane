# Terminology and naming

## Naming principles

- 技術的に一般的な用語を優先する。
- 同じ概念に複数の名前を使わない。
- product名とdomain termを混同しない。
- binary名が長いprefixの連続にならないようにする。
- Minecraft固有でないcomponentへ、無理にMinecraft由来の名前を付けない。
- 実装前に名称を変更できるため、早期の仮名へ互換性を持たせない。

## Terms currently used

| Term | Meaning |
| --- | --- |
| Control Plane | stateを保存し、controllerを実行し、外部interfaceを提供するsystem |
| Controller | desired stateとobserved stateを比較し、actual stateを収束させるcontrol loop |
| Reconciliation | controllerが差分を観測し、必要な変更を行う一回の処理 |
| Host | workloadを実行できる一つの管理対象実行環境 |
| Host daemon | 各Hostに常駐し、そのHostを観測・操作するprogram |
| Provider | Hostのための外部compute resourceを提供するsystem。現在はAkamai Cloud / Linode |
| Provider resource | Provider上の実resource。Hostの内部identityとは別物 |
| Allocation | Hostを一つの需要へ割り当てた関係 |
| RPC client | Control Planeの公開RPCだけを使用するinterface |
| Desired state | systemに実現してほしい状態 |
| Observed state | providerやHost daemonから実際に観測した状態 |

## Names not yet decided

- project name
- Rust package / crate prefix
- Control Plane daemon binary
- Host daemon binary
- operator CLI binary
- Host需要resourceを`HostRequest`、`HostClaim`、その他のどれと呼ぶか

文書では名称が決まるまで、説明語として「Control Plane daemon」「Host daemon」「operator CLI」「Host demand」を使用します。
