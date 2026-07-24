# ADR-0005: Stable release前は後方互換性を保証しない

- Status: Accepted
- Date: 2026-07-24

## Context

Projectは開発中で運用されていません。互換性維持は、より適切なresource model、protocol、database schema、CLIへ更新する妨げになります。

## Decision

最初のstable releaseまで、RPC、database、configuration、CLI、Host bootstrap、certificate、resource名の後方互換性を保証しません。
破壊的変更時にcompatibility shim、dual read、dual writeを追加しません。

## Consequences

常に現在もっとも適切な設計へ直接更新できます。
開発中のdatabase、Host、certificate、clientは変更に応じて作り直す必要があります。
