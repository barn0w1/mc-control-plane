# Gate 1: Infra lifecycle acceptance

- Implementation: Complete
- Automated verification: Complete
- Akamai Cloud live acceptance: Complete (2026-07-22)

Gate 1の目的は、MinecraftやHost agentより前に、Control Planeが一つのDebian 13 Linodeを
安全に作成、観測、削除できることを実accountで確認することである。通常testはcredentialも
課金も不要であり、この手順だけが明示的なopt-in live testである。

## 実装済みの検査

`linode-preflight`はresourceを作成せず、次を検査する。

- regionが`ok`で、`Linodes`、`Metadata`、`Linode Interfaces` capabilityを持つ。
- instance typeが存在する。
- imageがavailable、非deprecatedで、`cloud-init` capabilityを持つ。
- 指定した既存Firewallがenabledである。

capacityは検査直後にも変化し得るため、preflightは作成可能性を保証しない。create APIの応答を
最終判断とする。

`linode-gate1-check`は次を一つの試験として実行する。

1. `linode/debian13`、一意なownership tag、UTCの期限tag、cloud-init MetadataでVMを作る。
2. local disk encryptionを無効にし、新しいLinode Interfaceと指定Firewallを同時に作って
   `running`を待つ。
3. 完全なownership tag、`has_user_data=true`、`disk_encryption=disabled`を再観測する。
4. Linode InterfaceごとのFirewall endpointから実際の関連付けを再観測する。
5. Linode Backupが無効であることを確認する。
6. 完全なownership identityが一致する場合だけ削除し、API上の不存在を確認する。

create応答を失った場合は同じcreateを再送せず、一意なtagからresourceを再発見する。期限tagは
回収時の手掛かりであって削除権限ではない。削除はsystem、Server Unit、Runの3 tagすべてが
一致する場合に限る。

## 前提

- 対象region、instance type、手動作成済みでenabledなFirewall ID
- break-glass調査用SSH公開鍵のfile path
- 短い有効期限で専用に作成したPersonal Access Token
- API tokenの`linodes:read_write`と、preflight用の`firewall:read_only`。account user側にも
  Linode/Tagsの作成・変更権限が必要
- account-wide `backups_enabled`が無効であること

tokenは`LINODE_TOKEN`環境変数からだけ読み、引数、DB、通常出力へ保存しない。公式APIによれば
Linode作成は課金を開始し、account-wide backup設定が有効ならrequestの
`backups_enabled=false`より優先され追加料金が発生し得る。check自身も作成後にBackup無効を
検査するが、不要な料金を避けるため事前にaccount設定を確認する。

## 実行

まず課金なしのpreflightを行う。

```bash
export LINODE_TOKEN='temporary-purpose-scoped-token'

uv run mc-control-plane linode-preflight \
  --region us-ord \
  --instance-type g6-standard-2 \
  --firewall-id 12345 \
  --ssh-public-key ~/.ssh/id_ed25519.pub
```

値は実accountの選択に置き換える。preflight成功後、料金とcreate/deleteを理解した上で明示flagを
付ける。

```bash
uv run mc-control-plane linode-gate1-check \
  --region us-ord \
  --instance-type g6-standard-2 \
  --firewall-id 12345 \
  --ssh-public-key ~/.ssh/id_ed25519.pub \
  --confirm-billable-create-delete
```

開始時に`recovery-run-id`が表示される。成功条件は最後の出力が`metadata=yes firewall=yes
backups=disabled disk-encryption=disabled cleanup=confirmed`を含むことである。

Linodeのprovisioningと削除完了には時間がかかる。commandは各pollで`provisioning`、`running`、
`deleting`、`absent`などの進捗を表示する。cleanupはdelete requestの送信だけで成功とせず、APIで
不存在を確認するまで待つ。

process強制終了やControl Plane側のnetwork断で自動cleanupを確認できなかった場合は、表示された
Run IDを使う。次のcommandも完全なownership identityが一致するresourceだけを削除する。

```bash
uv run mc-control-plane linode-gate1-cleanup \
  --system-id mc-control-plane \
  --run-id gate1-REPLACE_WITH_RECORDED_ID \
  --ssh-public-key ~/.ssh/id_ed25519.pub \
  --confirm-owned-delete
```

live acceptance後はtokenをrevokeし、この文書のstatusと実行日、使用したregion/typeだけを更新する。
token、SSH key内容、resourceのIP addressはcommitしない。

## Live findings

2026-07-22の最初の試行では、`jp-tyo-3` / `g6-nanode-1`のpreflight、Linode作成、失敗時cleanupは
成功した。一方、Firewall確認がlegacy interface向けのLinode直下endpointを呼び、Linode
InterfacesではHTTP 400になった。失敗時cleanupによりtest Linodeの不存在は確認された。

この実測を受け、Firewall確認を各Linode Interfaceの公式endpointへ変更し、同じ誤りを再現する
adapter testを追加した。

同日の修正版による再試行では、同じregion/typeで`provisioning -> booting -> running`を観測し、
Metadata、Linode Interface上のFirewall、Backup無効、local disk encryption無効をすべて確認した。
check自身が所有Linodeを削除し、API上の不存在まで確認した。Cloud Managerのeventでも
create、boot、Firewall関連付け、shutdown、deleteの順序とresourceが残っていないことを人間が確認した。

`linode-gate1-cleanup`は正常系の後続stepではなく、checkが強制終了した場合の回収用である。check成功後に
`resources=none absent=yes`となるのは正常である。回収時の`--system-id`はcheckで使用した値と完全に
一致させる必要がある。

## Gate判定

実装、credential-free test、実accountのlive check、削除後の人間による確認が完了したため、
Gate 1全体をCompleteとする。

## 公式資料

- [Create a Linode](https://techdocs.akamai.com/linode-api/reference/post-linode-instance)
- [List Linode interface firewalls](https://techdocs.akamai.com/linode-api/reference/get-linode-interface-firewalls)
- [Disk encryption](https://techdocs.akamai.com/cloud-computing/docs/local-disk-encryption)
- [Get started with the Linode API](https://techdocs.akamai.com/linode-api/reference/get-started)
- [Manage personal access tokens](https://techdocs.akamai.com/cloud-computing/docs/manage-personal-access-tokens)
- [Official Python SDK](https://github.com/linode/linode_api4-python)
