# Gate 4: Data lifecycle acceptance

## Status

**Complete**（2026-07-23）。2026-07-22にproject ownerが実accountで3台のfresh Linodeを順番に
作成し、2回のsnapshot、2回の独立restore、内容digest一致、snapshot commit前には削除しないこと、
最後の残存Linodeなしを確認した。

Gate 4はMinecraftを起動せず、固定fixture dataだけで次を一周させる。

1. fresh Run AでServer Unit専用restic repositoryを初期化する。
2. 初期fixtureをsnapshotし、snapshot IDをSQLiteへcommitしてからRun AのLinodeを削除する。
3. fresh Run BでそのIDを明示restoreし、digest一致時にsnapshotを検証済みとして記録してfixtureを変更する。
4. 変更後snapshotをSQLiteへcommitしてからRun BのLinodeを削除する。
5. fresh Run Cで変更後IDを明示restoreし、digest一致時にsnapshotを検証済みとして記録してLinodeを削除する。

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
(method('GET') && path('/artifacts/mccp-host-agent-0.3.3.whl'))
```

## Host API

R2 credentialはHost APIがpoll delivery時に発行する。reconcilerやGate 4 commandへR2 secretを渡さない。

```bash
nohup uv run mc-control-plane host-api-serve \
  --database ./control-plane.db \
  --bind 127.0.0.1 \
  --port 8443 \
  --agent-wheel dist/host-agent/mccp_host_agent-0.3.3-py3-none-any.whl \
  --r2-account-id YOUR_ACCOUNT_ID \
  --r2-bucket YOUR_BUCKET \
  --r2-parent-access-key-id YOUR_PARENT_ACCESS_KEY_ID \
  --cloudflare-api-token-file ./cloudflare-api-token \
  > mccp.log 2>&1 &
```

Host APIが配送するdata lease schema v2にはrepository URL、prefix/operation限定R2 temporary credential、
permission、expiryだけが含まれる。passwordはwire、SQLite、agent journal、subprocess environmentへ含めない。

R2設定を渡した`host-api-serve`はlisten開始前に、Gate用prefixへ書き込み可能なtemporary credentialを
実際に一つ発行して破棄する。成功時には次の非secret行を出す。Cloudflare API tokenの権限、親access
key ID、bucket指定が不正ならprocessは起動せず、課金されるLinode作成より前に失敗する。

```text
R2 data lease preflight passed: bucket=YOUR_BUCKET permission=object-read-write ttl=3600s
```

default TTLは3600秒であり、data commandのdefault deadline 1800秒より60秒以上長い。timeoutを拡張する
場合は`--r2-lease-ttl-seconds`もcommand deadlineより60秒以上長くする。

## Live check

このcheckは最大3台のLinodeを順番に作成するため課金を伴う。各Runは通常のGate 3 durable startを使い、
同一Server Unitのactive Run lockを維持する。

```bash
uv run mc-control-plane linode-gate4-check \
  --database ./control-plane.db \
  --server-unit-id gate4-data-lifecycle \
  --host-bootstrap-key ./host-bootstrap.key \
  --control-plane-url https://mc-control-plane.hss-science.org \
  --agent-wheel dist/host-agent/mccp_host_agent-0.3.3-py3-none-any.whl \
  --fixture-image docker.io/library/alpine@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b \
  --region jp-tyo-3 \
  --instance-type g6-nanode-1 \
  --firewall-id 79203454 \
  --ssh-public-key ~/.ssh/akamai_ed25519.pub \
  --confirm-billable-three-host-check
```

成功行には`fresh-host-restore=passed snapshots=verified snapshot-before-delete=passed
cleanup=confirmed`が含まれる。

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

## Live acceptance結果

2026-07-22の実測結果は次の通りである。値はsecretではなく、restic snapshot IDとfixture digestである。

| 検査 | 結果 |
| --- | --- |
| Run A snapshot | `2baa5f107e08ae340a76f85ec5a5f8266d94545e8f0c30dd1d5cac8b55530624` |
| 初期fixture SHA-256 | `1bcde7808e545636a2134cb748f581b22ee8316a443680c4695306c6e08d834d` |
| Run B fresh restore | 初期digest一致 |
| Run B snapshot | `60dd65b48fad608325e8dfd32f91ef79017804a048bac45c08af6c6d025e46d6` |
| 変更後fixture SHA-256 | `ea5923f51c75dddb782390cbcf1537b0e7f72e3d0cd4062da4c4466b6664c1ed` |
| Run C fresh restore | 変更後digest一致 |
| 削除順序 | 各snapshot commit後にだけLinodeを削除 |
| 最終cleanup | 3台ともAPI上でabsent、ownership tag検索0件 |

password promptやrepository passwordなしで全restic操作が完了し、SQLiteには2件のSnapshot recordが
残った。これによりGate 4のlive acceptance criteriaを満たした。

## 実地検証からの改善

live acceptanceでは次の境界問題も発見したため、Gate 5へ進む前に修正した。

- Cloudflare API tokenの権限不足がHost側では連続HTTP 503にしか見えなかった。Host API起動時に
  temporary credential発行preflightを追加し、CloudflareのHTTP status/error code/messageだけを
  secretなしでControl Plane logへ残す。
- credential発行失敗中もHostCommandが`delivered`となり`delivery_count`が増えていた。data leaseの
  発行成功後にだけdeliveryをcommitし、失敗中は`pending`を保つ。
- Host APIとGate harnessという別processのSQLite writerが一度競合した。WALと`busy_timeout`に加え、
  enrollment/poll/delivery commitへ短いbounded retryを追加した。解消しない場合はstack traceで接続を
  切らず、agentが再試行できるHTTP 503と短い非secret logを返す。
- 以前はsnapshot作成時刻を同時に`verified_at`へ保存していた。snapshot作成成功と独立restore成功を
  区別し、fresh Hostでdigest一致を確認した時点だけを`verified_at`として保存する。migration 5は
  旧形式の`verified_at = created_at`を検証時刻不明の`NULL`へ正規化する。
- temporary credentialの旧default TTL 900秒は最大1800秒のrestic commandより短かった。defaultを
  3600秒へ変更し、lease発行時にcommand deadlineを十分に覆うことを検査する。

SQLiteは引き続き単一Control Planeに適切である。今回の1回の競合はdatabase serverへ移行する根拠では
なく、短いtransactionを使う複数process間で通常発生しうる一時競合として扱う。bounded retry後も
継続するlockが観測された場合は、次GateではなくControl Plane process配置を先に再検討する。

## Gate判定

live checkの成功、2件のSnapshot record、R2専用prefix、残存Linodeなしをproject ownerが確認したため、
Gate 4はCompleteである。Infra、Host、Data基盤の修正後checkpointも確認できたため、Gate 5はこの
確定済みdata lifecycleの上へMinecraft workloadだけを追加する。

加えて、Gate 4が作成したrepositoryをR2 credentialだけで開けることを確認する。Host agentは
`cat config`、`init`、`snapshots`、`backup`、`restore`のすべてへ`--insecure-no-password`を明示するため、
通常のpassword promptやpassword fileは発生しない。

この方式へ変更する前の開発版がpassword付きrepositoryを同じprefixへ作成していた場合、新agentはその
repositoryを開けない。正式なGate 4 acceptanceでは空password方式の新規prefixを使用済みである。
旧prefixに必要なsnapshotが存在する場合は、旧data root keyを破棄せずmigrationを別作業として行う。

## 公式資料

- [Cloudflare R2 temporary credentials](https://developers.cloudflare.com/api/resources/r2/subresources/temporary_credentials/methods/create/)
- [Cloudflare R2 authentication and permissions](https://developers.cloudflare.com/r2/api/tokens/)
- [SQLite WAL concurrency](https://www.sqlite.org/wal.html)
- [SQLite busy timeout](https://www.sqlite.org/pragma.html#pragma_busy_timeout)
- [restic S3 repository and temporary session token](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html)
- [restic exit codes and JSON output](https://restic.readthedocs.io/en/stable/075_scripting.html)
- [restic explicit snapshot restore](https://restic.readthedocs.io/en/stable/050_restore.html)
- [restic 0.18 `--insecure-no-password`](https://restic.readthedocs.io/en/v0.18.0/manual_rest.html)
- [restic repository format](https://restic.readthedocs.io/en/v0.18.0/100_references.html)
