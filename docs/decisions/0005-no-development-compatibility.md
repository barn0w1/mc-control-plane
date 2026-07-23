# ADR-0005: Stable release前の後方互換性を保証しない

- Status: Accepted
- Date: 2026-07-24

## Decision

最初のstable releaseまでは、開発中のRPC、database、configuration、binary名、resource名、Host protocolとの後方互換性を保証しません。

設計を変更するときはcompatibility shimを追加せず、不要な形式とcode pathを削除します。

## Reason

Projectは未運用であり、互換性維持よりも、その時点で最も適切な設計へ更新できることを優先するためです。
