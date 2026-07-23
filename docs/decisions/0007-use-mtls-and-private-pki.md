# ADR-0007: Host通信にmTLSとprivate PKIを使用する

- Status: Proposed
- Date: 2026-07-23

## Context

HostとControl PlaneはInternetまたはprovider networkを介して通信する可能性がある。
Bearer tokenだけでなく、Hostごとのcryptographic identityと相互認証を持たせたい。

`mccpd`が証明書を発行する案があるが、root CA private keyをonline daemonへ直接置くことは避けたい。

## Proposed decision

- Host RPC transportにTLS 1.3とclient certificate authenticationを使用する。
- Offline root CAと、`mccpd`が利用するonline intermediate CAを概念上分離する。
- `mccp-hostd`はHost内でprivate keyを生成する。
- 一回限りのenrollment tokenとCSRから短命certificateを発行する。
- Certificate identityをHost IDとincarnationへbindingする。
- Host termination、replacement、security incidentでidentityを失効する。
- Rotationを通常protocolとして実装する。

## Consequences

### Positive

- serverとHostを相互認証できる。
- bearer token漏洩だけで長期間なりすますriskを下げられる。
- Hostごとのauthorizationとrevocationが可能になる。

### Negative

- CA key management、rotation、clock、certificate validationが新たな運用責務になる。
- Development環境のbootstrapが複雑になる。
- Rust libraryとcertificate profileの選定が必要。

## Open points before acceptance

- CA storage
- certificate lifetime
- revocation mechanism
- SAN format
- Rust TLS/X.509 libraries
