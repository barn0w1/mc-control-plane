# ADR-0006: Data repositoryでresticのpassword protectionを使用しない

- Status: Accepted
- Date: 2026-07-24

## Decision

将来のData layerでは、restic repositoryを`--insecure-no-password`で作成・使用します。
Restic passwordをrepository accessのsecurity boundaryとして扱いません。

Repositoryを読み取れる主体は内容へアクセスできる前提とし、access controlはobject storage側のcredential、
resource isolation、最小権限、credential lifetimeで管理します。

## Reason

Python prototypeで、Hostへrepository passwordを配布・管理せず、一時的なstorage credentialだけでdata lifecycleを扱える構成が有用だと確認できました。

## Scope

このADRはpassword protectionを使わない方針だけを確定します。
Object storage、credential発行、repository単位、retention、restore verificationの詳細はData layer実装前に決めます。

## Reference

- [restic: Preparing a new repository](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html)
