# HostClaim specification

- Status: Target direction
- Implementation: not yet reflected in the current Rust resource types

`HostClaim`は、一台のAkamai Cloud Linodeに対する永続的かつ排他的な需要です。
一つのClaimを複数workloadでpackingしたり、複数Claimを一台へpackingしたりしません。

## Spec

wire上の最小形は次です。

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

`type`はAkamai Cloudの**正確なLinode Type ID**です。
`g7-dedicated-8-4`のように、Linode APIの`GET /v4/linode/types`または`GET /v4/linode/types/{typeId}`で扱われるIDをそのまま指定します。

- CLI flagは`--type`
- wire fieldは`type`
- Rustでは検証済みnewtype `LinodeTypeId`として扱う
- immutableとし、変更したい場合はClaimを置き換える
- Control PlaneはCPU、RAM、storageから別typeを自動選択しない
- Type IDが存在しない、利用できない、または固定regionで作成できない場合はClaimをterminal failureとして記録する

この形を採用する理由:

- CPU generation、dedicated/shared、RAM、local disk、価格はLinode Typeとして一体で提供される
- vCPU数だけではCPU classや性能特性を表せない
- 自動plan selectionはpolicy、catalog更新、reserve計算、tie-breakを必要とし、実用途に対する価値が小さい
- operatorまたは上位layerが意図したSKUを明示できる
- Akamai Cloud専用である事実をresource modelへ正直に反映できる

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

## Metadata

### `id`

- UUIDv7
- callerが生成する
- 同一ID・同一specのcreateはidempotent
- 同一ID・異なるspecはconflict

### `generation`

spec revisionです。最初はupdateを提供しないため`1`ですが、statusの観測世代を明示するため保持します。

### `deletion_timestamp`

通常のClaim解放要求です。
Host controllerは正常なlifecycleとして、必要なdata-safety条件を満たした後に対応Linodeを削除し、不存在を確認してからClaimをfinalizeします。

terminal/critical errorからの強制削除はHost controllerの責務ではなく、将来のCost controllerまたは人間の責務です。

## Status

```text
HostClaimStatus
  observed_generation
  host_id
  conditions
```

初期condition:

| Type | Meaning |
| --- | --- |
| `Accepted` | Type IDと固定deployment policyをControllerが評価済みか |
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

## Host and Linode identity

- `Host ID`: Control Planeが発行する論理identity
- `Linode ID`: Akamai Cloudが発行するinstance identity
- `Linode Type ID`: Claimが指定するSKU identity

Host IDとLinode IDは同一視しません。
Linode作成前にHost ID、Claim ID、Type IDを永続化し、label/tagへownership metadataを付与します。

## Current implementation gap

現在のRust vertical sliceは、`vcpus`、`memory_bytes`、`storage_bytes`からfake planを選択します。
これは基盤検証用の旧modelであり、Linode integration前に破壊的に置き換えます。

予定する変更:

- `HostResources`を`LinodeTypeId`へ置換
- plan catalog selectionを削除
- CLIを`control host claim create --type <type-id>`へ変更
- fake provider catalogを、Akamaiのtype IDとLinode状態を再現するfake Akamai APIへ変更
- database migrationは作り直し、互換migrationは提供しない

## Official references

- Linode Type list: <https://techdocs.akamai.com/linode-api/reference/get-linode-types>
- Linode Type lookup: <https://techdocs.akamai.com/linode-api/reference/get-linode-type>
- Linode creation: <https://techdocs.akamai.com/linode-api/reference/post-linode-instance>
