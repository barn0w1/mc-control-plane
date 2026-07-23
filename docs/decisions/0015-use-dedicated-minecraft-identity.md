# ADR-0015: Minecraft workloadへ固定された専用identityを使用する

- Status: Accepted
- Date: 2026-07-23

## Context

Gate 5の最初のlive acceptanceでは、空のRun data directoryにもかかわらず、
`itzg/minecraft-server`の起動が`Changing ownership of /data to 1000 ...`から進まなかった。
当該Linodeは追加調査前に削除されたため直接原因は確定できないが、rootで作成・restoreしたdataを
container起動時に別UIDへ再帰変換する設計は、data量に応じて遅くなり、ownershipの不整合を起動時まで
発見できない。

Execution Hostは一つのServer Unitだけを動かす専用・短命VMであり、複数tenantや対話利用者を
Unix userで分離する要件はない。一方、Minecraftやpluginをroot processとして動かす必要もない。
rootless Podman、user systemd、lingerまで導入すると短命Hostのbootstrap、restore、診断に新しい
失敗境界が増えるため、workload identityの分離とcontainer managerの分離は別に判断する。

## Decision

Host agent、rootful Podman、resticはrootで動かし、Minecraft Java processと永続dataには専用の
`mccp-minecraft` identityを使用する。

- cloud-init bootstrapが`mccp-minecraft` user/groupをUID/GID 2000で作成する。passwordはlockし、
  home directory、login shell、sudo権限、SSH keyを持たせない。
- Host agentはsystemd、Quadlet、rootful Podman、restic、ownership検証に必要なためrootのままとする。
- Run親directoryは`root:root`、mode `0700`とする。bind mountする`data` directoryだけを
  `2000:2000`、mode `0700`にする。
- Quadletはitzgが公式に提供する`UID=2000`と`GID=2000`を指定する。container entrypointは必要な
  初期化後、Minecraft serverをこのidentityで実行する。
- `SKIP_CHOWN_DATA=TRUE`を指定し、container起動時の再帰chownを行わない。Host agentはQuadlet適用前に
  accountとdata treeのUID/GIDを検証し、不一致は明確なerrorとして停止する。
- restoreではresticが保存したUID/GIDを維持する。互換性のないsnapshotを暗黙に再帰chownしない。
- Podman containerを`Privileged`にはせず、Host socketやHost root filesystemをmountしない。Run専用
  `/data`だけをbind mountし、image digest固定、`NoNewPrivileges=true`、closed Host command、
  手動Firewallを維持する。

rootless Podmanは採用しない。systemd user unit、linger、subuid/subgid、user session storageを
bootstrapと復旧の必須条件にせず、短命VMではsystem serviceとして一意に管理する。

## Consequences

### Positive

- Java processとworld/plugin dataをHostのroot identityから分離できる。
- fresh startとrestore後startが同じ数値ownership contractになる。
- 起動時のrecursive chown、rootless Podman state、手動permission修正を排除できる。
- account衝突、login可能な設定、snapshot ownership不一致をMinecraft起動前に検出できる。

### Negative

- Host agentとcontainer entrypointはroot権限を持つ。Java processだけを非rootへ切り替える構成である。
- UID/GID 2000はbootstrap、Host agent、Quadlet間のversioned contractになる。
- 古いsnapshotや外部から持ち込んだdataのownershipが異なる場合、自動変換せず起動を拒否する。

固定値と検証処理は自動testで一致を固定する。ownership migrationが必要になった場合は、起動処理へ
隠して追加せず、snapshot単位の明示的なmigrationとして設計する。

## Reconsider when

- 一つのVMで複数tenantのServer Unitを動かす。
- container entrypointもrootで動かせないsecurity要件が生じる。
- Hostを長期間維持し、人間のlogin accountや別serviceと共有する。
- Debianやitzg imageがUID/GID切替contractを変更する。

## References

- [itzg alternate UID/GID](https://docker-minecraft-server.readthedocs.io/en/latest/configuration/misc-options/#running-as-alternate-usergroup-id)
- [itzg ownership troubleshooting](https://docker-minecraft-server.readthedocs.io/en/latest/misc/troubleshooting/)
- [cloud-init users and groups](https://docs.cloud-init.io/en/latest/reference/examples.html#including-users-and-groups)
- [Podman Quadlet container units](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
