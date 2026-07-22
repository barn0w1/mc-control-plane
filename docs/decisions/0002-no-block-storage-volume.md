# ADR-0002: Block Storage Volumeを使用しない

- Status: Accepted
- Date: 2026-07-22

## Context

Minecraftの実行データをLinodeのroot diskに置くか、別のAkamai Cloud Block Storage Volumeへ置くかを決める必要があります。

VolumeにはLinodeとは独立したlifecycleがありますが、次の処理が追加されます。

```text
create -> attach -> format -> mount -> unmount -> detach -> delete
```

各処理には待機、再試行、途中状態の保存、cleanupが必要です。

## Decision

Block Storage Volumeを使用しません。実行中のServer UnitはLinodeのroot diskへ復元します。

停止時にrestic snapshotがR2へcommitされたことを確認してからLinodeを削除します。snapshot作成に失敗した場合は、データ保護のためLinodeを保持します。

Akamai Cloud adapterが自動管理するresource typeは当面Linodeだけとします。

## Consequences

### Positive

- resource lifecycleと状態遷移が大幅に減る。
- attach、mount、device識別、filesystem初期化を実装しなくてよい。
- orphan Volumeと継続課金を考慮しなくてよい。
- 確定済みの永続的な復旧点がR2であることが明確になる。

### Negative

- Linodeを削除するとroot disk上の未snapshotデータも失われる。
- snapshot失敗時はデータを保持するためLinode料金が継続する。
- Linodeから独立して実行中データだけを別VMへattachすることはできない。

## Rationale

このシステムは小規模コミュニティ向けで、商用サービス級の可用性を目標にしません。rareなVM/storage障害よりも、通常workflowの単純さとバグを減らすことを優先します。

正常停止時のsnapshotと長時間稼働中の定期snapshotにより、
致命的な長期間のデータ損失を避けます。

## Reconsider when

次のいずれかが実際の運用上の問題になった場合だけ再検討します。

- 長期間のR2障害中にLinode料金を止めつつ未snapshotデータを保持する必要がある。
- root disk容量が不足する。
- Linodeを頻繁に再作成しながら同じ実行中diskを引き継ぐ必要がある。

将来再検討しても、R2上のrestic snapshotを長期保存の正本とする方針は変更しません。
