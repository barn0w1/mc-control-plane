# ADR-0010: Host idle retentionをpolicyとして分離する

- Status: Accepted
- Date: 2026-07-23

## Context

HostClaim解放直後にLinodeを削除することが常に費用効率的とは限らない。
Akamai Cloudの利用時間は時間単位で切り上げられ、serviceがaccount上に存在する間は停止中でも課金される。
同じ要件のClaimが短時間で再発生するなら、既に課金された時間内でHostを再利用できる可能性がある。

一方、idle保持はsecurity、sanitization、予算、次の課金境界を考慮する必要がある。

## Decision

Claim解放とHost削除を分離する。

- HostClaim削除はallocationを解放する。
- Sanitization成功後にHostをIdleへ移せる。
- Host Controllerがretention policyを評価する。
- Policyはimmediate、fixed、billing-alignedなどを選べる。
- maximum idle duration、maximum idle hosts、billing safety marginを持つ。
- failed sanitization Hostは再利用しない。

## Consequences

### Positive

- 上位layerを課金detailから分離できる。
- 同じHostClassの短時間の再要求を低遅延で満たせる。
- Cost policyをHost lifecycleから独立して改善できる。

### Negative

- idle Hostも課金とattack surfaceを持つ。
- 課金境界の推定と削除時間marginが必要。
- Sanitizationの保証を明確にする必要がある。
