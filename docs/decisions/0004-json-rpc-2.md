# ADR-0004: RPC envelopeにJSON-RPC 2.0を使用する

- Status: Accepted
- Date: 2026-07-24

## Context

独自のrequest、response、error envelopeや手書きdispatcherを増やしたくありません。
複数のclientとHost通信で一貫したapplication protocolが必要です。

## Decision

RPC message envelopeにJSON-RPC 2.0を使用します。
Parser、dispatcher、HTTPなどは、要件を満たす既存の標準準拠libraryを優先します。

JSON-RPCが定義しないtransport、認証、schema、retry、idempotency、durable deliveryは、必要になる段階で別に決めます。
最初のlocal RPCではHTTP request/responseをUnix domain socket上で使用し、独自framingを作りません。

## Consequences

標準化されたmethod、params、id、result、errorを利用できます。
一方、JSON-RPCだけではsystemの信頼性やsecurityは完成しないため、それらをprotocol envelopeと混同しない必要があります。
