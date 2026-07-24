# Host control milestone

## Goal

中期目標は、Host需要の提示からHostの確保、観測、割当、解放、再利用または削除までを、
Control Planeが継続的かつ安全に管理できることです。

Minecraft workloadやdata restoreは、このmilestoneの完成条件に含めません。

## Core resources

### HostClaim

上位layerがHost subsystemへ提示する、一台のHostに対する永続的な需要です。
Claimが存在する間、controllerは要求を満たし続けます。最初のspecはworkloadへ提供する最小vCPU、memory、local storageだけを持ちます。
詳細は[HostClaim specification](host-claim-spec.md)を参照してください。

### Host

Control Planeがidentityとlifecycleを所有する論理的な実行環境です。
Host IDはLinode IDなどのprovider resource IDと分離します。

最初から`HostClass`、`HostAllocation`、`HostBinding`などを独立resourceとして固定しません。
実装で独立したlifecycleや不変条件が必要になった時点で追加します。

## Expected behavior

- 一つのHostClaimは一台の排他的Hostを要求する
- 複数Claimがあれば必要数のHostを管理する
- Claimがなくなっても上位layerはHost削除を直接命令しない
- Host subsystemがidle保持、再利用、削除を判断する
- provider resourceの作成・削除結果が不明でも二重作成や誤削除を避ける
- Host ID、provider ID、将来のHost identityを混同しない
- daemon再起動後も永続状態からreconciliationを再開する

## Completion outline

このmilestoneは、概ね次を実機で確認した時点で完成とします。

1. `control`から複数のHostClaimを作成する
2. Control Planeが必要数のLinodeを確保する
3. 各Hostの`host-agent`が認証され、状態を報告する
4. Claimを解放するとHostがpolicyに従ってidleになる
5. 互換Claimでidle Hostを再利用できる
6. 不要なHostをownership確認後に削除する
7. 各段階でdaemon、Host、networkを再起動しても安全に収束する
8. 通常経路でSSHやCloud Manager操作を必要としない

具体的なPKI、transport、retention policy、sanitizationの仕様は、それぞれの実装開始前に決めます。
