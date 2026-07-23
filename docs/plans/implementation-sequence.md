# Implementation sequence

各stepはbuild可能で、review可能な小さなcommitに分けます。
古いPython構造を一括翻訳しません。

## 1. Workspace skeleton

予定crate:

```text
crates/
  mccp-core/
  mccp-rpc/
  mccpd/
  mccp-hostd/
  mccpctl/
```

最初はcrate数を増やしすぎません。Providerやstorageは`mccpd`内部moduleから始めます。
独立したreuse boundaryが確定した場合だけcrateへ分割します。

## 2. Core types

- typed UUID/newtype IDs
- resource metadata
- generation and revision
- condition
- timestamp policy
- structured error
- secret wrapper with redacted debug output

Serialization formatとdatabase representationをgolden testで固定します。

## 3. RPC vertical slice

- JSON-RPC parser and response envelope
- method registry
- Unix domain socket transport
- `system.get_info`
- `mccpctl system info`
- OpenRPC generationまたはvalidation prototype

この段階でCLIがdaemon内部moduleを直接importできない構成を固定します。

## 4. Storage vertical slice

- SQLite connection and migrations
- resource repository boundary
- transaction helper
- revision conflict
- audit record
- controller wakeup tableまたはequivalent scheduler state

Databaseを破棄して再作成するdevelopment commandを用意しても、通常RPCから誤実行できないようにします。

## 5. Host resource model

- HostClass RPC
- HostClaim RPC
- Host and allocation query
- fake Provider controller
- multiple claim scenario
- release and idle state

Cloud APIより先にresource ownershipとstate transitionをtestします。

## 6. Linode provider

- configuration preflight
- ownership identity
- create/discover/observe/delete
- status normalization
- Activity integration
- timeout and outcome-unknown tests
- opt-in live acceptance harness

SDKまたは直接HTTP clientの選択は実装直前に決めます。Provider typeをdomainへ漏らさないことを優先します。

## 7. PKI and host bootstrap

- development CA tooling
- enrollment record
- cloud-init/bootstrap artifact
- Host private key generation
- certificate issue and validation
- rotation and revocation

Root CA運用とonline intermediate key storageを明示的に分離します。

## 8. Host RPC and journal

- `hostd.enroll`
- `hostd.exchange`
- observation
- command journal
- closed fixture command set
- command replay tests
- Host reboot and daemon restart tests

## 9. Allocation and retention

- compatibility matching
- fencing token
- sanitization
- Idle reuse
- billing-aligned retention
- safe termination

## 10. Acceptance and cleanup

- end-to-end `mccpctl` workflow
- fault injection matrix
- live acceptance
- documentation update
- obsolete design scaffolding removal

## Rust validation expected from project owner

この環境ではRust toolchainを導入しません。Rust実装を追加した後は、project owner側で少なくとも次を実行します。

```text
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
cargo build --workspace --all-targets
```

必要なtoolchain versionや追加commandは、workspace作成時に確定します。
