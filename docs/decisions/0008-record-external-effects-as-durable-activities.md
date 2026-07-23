# ADR-0008: 外部副作用をdurable Activityとして記録する

- Status: Accepted
- Date: 2026-07-23

## Context

Provider create/delete、Host command、certificate issueはdatabase transactionとatomicに実行できない。
Timeoutやprocess crash後に副作用が起きたか不明になる。

旧実装ではworkflow stepごとに対処していたが、外部boundary全体で一貫したfailure modelが必要である。

## Decision

外部副作用をActivityとして永続化する。

Activityはidempotency identity、attempt、state、outcome、retry scheduleを持つ。
OutcomeUnknownを第一級状態とし、再実行の前に外部状態を再観測する。

Activityは汎用workflow programming languageにはしない。Resource controllerが所有する明示的なaction記録とする。

## Consequences

### Positive

- 外部boundaryのretry、audit、crash recoveryを統一できる。
- Create、delete、Host commandで同じfailure taxonomyを使える。
- Operatorへ現在の副作用進行を説明できる。

### Negative

- Resource stateとActivity stateの整合を設計する必要がある。
- Activity tableとschedulerが必要になる。
- 過度に抽象化するとdomain workflowが読みにくくなるため、用途を限定する必要がある。
