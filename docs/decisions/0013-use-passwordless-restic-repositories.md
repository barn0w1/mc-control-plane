# ADR-0013: restic repositoryを空passwordで運用する

- Status: Accepted
- Date: 2026-07-22

## Context

R2上のrestic repositoryはMinecraft payloadの重複排除、圧縮、snapshot、復元を目的とする。
payloadは機密情報として扱わず、別管理のrepository passwordやdata root keyを失うことで、正常なR2
objectから復元できなくなる方がこのprojectでは大きなriskである。

resticには暗号化を無効にしたrepository formatがない。`--insecure-no-password`はrepository passwordを
空にするが、repository master keyと保存データの暗号化・認証処理自体は残る。重複排除は平文chunkの
hashを使うため、この選択では失われない。

## Decision

- 新しいrepositoryは`restic --insecure-no-password init --repository-version 2`で初期化する。
- `cat config`、`snapshots`、`backup`、`restore`を含む、repositoryへ接続するすべてのrestic commandへ
  `--insecure-no-password`を明示する。
- repository password、password file、password導出用data root keyを作成、保存、配布しない。
- Host data lease schema v2はR2 temporary credential、repository URL、permission、expiryだけを持つ。
- R2 access権を持つ主体はrepository内容を復号できるものとし、prefix、permission、短いTTLでaccessを
  制限する。

## Consequences

### Positive

- passwordやdata root keyの紛失を原因とする復元不能がなくなる。
- Control PlaneとHost間のsecret deliveryがR2 temporary credentialだけになる。
- password fileの作成・permission・削除が不要になる。
- resticの重複排除、圧縮、snapshot ID、S3-compatible backendを継続利用できる。

### Negative

- R2 objectへread accessを得た主体に対して、restic passwordによる追加の機密性を提供しない。
- restic内部の暗号化処理はformatに必須であり、性能上のcostを完全には除去できない。
- password付きで初期化済みのrepositoryを、この構成からそのまま開くことはできない。

Gate 4 live acceptanceは未実施なので、正式なacceptance prefixは空password方式で新規作成する。
旧開発版が作ったpassword付きtest repositoryに必要なsnapshotがある場合は、旧data root keyを保持した
状態で別途migrationしてから切り替える。

## References

- [restic 0.18 manual: `--insecure-no-password`](https://restic.readthedocs.io/en/v0.18.0/manual_rest.html)
- [restic repository format](https://restic.readthedocs.io/en/v0.18.0/100_references.html)
