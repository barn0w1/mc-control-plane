# ADR-0016: 操作interfaceを共有use caseへ接続し、Operationを直列化する

- Status: Accepted
- Date: 2026-07-23

## Context

Gate 1から5でInfra、Host、Data、Minecraftのprimitiveは実機検証できた。通常運用ではCLIに加えて
Discord Botも予定しているが、それぞれが独自にstart、snapshot、stopの条件分岐を持つと、排他規則、
snapshot-before-delete、error応答がinterfaceごとにずれる。

また、VM start中やsnapshot中にstopが要求された場合、現在処理を途中で中断するか、後続操作を予約するか
を決める必要がある。暗黙の予約は、処理完了までに利用者の意図が変わっても後から実行される。
中断はLinode作成、Minecraft quiesce、snapshot commit、resource削除の境界を増やす。

## Decision

CLIと将来のDiscord Botは薄いinbound adapterとし、同じapplication command/queryを呼ぶ。
interfaceはDB、Linode SDK、Host commandを直接操作しない。

一つのServer Unitには未完了の変更Operationを最大一つだけ許可する。

- active Operationを途中で取り消さない。
- 別の操作を暗黙にqueueしない。
- 競合要求は`operation_conflict`として拒否し、現在のoperation ID、kind、state、stepを返す。
- 利用者は現在のOperation完了後に意図した操作を再要求する。
- 自動回復しない失敗は`blocked`にし、原因修正後に明示的な`operation-retry`で同じOperationを再開する。
- retry時のHost command IDにはattempt番号を含める。同一attempt内は同じcommandとなり、
  明示retryは新しいcommandとして実行できる。

`ServerUnit`は現在の`RuntimeSpec`と`MinecraftSpec`を持ち、`Run`作成時に両方をコピーする。
実行途中の設定変更はactive Runへ影響しない。通常startは明示snapshot IDがなければ最新のcommit済み
snapshotを選び、snapshotがなければ空dataを初期化する。

stopは`Minecraft停止 -> 停止snapshot commit -> Linode削除確認 -> Run終了`の順を変えない。
snapshot中のstop、start中のstop、stop中のsnapshotはすべて現在Operationを返して拒否する。

## Consequences

### Positive

- CLIとDiscord Botで同じ不変条件、error code、Operation IDを利用できる。
- cancellation、優先度、予約queue、複数workerを導入せず、操作競合を説明できる。
- 利用者が古い予約操作の実行に驚くことがない。
- process再起動後もSQLite上のOperation stepと決定論的Host command IDから再開できる。
- snapshot commit前にstopが割り込んでLinodeを削除する経路が存在しない。

### Negative

- snapshot中にstopを押しても自動では後続実行されず、完了後の再操作が必要になる。
- Discord Botは進行中Operationを表示し、再操作を案内する必要がある。
- `blocked`の原因修正は現時点では運用者判断を必要とする。

## Reconsider when

- 複数のControl Plane writerやworkerが必要になる。
- 明示的なrequest IDを持つ外部APIを公開する。
- 利用者が確認できる予約queue、優先度、cancel semanticsの価値が明確になる。
