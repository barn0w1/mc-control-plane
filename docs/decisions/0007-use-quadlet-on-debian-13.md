# ADR-0007: Debian 13上のcontainer lifecycleにPodman Quadletを使用する

- Status: Accepted
- Date: 2026-07-22

## Context

Execution Hostでは`itzg/minecraft-server` containerを長時間動かし、正常停止、再起動、
状態観測、log確認を一貫した方法で行う必要がある。Host agentが`podman run`を直接組み立てて
processを保持すると、service manager、restart policy、boot時の挙動を別に実装することになる。

対象OSはDebian 13 (Trixie)である。Debian 13の標準repositoryにはPodman 5.4系があり、
Podmanはsystemd管理用としてQuadletを推奨している。Quadletは`.container`などの宣言から
通常のsystemd serviceを生成するため、独自のcontainer supervisorを作らずに済む。

## Decision

Execution Hostの基準OSをDebian 13とし、Minecraft workloadをsystem-wideのPodman Quadletで
管理する。

- Host agentはrootのsystemd serviceとして動かし、`/etc/containers/systemd/`のQuadletと
  workload用data directoryを管理する。
- Minecraft containerの起動、停止、状態、restart、logは生成されたsystemd serviceを通して
  操作・観測する。Host agentはPodman processを直接superviseしない。
- workload dataはroot disk上の明示したdirectoryへbind mountする。Podman named volumeは
  使用せず、resticが同じdirectoryを直接扱えるようにする。
- QuadletはHost agentが固定schemaの`WorkloadSpec`から生成する。Control Planeから任意のunit文や
  shell commandを渡さない。
- container imageはversionまたはdigestで固定し、Runの途中で自動更新しない。
- 新しいQuadletは一時directoryでgeneratorのdry-runと`systemd-analyze verify`を通してから
  atomicallyに配置し、`daemon-reload`する。
- image pullはsystemd既定の起動timeoutを超え得るため、開始前の明示的pullと十分な
  `TimeoutStartSec`を使う。
- serviceの再起動はPodman側ではなくsystemdの`Restart=`へ一元化する。
- Minecraft Quadletをboot時に無条件で開始しない。Host agentがdata stateを確認した後で
  明示的に開始する。予期しないVM reboot後も、restore済みdataとControl Planeのintentを
  再確認してから再開する。
- Host agentはrootful Podmanを管理するroot processなので、`ProtectSystem=strict`とPodman内部pathの
  `ReadWritePaths`列挙を使用しない。Podmanはgraphrootやrunrootに加え、Netavark lock、OCI runtime、
  namespaceなどversionや操作に応じた書き込み先を持ち、個別列挙は不完全な権限境界になる。
  `ProtectSystem`、`ProtectHome`、`PrivateTmp`を設定せず、Host filesystemを通常どおりPodmanとagentへ
  公開する。`NoNewPrivileges`と`UMask=0077`は維持する。安全境界は閉じたcommand schema、Host認証、
  Run/resource identity、所有tag、data leaseの権限制限で構成する。
- container存在判定には`podman container exists`の終了契約を使う。0は存在、1は不存在、125は
  local storage障害であり、任意の非zero終了を`absent`へ変換しない。

初期実装ではrootful Podmanを使用する。system service、data所有権、低portではないMinecraft
portを一つの場所で扱え、rootless systemd user instanceとlingerの状態を追加せずに済むためである。
Gate 5のlive acceptanceでentrypointのownership・identity変換が起動停止点になったため、
Minecraftにはimage既定値と一致する固定identityを用意する。Quadletから直接このidentityで開始し、
itzgによるUID/GID変更、`gosu`、起動時chownを行わない。rootful Podmanを維持しつつJava processを
最初からnon-rootにする境界は[ADR-0015](0015-use-dedicated-minecraft-identity.md)で固定する。

## Host baseline

cloud-initはHostを完全に構成するconfiguration managerではなく、次の最小bootstrapだけを行う。

1. Debian package indexを更新し、Podman、restic、Python 3.13と必要なsystem packageをinstallする。
2. 専用directoryとrootで動くHost agent system serviceを用意する。
3. versionとchecksumを固定したHost agent artifactをinstallする。
4. 一回限りのenrollment情報を配置し、Host agentを有効化・起動する。

Minecraft image pull、restore、Quadlet適用、Minecraft起動はHost agentのcommandとして行い、
cloud-init成功とworkload readyを同じ状態にしない。

Control Plane本体は現在Python 3.14を対象にしている一方、Debian 13の標準Pythonは3.13である。
Host agentはControl Planeの配布物と分離したpackageとしてPython 3.13をsupportし、Linode SDKや
Control Plane persistenceを依存に含めない。VMごとに別のPython runtimeをdownloadする設計は
採用しない。

## Consequences

### Positive

- systemdのservice lifecycle、restart、dependency、timeout、journalを再利用できる。
- container設定が宣言的なfileとして観測・検証できる。
- Host agentは高水準の状態遷移へ集中でき、container process supervisorを実装しなくてよい。
- Debian標準packageを使うため、VM bootstrapの外部依存とversion差を減らせる。
- Podman storage障害をworkload不存在と区別し、元のerrorとdiagnostic observationを別々に保存できる。

### Negative

- Quadletと生成後のserviceという二段階を理解し、両方のerrorを報告する必要がある。
- `.container` file変更後の`daemon-reload`、service restart、rollback手順が必要になる。
- root権限を持つHost agentがtrust boundaryになるため、閉じた入力schema、command認証、所有権検証が
  重要になる。Podman内部pathをsystemdのfilesystem allow-listで完全に閉じ込めることは目標にしない。
- Control PlaneとHost agentでPython package/runtime targetを分けて管理する必要がある。

## Reconsider when

- Debian標準のPodman/Quadletに必要な機能がなく、実運用で回避不能になった。
- 複数の独立workloadやhostを一つのVMで扱う必要が生じ、rootless分離の価値が増えた。
- systemd以外のOSをsupportする具体的な要求が生じた。

## References

- [Debian 13 Podman package](https://packages.debian.org/trixie/podman)
- [Debian 13 Python 3 package](https://packages.debian.org/trixie/python3)
- [Podman Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
- [Podman Quadlet basic usage](https://docs.podman.io/en/latest/markdown/podman-quadlet-basic-usage.7.html)
- [Podman rootful storage paths](https://docs.podman.io/en/stable/markdown/podman.1.html)
- [Podman container exists](https://docs.podman.io/en/stable/markdown/podman-container-exists.1.html)
- [systemd filesystem sandbox](https://www.freedesktop.org/software/systemd/man/systemd.exec.html)
- [crun runtime state](https://github.com/containers/crun/blob/main/crun.1.md)
- [Deprecated podman generate systemd](https://docs.podman.io/en/latest/markdown/podman-generate-systemd.1.html)
