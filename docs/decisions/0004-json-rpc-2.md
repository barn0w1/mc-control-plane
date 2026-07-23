# ADR-0004: RPC envelopeにJSON-RPC 2.0を使用する

- Status: Accepted
- Date: 2026-07-24

## Decision

Control Planeが提供するRPCのrequest、response、error envelopeにはJSON-RPC 2.0を使用します。

Transport、authentication、authorization、schema generation、retry、durable command deliveryはJSON-RPCとは別の問題として、
必要な段階で決めます。

## Reason

独自のRPC envelopeを作らず、既存の小さく明確な標準を利用するためです。

## Reference

- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
