# ADR-0017: 通常運用を独立した単一ノードserviceで実行する

- Status: Accepted
- Date: 2026-07-23

## Context

Gate harnessでは長い引数列、`uv run`、`nohup`、複数terminalが必要だった。これは検証には有用だが、
通常運用のinterfaceとしては不適切である。一方、Control Planeは一台、SQLite writerも一つでよく、
第三者配布や汎用platform化はscope外である。

長時間workflowをCLI process内で同期実行すると、terminal切断で処理の所有者が失われ、別操作も
待たされる。逆に最初から複数workerや分散queueを導入すると、Operation claim、lease、SQLite write
競合を増やす。

Python標準の`http.server`は一般的な公開production server向けではない。そのためHost APIを直接
Internetへbindせず、loopback上の限定endpointとしてCaddyの後ろへ置く必要がある。

## Decision

- CLIと将来のDiscord Botはapplication command/queryを呼ぶ薄いadapterとする。変更要求はSQLiteへ
  commitした時点で応答し、長時間処理を所有しない。
- Host APIとOperation reconcilerを別のsystemd serviceとして常駐させ、一つのtargetで管理する。
- reconcilerはOperationを一stepずつ進め、外部状態待ちは`retry_wait`として永続化する。
- Pythonの`asyncio`を採用条件にしない。workflow/process-levelの非同期性、bounded I/O、
  durable resumeを必要条件とする。
- Host APIは`127.0.0.1`へbindし、Caddyが許可した二つのPOST endpointと一つのartifactだけを
  HTTPS公開する。
- runtimeはrepositoryの`.venv`にinstall済みのconsole scriptを使い、通常運用で`uv run`を使わない。
- strict TOML configを`/etc/mc-control-plane/config.toml`へ集約し、secret値はconfigへ書かない。
- agent artifact URLは`/artifacts/host-agent.whl`へ固定し、cloud-initのSHA-256検証をartifact identity
  とする。HTTP cacheは無効にする。
- 日常CLIは`start`, `status`, `snapshot`, `snapshots`, `stop`を提供する。既定は即時応答で、
  `--wait`はCLIだけが状態をpollする任意の表示機能とする。

## References

- [systemd unit relationships](https://www.freedesktop.org/software/systemd/man/systemd.unit.html)
- [Python `http.server` security warning and `ThreadingHTTPServer`](https://docs.python.org/3/library/http.server.html)
- [Python `tomllib`](https://docs.python.org/3/library/tomllib.html)

## Consequences

### Positive

- terminal、CLI、Discord Botが落ちても常駐reconcilerはOperationを継続する。
- Host APIのR2/API遅延とreconcilerのprovider遅延をprocess境界で分離できる。
- systemdが再起動、log、boot時起動を管理し、`nohup`と手動process管理が不要になる。
- agent更新でCaddyfileを変更しない。
- 日常操作は一commandになり、Gate用の詳細引数を繰り返さない。

### Negative

- reconcilerは一cycle内でdue Operationを直列処理する。一つのbounded provider call中は後続Operationが
  遅れる。
- `opc`と固定filesystem layoutを前提にするprivate deploymentであり、汎用installerではない。
- 標準HTTP serverはloopback/Caddy/限定routeという外部境界へ依存する。

## Reconsider when

- 同時に多数のServer Unitを操作し、bounded I/Oの直列遅延が実害になる。
- Control Plane writerを複数nodeへ増やす。
- Host APIをCaddyの限定route外で公開する、または第三者へserviceとして提供する。
- 明示的なAPI serviceを導入し、CLIもremote clientへ変える。
