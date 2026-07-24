# Architecture Decision Records

ADRには、現在採用している大きな方針だけを記録します。
実装libraryや細かなschemaは、長期的な設計判断でない限り実装計画またはcodeで管理します。

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-rust-foundation.md) | Accepted | Rustでfoundationを新しく構築する |
| [0002](0002-single-daemon-and-rpc-clients.md) | Accepted | 単一Control Plane daemonとRPC clientを使用する |
| [0003](0003-controller-based-host-management.md) | Accepted | Host需要をHostClaimとして提示しControllerで収束させる |
| [0004](0004-json-rpc-2.md) | Accepted | RPC envelopeにJSON-RPC 2.0を使用する |
| [0005](0005-no-development-compatibility.md) | Accepted | Stable release前は後方互換性を保証しない |
| [0006](0006-passwordless-restic-repositories.md) | Accepted | Data repositoryにpasswordless resticを使用する |
| [0007](0007-naming-and-initial-workspace.md) | Accepted | 名称と最初のCargo workspace境界を定める |
| [0008](0008-local-rpc-http2-over-unix-socket.md) | Accepted | Local operator RPCにHTTP/2 over Unix domain socketを使用する |
| [0009](0009-hostclaim-allocatable-resources.md) | Superseded | HostClaimはallocatable CPU、memory、local storageだけを要求する |
| [0010](0010-akamai-native-host-model.md) | Accepted | Host layerをAkamai Cloud nativeとして設計する |
| [0011](0011-critical-failure-and-cost-controller.md) | Accepted | Critical failureからのforced deletionをCost controllerへ分離する |
