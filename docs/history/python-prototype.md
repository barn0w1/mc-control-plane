# Python prototypeから得た知見

## 1. Reference

Rust foundation redesign前のPython実装は次で参照できます。

```text
Tag: python-prototype-reference-2026-07-23
Commit: 320a105a5d2f7dce6c41cdd57bb755c846f06a30
```

旧実装は互換対象ではなく、新しいworking treeへ残しません。

## 2. Proven ideas to preserve

- 外部resource createのtimeoutを失敗と断定しない。
- create結果が不明なら、再createの前にownership metadataで再発見する。
- delete前にprovider resource identityとownershipを再検証する。
- delete responseではなくprovider上の不存在をterminal conditionにする。
- process restart間でworkflow stateを永続化する。
- database transaction中に長いexternal I/Oを待たない。
- Host commandのat-least-once配送にlocal journalで耐える。
- command IDとpayload digestを結び、異なるpayloadへの再利用を拒否する。
- Host control surfaceをclosed command setに限定し、任意shellを公開しない。
- temporary data credentialを永続command recordから分離する。
- provider、Host、workload、dataの観測を一つの曖昧なstatusへ潰さない。
- 実accountで見つけたfailureをtestとdocumentへ戻す。

## 3. Problems not to reproduce

- 宣言的desired stateと命令的Operation modelの混在。
- CLIがcomposition root、database client、provider client、acceptance harnessを兼ねる構造。
- Host APIとreconcilerを別processにしてstate ownershipを複雑化したこと。
- `dict[str, Any]`と文字列kindに依存するprotocol schema。
- application state、scheduling state、failure classificationの混在。
- fixed retry intervalと無期限retry。
- single reconcilerという運用前提をcode invariantで十分に表現していないこと。
- enumに存在するが実装されていない機能が完成機能に見えること。
- acceptance harnessとproduction CLIの同居。
- 通常workflowの保証とGate専用検証の保証が一致しない箇所。

## 4. Reuse policy

旧コードからcopyする場合は、module単位で移植しません。

1. どのfailureまたはinvariantを扱っていたか説明する。
2. 新しいresource model上のownerを決める。
3. Rustの型とtestで再表現する。
4. 旧interfaceやdatabase schemaへのcompatibilityは追加しない。

旧実装は答えそのものではなく、実環境で得た証拠とtest caseのsourceとして利用します。
