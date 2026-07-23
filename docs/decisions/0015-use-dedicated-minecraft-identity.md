# ADR-0015: Minecraft workloadへ固定された専用identityを使用する

- Status: Accepted
- Date: 2026-07-23

## Context

Gate 5の最初のlive acceptanceでは、空のRun data directoryにもかかわらず、
`itzg/minecraft-server`の起動が`Changing ownership of /data to 1000 ...`から進まなかった。
専用UID/GID 2000と`SKIP_CHOWN_DATA=TRUE`へ変更した二回目はownership変更を回避したが、
entrypointが`Changing uid of minecraft to 2000`、`Changing gid of minecraft to 2000`を出した後、
`gosu` processがCPUを使い続け、`/data`を初期化しなかった。

上流imageはrootで開始した場合だけ内部accountを`UID`/`GID`へ変更し、必要なら`/data`をchownしてから
`gosu`で権限を落とす。Podmanの`--user`またはCompose/Quadletの`User`を指定すると、このroot専用分岐を
skipすることが公式に説明されている。`gosu`は対象processへ`exec`する小さなlauncherなので、
高CPUの`gosu`が残り続ける状態は正常なMinecraft初期化ではない。

削除済みHostからsyscall単位の直接原因は確定できない。`NoNewPrivileges`、capability、Podman、
image entrypointのどれかを推測してroot権限を増やすより、不要なaccount変更とprivilege transitionを
起動経路から除く方が単純で、上流が明示的にsupportする。

Execution Hostは一つのServer Unitだけを動かす専用・短命VMであり、複数tenantや対話利用者を
Unix userで分離する要件はない。一方、Minecraftやpluginをroot processとして動かす必要もない。
rootless Podman、user systemd、lingerまで導入すると短命Hostのbootstrap、restore、診断に新しい
失敗境界が増えるため、workload identityの分離とcontainer managerの分離は別に判断する。

## Decision

Host agent、rootful Podman、resticはrootで動かし、Minecraft Java processと永続dataには専用の
`mccp-minecraft` identityを使用する。

- cloud-init bootstrapが`mccp-minecraft` user/groupをUID/GID 1000で作成する。これは
  `itzg/minecraft-server`のimage-native `minecraft` identityと一致させる。passwordはlockし、
  home directory、login shell、sudo権限、SSH keyを持たせない。
- Host agentはsystemd、Quadlet、rootful Podman、restic、ownership検証に必要なためrootのままとする。
- Run親directoryは`root:root`、mode `0700`とする。bind mountする`data` directoryだけを
  `1000:1000`、mode `0700`にする。
- Quadletは`User=1000`と`Group=1000`を明示し、containerを最初からnon-rootで起動する。同じ値を
  `UID`/`GID` environmentにも明示するが、entrypoint内でaccountを変更させない。
- `SKIP_CHOWN_DATA=TRUE`を指定し、container起動時の再帰chownを行わない。Host agentはQuadlet適用前に
  accountとdata treeのUID/GIDを検証し、不一致は明確なerrorとして停止する。
- restoreではresticが保存したUID/GIDを維持する。互換性のないsnapshotを暗黙に再帰chownしない。
- Podman containerを`Privileged`にはせず、Host socketやHost root filesystemをmountしない。Run専用
  `/data`だけをbind mountし、image digest固定、`DropCapability=all`、`NoNewPrivileges=true`、
  closed Host command、手動Firewallを維持する。

rootless Podmanは採用しない。systemd user unit、linger、subuid/subgid、user session storageを
bootstrapと復旧の必須条件にせず、短命VMではsystem serviceとして一意に管理する。

## Consequences

### Positive

- entrypointを含むcontainer processとworld/plugin dataをHostのroot identityから分離できる。
- fresh startとrestore後startが同じ数値ownership contractになる。
- 起動時のUID/GID変更、`gosu`、recursive chown、rootless Podman state、手動permission修正を
  排除できる。
- account衝突、login可能な設定、snapshot ownership不一致をMinecraft起動前に検出できる。

### Negative

- Host agentとrootful Podman daemon側はroot権限を持つが、Minecraft container processは持たない。
- UID/GID 1000はupstream image、bootstrap、Host agent、Quadlet間のversioned contractになる。
- Debian imageで1000が別accountに使われるようになった場合、bootstrapは衝突を検出して失敗する。
- 古いsnapshotや外部から持ち込んだdataのownershipが異なる場合、自動変換せず起動を拒否する。

固定値と検証処理は自動testで一致を固定する。ownership migrationが必要になった場合は、起動処理へ
隠して追加せず、snapshot単位の明示的なmigrationとして設計する。

## Reconsider when

- 一つのVMで複数tenantのServer Unitを動かす。
- upstream imageの既定UID/GIDまたはdirect-user contractが変わる。
- Hostを長期間維持し、人間のlogin accountや別serviceと共有する。
- Debianやitzg imageがUID/GID切替contractを変更する。

## References

- [itzg alternate UID/GID](https://docker-minecraft-server.readthedocs.io/en/latest/configuration/misc-options/#running-as-alternate-usergroup-id)
- [itzg ownership troubleshooting](https://docker-minecraft-server.readthedocs.io/en/latest/misc/troubleshooting/)
- [gosu design and usage](https://github.com/tianon/gosu)
- [cloud-init users and groups](https://docs.cloud-init.io/en/latest/reference/examples.html#including-users-and-groups)
- [Podman Quadlet container units](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
