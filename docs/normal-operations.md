# 通常運用CLI

この文書はGate acceptance harnessではなく、通常の永続Operationを操作する入口を示す。
Control Plane API、Host API、reconcilerは常駐service化する前段階のため、現在は別processとして起動する。

## 1. Server Unit登録

Minecraft imageはdigest、Minecraft versionとPaper buildはexact valueで固定する。

```bash
uv run mc-control-plane server-unit-create \
  --database ./control-plane.db \
  --id survival \
  --name "Survival" \
  --region jp-tyo-3 \
  --instance-type g6-nanode-1 \
  --image linode/debian13 \
  --firewall-id 79203454 \
  --minecraft-image "$MINECRAFT_IMAGE" \
  --minecraft-version "$MINECRAFT_VERSION" \
  --paper-build "$PAPER_BUILD" \
  --minecraft-memory 512M \
  --accept-minecraft-eula
```

Minecraft設定、plugin、world内容はCLI引数に展開しない。これらはServer Unitのdata payloadとして
snapshot/restoreされる。

## 2. 常駐process

Host APIはGate 4/5と同じR2設定で起動し、CaddyなどのTLS reverse proxyから
`/v1/host/enroll`、`/v1/host/poll`、agent artifactだけを公開する。reconcilerは同じSQLite DBを使う。

```bash
export LINODE_TOKEN='...'

uv run mc-control-plane reconciler-run \
  --database ./control-plane.db \
  --host-bootstrap-key ./host-bootstrap.key \
  --control-plane-url https://mc-control-plane.example \
  --agent-wheel dist/host-agent/mccp_host_agent-0.3.4-py3-none-any.whl \
  --fixture-image "$FIXTURE_IMAGE" \
  --system-id mc-control-plane \
  --ssh-public-key ~/.ssh/akamai_ed25519.pub
```

`host-api-serve`にはR2 account、bucket、parent access key ID、Cloudflare API token fileを渡す。
secretをshell historyや文書へ残さない。

## 3. 操作

```bash
# 最新のcommit済みsnapshotを自動選択。存在しなければ空dataから開始する。
uv run mc-control-plane server-unit-start \
  --database ./control-plane.db \
  --server-unit-id survival

# 特定snapshotを復元する場合
uv run mc-control-plane server-unit-start \
  --database ./control-plane.db \
  --server-unit-id survival \
  --source-snapshot-id <snapshot-id>

uv run mc-control-plane server-unit-status \
  --database ./control-plane.db \
  --server-unit-id survival

uv run mc-control-plane server-unit-snapshot \
  --database ./control-plane.db \
  --server-unit-id survival

uv run mc-control-plane server-unit-snapshots \
  --database ./control-plane.db \
  --server-unit-id survival

uv run mc-control-plane server-unit-stop \
  --database ./control-plane.db \
  --server-unit-id survival
```

startはLinode/Host ready後にrestoreまたはrepository初期化を行い、Quadlet適用、Paper readinessまで
同じOperationで進む。manual snapshotはMinecraftをquiesceするHost commandを使う。stopはgraceful
stopと停止snapshot commitが成功するまでLinodeを削除しない。

## 4. 競合とblocked

進行中Operationがあると別要求は拒否され、現在のoperation ID、kind、state、stepが表示される。
処理を中断したり後続要求を予約したりはしない。現在処理の完了後に操作を再実行する。

`blocked`は自動で進めるべきでない失敗である。原因をstatusとControl Plane/Host logで確認し、
修正後に同じOperationを明示的に再開する。

```bash
uv run mc-control-plane operation-retry \
  --database ./control-plane.db \
  --operation-id <operation-id>
```

resource所有権不一致や原因不明のdata errorを、確認せずretryしない。

## 5. 現在の検証状態

Gate 5 harnessによる実accountの一周と、通常Start/Snapshot/Stop Operationのdeterministic scenario testは
完了している。通常CLIからの一周は次のlive acceptance checkpointで確認し、それまではOperational MVPを
実環境Completeとは扱わない。
