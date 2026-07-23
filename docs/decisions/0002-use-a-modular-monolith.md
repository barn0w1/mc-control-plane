# ADR-0002: mccpdをmodular monolithにする

- Status: Accepted
- Date: 2026-07-23

## Context

旧実装はHost APIとreconcilerを別serviceとして動かしたが、一つのSQLite stateを共有し、configuration、
composition root、writer concurrency、deploymentを複雑化した。

論理layerを分離する必要はあるが、独立deployment、failure domain、scaling unitへ分ける必要はない。

## Decision

`mccpd`を一つのRust daemonとして動かす。

内部ではRPC、identity、scheduler、controller、Activity、storage、providerを独立moduleとasync taskに分ける。
SQLite stateのapplication owner、process lifecycle、configuration、observabilityは`mccpd`へ統一する。

## Consequences

### Positive

- 単一state ownerと明確なtransaction boundaryを持てる。
- CLIやHost APIごとのcomposition rootが不要になる。
- systemd service、configuration、logging、upgrade経路が単純になる。
- module境界を保ったまま、network分散のcostを避けられる。

### Negative

- process crashは全controllerとRPC endpointへ影響する。
- 重いtaskがruntimeを枯渇させない設計が必要。
- 将来独立serviceへ分ける場合、module contractをnetwork contractへ昇格する作業が必要。
