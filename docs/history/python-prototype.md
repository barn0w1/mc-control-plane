# Python prototypeから得た知見

## Reference

```text
Tag: python-prototype-reference-2026-07-23
Commit: 320a105a5d2f7dce6c41cdd57bb755c846f06a30
```

旧実装は互換対象ではなく、実環境で得た証拠とtest caseのsourceとして利用します。

## Preserve

- external createのtimeoutを失敗と断定せず、再実行前にresourceを再発見する
- delete前にresource identityとownershipを再検証する
- delete responseではなくprovider上の不存在をterminal conditionにする
- external I/O中にdatabase transactionを保持しない
- process restartを通常の制御フローとして扱う
- Host control surfaceを閉じた操作集合に限定し、任意shellを公開しない
- command再配送で副作用を重複実行しない
- temporary data credentialを永続command stateから分離する
- provider、Host、workload、dataの観測を一つの曖昧なstatusへ潰さない
- passwordless restic repositoryと一時object storage credentialを使用する
- 実accountで発見したfailureをtestと文書へ戻す

## Do not reproduce

- CLIがdatabase、provider、composition root、acceptance harnessを所有する構造
- 一つのlogical Control Planeを複数serviceへ分割した複雑さ
- `dict[str, Any]`と文字列kindに依存するprotocol
- 宣言的desired stateと命令的Operation modelの混在
- 将来の構想をenumやdocumentだけで完成機能のように見せること
- fixed intervalによる無期限retry

旧codeを利用するときはmodule単位で移植せず、扱っていたfailureまたはinvariantを新しいmodelとRust testで再表現します。
