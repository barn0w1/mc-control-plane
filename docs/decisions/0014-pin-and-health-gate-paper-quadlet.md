# ADR-0014: Paper Quadletを固定しhealthでreadinessを判定する

- Status: Accepted
- Date: 2026-07-23

## Context

Gate 5では`itzg/minecraft-server`を起動できるだけでなく、同じ入力から同じworkloadを再構成し、
Minecraftが実際にrequestを受けられる状態とsystemd processが存在するだけの状態を区別する必要がある。
またMinecraftの終了にはworld保存時間が必要で、systemdのtimeoutだけを延ばしてもPodman既定の短い
stop timeoutによる強制終了を防げない。

## Decision

- container imageはSHA-256 digest、Minecraftは完全version、Paperは正の整数buildで固定する。
- `TYPE=PAPER`を使い、EULA同意はlive CLIの明示flagでだけ受け付ける。
- root disk上のRun専用data directoryを`/data`へbind mountする。
- [ADR-0015](0015-use-dedicated-minecraft-identity.md)に従い、Minecraft processとdataを固定された
  非login UID/GIDへ揃え、起動前に検証してから起動時chownを無効にする。
- Quadletの`HealthCmd=mc-health`と`Notify=healthy`を使い、systemd active、container running、
  health healthy、not pausedを同時に満たした状態だけを`ready`とする。
- `STOP_DURATION=120`、Quadlet `StopTimeout=180`、systemd `TimeoutStopSec=240`の順に長くし、
  Minecraft wrapper、Podman、systemdの各層に正常終了の猶予を与える。
- image pullは`Pull=missing`とし、digestがlocalにない場合だけ取得する。Run中の自動更新は行わない。
- Quadletはboot時にenableせず、restore確認後にHost commandから明示起動する。

実行中の手動snapshotでは、RCONでsaveを止めてflushし、containerをpauseしている間だけresticを実行する。
必ずunpauseとsave再開を試み、agent中断後の同一command再配送では残ったpauseを先に回復する。

## Consequences

- image/tagやPaperの上流更新でRunの内容が暗黙に変わらない。
- Java processが起動しただけの状態を利用可能と誤報しない。
- graceful stopとlive snapshotの整合性をHost agentの固定actionとしてtestできる。
- version/build更新は明示的なspec変更と再検証が必要になる。
- `mc-health`、RCON、itzg imageのshutdown contractへ依存するため、image更新時にGate 5を再確認する。
- 外部clientからTCP 25565へ到達できることは内部healthとは別の観測であり、Gate 5の後続項目となる。

## References

- [Podman Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
- [Paper server type](https://docker-minecraft-server.readthedocs.io/en/latest/types-and-platforms/server-types/paper/)
- [Shutdown options](https://docker-minecraft-server.readthedocs.io/en/latest/configuration/misc-options/)
