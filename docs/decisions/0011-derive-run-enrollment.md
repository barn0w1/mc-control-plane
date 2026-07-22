# ADR-0011: Run単位でHost enrollment credentialを決定的に導出する

- Status: Accepted
- Date: 2026-07-22

## Context

start workflowは外部actionの途中でControl Plane processが終了しても、同じRunを重複作成せず再開する。
Linode createへ渡すcloud-initも再現可能でなければ、process終了位置によって別のenrollment tokenを
DBへ増やし、どれが実際のLinodeへ渡ったか判定できなくなる。一方、token平文をSQLiteへ保存すると、
DBのread権限だけで未使用Host credentialを取得できる。

## Decision

Control Planeに32 byteのroot-only bootstrap keyを一つ置き、HMAC-SHA-256へRun IDと
resource identityをdomain separation付きで入力して、一回限りのenrollment tokenを決定的に導出する。

- key fileは排他的に作成し、mode `0600`以外を拒否する。
- SQLiteにはtokenのSHA-256 hashだけを保存する。
- 一つのRunにenrollment recordを最大一つとする。
- 同じkeyとRunからの再生成は同じtokenとなり、未使用recordの期限だけを延長できる。
- active Runの途中でkeyが変わった場合は、新tokenを追加せずOperationを`blocked`にする。
- root key自体をLinode、cloud-init、Host agent、Control Plane DBへ渡さない。
- enrollment tokenはcloud-initへ渡すが、一回使用または期限切れで無効になる。

## Consequences

- create前後でControl Planeが終了しても、bootstrap入力とDB recordを再現できる。
- DB単体の漏えいから未使用token平文は得られない。
- keyを失うとactive Runのcreateを同じ入力で再開できないため、Control Plane DBと同じ運用上の重要度で
  backupする必要がある。
- key rotationはactive Runがない時点で行う。active Run中の暗黙rotationは安全側に停止する。
- これはHost enrollment専用keyであり、agentの長期credentialやR2 credentialの導出には再利用しない。

## References

- [Python `hmac` module](https://docs.python.org/3/library/hmac.html)
- [Python `secrets` module](https://docs.python.org/3/library/secrets.html)
- [SQLite WAL](https://www.sqlite.org/wal.html)
