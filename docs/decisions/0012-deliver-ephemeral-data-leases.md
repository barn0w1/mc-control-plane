# ADR-0012: data credentialを永続HostCommandから分離する

- Status: Accepted
- Date: 2026-07-22

## Context

resticがR2へ接続する間だけ、S3 access key、secret key、session tokenがHostに必要になる。
一方、HostCommandはat-least-once配送のためSQLiteとagent journalへ永続化される。
credentialを通常payloadへ含めると、短命credentialでも不要な複製と残存が起きる。

## Decision

- 永続HostCommandにはServer Unit、固定action、明示snapshot IDなど非secretの意図だけを保存する。
- Host pollでdata commandを配送するたびに、Control PlaneがCloudflare APIからtemporary credentialを
  発行し、poll responseの一時的な`data_lease`として添付する。
- leaseはHostCommandのdigest、agent journal、command result、SQLiteへ含めない。
- Cloudflare API tokenとR2親access key IDはControl Planeだけが保持する。
- repositoryはServer Unit IDのSHA-256から導く専用prefixへ分離する。
- restore leaseは`object-read-only`、init/snapshot leaseは`object-read-write`とし、一つのbucket、prefix、
  operation、TTLへ制限する。
- Hostはleaseをrestic subprocessのenvironmentへだけ展開し、永続化しない。
- repository passwordはdata leaseに含めず、Hostはすべてのrepository commandへ
  `--insecure-no-password`を明示する。

Host APIとreconcilerが別processであるため、lease issuerは`host-api-serve`側に配置する。Commandの所有
Server Unitはpayloadを信用せず、`host_commands -> runs -> server_units`のDB関係から再解決して照合する。

## Consequences

- poll responseが失われても、再配送時に新しいleaseを発行できる。
- Host agentの再実行journalはsecretを知らずに同じdurable commandを識別できる。
- Control Plane Host上のCloudflare API tokenの保護は運用上の必須条件になる。
- R2停止中やcredential発行失敗時はcommandを実行せず、Hostはpollを再試行する。
- passwordやdata root keyを紛失してrepositoryを復元できなくなる経路がなくなる。
- R2 temporary credentialを持つ主体はrepositoryを復号できるため、credential scopeとTTLが主な
  access-control境界になる。

## References

- [Cloudflare R2 temporary credentials](https://developers.cloudflare.com/api/resources/r2/subresources/temporary_credentials/methods/create/)
- [restic S3-compatible storage](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html)
- [restic scripting](https://restic.readthedocs.io/en/stable/075_scripting.html)
