# ADR-0001: FoundationをRustで構築する

- Status: Accepted
- Date: 2026-07-24

## Decision

Control Plane daemonとHost daemonをRustで新しく実装します。
旧Python実装のmodule構造、API、databaseを移植しません。

## Reason

基盤componentでは、明確な型、closed state、予測可能なerror handling、privileged Host processの安全性を優先します。
Python prototypeは、要件、failure case、検証結果を得るためのreferenceとして使用します。
