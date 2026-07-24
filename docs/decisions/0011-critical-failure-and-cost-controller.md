# ADR-0011: Critical failureからのforced deletionをCost controllerへ分離する

- Status: Accepted
- Date: 2026-07-24

## Context

Cloud APIのmutation結果不明、ownership mismatch、削除API failureなどをHost controllerが無期限に回復しようとすると、複雑性と誤削除riskが増えます。
一方、Linodeはpowered offでもaccount上に存在する限り課金されるため、terminal errorのresourceを永続的に放置することも適切ではありません。

## Decision

Host controllerはnormal lifecycle mutationだけを行います。
短いbounded observationで解決しない外部不整合はterminalまたはCriticalとして記録し、推測的なrepair/deleteを停止します。

terminal/critical状態で一定時間を超えた、ownershipが確定したbillable Linodeのforced deletionは、将来の独立したCost controllerがpolicyに従って行います。
Cost controllerは同じControl Plane daemon内で動かし、Host repairやreplacementは行いません。

DataProtectionHold、active Claim、曖昧なownershipがあるresourceは自動削除しません。
削除APIが失敗した場合はCriticalとしてoperator interventionを要求します。

## Consequences

Host lifecycleのfailure modelを単純に保ちつつ、cost leakへ限定的な自動対処を追加できます。
ただし、Critical alertとoperator runbook、およびdata-safety signalが必要になります。
