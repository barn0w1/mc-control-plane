# ADR-0001: resticをバックアップエンジンに採用する

- Status: Accepted
- Date: 2026-07-22

## Context

Minecraft Server UnitをCloudflare R2へ保存し、Linodeの作成時に復元、削除前にsnapshotを作成する必要があります。

必要な特性は次の通りです。

- S3-compatible object storageへの対応
- snapshot履歴
- 差分転送、重複排除、圧縮
- client-side encryption
- backup、restore、整合性検査
- CLIからの自動実行
- 中断後の再実行
- 独自バックアップフォーマットを実装しなくてよいこと

restic、Kopia、Duplicacy、Borg、単純なarchive uploadを検討しました。

## Decision

バックアップエンジンとしてresticを採用します。

- R2のS3-compatible endpointをbackendにする。
- 原則としてServer Unitごとにrestic repositoryまたはR2 prefixを分離する。
- Control Planeはrestic snapshot IDを明示的に記録する。
- restore時に`latest`を暗黙選択しない。
- Server Unitごとにrepositoryを分離し、backupのhost識別子は固定値にしてRun間のparent選択を安定させる。
- snapshot作成の成功条件はresticの終了コード0とJSON summary内のsnapshot ID取得とする。
- `forget`と`prune`はstop workflowから分離したmaintenance operationで行う。
- repository passwordとR2 credentialは別のsecretとして管理する。

restic以外を交換可能にするためapplication層ではsnapshot操作をinterfaceにしますが、複数engineを同時サポートする実装は行いません。

概念的なinterfaceは次の責務だけを持ちます。

```text
restore(server_unit_id, snapshot_id, target)
create(server_unit_id, run_id, source)
list(server_unit_id)
check(server_unit_id)
forget(server_unit_id, policy)
```

## Consequences

### Positive

- 重複排除、圧縮、暗号化、snapshot、検証を自作しなくてよい。
- R2への転送量を抑えられる。
- JSON出力と終了コードをControl Planeから扱える。
- temporary S3 session credentialを利用できる。
- backupが中断されても、再実行時にアップロード済みデータを再利用できる。

### Negative

- repository formatとmaintenanceはresticに依存する。
- pruneは独立した排他的operationとして扱う必要がある。
- ephemeral Linodeではローカルcacheが毎回失われる。
- Server Unitごとにrepositoryを分ける場合、Server Unit間の重複排除は得られない。

## Rejected alternatives

### Kopia

必要な機能は満たしますが、maintenance owner、自動maintenance、policyなど、Control Planeの責務と重なる状態が増えます。このプロジェクトではresticで十分と判断し、比較benchmarkは行いません。

### Duplicacy

lock-free設計は今回の単一active Runという前提では主要な利点になりません。独自ライセンスとstorage operationの特性も考慮して採用しません。

### Borg

repositoryがlocal filesystemまたはSSHを中心としており、R2を直接backendにする構成に適しません。

### tar/archive + object upload

実装は単純ですが、snapshot管理、差分転送、重複排除、検証、retentionの多くをControl Plane側で組み立てる必要があります。

## References

- [restic documentation](https://restic.readthedocs.io/en/stable/)
- [restic scripting and JSON output](https://restic.readthedocs.io/en/stable/075_scripting.html)
- [restic S3-compatible storage](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html)
- [Cloudflare R2 temporary credentials](https://developers.cloudflare.com/r2/api/s3/temporary-credentials/)
