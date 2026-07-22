# ADR-0006: 公式Linode SDKをCompute adapter内に隔離して使用する

- Status: Accepted
- Date: 2026-07-22

## Context

最初の外部adapterとして、Akamai CloudのLinode作成、検索、観測、削除を実装する。
Applicationはprovider固有のSDK objectやstatusへ依存してはならない。一方、認証、pagination、
filter、retry、API errorの解釈を独自HTTP clientとして重複実装する理由もない。

Linode作成時にはimage、type、regionに加えて、少なくとも一つの認証方法が必要である。
また、Linode tagは3〜50文字、Linode labelは3〜64文字というprovider制約を持つ。
Domain IDをそのままtagへ連結すると、UUIDなどで上限を超える可能性がある。

## Decision

- Python 3.14を明示的にサポートする公式`linode-api4` 5系を使用する。
- SDK importとobject変換は`adapters.outbound.compute.linode`だけに置く。
- Applicationへ返すのは`RuntimeObservation`と正規化した`ComputeLifecycle`だけにする。
- providerのraw statusも同時に保存し、将来の未知statusを捨てない。
- 所有tagはDomain IDのBLAKE2s digestから決定論的に生成し、各tagを50文字以内にする。
- 検索はsystemとServer Unitの2 tagをserver-side filterへ渡し、取得後にも完全一致を確認する。
- 作成時のroot loginにはControl Plane設定から渡されたSSH公開鍵を使い、root passwordを生成・保存しない。
- 手動作成済みFirewall IDだけを参照し、VolumeやCloud Firewall自体は作成しない。
- SDKのbuilt-in retryはPOSTも再送するため無効化し、接続・読み取りtimeoutを明示する。
- mutating requestの結果が確定できない場合は`ComputeActionUncertain`とし、同じcreateを
  直ちに繰り返さずtag検索へ戻す。再試行判断は永続Operationへ一元化する。
- readの一時失敗は`retry_wait`、確定した4xx rejectionやresource消失は`blocked`として永続化する。
- deleteの404は、目的の「不存在」が達成済みなので成功として扱う。

現在の状態mappingは次の通りとする。

| Linode status | Compute lifecycle |
| --- | --- |
| `running` | `running` |
| `offline`, `stopped` | `stopped` |
| `booting`, `busy`, `rebooting`, `shutting_down`, `provisioning` | `pending` |
| `migrating`, `rebuilding`, `cloning`, `restoring` | `pending` |
| `deleting` | `deleting` |
| `billing_suspension` | `blocked` |
| 未知の将来値 | `unknown` |

## Consequences

### Positive

- ApplicationとDomainはLinode SDKの変更から隔離される。
- paginationやAPI filterを公式SDKへ委譲できる。
- timeout後の重複VM作成を既存workflowの再発見処理で防げる。
- provider制約違反を実resource作成前のtestで検出できる。
- tokenやroot passwordをDBへ保存する必要がない。

### Negative

- SDK major version更新時はadapterの契約testが必要になる。
- SSH公開鍵とAPI tokenを供給するbootstrap/configurationは別途実装が必要になる。
- cloud-init metadataはまだ渡していないため、現段階の到達点はVMの`running`観測までである。
- 実accountに対するcreate/delete integration testはcredentialと課金を伴うため自動testには含めない。

## Reconsider when

- 公式SDKが必要なAPI機能やPython versionをサポートしなくなる。
- SDKのretryやfilter semanticsがControl Planeの冪等性要件を満たさない。
- 別cloud providerを実際に追加し、共通設定の境界を見直す必要が生じる。

## References

- [linode-api4 5.46.0 on PyPI](https://pypi.org/project/linode-api4/5.46.0/)
- [Create a Linode](https://techdocs.akamai.com/linode-api/reference/post-linode-instance)
- [Create a tag](https://techdocs.akamai.com/linode-api/reference/post-tag)
- [Official Python SDK](https://github.com/linode/linode_api4-python)
- [SDK pagination](https://linode-api4.readthedocs.io/en/latest/linode_api4/paginated_list.html)
