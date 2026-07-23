# Persistence and consistency

## 1. State owner

初期構成では`mccpd`が永続stateの唯一のapplication ownerです。
Interface clientと`mccp-hostd`はSQLite fileへ直接アクセスしません。

SQLiteを初期databaseとして使用します。
理由は、single-node deployment、transaction、部分unique constraint、運用単純性、backup容易性です。

## 2. Single process, multiple tasks

`mccpd`内部には複数のasync controller taskが存在できますが、database accessは共通storage layerを通します。

- transaction中にnetwork I/Oを行わない。
- resourceごとのleaseまたはserialized updateで同時reconcileを防ぐ。
- optimistic concurrency用にgenerationまたはrevisionを使用する。
- DB busyを無限retryしない。
- migrationは起動時に一方向へ適用する。

## 3. Invariants

最初のschemaでdatabase constraintとして保証する候補:

- 一つのHostClaimにactive HostAllocationは最大一つ。
- 一つのHostにactive HostAllocationは最大一つ。
- 一つのHostにactive ProviderResourceは最大一つ。
- 一つのHost incarnationにactive HostIdentityは最大一つ。
- 一つのidempotency keyにactive external Activityは最大一つ。
- deleted resourceを新規allocationへ使用しない。

Application validationだけでなく、可能な限りunique index、foreign key、check constraintでも保証します。

## 4. External consistency

Database transactionとprovider APIまたはHost RPCをatomicにはできません。
外部副作用には次のpatternを使います。

1. 実行intentとidempotency identityをcommitする。
2. transaction外でexternal actionを行う。
3. resultをcommitする。
4. timeoutやconnection lossではoutcomeを推測せず、external systemを再観測する。

Createやdeleteのresponseを失った場合は、成功・失敗の二値ではなく`OutcomeUnknown`を表現します。

## 5. Events and audit

完全なevent sourcingは採用しません。
ただし、重要なresource transition、RPC authorization failure、certificate lifecycle、external Activityは
append-orientedなaudit recordとして残します。

Audit logにsecret、private key、enrollment token、temporary credential、任意のworkload dataを含めません。

## 6. Compatibility

開発中はschema migrationの後方互換性を保証しません。
必要ならdatabaseを破棄して作り直せます。

ただしmigration自体のdeterminismと、誤ったproduction pathへ接続しない安全性は必要です。
破壊的migrationには明示的なdevelopment-only guardを設けることを検討します。
