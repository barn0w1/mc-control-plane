# ADR-0009: SQLiteを単一mccpd ownerで使用する

- Status: Accepted
- Date: 2026-07-23

## Context

初期deploymentはprivate single-nodeであり、複数Control Plane nodeや高可用databaseを必要としない。
旧実装でSQLiteのtransaction、WAL、partial unique index、migrationが有効であることを確認した。

問題はSQLiteそのものより、複数serviceとCLIからstate accessが分散したことにある。

## Decision

初期databaseにSQLiteを使用し、application levelの唯一のownerを`mccpd`とする。

- Interface clientと`mccp-hostd`はdatabaseへ直接接続しない。
- External I/O中にtransactionを保持しない。
- Invariantをdatabase constraintでも保証する。
- Schema後方互換性はstable releaseまで保証しない。
- 複数writer nodeは実装しない。

## Consequences

### Positive

- deployment、backup、inspectionが単純。
- 強いlocal transactionとconstraintを利用できる。
- 単一daemon設計と一致する。

### Negative

- Control Plane node failure時にautomatic failoverしない。
- 長いwrite transactionや不適切なconcurrencyで全体へ影響する。
- 将来multi-node化する場合はdatabaseとcontroller leaseの再設計が必要。
