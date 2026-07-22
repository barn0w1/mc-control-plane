# ADR-0005: 永続化されたOperationを同期的に一stepずつ処理する

- Status: Accepted
- Date: 2026-07-22

## Context

start、stop、snapshotは複数の外部actionと待機を含み、完了まで数分かかる可能性があります。
一つの関数がその間ずっと実行状態をmemoryだけに保持すると、Control Planeの再起動後に
どこから再開すべきか分からなくなります。

一方、Control Planeは一つで、高いthroughputや多数の同時workerは必要ありません。
最初からasync framework、task queue、message brokerを導入すると、workflow本体よりも
実行基盤の状態管理が増えます。

## Decision

Application coreとoutbound portは同期interfaceとして実装します。長時間workflowは一回の
呼び出しで完了させず、SQLiteへ保存した`Operation.step`をreconcilerが一stepずつ進めます。

- 一回のreconcileでは、外部actionまたは観測を最大一つ進める。
- 外部I/Oの待機中にdatabase transactionを保持しない。
- action結果と次stepをtransactionで保存してから制御を返す。
- timeout時は同じmutating actionを直ちに繰り返さず、実状態を再観測するstepへ戻す。
- 待機はprocess内の長いsleepではなく`next_attempt_at`として永続化する。
- 初期実装は単一reconciler writerとし、Celery、Redis、message brokerを導入しない。
- CLIはOperationを作成して状態を読み取る薄いinbound adapterとする。

Discord Botなどasyncなinbound adapterを将来追加しても、application coreをasyncへ変更するとは
限りません。必要であればadapter境界で同期use caseを呼び出します。

## Consequences

### Positive

- process再起動後もdatabaseからworkflowを再開できる。
- 同じstepを繰り返すscenarioをfake adapterで決定的にtestできる。
- task queueやbrokerの運用が不要になる。
- transactionと外部network callの境界が明確になる。
- 処理速度より理解しやすさを優先できる。

### Negative

- 一つのOperationが進む速度はreconcile intervalに影響される。
- 各workflowを明示的なstep machineとして記述する必要がある。
- 外部adapterには適切なtimeoutを設定し、一回のstepが無期限にblockしないようにする必要がある。

## Reconsider when

- 独立したOperationを同時に多数処理する必要がある。
- 一つのprocessでは外部I/O待ちが実際のbottleneckになる。
- 複数Control Planeや複数writerが必要になる。

その場合も、永続Operationと一stepずつ進めるmodelは維持し、worker実行方式だけを再検討します。
