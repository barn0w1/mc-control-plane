# Architecture Decision Records

## Status values

- `Proposed`: 検討中。実装前または実測後に変更できる。
- `Accepted`: 現在の設計方針。
- `Superseded`: 新しいADRで置き換えられた。
- `Rejected`: 検討したが採用しなかった。

## Index

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-rebuild-foundation-in-rust.md) | Accepted | Rustでfoundationを新しく構築する |
| [0002](0002-use-a-modular-monolith.md) | Accepted | `mccpd`をmodular monolithにする |
| [0003](0003-make-every-interface-an-rpc-client.md) | Accepted | すべてのinterfaceをRPC clientにする |
| [0004](0004-connect-layers-with-claims-and-controllers.md) | Accepted | LayerをClaimとControllerで接続する |
| [0005](0005-host-subsystem-owns-host-identity.md) | Accepted | Host subsystemがHost identityを所有する |
| [0006](0006-use-json-rpc-and-openrpc.md) | Proposed | JSON-RPC 2.0とOpenRPCを使用する |
| [0007](0007-use-mtls-and-private-pki.md) | Proposed | Host通信にmTLSとprivate PKIを使用する |
| [0008](0008-record-external-effects-as-durable-activities.md) | Accepted | 外部副作用をdurable Activityとして記録する |
| [0009](0009-use-sqlite-with-a-single-application-owner.md) | Accepted | SQLiteを単一`mccpd` ownerで使用する |
| [0010](0010-separate-host-retention-policy.md) | Accepted | Host idle retentionをpolicyとして分離する |
| [0011](0011-no-compatibility-before-stable-release.md) | Accepted | Stable release前は後方互換性を保証しない |
