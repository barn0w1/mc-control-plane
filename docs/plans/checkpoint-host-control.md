# Checkpoint: Complete Host control

## 1. Goal

中期checkpointは、`mccpd`、`mccp-hostd`、`mccpctl`をすべての基盤として完成させ、
上位layerから提示されたHost要求を安全に満たせることです。

Minecraft、restic、R2、Paperはこのcheckpointの完成条件に含めません。
Host上では安全なfixtureだけを使用します。

## 2. User-visible scenario

`mccpctl`から次の一周を実行します。

1. `HostClass`を登録する。
2. 二つの`HostClaim`を作成する。
3. `mccpd`が二台のLinodeを重複なく確保する。
4. 各Hostで`mccp-hostd`が起動し、一回限りenrollmentを行う。
5. mTLS接続後にHost observationが表示される。
6. 限定されたfixture commandを実行し、結果を取得する。
7. `mccpd`を再起動し、同じHostとClaimを再発見する。
8. 一台の`mccp-hostd`を再起動し、journalとidentityを維持して再接続する。
9. 一つのClaimを解放し、Hostをsanitization後にIdleへ移す。
10. 互換Claimを再作成し、課金policyに従ってIdle Hostを再利用する。
11. Claimをすべて解放する。
12. policyが削除を選び、ownership確認後にLinodeを削除する。
13. provider上の不存在、certificate失効、database上のterminal stateを確認する。

通常経路でSSH、Cloud Manager操作、Host上のshell操作を要求しません。

## 3. Functional acceptance criteria

### RPC foundation

- `mccpctl`はRPC以外で`mccpd`のstateへ触れない。
- method、params、result、errorがtyped schemaを持つ。
- request correlationとstructured errorを表示できる。
- local Control Plane再起動後もclientが再接続できる。

### Host demand

- 一つのHostClaimにつき一つの排他的Hostを割り当てる。
- 複数Claimは必要数のHostを生成する。
- Claim作成を重複送信しても、idempotency keyにより重複要求を作らない。
- Claim削除はHostを直接削除せず、allocationを解放する。

### Provider lifecycle

- create、discover、observe、deleteを実accountで確認する。
- create response lossを模擬しても二重作成しない。
- delete response lossを模擬しても誤ったresourceを削除しない。
- provider ownershipが一致しないresourceを破壊しない。
- resource不存在を確認するまでHostをterminatedにしない。

### Identity and communication

- `mccp-hostd`がHost内でprivate keyを生成する。
- enrollment tokenは一回限りで再利用できない。
- `mccpd`と`mccp-hostd`がmTLSで相互認証する。
- certificate identity、Host ID、incarnationが一致しないrequestを拒否する。
- certificate rotationがHost停止なしで完了する。
- terminated Hostのcertificateを拒否する。

### Durable command execution

- closed command enum以外を受け付けない。
- 同じcommand IDとpayloadの再配送で副作用を再実行しない。
- 同じcommand IDに異なるpayloadを送ると拒否する。
- result送信前にHostが再起動してもjournalから再報告できる。
- stale allocationのcommandとresultをfencing tokenで拒否する。

### Idle and reuse

- Claim解放後にsanitizationが成功するまでReusableにならない。
- compatibleでないHostClassへ再割当しない。
- billing-aware policyがIdle保持と削除予定を説明できる。
- 最大idle台数と最大idle時間を超えたHostを削除する。
- failed sanitization Hostを再利用しない。

## 4. Reliability acceptance criteria

- 任意のcontroller step間で`mccpd`を停止・再起動して再開できる。
- `mccp-hostd`通信断をresource failureと即断しない。
- observation freshnessをstatusに表示する。
- provider API timeoutをOutcomeUnknownとして再観測する。
- retryは永続化されたbackoffに従う。
- operator interventionが必要な状態を無限retryしない。
- database constraintが二重allocationを拒否する。

## 5. Security acceptance criteria

- logとdatabaseにprivate key、enrollment token平文、temporary secretを残さない。
- Host RPCに任意shell、任意path、任意systemd unit操作を公開しない。
- provider delete前にownershipを再検証する。
- Host termination時にidentityを失効する。
- Host再利用前にfixture stateとcredential残留がないことを検査する。
- RPC request size、command output size、poll durationに上限を持つ。

## 6. Observability acceptance criteria

`mccpctl host get`で少なくとも次を確認できます。

- Host IDとincarnation
- HostClass
- Claimとallocation generation
- provider resource identityと状態
- Host lifecycle phase
- conditionsとreason
- `mccp-hostd` certificate期限
- `mccp-hostd` observation時刻とfreshness
- 現在または直近のActivity
- idle開始時刻と削除予定判断

## 7. Completion rule

自動testだけではCompleteとしません。

- deterministic unit and scenario tests
- fault injection tests
- disposable Linodeを使うopt-in live acceptance
- 実行結果と発見事項のdocument化

のすべてを満たした時点でHost control checkpointをCompleteとします。
