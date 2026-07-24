# ADR-0003: Host需要をHostClaimとして提示しControllerで収束させる

- Status: Accepted
- Date: 2026-07-24

## Context

上位layerがprovider操作手順を直接命令すると、Linode detail、retry、再利用、削除policyが全体へ漏れます。
Hostの確保は一回の命令ではなく、需要が存在する間維持されるべき状態です。

## Decision

一台のHost需要を永続resource `HostClaim`として表します。
一つのClaimは一つの排他的Hostを要求し、複数Claimを一台へpackingしません。

Host controllerはdaemon稼働中に継続責任を持つnon-terminating control loopです。HostClaimと観測されたHostをlevel-triggeredに比較し、要求を満たすように継続的にreconcileします。event notificationやtimerは内部scheduler detailであり、event配送を正しさの前提にしません。

`Host`はControl Planeが所有する論理identityとし、provider resource IDから分離します。
Claimの削除はHostの直接削除命令ではありません。Host subsystemが解放後の再利用または削除を管理します。

実providerはAkamai Cloudを対象とします。汎用provider plugin systemは作らず、責務分離とfake testのためのprivate infrastructure boundaryだけを持つ方向です。詳細は[Host management direction](../host-management-direction.md)に記録します。

## Consequences

上位layerをprovider lifecycleから分離でき、複数Claimを必要数のHostへ収束できます。
一方、処理は非同期となり、spec、status、中間状態、観測の古さを扱う必要があります。
