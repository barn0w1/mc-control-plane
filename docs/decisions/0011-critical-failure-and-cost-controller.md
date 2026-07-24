# ADR-0011: Critical failureからのforced deletionをCost controllerへ分離する

- Status: Superseded
- Date: 2026-07-24
- Superseded by: ADR-0012

## Context

Cloud APIのmutation結果不明、ownership mismatch、削除API failureなどをHost controllerが無期限に回復しようとすると、複雑性と誤削除riskが増えます。
一方、Linodeはaccount上に存在する限り課金されるため、当初はControl Plane内部にCost controllerを追加する案を採用しました。

## Decision

このADRはADR-0012によって置き換えられました。
Control Plane内部にはCost controllerを実装しません。

## Reason for supersession

forced deletionとcost enforcementはHost lifecycleの正しさに必要な責務ではなく、Control Planeへ組み込むとfailure handlingと削除policyが複雑になります。
予測不能な外部failureはFatal Incidentとして記録し、人間または完全に独立した外部programへ対応を委ねます。
