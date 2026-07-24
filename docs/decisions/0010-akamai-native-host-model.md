# ADR-0010: Host layerをAkamai Cloud nativeとして設計する

- Status: Accepted
- Date: 2026-07-24
- Supersedes: ADR-0009

## Context

Hostの実体は現在も将来もAkamai Cloud Linodeです。
provider-neutral modelを維持すると、Linode Type、status、network、billing、failure semanticsを抽象化するmappingが必要になりますが、別providerへ交換する具体的な利益がありません。

CPU、RAM、storageからLinode Typeを自動選択する方式は、CPU class、generation、pricing、region availability、system reserveを追加policyとして扱う必要があります。

## Decision

HostClaim、Host、Host controller、infrastructure integrationをAkamai Cloud nativeとして設計します。

- `HostClaim.spec.type`は正確なLinode Type ID
- Control Planeはresource quantityからtypeを自動選択しない
- raw Linode statusを観測・保存する
- region、network、image、cloud-initなど共通値はdeployment configurationが所有する
- generic provider plugin systemは作らない
- test用にAkamai固有operationを再現するprivate fakeを持つ
- Host IDとLinode IDは分離する

## Consequences

resource modelとcontrollerはAkamaiの実際のAPI、state、billingへ直接合わせられます。
provider-neutral abstractionの保守costを避けられます。

将来別providerを追加する場合は大きな再設計が必要ですが、stable release前の後方互換性は保証せず、現在その必要性もありません。
