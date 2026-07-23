# Python prototype

旧Python実装は次から参照できます。

```text
Tag: python-prototype-reference-2026-07-23
Commit: 320a105a5d2f7dce6c41cdd57bb755c846f06a30
```

新しいRust実装との互換性は持たせません。

## Knowledge to retain

- 外部resource作成のtimeoutを、作成失敗と断定しない。
- 結果不明のcreateは、再作成の前にresourceを再発見する。
- provider resourceを削除する前にownershipを検証する。
- delete responseではなく、provider上の不存在を終了条件にする。
- process restartを通常の動作として、reconciliation stateを永続化する。
- Host control surfaceを限定し、任意shell executionを公開しない。
- Host側でcommandの再配送に耐える。
- temporary storage credentialを長期credentialや永続command payloadから分離する。
- Host、provider、workload、dataの状態を一つの曖昧なstatusへ統合しない。
- 実環境で発見したfailureをtestとdocumentへ戻す。

## Structures not to preserve

- CLIがcomposition root、database client、provider clientを兼ねる構造
- Host APIとreconcilerを別processとして所有者を増やす構造
- 文字列kindとuntyped JSON objectに依存するprotocol
- 宣言的stateと命令的workflowを曖昧に混在させるmodel
- 未実装の将来機能を先にschemaやenumへ追加すること
