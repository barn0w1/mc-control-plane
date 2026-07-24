# Host control milestone

## Goal

中期目標は、Host需要の提示からHostの確保、観測、割当、解放、再利用または削除までを、
Control Planeが継続的かつ安全に管理できることです。

Minecraft workloadやdata restoreは、このmilestoneの完成条件に含めません。

## Core resources

### HostClaim

上位layerがHost subsystemへ提示する、一台のHostに対する永続的な需要です。
Claimが存在する間、controllerは要求を満たし続けます。specは正確なAkamai Cloud Linode Type IDを持ちます。
詳細は[HostClaim specification](host-claim-spec.md)を参照してください。

### Host

workloadを実行する土台となる、一つの管理されたGNU/Linux実行環境です。
このprojectでは一つのHostを一つのAkamai Cloud Linodeとして管理します。
Host IDはLinode IDと分離します。

最初から`HostClass`、`HostAllocation`、`HostBinding`などを独立resourceとして固定しません。
実装で独立したlifecycleや不変条件が必要になった時点で追加します。

## Expected behavior

- 一つのHostClaimは一台の排他的Hostを要求する
- 複数Claimを一台のHostへpackingしない
- 複数Claimがあれば同数のHostを管理する
- Controllerはevent handlerではなく、daemon稼働中に継続責任を持つcontrol loopとして動作する
- Claimがなくなっても上位layerはHost削除を直接命令しない
- Host subsystemがidle保持、再利用、削除を判断する
- Akamai API mutationの結果が不明な場合は反復せず、bounded observation後にCriticalとして停止する
- Host ID、Linode ID、将来のHost Agent identityを混同しない
- daemon再起動後も永続状態からreconciliationを再開する

## Completion outline

このmilestoneは、概ね次を実機で確認した時点で完成とします。

1. `control`から複数のHostClaimを作成する
2. Control Planeが必要数のLinodeを確保する
3. 各Hostの`host-agent`が認証され、状態を報告する
4. Claimを解放するとHostがpolicyに従ってidleになる
5. 互換Claimでidle Hostを再利用できる
6. 正常lifecycleで不要なHostをownership確認後に削除する
7. terminal/critical状態の課金LinodeをCost controllerのpolicyで検出できる
8. 各段階でdaemon、Host、networkを再起動しても安全に状態を再構築できる
9. 通常経路でSSHやCloud Manager操作を必要としない

完成形の設計方向は[Host management direction](host-management-direction.md)、異常な課金resourceの扱いは[Cost control direction](cost-control-direction.md)を参照してください。
具体的なPKI、transport、retention policy、sanitizationの仕様は、それぞれの実装開始前に決めます。
