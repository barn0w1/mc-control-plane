# Gate 1: Infra lifecycle acceptance

- Implementation: Complete
- Automated verification: Complete
- Akamai Cloud live acceptance: Pending

Gate 1の目的は、MinecraftやHost agentより前に、Control Planeが一つのDebian 13 Linodeを
安全に作成、観測、削除できることを実accountで確認することである。通常testはcredentialも
課金も不要であり、この手順だけが明示的なopt-in live testである。

## 実装済みの検査

`linode-preflight`はresourceを作成せず、次を検査する。

- regionが`ok`で、`Linodes`と`Metadata` capabilityを持つ。
- instance typeが存在する。
- imageがavailable、非deprecatedで、`cloud-init` capabilityを持つ。
- 指定した既存Firewallがenabledである。

capacityは検査直後にも変化し得るため、preflightは作成可能性を保証しない。create APIの応答を
最終判断とする。

`linode-gate1-check`は次を一つの試験として実行する。

1. `linode/debian13`、一意なownership tag、UTCの期限tag、cloud-init MetadataでVMを作る。
2. 新しいLinode interfaceと指定Firewallを同時に作り、`running`を待つ。
3. 完全なownership tag、`has_user_data=true`、実際のFirewall関連付けを再観測する。
4. Linode Backupが無効であることを確認する。
5. 完全なownership identityが一致する場合だけ削除し、API上の不存在を確認する。

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

開始時に`recovery-run-id`が表示される。成功条件は最後の出力が
`metadata=yes firewall=yes backups=disabled cleanup=confirmed`を含むことである。

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

## Gate判定

実装とcredential-free testは完了している。実accountのlive checkが成功し、人間がAkamai Cloud上に
test Linodeが残っていないことを確認した時点でGate 1全体をCompleteとする。それまではGate 2の
設計・local実装を進めてもよいが、Gate 2の実環境検証へGate 1の未確認事項を持ち込まない。

## 公式資料

- [Create a Linode](https://techdocs.akamai.com/linode-api/reference/post-linode-instance)
- [Get started with the Linode API](https://techdocs.akamai.com/linode-api/reference/get-started)
- [Manage personal access tokens](https://techdocs.akamai.com/cloud-computing/docs/manage-personal-access-tokens)
- [Official Python SDK](https://github.com/linode/linode_api4-python)
