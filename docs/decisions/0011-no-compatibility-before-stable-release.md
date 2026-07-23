# ADR-0011: Stable release前は後方互換性を保証しない

- Status: Accepted
- Date: 2026-07-23

## Context

Projectは開発中で運用されていない。既存Python API、protocol、database、configuration、CLIを利用する
production userは存在しない。互換性維持はconcept cleanupと安全な型設計を妨げる。

## Decision

最初のstable releaseまで、次の後方互換性を保証しない。

- RPC methodとschema
- database schemaとmigration path
- configuration format
- CLI commandとoutput
- Host bootstrap format
- certificate profile
- internal resource name

破壊的変更時にcompatibility shim、dual read、dual writeを追加しない。
`mccpd`と`mccp-hostd`のversion mismatchは明示的に拒否する。

## Consequences

### Positive

- 最良の現行modelへ直接更新できる。
- obsolete schemaとcode pathをすぐ削除できる。
- test matrixを小さく保てる。

### Negative

- 開発中のdatabase、Host、certificateを作り直す必要がある。
- 複数branch間でprotocolが一致しない期間が生じる。
- Stable releaseの定義と、その時点からのcompatibility policyを後で明確にする必要がある。
