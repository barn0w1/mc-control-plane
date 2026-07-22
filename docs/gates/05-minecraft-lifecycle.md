# Gate 5: Minecraft lifecycle acceptance

## Status

**Implementation complete / live acceptance pending**（2026-07-23）。自動testでは、Paper Quadletの
生成前検証、固定version、health-based readiness、graceful stop、実行中snapshotのquiesce/resume、
中断後のpause復旧、Host command再配送、snapshot種別を確認している。実accountでの2台のfresh
Linodeを使うcheckが成功し、project ownerが結果を確認した時点でCompleteとする。

Gate 5はMinecraftの基礎的なworkload lifecycleを、Gate 1から4で完成したInfra、Host、Dataの上へ
載せる。Minecraft設定、plugin、定期snapshot、retentionは扱わない。

## 合格する一連の処理

| Phase | 検証内容 |
| --- | --- |
| fresh Run A | passwordless repositoryを初期化し、固定した`itzg/minecraft-server` Paper Quadletを適用する |
| first start | systemd service、Podman container、`mc-health`がすべて正常になった時だけ`ready`とする |
| manual snapshot | `save-off`、`save-all flush`、container pause、restic、unpause、`save-on`を一command内で行う |
| first stop | Podmanへ180秒、systemdへ240秒の猶予を与えて正常停止し、停止後snapshotをcommitする |
| delete A | 停止後snapshotのIDがSQLiteへcommitされた後だけRun AのLinodeを削除する |
| fresh Run B | Run Aの停止後snapshotを明示restoreし、起動前のdata digest一致で検証済みにする |
| restart | 同じ固定Quadletを適用し、Paperが再び`ready`になることを確認する |
| final stop | 正常停止、最終snapshot commit、Run BのLinode削除、所有tag検索0件を確認する |

実行中snapshotの途中でagent processが止まりcontainerがpauseされた場合、同じcommandの再配送時に
最初にunpauseと`save-on`を行ってからsnapshotをやり直す。snapshotや停止後snapshotの確定前に失敗した
場合は、その時点のactive Linodeを自動削除しない。

## 事前準備

Gate 4のDBを残しておく必要はない。空のDB、Host bootstrap key、R2設定、手動作成済みFirewallがあれば
よい。Gate 5用のworldやserver設定を事前作成する必要もない。初回起動時にPaperが空のdata directoryを
初期化し、その全体を不透明なServer Unit payloadとして保存する。

### 1. artifact、DB、bootstrap key

```bash
uv build --project host_agent --out-dir dist/host-agent
uv run mc-control-plane init-db ./control-plane.db
```

keyがない場合だけ作成する。

```bash
uv run mc-control-plane host-bootstrap-key-create ./host-bootstrap.key
```

### 2. Gate 5専用Server Unit

```bash
uv run mc-control-plane server-unit-create \
  --database ./control-plane.db \
  --id gate5-minecraft-lifecycle \
  --name "Gate 5 Minecraft Lifecycle" \
  --region jp-tyo-3 \
  --instance-type g6-nanode-1 \
  --image linode/debian13 \
  --firewall-id 79203454
```

`server-unit-start`は実行しない。二つのRunは`linode-gate5-check`が作成する。1 GB Hostで
`--minecraft-memory 512M`を使うのは最小acceptance用である。Java processがOOMになる場合は、まず
memory設定だけを増やさず、余裕のあるLinode typeへ変更する。

### 3. Minecraft artifactを固定する

次の三つを実行前に決める。

- `--minecraft-image`: `docker.io/itzg/minecraft-server@sha256:...`形式のimage digest
- `--minecraft-version`: `1.21.8`のような完全なMinecraft version
- `--paper-build`: そのMinecraft versionに存在する正の整数のPaper build

mutable tag、`LATEST`、`latest`は受け付けない。image tagを一度resolveする場合も、Gateへ渡す値は
registry名と取得したdigestを結合した完全な参照にする。Minecraft versionとPaper buildは
Gate実行時に自動追従させない。

### 4. CaddyとHost API

Caddyが公開するagent artifactを0.3.0へ更新する。

```caddyfile
mc-control-plane.hss-science.org {
	@allowed <<CEL
        (method('POST') && path('/v1/host/enroll', '/v1/host/poll'))
        ||
        (method('GET') && path('/artifacts/mccp-host-agent-0.3.0.whl'))
    CEL

	handle @allowed {
		reverse_proxy http://127.0.0.1:8443
	}

	handle {
		respond 404
	}
}
```

Gate 4と同じR2設定でHost APIを起動する。default data lease TTL 3600秒はGate 5のdefault command
deadline 2400秒を覆う。`--timeout-seconds`を3600秒近くまで延ばす場合は、lease TTLもdeadlineより
60秒以上長くする。

```bash
nohup uv run mc-control-plane host-api-serve \
  --database ./control-plane.db \
  --bind 127.0.0.1 \
  --port 8443 \
  --agent-wheel dist/host-agent/mccp_host_agent-0.3.0-py3-none-any.whl \
  --r2-account-id YOUR_ACCOUNT_ID \
  --r2-bucket YOUR_BUCKET \
  --r2-parent-access-key-id YOUR_PARENT_ACCESS_KEY_ID \
  --cloudflare-api-token-file ./cloudflare-api-token \
  > mccp.log 2>&1 &
```

起動時に`R2 data lease preflight passed`が表示されることを確認する。CaddyはHost APIだけをroutingする。
Minecraft clientのTCP 25565はCaddyを通らず、Linode Interfaceと手動Firewallを経由する。

### 5. 課金なしpreflight

```bash
export LINODE_TOKEN='secret'

uv run mc-control-plane linode-preflight \
  --region jp-tyo-3 \
  --instance-type g6-nanode-1 \
  --image linode/debian13 \
  --firewall-id 79203454 \
  --ssh-public-key ~/.ssh/akamai_ed25519.pub
```

## Live check

このcheckは最大2台のLinodeを順番に作成するため課金を伴う。Minecraft EULAを確認し、同意する権限が
ある場合だけ`--accept-minecraft-eula`を渡す。このflagはCLIで明示しなければならず、DBや設定から
暗黙に補完しない。先に固定値をshell変数へ設定する。

```bash
export MINECRAFT_IMAGE='docker.io/itzg/minecraft-server@sha256:64_HEX_DIGEST'
export MINECRAFT_VERSION='1.21.8'
export PAPER_BUILD='EXACT_NUMERIC_BUILD'
```

```bash
uv run mc-control-plane linode-gate5-check \
  --database ./control-plane.db \
  --server-unit-id gate5-minecraft-lifecycle \
  --host-bootstrap-key ./host-bootstrap.key \
  --control-plane-url https://mc-control-plane.hss-science.org \
  --agent-wheel dist/host-agent/mccp_host_agent-0.3.0-py3-none-any.whl \
  --fixture-image docker.io/library/alpine@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b \
  --minecraft-image "$MINECRAFT_IMAGE" \
  --minecraft-version "$MINECRAFT_VERSION" \
  --paper-build "$PAPER_BUILD" \
  --minecraft-memory 512M \
  --region jp-tyo-3 \
  --instance-type g6-nanode-1 \
  --firewall-id 79203454 \
  --ssh-public-key ~/.ssh/akamai_ed25519.pub \
  --accept-minecraft-eula \
  --confirm-billable-two-host-check
```

`--fixture-image`は既存のHost bootstrap schemaに必要だが、Gate 5はGate 2 fixture containerを起動しない。

成功行には次が含まれる。

```text
paper=ready live-snapshot=quiesced graceful-stop=passed fresh-host-restore=passed restart=passed cleanup=confirmed
```

snapshot recordを確認する。

```sql
SELECT id, run_id, kind, created_at, verified_at
FROM snapshots
WHERE server_unit_id = 'gate5-minecraft-lifecycle'
ORDER BY created_at;
```

少なくともRun Aの`manual`と`stop`、Run Bの`stop`が残る。Run Aの停止後snapshotだけはfresh Run Bの
起動前digest一致により`verified_at`が設定される。

失敗後はlogとHost command resultを調査し、現在のactive Runだけを明示削除する。

```bash
uv run mc-control-plane linode-gate3-cleanup \
  --database ./control-plane.db \
  --server-unit-id gate5-minecraft-lifecycle \
  --system-id mc-control-plane \
  --ssh-public-key ~/.ssh/akamai_ed25519.pub \
  --confirm-owned-delete
```

## Gate 5に含めないもの

- 定期snapshotとschedule
- retention、`forget`、`prune`
- Paper設定、plugin、world生成内容の編集
- 自動upgradeやmutable image tag追従
- VM reboot後のMinecraft自動再開
- Minecraft clientからの外部TCP接続を含むplayability test

手動snapshotの安全なprimitiveは実装済みなので、定期実行は運用要件と失敗時の表示を決めた後で同じ
primitiveへ接続できる。VM reboot recoveryと外部接続確認も、Gate 5の基本lifecycleを実地確認してから
独立したacceptance項目として追加する。

## 公式資料

- [Podman Quadlet container units](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
- [itzg/minecraft-server Paper configuration](https://docker-minecraft-server.readthedocs.io/en/latest/types-and-platforms/server-types/paper/)
- [itzg/minecraft-server shutdown options](https://docker-minecraft-server.readthedocs.io/en/latest/configuration/misc-options/)
- [Paper downloads service](https://docs.papermc.io/misc/downloads-service/)
- [Minecraft EULA](https://www.minecraft.net/eula)
