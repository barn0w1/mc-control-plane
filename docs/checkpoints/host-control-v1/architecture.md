# Host Control System v1 architecture

この文書は、Host Control System v1のtarget architectureをまとめます。
現在のRust codeがまだ反映していないtargetも含むため、実装済み範囲は[Implementation status and plan](implementation.md)を参照してください。

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
- Type IDが存在しない、利用できない、または固定regionで作成できない場合はterminal failureとして記録する

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
| `Critical` | 自動処理を停止し、人間の判断が必要か |

代表的reason:

- `TypeAccepted`
- `UnknownLinodeType`
- `RegionUnavailable`
- `HostAssigned`
- `LinodeProvisioning`
- `HostAgentPending`
- `HostReady`
- `TerminalFailure`
- `CreationOutcomeUnknown`
- `OwnershipConflict`
- `DeletionFailed`

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

### Fake

Fakeは`AkamaiHostService`と同じprivate contractを再現します。

- Linode Type ID
- Linode ID
- raw status transition
- ownership metadata
- create/delete response loss
- timeoutとAPI rejection
- account inventory

runtimeでfakeと実providerを動的に切り替えるplugin systemは作りません。

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
Failed
Critical
```

`running`なLinodeでもHost Agentが未認証、bootstrap不一致、health不良なら`Ready`ではありません。

## Controller model

HostControllerは、daemon稼働中に担当resourceへ継続責任を持つlevel-triggered control loopです。

- eventやnotificationは処理を早めるhint
- periodic full observationを正しさの基礎にする
- busy loopにはしない
- operator向けの`retry`や`reconcile`操作を通常lifecycleの前提にしない
- 同じreconciliationを何度実行しても安全な処理へ分解する
- external I/O中にdatabase transactionを保持しない

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

## Terminal and Critical failures

複雑な自動repairは行いません。
外部mutationの結果不明やstate mismatchは短いbounded observationで確認し、解決しなければCriticalとして通常mutationを停止します。

### Creation outcome unknown

1. create前にHost ID、Claim ID、Type ID、intentを永続化する
2. create responseが失われても同じcreateを即座に繰り返さない
3. ownership metadataを使ってaccount inventoryを観測する
4. 発見できれば通常処理へ戻る
5. window内に一意に確認できなければ`CreationOutcomeUnknown`としてCriticalにする

### Ownership mismatch

保存済みLinode ID、Host ID、Claim ID、deployment IDとinventoryが一致しなければ破壊操作を行いません。
Critical recordへ最後の観測と不一致内容を保存します。

### Deletion failure

normal deleteがdefinitiveに拒否された場合、または結果不明後もresourceが存在する場合はCriticalとします。
HostControllerは無期限のdelete retryを行いません。

## CostController

CostControllerは、HostControllerが正常に管理できなくなった課金resourceを、設定された猶予期間後に処分する独立controllerです。
初期deploymentでは同じ`control-plane` process内で動かし、別serviceにはしません。

### Eligibility

最低限、次をすべて満たす必要があります。

```text
Control Plane ownership is exact
terminal or critical state is recorded
configured grace period elapsed
no active claim references the Host
no normal lifecycle operation is running
no data protection hold exists
automatic force deletion is enabled
```

orphan inventoryはownership tagだけで即削除せず、Control Plane recordへ取り込みCriticalとして提示してからpolicyを適用します。

### Action

1. eligibilityをtransactionで確定し、disposal intentを記録する
2. exact Linode IDへdeleteを一度要求する
3. bounded window内で不存在を観測する
4. 不存在ならdisposedとして記録する
5. API rejection、resource lock、または期限後も存在する場合はCriticalを更新する
6. operator acknowledgementまたはpolicy generation変更までmutationを停止する

CostControllerはClaimを作成、置換、修復しません。backup成功も推測しません。

## Data safety

将来Data layerが追加された場合、costよりdata safetyを優先します。

```text
backup成功またはdata不要を確認
  -> Host削除を許可

backup失敗または結果不明
  -> DataProtectionHold
  -> HostControllerとCostControllerの削除を禁止
  -> Critical alert
```

passwordless restic repositoryを継続し、object storage credentialとresource isolationをsecurity boundaryにします。

## Official references

- Linode Type list: <https://techdocs.akamai.com/linode-api/reference/get-linode-types>
- Linode Type lookup: <https://techdocs.akamai.com/linode-api/reference/get-linode-type>
- Linode creation: <https://techdocs.akamai.com/linode-api/reference/post-linode-instance>
- Linode lookup: <https://techdocs.akamai.com/linode-api/reference/get-linode-instance>
- Linode deletion: <https://techdocs.akamai.com/linode-api/reference/delete-linode-instance>
