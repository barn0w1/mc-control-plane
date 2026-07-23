# Architecture decisions

ADRには、現在確定しているproject-levelの判断だけを記録します。
実装予定の詳細や候補比較は、該当実装の直前までADRにしません。

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-rust-foundation.md) | FoundationをRustで構築する | Accepted |
| [0002](0002-single-daemon-and-rpc-clients.md) | 一つのControl Plane daemonとRPC client interfaceを使用する | Accepted |
| [0003](0003-controller-based-host-management.md) | Hostをcontrollerとreconciliationで管理する | Accepted |
| [0004](0004-json-rpc-2.md) | RPC envelopeにJSON-RPC 2.0を使用する | Accepted |
| [0005](0005-no-development-compatibility.md) | Stable release前の後方互換性を保証しない | Accepted |
| [0006](0006-passwordless-restic-repositories.md) | Data repositoryでresticのpassword protectionを使用しない | Accepted |
