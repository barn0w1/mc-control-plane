# ADR-0004: Control Plane databaseにSQLiteを使用する

- Status: Accepted
- Date: 2026-07-22

## Context

Control Planeはdesired state、Run、Operation、Runtime Instance、Snapshot metadataを
transactionalに保存する必要があります。同じServer Unitにactive Runを最大一つとするなど、
applicationの事前確認だけではなくdatabase constraintで守るべき不変条件もあります。

一方、Control Planeは一つだけで、処理速度や複数writerへの水平scaleは求めません。
database serverを別途運用すると、今回必要な規模に対して構成と障害点が増えます。

## Decision

Control Plane databaseとしてSQLiteを使用し、Python標準libraryの`sqlite3`から明示的なSQLを
実行します。現時点ではORMを導入しません。

- database fileはControl Planeと同じhostのlocal filesystemへ置く。
- connectionごとに`PRAGMA foreign_keys = ON`を設定し、実際に有効になったことを確認する。
- file databaseではWAL modeを使用する。
- `busy_timeout`を設定する。論理的なControl Planeは一つだが、Host APIとreconciler/CLIは同じhost上の
  別processとして短時間だけwriterになりうる。
- Host protocolの高頻度な短いtransactionは、`SQLITE_BUSY`、`SQLITE_BUSY_SNAPSHOT`、
  `SQLITE_LOCKED`を一度だけ短く再試行する。解消しない場合はHTTP 503としてagentへ再試行させる。
- transactionはUnit of Work単位で明示的にcommitまたはrollbackする。
- active Runと未完了Operationはunique partial indexで保証する。
- schema migrationはversion付きの小さなSQL migrationとしてrepository内で管理する。
- datetimeはUTCのISO 8601文字列として保存する。
- `RuntimeSpec`とprovider tagはJSONとして保存するが、識別・排他に使う値は通常columnへ分ける。

## Schema invariants

最低限、次のconstraintをdatabaseへ持たせます。

```sql
CREATE UNIQUE INDEX ... ON runs(server_unit_id)
WHERE ended_at IS NULL;

CREATE UNIQUE INDEX ... ON operations(server_unit_id)
WHERE state IN ('pending', 'running', 'retry_wait', 'blocked');
```

Application側の確認は利用者へ理解しやすいerrorを返すために残しますが、正しさは最終的に
database constraintへ依存します。

## Consequences

### Positive

- database serverと接続poolの運用が不要になる。
- transactionとconstraintを使いながら構成を小さく保てる。
- partial unique indexでdomain invariantを直接表現できる。
- 標準libraryだけでpersistence adapterを実装できる。
- database fileのbackupとlocal testが容易になる。

### Negative

- writerは同時に一つなので、将来複数Control Planeへ拡張する用途には適さない。
- WALでもwriter同士は直列化され、別processのtransactionが一時的にbusyとなる可能性は残る。
- ORMを使わないためSQLとrow mappingを保守する必要がある。
- WAL fileを含むdatabase fileをnetwork filesystem上で共有できない。
- schema migration機能は必要最小限を自分たちで管理する必要がある。

## Reconsider when

- 複数Control Planeまたは複数writerが必要になった。
- database lock待ちが実際の運用問題になった。
- Control Planeを複数hostへ分散する必要が生じた。
- SQLiteで表現しにくいqueryやmigrationが継続的に増えた。

その場合はportを維持したままPostgreSQL adapterへの移行を検討します。

## References

- [Python 3.14 sqlite3 documentation](https://docs.python.org/3.14/library/sqlite3.html)
- [SQLite partial indexes](https://www.sqlite.org/partialindex.html)
- [SQLite write-ahead logging](https://www.sqlite.org/wal.html)
- [SQLite `SQLITE_BUSY_SNAPSHOT`](https://www.sqlite.org/rescode.html#busy_snapshot)
- [SQLite `busy_timeout`](https://www.sqlite.org/pragma.html#pragma_busy_timeout)
- [SQLite foreign key support](https://www.sqlite.org/foreignkeys.html)
