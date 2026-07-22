# ADR-0003: 信頼性と可用性の目標

- Status: Accepted
- Date: 2026-07-22

## Context

自動化システムには正しい状態管理が必要ですが、このプロジェクトは小規模コミュニティ向けであり、商用サービスではありません。低確率の障害へ対応するために構成と実装を過度に複雑化すると、通常操作のバグや開発負担が増えます。

## Decision

### 保証すること

- 同じServer Unitのactive Runを同時に複数作成しない。
- 正常なstart/stop/snapshot処理を再実行可能にする。
- 外部API timeout後は、同じresourceを重複作成する前に実状態を再観測する。
- 新しいsnapshotがcommitされる前に、唯一の実行中データを意図的に削除しない。
- 正常停止時にsnapshotを作成する。
- 長時間稼働するRunに対して定期snapshotを作成する。
- 最後に成功したsnapshotから手動または自動で復元できる。
- 操作失敗をログと永続状態から確認できる。

### 保証しないこと

- Control Planeのhigh availability。
- active-active構成や分散consensus。
- region障害からの自動failover。
- 最新書き込みを一件も失わないzero-RPO。
- Minecraftの無停止snapshot。
- rareなLinode root disk障害から、最後のsnapshot以降のデータを回収すること。
- あらゆる失敗の完全自動復旧。

## Failure handling policy

この文書でいうfailureは、主に通常運用でも起こり得る次の事象を指します。

- Akamai Cloud APIまたはR2 APIの一時的なtimeout。
- cloud-init、container起動、restic commandの非ゼロ終了。
- Minecraftのgraceful stopまたはreadinessのtimeout。
- Control Planeの処理途中での再起動。

これらには限定回数のretryと実状態の再観測で対応します。解決しない場合は`blocked`として人間が判断できるようにします。

provider全体の大規模障害、極めて低確率のstorage喪失、Control Plane VMそのものの長期停止に対する高度な自動復旧は実装しません。

## Recovery point

復旧点は最後に成功したrestic snapshotです。

- 正常停止時snapshotは必須。
- 定期snapshotの間隔とretentionは設定可能にする。
- 定期snapshot失敗時もMinecraftの実行は継続し、失敗を記録して次回再試行する。
- 許容される損失幅は概ねsnapshot間隔となる。

具体的な既定間隔と保持数は、運用コストと実際のServer Unit容量が分かる設定スキーマ設計時に決めます。

## Consequences

### Positive

- 実装と運用が理解しやすい。
- 低確率事象のためにVolume、cluster、distributed lockを導入せずに済む。
- 通常workflowとbackupの正しさへ開発時間を集中できる。

### Negative

- 最後のsnapshot以降の進捗を失う可能性がある。
- 一部の問題は人間による確認が必要になる。
- Control Plane停止中は新しい操作を受け付けられない。
