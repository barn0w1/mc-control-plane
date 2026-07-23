# ADR-0002: 一つのControl Plane daemonとRPC client interfaceを使用する

- Status: Accepted
- Date: 2026-07-24

## Decision

Control Planeのstate、controller、provider integration、RPC serverは一つのdaemonが所有します。
CLIと将来のinterfaceは、そのdaemonのRPC clientとします。

## Reason

一つのownerにより、configuration、lifecycle、database access、controller coordinationを一貫させられます。
Interfaceが内部実装へ直接依存しないため、CLI、Bot、Webなどを同じ境界上に追加できます。

必要性が確認される前に、論理moduleを別serviceへ分割しません。
