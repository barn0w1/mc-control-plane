# Gate 4: Data lifecycle acceptance

## Status

**実装と自動testは完了、実accountのlive acceptance待ち**（2026-07-22）。Gate 3はproject ownerの
実地結果によりCompleteである。

Gate 4はMinecraftを起動せず、固定fixture dataだけで次を一周させる。

1. fresh Run AでServer Unit専用restic repositoryを初期化する。
2. 初期fixtureをsnapshotし、snapshot IDをSQLiteへcommitしてからRun AのLinodeを削除する。
3. fresh Run BでそのIDを明示restoreし、digest一致を確認してfixtureを変更する。
4. 変更後snapshotをSQLiteへcommitしてからRun BのLinodeを削除する。
5. fresh Run Cで変更後IDを明示restoreし、digest一致を確認してLinodeを削除する。

途中で失敗した場合、現在のRunは自動削除しない。特に変更後dataがsnapshot未確定のときにroot diskを
失わないためである。調査後、明示的なGate 3 cleanupで所有Runだけを削除する。

## 事前準備

Gate 4にMinecraft Server、world、Paper、pluginは不要であり、用意しない。Host agentが
`gate4-fixture.json`という小さな固定JSONを作成し、そのsnapshot、fresh Hostへのrestore、変更後の
再snapshotだけを検証する。CLIの`--fixture-image`にはbootstrap設定上必要な固定Alpine imageを渡すが、
Gate 4はMinecraft containerもGate 2用fixture containerも起動しない。

DBを削除していてもGate 3のrecordを復元する必要はない。ここでいう「Gate 3を通過したControl Plane」
とは、Gate 3までの実装を含む現在のcodebaseと、LinodeからHTTPSで到達できるHost APIがあることを指す。

### 1. ローカルartifactと空のDBを用意する

```bash
uv build --project host_agent --out-dir dist/host-agent
uv run mc-control-plane init-db ./control-plane.db
```

Host bootstrap keyが手元にない場合だけ新規作成する。DBをresetしても既存keyは再利用できる。

```bash
uv run mc-control-plane host-bootstrap-key-create ./host-bootstrap.key
```

### 2. Gate 4専用Server Unitを登録する

`RuntimeSpec`は別の設定fileではない。次の`server-unit-create`へ渡すregion、instance type、Debian 13
image、Firewall IDの組がRuntimeSpecとしてSQLiteへ保存される。新規作成直後のServer Unitは
`desired-state=stopped`で、active RunもOperationもないため、そのままGate 4へ使用できる。

```bash
uv run mc-control-plane server-unit-create \
  --database ./control-plane.db \
  --id gate4-data-lifecycle \
  --name "Gate 4 Data Lifecycle" \
  --region jp-tyo-3 \
  --instance-type g6-nanode-1 \
  --image linode/debian13 \
  --firewall-id 79203454
```

このServer Unitへ`server-unit-start`を実行しない。3回のRun作成、data操作、snapshot確定後の削除は
`linode-gate4-check`が順番に行う。

### 3. Akamai Cloud側の入力を確認する

- `LINODE_TOKEN`を環境変数へ設定する。
- `--firewall-id`には手動作成済みFirewallの数値IDを使う。Minecraft用port 25565の疎通はGate 4の
  判定対象ではない。HostからControl PlaneとR2へのoutbound HTTPS、およびDebian bootstrapに必要な
  outbound通信が許可されていればよい。
- `--ssh-public-key`には既存の公開鍵を指定する。通常workflowはSSH接続を使用しない。

同じ値を使って、課金resourceを作成しないpreflightを先に通す。

```bash
export LINODE_TOKEN='secret'

uv run mc-control-plane linode-preflight \
  --region jp-tyo-3 \
  --instance-type g6-nanode-1 \
  --image linode/debian13 \
  --firewall-id 79203454 \
  --ssh-public-key ~/.ssh/akamai_ed25519.pub
```

### 4. R2設定を用意する

- 事前作成済みR2 bucket
- temporary credentialを発行できるCloudflare API token
- temporary credentialの親となるR2 access key ID

Cloudflare API tokenはControl Plane Host上のfileへ保存する。secret fileにはgroup/other permissionを
付けない。

```bash
chmod 600 ./cloudflare-api-token
```

restic repositoryは`--insecure-no-password`で作成し、repository passwordやdata root keyを使用しない。
restic format内部の暗号化・認証は残るが、復元に必要なsecretはR2 access credentialだけである。

Caddyで公開するartifact matcherを新versionへ更新する。

```caddyfile
(method('GET') && path('/artifacts/mccp-host-agent-0.2.1.whl'))
```

## Host API

R2 credentialはHost APIがpoll delivery時に発行する。reconcilerやGate 4 commandへR2 secretを渡さない。

```bash
nohup uv run mc-control-plane host-api-serve \
  --database ./control-plane.db \
  --bind 127.0.0.1 \
  --port 8443 \
  --agent-wheel dist/host-agent/mccp_host_agent-0.2.1-py3-none-any.whl \
  --r2-account-id YOUR_ACCOUNT_ID \
  --r2-bucket YOUR_BUCKET \
  --r2-parent-access-key-id YOUR_PARENT_ACCESS_KEY_ID \
  --cloudflare-api-token-file ./cloudflare-api-token \
  > mccp.log 2>&1 &
```

Host APIが配送するdata lease schema v2にはrepository URL、prefix/operation限定R2 temporary credential、
permission、expiryだけが含まれる。passwordはwire、SQLite、agent journal、subprocess environmentへ含めない。

## Live check

このcheckは最大3台のLinodeを順番に作成するため課金を伴う。各Runは通常のGate 3 durable startを使い、
同一Server Unitのactive Run lockを維持する。

```bash
uv run mc-control-plane linode-gate4-check \
  --database ./control-plane.db \
  --server-unit-id gate4-data-lifecycle \
  --host-bootstrap-key ./host-bootstrap.key \
  --control-plane-url https://mc-control-plane.hss-science.org \
  --agent-wheel dist/host-agent/mccp_host_agent-0.2.1-py3-none-any.whl \
  --fixture-image docker.io/library/alpine@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b \
  --region jp-tyo-3 \
  --instance-type g6-nanode-1 \
  --firewall-id 79203454 \
  --ssh-public-key ~/.ssh/akamai_ed25519.pub \
  --confirm-billable-three-host-check
```

成功行には`fresh-host-restore=passed snapshot-before-delete=passed cleanup=confirmed`が含まれる。

```sql
SELECT id, server_unit_id, run_id, kind, created_at, verified_at
FROM snapshots
WHERE server_unit_id = 'gate4-data-lifecycle'
ORDER BY created_at;
```

失敗後の明示cleanupは現在のactive Runだけを対象にする。

```bash
uv run mc-control-plane linode-gate3-cleanup \
  --database ./control-plane.db \
  --server-unit-id gate4-data-lifecycle \
  --system-id mc-control-plane \
  --ssh-public-key ~/.ssh/akamai_ed25519.pub \
  --confirm-owned-delete
```

## Gate判定

live checkの成功、2件のSnapshot record、R2専用prefix、残存Linodeなしをproject ownerが確認した時点で
Gate 4をCompleteとする。それまでは実装と自動testだけがCompleteであり、Gate 5へは進まない。

加えて、Gate 4が作成したrepositoryをR2 credentialだけで開けることを確認する。Host agentは
`cat config`、`init`、`snapshots`、`backup`、`restore`のすべてへ`--insecure-no-password`を明示するため、
通常のpassword promptやpassword fileは発生しない。

この方式へ変更する前の開発版がpassword付きrepositoryを同じprefixへ作成していた場合、新agentはその
repositoryを開けない。Gate 4 live acceptance未実施の前提では新しいServer Unit IDまたは空prefixを使う。
必要なsnapshotが存在する場合は、旧data root keyを破棄せずmigrationを別作業として行う。

## 公式資料

- [Cloudflare R2 temporary credentials](https://developers.cloudflare.com/api/resources/r2/subresources/temporary_credentials/methods/create/)
- [restic S3 repository and temporary session token](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html)
- [restic exit codes and JSON output](https://restic.readthedocs.io/en/stable/075_scripting.html)
- [restic explicit snapshot restore](https://restic.readthedocs.io/en/stable/050_restore.html)
- [restic 0.18 `--insecure-no-password`](https://restic.readthedocs.io/en/v0.18.0/manual_rest.html)
- [restic repository format](https://restic.readthedocs.io/en/v0.18.0/100_references.html)
