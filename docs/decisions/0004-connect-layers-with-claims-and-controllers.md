# ADR-0004: LayerをClaimとControllerで接続する

- Status: Accepted
- Date: 2026-07-23

## Context

SystemはHost、data、workload、server sessionなど複数のownership layerを持つ。
上位workflowが下位実装へ「Linodeを作成する」のような手順を直接命令すると、provider detailとfailure handlingが
全体へ漏れ、各layerを独立して改善できない。

## Decision

Layer間はResource、Claim、Statusで接続する。

上位layerは必要な下位resourceのClaimを作成し、下位controllerが要求を満たすようにreconcileする。
外部副作用はcontroller内部のdurable Activityとして実行する。

## Consequences

### Positive

- 各layerのownerとcontractが明確になる。
- 下位resourceの再利用、retry、replacementを上位から隠蔽できる。
- process restart後も保存されたresourceから再開できる。

### Negative

- 最終状態への収束は非同期になり、中間状態を扱う必要がある。
- Resource schema、condition、finalizer、generationの設計が必要。
- 一つのrequestを単純な同期関数呼び出しとして理解できなくなる。
