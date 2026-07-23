# Documentation index

## 現在のcheckpoint

2026-07-23時点でGate 1から5までの実装、自動test、実account live acceptanceが完了している。
これにより、Linode作成、Debian 13 Host bootstrap、永続start、R2/restic data lifecycle、
Paperのstart・手動snapshot・stop・fresh Host restore/restartという技術的な一周を確認した。

通常運用向けの永続Start/Stop/Snapshot Operation、strict config、独立systemd service、短縮CLI、
自動scenario testまで完了した。通常CLIによる実accountの一周は未検証なので、Gate完了と
Operational MVPの実環境Completeは区別する。

## 正本

| 文書 | 役割 |
| --- | --- |
| [Operational MVP](operational-mvp.md) | 中期目標、現在地、次にproduct化する範囲 |
| [Architecture](architecture.md) | system境界、依存方向、不変条件 |
| [Project structure](project-structure.md) | 現在のpackage配置と責務 |
| [State machines](state-machines.md) | layer別状態とworkflowの実装範囲 |
| [通常運用CLI](normal-operations.md) | product workflowの起動、操作、競合、retry |

設計判断はADR、実環境の証拠と再実行手順はGate文書へ記録する。READMEは入口に留め、詳細な判断や
長い実測logを重複させない。

## Acceptance Gates

| Gate | Status | 検証対象 |
| --- | --- | --- |
| [Gate 1](gates/01-infra-lifecycle.md) | Complete (2026-07-22) | Linode create/observe/delete、所有権 |
| [Gate 2](gates/02-host-foundation.md) | Complete (2026-07-22) | Debian 13、Host agent、Quadlet、reboot |
| [Gate 3](gates/03-durable-orchestration.md) | Complete (2026-07-22) | 永続Operation、process再開、Host ready |
| [Gate 4](gates/04-data-lifecycle.md) | Complete (2026-07-23) | R2/restic、snapshot、fresh Host restore |
| [Gate 5](gates/05-minecraft-lifecycle.md) | Complete (2026-07-23) | Paper lifecycle、quiesced snapshot、restore/restart |

Gate文書は通常利用者向け操作説明ではなく、課金を伴うacceptance手順と実測記録である。

## Architecture decisions

### Core and infrastructure

- [ADR-0001: resticをバックアップエンジンに採用する](decisions/0001-use-restic.md)
- [ADR-0002: Block Storage Volumeを使用しない](decisions/0002-no-block-storage-volume.md)
- [ADR-0003: 信頼性と可用性の目標](decisions/0003-reliability-scope.md)
- [ADR-0004: SQLiteを使用する](decisions/0004-use-sqlite.md)
- [ADR-0005: 永続Operationを一stepずつ処理する](decisions/0005-use-stepwise-reconciler.md)
- [ADR-0006: 公式Linode SDKをadapterへ隔離する](decisions/0006-use-official-linode-sdk.md)
- [ADR-0009: 一時Linodeのlocal disk encryptionを無効にする](decisions/0009-disable-local-disk-encryption.md)

### Host and protocol

- [ADR-0007: Debian 13でPodman Quadletを使用する](decisions/0007-use-quadlet-on-debian-13.md)
- [ADR-0008: outbound polling Host agentを使用する](decisions/0008-use-outbound-host-agent.md)
- [ADR-0010: versioned closed Host protocolを使用する](decisions/0010-use-versioned-host-protocol.md)
- [ADR-0011: Run enrollment credentialを導出する](decisions/0011-derive-run-enrollment.md)
- [ADR-0012: data credentialを永続commandから分離する](decisions/0012-deliver-ephemeral-data-leases.md)

### Data and Minecraft

- [ADR-0013: passwordless restic repositoryを使用する](decisions/0013-use-passwordless-restic-repositories.md)
- [ADR-0014: Paper Quadletを固定しhealthで判定する](decisions/0014-pin-and-health-gate-paper-quadlet.md)
- [ADR-0015: Minecraft専用identityを使用する](decisions/0015-use-dedicated-minecraft-identity.md)
- [ADR-0016: 操作interfaceを共有use caseへ接続し、Operationを直列化する](decisions/0016-serialize-application-operations.md)
- [ADR-0017: 通常運用を独立した単一ノードserviceで実行する](decisions/0017-run-private-single-node-services.md)

## 更新規則

- 実装前の案と実装済みの事実を明確に区別する。
- Gateのstatus変更には、成功した最終行と安全条件を満たした実測記録を残す。
- 外部環境で発見した問題は、可能なら自動testとADRの両方へ戻す。
- secret、API token、credentialは文書やfixtureへ残さない。
- 後方互換性が不要な期間でも、破壊的変更の理由はADRまたはcommitへ残す。
