# HostClaim specification

この文書は、最初のHost controllerが扱う`HostClaim`のresource modelとreconciliation semanticsを定義します。
HostClaimはLinode作成parameterではありません。上位layerが必要とする**利用可能なHost capacity**を表します。

## Design goals

- provider固有のAPIやresource IDを上位layerへ漏らさない
- firewall、VPC、image、cloud-init、Host Agent設定をClaimごとに繰り返さない
- 上位layerが本当に必要とするCPU、memory、local storageだけを表す
- 一つのClaimが存在する間、一台の排他的Hostを維持する
- RPC timeoutやdaemon restart後にもduplicate ClaimやHostを作らない
- 将来必要になるかもしれないconstraintを先回りして追加しない

## Resource shape

概念上のJSON表現は次のとおりです。実際のRust型が正本になります。

```json
{
  "id": "019c...",
  "generation": 1,
  "created_at": "2026-07-24T12:00:00Z",
  "deletion_timestamp": null,
  "spec": {
    "resources": {
      "vcpus": 2,
      "memory_bytes": 4294967296,
      "storage_bytes": 42949672960
    }
  },
  "status": {
    "observed_generation": 1,
    "host_id": null,
    "conditions": []
  }
}
```

## Metadata

### `id`

- UUIDv7
- callerが生成して`host.claim.create`へ渡す
- createは同じIDと同じspecに対してidempotent
- 同じIDで異なるspecを送った場合はconflict

caller-generated IDにすることで、create responseが失われても同じRPCを安全に再試行できます。

### `generation`

spec revisionです。
最初の実装ではspec updateを提供しないため常に`1`ですが、specとstatusの観測関係を最初から明確にするため保持します。

### `created_at`

Control Planeが受理したUTC timestampです。

### `deletion_timestamp`

`host.claim.delete`が受理された時刻です。
Delete RPCはrowを即時削除せず、このfieldを設定します。
ControllerがHostを解放し、必要なcleanupを完了した後にClaimを最終削除します。

最初の実装ではgeneric finalizer frameworkを作らず、HostClaim repositoryとcontrollerに必要なdelete lifecycleだけを実装します。

## Spec

```text
HostClaimSpec
  resources
    vcpus
    memory_bytes
    storage_bytes
```

### Resource semantics

各値はprovider planのraw capacityではなく、workloadへ提供可能であるべき**minimum allocatable capacity**です。

| Field | Type | Meaning |
| --- | --- | --- |
| `vcpus` | positive `u32` | workloadへ割り当て可能な最小vCPU数 |
| `memory_bytes` | positive `u64` | workloadへ割り当て可能な最小memory bytes |
| `storage_bytes` | positive `u64` | workload dataへ割り当て可能な最小local storage bytes |

provider adapterはOS、filesystem、Host Agentなどのsystem reserveを加味して、要求を満たすplanを選択します。

`storage_bytes`はHost上の一時的なworking storage capacityです。Hostの消失に対するdurabilityやbackupを意味しません。永続性は将来のData layerとpasswordless restic repositoryが所有します。

例:

```text
Claim memory:       4 GiB allocatable
Configured reserve: 512 MiB
Required plan RAM:  at least 4.5 GiB
```

wire formatはinteger bytesです。CLIは`MiB`、`GiB`などのIEC inputを受け付け、canonical bytesへ変換します。

SQLiteはsigned 64-bit integerを使用するため、最初の実装では`memory_bytes`と`storage_bytes`を`i64::MAX`以下に制限します。Rustのdomain型では検証済みnewtypeとして扱い、raw integerをcontroller全体へ流しません。

### Validation

- すべてのresource値は0より大きい
- daemon configurationで定めるhard maximumを超えない
- integer overflowを起こさない
- 現在のprovider catalogとpolicyで満たせないことはschema errorではない
  - Claim自体は保存できる
  - `Accepted=False`, `reason=Unsatisfiable`としてstatusへ表す

一時的なplan availabilityやpolicy変更で後から満たせる可能性があるため、unsatisfiable ClaimをRPC validationで消失させません。


## Why the initial spec has only three resources

HostClaimは「どのLinodeを作るか」ではなく、「workloadが最低限何を利用できる必要があるか」を表します。初期systemでは、上位layerがHost選択に影響させる必要がある量はCPU、memory、local working storageだけです。

- CPU architectureはdeployment policyで一つに固定する
- shared/dedicated CPUなどvCPU性能差はallowed plan familyで固定する
- regionとnetwork topologyはdeployment policyで固定する
- network transfer量やbandwidthは選択可能な独立Host classとして扱わない
- acceleratorは現在使用しない
- data localityはHost local diskではなく将来のData layerが扱う

この前提が変わり、上位layerが異なる選択肢を必要とした場合だけ、`architecture`、`cpu_class`、`accelerator`、`locality`などをprovider-neutral constraintとして追加します。Linodeのtype IDやfirewall IDをClaimへ直接追加しません。

## Fields intentionally excluded

最初のHostClaimには次を含めません。

- cloud provider name
- Linode type IDまたはplan family
- region、availability zone
- firewall IDまたはfirewall rule
- VPC、subnet、interface
- public/private IP configuration
- image、kernel、disk layout
- cloud-init、metadata本文
- Host Agent versionまたはartifact URL
- SSH key
- tags
- backup setting
- encryption setting
- price、hourly budget
- idle retention policy
- count

これらはClaimごとのworkload requirementではなく、Control Plane deploymentまたはHost provisioning policyが所有します。

将来、CPU isolation、architecture、accelerator、placement localityなどが上位layerの実要件になった場合だけ、provider-neutralなconstraintとして追加します。

## Provider policy

最初の実Linode adapterでは、Control Plane configurationが次を所有します。

```text
Linode provisioning policy
  region
  allowed plan families or explicit type allowlist
  system resource reserve
  image
  firewall
  VPC/network attachment
  cloud-init template
  host-agent artifact/version
  ownership tags
  other fixed security settings
```

HostClaimはこのpolicyを選択・上書きしません。
一つのControl Plane deploymentでは、すべてのHostに同じbootstrapとHost Agentを使用します。

最初のHost milestoneでは、Control PlaneはfirewallやVPC自体を作成・更新しません。operatorが事前に用意したnetwork/security resourceをconfigurationで参照し、Host作成時に一貫して適用します。configurationが不正または参照先が存在しない場合は、Claim単位で不定なfallbackを行わずstartupまたはprovider readinessを失敗させます。

## Plan selection

providerは利用可能なplan catalogを観測し、次の順序でplanを選びます。

1. deployment policyで許可されたplanだけを残す
2. plan raw capacityからconfigured system reserveを引き、allocatable capacityを計算する
3. CPU、memory、storageのすべてがClaim requirement以上のplanだけを残す
4. hourly priceが最小のplanを選ぶ
5. priceが同じ場合は、memory、storage、vCPUの順にresource surplusが小さいものを選ぶ
6. 最後はprovider type IDで安定的にtie-breakする

この選択はdeterministicでなければなりません。
Provider catalogまたはpolicyが変わった場合、未充足Claimを再評価します。

最初のmilestoneでは、すでに割り当てられたHostを安価なplanへ自動resizeまたはreplaceしません。

## Status

```text
HostClaimStatus
  observed_generation
  host_id
  conditions
```

### `observed_generation`

Controllerがstatusを計算したspec generationです。
`generation`と異なる場合、statusは古い可能性があります。

### `host_id`

割り当てられたControl Plane内部のHost IDです。
Linode IDではありません。
未割当時はnullです。

### Conditions

共通condition shape:

```text
Condition
  type
  status: True | False | Unknown
  reason
  message
  observed_generation
  last_transition_at
```

最初のHostClaim condition:

| Type | Meaning |
| --- | --- |
| `Accepted` | current policy/catalogで要求が理解され、充足可能か |
| `Bound` | Host IDが割り当てられているか |
| `Ready` | 割り当てHostが現在Readyか |

`message`はoperator向け説明です。client logicは`type`、`status`、`reason`を使用します。

代表的なreason:

- `RequirementsValid`
- `Unsatisfiable`
- `HostPending`
- `HostAssigned`
- `HostReady`
- `HostNotReady`
- `DeletionRequested`

## Lifecycle

### Create

1. clientがUUIDv7のClaim IDとspecを送る。`control`は`--id`がなければ送信前にIDを生成する
2. Control Planeがshapeとhard limitを検証する
3. Claimをtransactionで保存する
4. 同じID、同じspecが既に存在すれば既存Claimを返す
5. 同じID、異なるspecならconflictを返す
6. Controllerが非同期にreconcileする

RPC成功はHost確保完了を意味しません。transport結果が不明な場合、CLIは生成済みClaim IDをerror outputへ含め、同じ`--id`で安全に再試行できるようにします。

### Reconcile

1. deletion未要求でHost未割当のClaimを観測する
2. Claimを満たすHost resourceを作成する
3. ClaimとHostの対応をdatabase constraintで一意にする
4. Host controller/provider adapterがexternal resourceを確保する
5. HostがReadyになったらClaim statusへ反映する

一つのHostClaimは一つの排他的Hostを要求します。
複数Claimを一つのHostへpackingしません。

### Delete

1. Delete RPCが`deletion_timestamp`を設定する
2. ControllerがClaimを新しい割当対象から除外する
3. Hostをreleaseする
4. 最初のfake provider実装ではHostとprovider resourceを即時削除する
5. Host cleanup完了後にClaim rowを最終削除する

将来idle reuseを追加するときも、Claim deleteの意味は変えません。ClaimはHost需要を解放し、Host subsystemがHostの次の状態を決定します。

## Minimal Host model supporting the Claim

最初のHost resourceは、Claimから独立したidentityとprovider lifecycleを持ちます。

```text
Host
  id: UUIDv7
  claim_id: UUIDv7
  requested_resources
  provider_resource_id: optional opaque string
  observed_capacity: optional resources
  state
  conditions
```

- Host IDはprovider create前に生成・保存する
- provider resourceにはHost IDをownership/discovery keyとして関連付ける
- create resultが不明な場合、Host IDでproviderを再観測する
- active Hostの`claim_id`にはunique constraintを設ける
- provider resource IDだけを根拠にdeleteしない

後にidle reuseを導入すると`claim_id`をallocation modelへ分離する可能性がありますが、最初の実装では独立resourceを先回りして追加しません。

## Controller execution model

最初のcontrollerは単一workerで動作します。throughputよりも、同じresourceを並行処理しないこととstate transitionの予測可能性を優先します。

- RPC mutation後にin-process `Notify`でcontrollerをwakeする
- notification lossや外部状態変化を回復するためperiodic scanも行う
- due resourceは永続化した`next_reconcile_at`とstable ID orderで選ぶ
- 一回のreconciliationで長時間sleepしない
- 一回のreconciliationで外部mutationは最大一つとする
- external I/OはSQLite transaction外で行う
- retry attempt、next reconcile time、last classified errorを永続化する
- process restart後はdatabase scanだけで再開できる

単一workerが実際のbottleneckになった場合にのみ、bounded concurrencyとresource leaseまたはfencingを追加します。

## Provider boundary

最初のprovider interfaceは、少なくとも次の能力を分離します。

```text
list plans / observe catalog
observe resource by Host ownership ID
create resource for a persisted Host ID and selected plan
delete an owned resource
```

Provider create/delete errorは`Transient`、`Permanent`、`OutcomeUnknown`へ分類します。`OutcomeUnknown`ではmutationを即時再実行せず、Host ownership IDによるobservationへ戻ります。

## Fake provider model

fake providerはControl Plane databaseとは別のSQLite fileを使用します。
これは外部systemを模擬し、daemon restart後も独立したobserved stateを保持するためです。

fake providerは次を持ちます。

- plan catalog
- provider resource ID
- ownership Host ID
- requested/observed capacity
- lifecycle state
- create/delete fault injection

必須fault:

- create前のdefinitive failure
- create成功後のresponse loss (`OutcomeUnknown`)
- delete成功後のresponse loss
- observationのtemporary failure

Controllerは`OutcomeUnknown`の後にcreate/deleteを無条件再実行せず、Host IDによる再観測で解決します。

## Initial RPC methods

```text
host.claim.create
host.claim.get
host.claim.list
host.claim.delete
host.get
host.list
```

`host.claim.create` paramsの概念形:

```json
{
  "id": "019c...",
  "spec": {
    "resources": {
      "vcpus": 2,
      "memory_bytes": 4294967296,
      "storage_bytes": 42949672960
    }
  }
}
```

## Deferred extensions

- mutable Claim specとreplacement semantics
- idle Host allocation/binding resource
- CPU isolation requirement
- architecture/accelerator requirement
- placement/locality
- multiple provider profiles
- multi-Host count Claim
- shared Host scheduling
- autoscaling

## Provider references

Akamai Cloudのcompute planはvCPU、memory、storageを持ち、plan familyによってshared/dedicated CPUなどの特性が異なります。
最初のClaimはresource floorだけを表し、plan familyはdeployment policyで制限します。

- Plan selection overview: <https://techdocs.akamai.com/cloud-computing/docs/how-to-choose-a-compute-instance-plan>
- Linode types API: <https://techdocs.akamai.com/linode-api/reference/get-linode-types>
- Create Linode API: <https://techdocs.akamai.com/linode-api/reference/post-linode-instance>
