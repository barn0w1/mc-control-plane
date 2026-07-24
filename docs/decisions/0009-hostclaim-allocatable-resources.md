# ADR-0009: HostClaimはallocatable CPU、memory、local storageだけを要求する

- Status: Superseded by ADR-0010
- Date: 2026-07-24

## Context

実際のproviderはLinodeですが、上位layerがfirewall、VPC、region、image、cloud-init、Host Agent、Linode type IDを指定するとHost managementの責務が漏れます。
これらはほぼすべてのHostで共通であり、Control Plane deploymentが安全な値を一貫して適用すべきです。

上位layerが必要とする本質的な情報は、workloadを実行するために利用可能なCPU、memory、local storageです。

## Decision

最初の`HostClaim.spec`は次のminimum allocatable capacityだけを持ちます。

- vCPU count
- memory bytes
- local storage bytes

provider、region、plan family/type、network、firewall、image、cloud-init、Host Agent設定などはHost provisioning policyが所有します。
Providerはsystem reserveを考慮し、Claimを満たす許可済みplanからdeterministically選択します。

CPU isolation、architecture、accelerator、placementなどは、上位layerの実要件になった場合だけprovider-neutral constraintとして追加します。

## Superseded

この判断は、Host layer全体をAkamai Cloud nativeとして設計し、Claimで正確なLinode Type IDを指定するADR-0010により置き換えられました。現在のRust vertical sliceの実装履歴として残します。

## Consequences

HostClaimは小さく安定し、上位layerをLinode APIとsecurity configurationから分離できます。
一方、同じControl Plane deployment内でClaimごとにregionやplan familyを選ぶことはできません。必要性が明らかになった時点でpolicy/profile modelを追加します。
