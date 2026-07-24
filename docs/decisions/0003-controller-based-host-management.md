# ADR-0003: Host需要をHostClaimとして提示しControllerで収束させる

- Status: Accepted
- Date: 2026-07-24

## Context

上位layerがprovider操作手順を直接命令すると、Linode detail、retry、再利用、削除policyが全体へ漏れます。
Hostの確保は一回の命令ではなく、需要が存在する間維持されるべき状態です。

## Decision

一台のHost需要を永続resource `HostClaim`として表します。
Host controllerはHostClaimと観測されたHostを比較し、要求を満たすように継続的にreconcileします。

`Host`はControl Planeが所有する論理identityとし、provider resource IDから分離します。
Claimの削除はHostの直接削除命令ではありません。Host subsystemが解放後の再利用または削除を管理します。

## Consequences

上位layerをprovider lifecycleから分離でき、複数Claimを必要数のHostへ収束できます。
一方、処理は非同期となり、spec、status、中間状態、観測の古さを扱う必要があります。
