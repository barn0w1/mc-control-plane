# ADR-0006: JSON-RPC 2.0とOpenRPCを使用する

- Status: Proposed
- Date: 2026-07-23

## Context

旧Host protocolは独自HTTP endpoint、独自envelope、手書きpayload validationを持っていた。
標準化されたRPC envelopeと機械可読schemaを使い、interface間の一貫性を高めたい。

通信にはlocal Unix socket、HTTPS、WebSocket、HTTP/2、QUICなど複数候補がある。
Application protocolとtransportを分離して判断する必要がある。

## Proposed decision

- Application protocolにJSON-RPC 2.0を使用する。
- API descriptionにOpenRPCを使用する。
- Rustの共有型とschemaの不一致をCIで検出する。
- Host transportの最初の候補をJSON-RPC over HTTPS request/responseとする。
- Hostはoutbound connectionだけを開始する。
- raw TCPまたはraw QUIC上の独自framingは最初に採用しない。
- JSON-RPC batchとnotificationは必要性が確認されるまで使用しない。

## Consequences

### Positive

- 標準request、response、error envelopeを利用できる。
- Operator、Host、将来interfaceで共通toolingを利用できる。
- OpenRPCからdocumentationやclient生成を行える可能性がある。

### Negative

- JSON-RPC自体は認証、retry、idempotency、streamingを定義しない。
- JSON schemaとRust typeの正本を決める必要がある。
- Host command delivery semanticsはproject側で設計する必要がある。

## Open points before acceptance

- Host transport profile
- Unix socket framing
- Schema generation direction
- Batch/notification policy
- Request and response size limits
