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

- Gate 3を通過したControl Plane、Firewall、Debian 13 RuntimeSpec
- 事前作成済みR2 bucket
- temporary credentialを発行できるCloudflare API tokenと親R2 access key ID
- 停止中でactive Run/Operationを持たないServer Unit
- `LINODE_TOKEN`環境変数

secret fileはControl Plane Hostだけに置き、group/other permissionを付けない。data root keyは失うと
Server Unitごとのrestic passwordを再導出できないため、安全な場所へbackupする。

```bash
uv build --project host_agent --out-dir dist/host-agent
uv run mc-control-plane data-root-key-create ./data-root.key
chmod 600 ./cloudflare-api-token
```

Caddyで公開するartifact matcherを新versionへ更新する。

```caddyfile
(method('GET') && path('/artifacts/mccp-host-agent-0.2.0.whl'))
```

## Host API

R2 credentialはHost APIがpoll delivery時に発行する。reconcilerやGate 4 commandへR2 secretを渡さない。

```bash
nohup uv run mc-control-plane host-api-serve \
  --database ./control-plane.db \
  --bind 127.0.0.1 \
  --port 8443 \
  --agent-wheel dist/host-agent/mccp_host_agent-0.2.0-py3-none-any.whl \
  --r2-account-id YOUR_ACCOUNT_ID \
  --r2-bucket YOUR_BUCKET \
  --r2-parent-access-key-id YOUR_PARENT_ACCESS_KEY_ID \
  --cloudflare-api-token-file ./cloudflare-api-token \
  --data-root-key ./data-root.key \
  > mccp.log 2>&1 &
```

## Live check

このcheckは最大3台のLinodeを順番に作成するため課金を伴う。各Runは通常のGate 3 durable startを使い、
同一Server Unitのactive Run lockを維持する。

```bash
uv run mc-control-plane linode-gate4-check \
  --database ./control-plane.db \
  --server-unit-id gate3-survival \
  --host-bootstrap-key ./host-bootstrap.key \
  --control-plane-url https://mc-control-plane.hss-science.org \
  --agent-wheel dist/host-agent/mccp_host_agent-0.2.0-py3-none-any.whl \
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
WHERE server_unit_id = 'gate3-survival'
ORDER BY created_at;
```

失敗後の明示cleanupは現在のactive Runだけを対象にする。

```bash
uv run mc-control-plane linode-gate3-cleanup \
  --database ./control-plane.db \
  --server-unit-id gate3-survival \
  --system-id mc-control-plane \
  --ssh-public-key ~/.ssh/akamai_ed25519.pub \
  --confirm-owned-delete
```

## Gate判定

live checkの成功、2件のSnapshot record、R2専用prefix、残存Linodeなしをproject ownerが確認した時点で
Gate 4をCompleteとする。それまでは実装と自動testだけがCompleteであり、Gate 5へは進まない。

## 公式資料

- [Cloudflare R2 temporary credentials](https://developers.cloudflare.com/api/resources/r2/subresources/temporary_credentials/methods/create/)
- [restic S3 repository and temporary session token](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html)
- [restic exit codes and JSON output](https://restic.readthedocs.io/en/stable/075_scripting.html)
- [restic explicit snapshot restore](https://restic.readthedocs.io/en/stable/050_restore.html)
