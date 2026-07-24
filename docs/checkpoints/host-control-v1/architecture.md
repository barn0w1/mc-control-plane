# Host Control System v1 architecture

この文書は、Host Control System v1のtarget architectureをまとめます。
現在のRust codeがまだ反映していないtargetも含むため、実装済み範囲は[Implementation status and plan](implementation.md)を参照してください。

共通のerror classificationとIncident modelは[Failure model](../../failure-model.md)を正本とします。

## Akamai-native model

HostClaim、Host、HostController、infrastructure integrationをAkamai Cloud nativeとして設計します。
汎用provider plugin systemは作りません。

- Hostの実体はAkamai Cloud Linode
- Claimは正確なLinode Type IDを指定する
- raw Linode statusを保存・表示する
- region、network、image、cloud-initなどの共通値はdeployment configurationが所有する
- Host IDとLinode IDは分離する
- fakeは別providerではなくAkamai固有contractのtest double

## HostClaim

`HostClaim`は、一台のAkamai Cloud Linodeに対する永続的かつ排他的な需要です。

```json
{
  "id": "019c...",
  "generation": 1,
  "created_at": "2026-07-24T12:00:00Z",
  "deletion_timestamp": null,
  "spec": {
    "type": "g7-dedicated-8-4"
  },
  "status": {
    "observed_generation": 1,
    "host_id": null,
    "conditions": []
  }
}
```

### `spec.type`

`type`はAkamai Cloudの正確なLinode Type IDです。

- CLI flagは`--type`
- wire fieldは`type`
- Rustでは検証済みnewtype `LinodeTypeId`として扱う
- immutableとし、変更したい場合はClaimを置き換える
- Control PlaneはCPU、RAM、storageから別typeを自動選択しない
- 存在しないType IDや固定regionで利用できないTypeは、Claim rejectionとして表現する

Linode TypeはCPU generation、dedicated/shared、vCPU数、RAM、local disk、価格を一体として表します。
resource quantityからtypeを自動選択するpolicyを追加するより、operatorまたは上位layerが意図したSKUを明示する方が単純で予測可能です。

### Metadata

- `id`: caller-generated UUIDv7
- 同一ID・同一specのcreateはidempotent
- 同一ID・異なるspecはconflict
- `generation`: spec revision。初期はupdateを提供しないため`1`
- `deletion_timestamp`: 通常のClaim解放要求

### Status

```text
HostClaimStatus
  observed_generation
  host_id
  conditions
```

初期condition:

| Type | Meaning |
| --- | --- |
| `Accepted` | Type IDとdeployment policyをControllerが評価済みか |
| `Bound` | Control PlaneのHost IDが割り当てられているか |
| `Ready` | Hostが上位layerへ渡せる状態か |
| `Critical` | Fatal Incidentにより自動mutationが停止しているか |

代表的reason:

- `TypeAccepted`
- `UnknownLinodeType`
- `RegionUnavailable`
- `HostAssigned`
- `LinodeProvisioning`
- `HostAgentPending`
- `HostReady`
- `FatalExternalFailure`
- `CreationOutcomeUnknown`
- `OwnershipConflict`
- `DeletionFailed`

`UnknownLinodeType`や`RegionUnavailable`は、Akamai APIが正常に応答して要求を満たせないことが確認できた場合のexpected rejectionです。
transport error、unexpected API rejection、response decode failureなどはFatal Incidentです。

## Deployment-owned configuration

Claimごとに変えない次の値は、Control Plane deployment configurationが所有します。

- region
- image
- disk encryption policy
- boot behavior
- Cloud Firewall ID
- VPC、subnet、interface構成
- Metadata user data / cloud-init template
- Host Agent artifactとbootstrap revision
- SSH access policy
- ownership label、tag、deployment ID
- maintenance policy、watchdog policy

これらはハードコードせず、startup時に読み込むtyped configurationとして管理します。
不正なconfigurationに対してClaimごとのfallbackは行いません。
startup時に安全な継続ができないconfiguration errorはprocess startup failureとして扱います。

## Internal boundaries

Akamai専用であっても、HTTP client、Host orchestration、controllerを一つのmoduleへ混在させません。

```text
HostController
    |
    v
AkamaiHostService
    |
    v
LinodeApiClient
```

### `LinodeApiClient`

- HTTP transportとauthentication
- Akamai API request/response型
- pagination
- status codeとAPI errorのdecode
- request timeout
- raw Linode Type、instance、statusの取得

Host lifecycleの判断やControl Plane resource更新は行いません。
すべてのnon-success responseとtransport failureをtyped errorとして返します。

### `AkamaiHostService`

HostControllerが必要とするAkamai固有operationを意味単位で提供します。

```text
validate_type(type_id, deployment_config)
observe_owned_host(host_identity)
create_host(host_identity, type_id, deployment_config)
delete_host(linode_id, expected_ownership)
list_owned_hosts(deployment_id)
```

Akamai API endpointを一対一で写すだけのwrapperにはしません。
ownership検証、label/tag規則、共通configurationの適用を一か所に閉じ込めます。

このboundaryはerrorを自動回復しません。
expected absenceやvalidation rejectionをdomain resultへ変換し、それ以外はtyped fatal external errorとしてHostControllerへ返します。

### Fake

Fakeは`AkamaiHostService`と同じprivate contractを再現します。

- Linode Type ID
- Linode ID
- raw status transition
- ownership metadata
- transport failure
- API rejection
- mutation outcome unknown
- account inventory

runtimeでfakeと実providerを動的に切り替えるplugin systemは作りません。
Fakeは正常pathとFatal Incident pathのtest doubleです。

## Identity

- Host ID: Control Planeが発行する論理identity
- Linode ID: Akamai Cloudが発行するinstance identity
- Linode Type ID: Claimが指定するSKU identity
- Host Agent identity: Host内で動作するdaemonのidentity

Linode作成前にHost ID、Claim ID、Type IDを永続化し、label/tagへownership metadataを付与します。

## State model

### Raw Linode state

Akamai APIが返すstatusを独自phaseへ変換して捨てず、raw observationとして保存します。
未知のstatusを保持できるforward-compatibleなRust型を使用します。

```text
LinodeObservation
  linode_id
  type_id
  raw_status
  observed_at
  ownership
```

### Abstract Host state

Host状態は、LinodeだけでなくHost Agentとallocationも含む上位概念です。

```text
Pending
Provisioning
Ready
Released
Idle
Deleting
Critical
```

`running`なLinodeでもHost Agentが未認証、bootstrap不一致、health不良なら`Ready`ではありません。
`Critical`はresource-localなFatal Incidentにより自動mutationを停止した状態です。

## Controller model

HostControllerは、daemon稼働中に担当resourceへ継続責任を持つlevel-triggered control loopです。

- eventやnotificationは処理を早めるhint
- periodic full observationを正しさの基礎にする
- busy loopにはしない
- operator向けの`retry`や`reconcile`操作を通常lifecycleの前提にしない
- 同じ正常reconciliationを何度実行しても安全な処理へ分解する
- external I/O中にdatabase transactionを保持しない
- Critical scopeには新しいmutationを発行しない

内部でtimer、next observation time、queue、notificationを使うことはできますが、これらをResourceのpublic conceptにはしません。

## Normal lifecycle deletion

Claim解放後、HostControllerは通常lifecycleとして次を行います。

1. HostをReleasedへ移す
2. policyによりIdle保持または削除を選択する
3. 再利用時はcompatibilityとsanitizationを検証する
4. 削除時はexact ownershipを確認する
5. Linode deleteを要求する
6. Akamai inventory上の不存在を確認する
7. HostとClaimをfinalizeする

正常なClaim解放による削除はHostControllerの責務です。

## Fatal external failures

HostControllerは、Akamai APIやHost Agentの予測不能なfailureに対して独自のrecovery state machineを作りません。

### Examples

- Akamai API transport failureまたはtimeout
- unexpected non-success response
- response decode failure
- create/delete requestの結果不明
- ownership metadataの不一致
- 保存済みLinode IDとinventoryの矛盾
- normal delete request failure

### Handling

1. operation前に必要なidentityとintentを永続化する
2. external errorをtyped valueとして受け取る
3. `FatalIncident`をcommon Incident storeへ保存する
4. HostまたはAkamai subsystemを`Critical`にする
5. affected scopeへの自動mutationを停止する
6. operator調査に必要な最後のobservationとrequest identityを保持する
7. operatorが明示的にresolveするまで自動再開しない

mutation結果が不明な場合も、同じmutationを自動再送しません。
事実確認のためのread-only observationを行うことはできますが、自動的に正常lifecycleへ復帰させる無期限loopにはしません。

## Incident persistence

Fatal IncidentはHost専用tableではなく、全Controllerで共有するcommon Incident storeに保存します。

```text
FatalIncident
  id
  state
  severity
  subsystem
  resource_kind
  resource_id
  operation
  code
  summary
  details
  occurred_at
  acknowledged_at
  resolved_at
```

Discord Botなどの将来interfaceはJSON-RPCからIncidentを取得し、人間へ通知できます。
acknowledgementは確認済みを表すだけで、自動mutationを再開しません。

詳細は[Failure model](../../failure-model.md)を参照してください。

## No forced cleanup inside Control Plane

Fatal Incident後のLinode強制削除、stale billing resource監視、account-wideなcost enforcementはHostControllerにも別Controllerにも実装しません。

必要ならControl Planeとは独立した別programがAkamai accountを監視します。
そのprogramはこのrepositoryのscope外であり、Host Control System v1の完成条件でもありません。

## Actual Rust panic

Akamai API errorにはactual `panic!`を使用しません。
external boundaryから`Result`として受け取り、Fatal Incidentへ変換します。

`panic!`はprogram bugまたは内部不変条件違反に限定します。
panic hookは構造化logを出せますが、panic中のdatabase writeを正しさの前提にしません。

## Future Data safety

将来Data layerが追加された場合、durable dataを保護できない状態で通常削除を進めてはいけません。
backup failureまたは結果不明はFatal Incidentとして記録し、正常lifecycleのHost削除を開始しません。

passwordless restic repositoryを継続し、object storage credentialとresource isolationをsecurity boundaryにします。
具体的なresource連携はData checkpointで決めます。

## Official references

- Linode API errors: <https://techdocs.akamai.com/linode-api/reference/errors>
- Linode Type list: <https://techdocs.akamai.com/linode-api/reference/get-linode-types>
- Linode Type lookup: <https://techdocs.akamai.com/linode-api/reference/get-linode-type>
- Linode creation: <https://techdocs.akamai.com/linode-api/reference/post-linode-instance>
- Linode lookup: <https://techdocs.akamai.com/linode-api/reference/get-linode-instance>
- Linode deletion: <https://techdocs.akamai.com/linode-api/reference/delete-linode-instance>
