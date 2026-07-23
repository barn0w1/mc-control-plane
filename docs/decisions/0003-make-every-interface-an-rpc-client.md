# ADR-0003: すべてのinterfaceをRPC clientにする

- Status: Accepted
- Date: 2026-07-23

## Context

旧CLIはdatabase、application use case、provider wiring、service起動、acceptance harnessを直接所有し、
責務が大きくなった。将来のDiscord Botなどが同じ内部moduleへ接続すると、interfaceごとにstate accessが分散する。

## Decision

`mccpctl`を含むすべてのinterfaceは`mccpd`のRPC clientとする。

- clientはSQLite fileを開かない。
- clientはprovider APIを呼ばない。
- clientはcontroller moduleをlinkして直接実行しない。
- business rule、authorization、idempotencyはserver側に置く。
- local CLIはUnix domain socketを第一候補とする。

## Consequences

### Positive

- state mutationの入口が一つになる。
- CLI、Bot、Web interfaceで同じsemanticsを共有できる。
- Interfaceを独立して変更・追加できる。

### Negative

- daemonが停止中は通常操作できない。
- local-only operationにもRPC schemaとerror handlingが必要。
- emergency recovery commandを通常RPCと分離して設計する必要がある。
