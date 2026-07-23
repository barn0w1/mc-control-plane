# Failure model

## 1. Principles

- timeoutは失敗を意味しない。
- process crashは通常の回復経路である。
- external side effectは再実行可能または再観測可能でなければならない。
- 破壊的actionではavailabilityよりownership確認を優先する。
- 不明な状態で推測して進めない。
- error messageではなくstructured categoryで制御する。

## 2. Failure categories

```text
Transient
  一時的で自動retryしてよい

Permanent
  同じinputでは成功しない

OutcomeUnknown
  副作用が発生したか判定できないため再観測が必要

OperatorRequired
  自動化が安全に判断できず、人間の確認が必要

ProtocolViolation
  peer、schema、identity、generationが期待と一致しない

Cancelled
  ownerの要求が明示的に撤回された
```

Categoryとretryabilityは別fieldとして保持できます。たとえば一時的errorでも、retry budget超過後は
operator actionを要求できます。

## 3. Durable Activity

外部副作用はActivityとして記録します。

```text
Pending
Running
Succeeded
Failed
OutcomeUnknown
Cancelled
```

Activityは汎用job DSLではありません。Provider create、provider delete、Host command、certificate issueなど、
再試行と観測が必要な外部boundaryを統一して扱うための記録です。

## 4. Create pattern

```text
persist create intent
call provider create

success with identity:
  persist external identity
  observe resource

timeout or connection loss:
  mark OutcomeUnknown
  discover by ownership identity

definitive rejection:
  mark Failed
```

OutcomeUnknownからcreateを直接再実行しません。ownership identityで再発見してから判断します。

## 5. Delete pattern

```text
verify no active owner
verify ownership metadata
persist delete intent
call provider delete
observe until absent
only then mark terminated
```

Delete responseを失った場合は存在確認へ進みます。

## 6. Host command pattern

- command IDはdeterministicまたは永続化された一意ID。
- command payloadのcanonical digestを記録する。
- Host journalは結果を永続化してから報告する。
- 結果報告が失われても同じcommandを再実行しない。
- deadline超過後の遅延resultはallocation fencing tokenで検証する。

## 7. Retry policy

- bounded exponential backoff
- jitter
- per-activity attempt limit
- global provider rate limit awareness
- explicit next retry time
- operator-triggered retry

固定間隔で無期限にretryしません。

## 8. Crash recovery

`mccpd`再起動時:

- Running Activityを自動的にfailedとはしない。
- external stateを再観測する。
- expired controller leaseを回収する。
- due resourceをreconcile queueへ戻す。

`mccp-hostd`再起動時:

- local journalを読み込む。
- Host boot IDを報告する。
- 未報告resultを再送する。
- 実行中だったcommandはkind固有のrecovery procedureで再観測する。
