# Security model

## 1. Trust boundaries

```text
Operator client
    authenticated to mccpd

mccpd
    trusted state owner and online identity authority

mccp-hostd
    privileged Host component, trusted only for its own Host identity

Akamai Cloud API
    external provider boundary

Managed workload
    untrusted relative to mccp-hostd and mccpd
```

`mccp-hostd`はHost管理のため高い権限を必要とします。そのため、受信可能なoperationを最小化し、
任意code execution interfaceを提供しません。

## 2. Security invariants

- すべてのHost RPCは認証済みidentityへbindingする。
- Hostは自分以外のHost IDを指定できない。
- destructive provider actionは直前にownershipを検証する。
- command schemaは閉じたRust enumで表現する。
- secretをdatabase、command journal、logへ不必要に永続化しない。
- temporary credentialは最小scopeと短いTTLを持つ。
- workload processとHost management processを分離する。
- stale allocation generationからのresultやcommandを拒否する。
- bootstrap artifactとHost softwareのintegrityを検証する。

## 3. Threats considered

- enrollment tokenの漏洩と再利用
- 古いHost certificateの再利用
- Host imageまたはbootstrapの改変
- RPC replay
- command ID collisionまたはpayload substitution
- provider resource IDの取り違え
- stale `mccp-hostd`による遅延result
- compromised workloadからHost managementへのescape
- log、crash dump、process environmentからのsecret漏洩
- Host再利用時の前workload state残留

## 4. Explicit non-goals for the first checkpoint

- 悪意あるControl Plane administratorからの防御
- hardware-backed key protection
- confidential computing
- multi-tenant hostile workload isolation
- public Internet向けgeneral-purpose remote execution
- zero-trust productの完全な実装

Non-goalであっても、将来の改善を不可能にするidentity reuseやsecret persistenceは避けます。

## 5. Privilege reduction

`mccp-hostd`全体を無条件にrootで実行する構成を最終形とは決めません。
実装時に次を比較します。

- root daemon内で厳密なcommand dispatchを行う。
- unprivileged daemonと小さなprivileged helperへ分離する。
- systemd/polkit capabilityでoperationごとに権限を限定する。

最初は正しさとauditabilityを優先し、抽象的なsandbox設定だけで安全と判断しません。
