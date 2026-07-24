# Host management direction

- Status: Direction
- Scope: Akamai Cloud Host control milestone

この文書はHost管理の完成形に向けた方針です。具体的なRust trait、database enum、timeout値、HTTP client libraryは実装直前に決めます。

## Host model

`Host`は、workloadを実行する土台となる、一つの管理されたGNU/Linux実行環境です。
このprojectではHostの実体を**Akamai Cloud Linodeに固定**します。
物理machineや他cloudを扱う共通modelは作りません。

```text
HostClaim 1 ── 1 Host ── 1 Linode
```

- 一つのClaimは一つの排他的Hostを要求する
- 複数Claimを一台へpackingしない
- HostClaimは正確なLinode Type IDを指定する
- Host上で動くworkloadは将来のWorkload layerが管理する

## Akamai-native design

HostClaim、Host、Host controller、Akamai integrationは縦にすべてAkamai Cloud専用として設計します。
provider-neutralなresource modelやruntime plugin systemは作りません。

Akamai固有として扱ってよいもの:

- Linode Type ID
- Linode ID
- region
- Linode status
- labelとtag
- Cloud Firewall、VPC、Linode interface
- Metadata user data
- image、disk encryption、boot behavior
- Akamai API errorとEvent
- hourly billingと削除による課金停止

これにより、存在しない将来providerのためのmapping、capability negotiation、最小公倍数APIを避けます。

## Internal boundaries

Akamai専用であっても、すべてを一つのmoduleへ混在させません。
境界はprovider交換のためではなく、責務とtestのために置きます。

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

HTTP、authentication、pagination、API response/error decodingを所有します。
公式Linode APIのoperationと型に近い低level clientです。

### `AkamaiHostService`

Host lifecycleに必要なAkamai固有operationを所有します。
例えば:

```text
get_type(type_id)
find_linode_by_host_identity(host_id, deployment_id)
create_linode(host_id, claim_id, type_id, fixed_configuration)
get_linode(linode_id)
delete_linode(linode_id)
list_owned_linodes(deployment_id)
```

この境界は公開plugin contractではありません。
third-party implementation、dynamic loading、provider selectionは提供しません。

### Fake

Fakeは`AkamaiHostService`のtest doubleとしてだけ存在します。
抽象的なcloud providerを模倣せず、次のAkamai固有挙動を再現します。

- Linode Type ID
- Linode ID
- raw Linode status transition
- label/tag ownership
- create/delete response loss
- API rejection
- inventory drift

## Controller model

Host controllerはdaemon稼働中、Host subsystemへ継続的な責任を持つnon-terminating、level-triggered control loopです。
Claim変更eventがなければ動かないjobではありません。

```text
while control-plane is running:
    read HostClaims and Hosts
    observe Akamai state
    derive Host status
    apply at most one normal lifecycle mutation
    persist observations and conditions
    wait efficiently, then repeat
```

実装はbusy loopにせず、timer、notification、periodic scanを利用できます。
`wake`や`retry`はdomain conceptやoperator APIにはしません。

## Two state layers

Akamaiの状態とHostの状態を一つのenumへ潰しません。

### Raw Linode state

Akamai APIが返す`status`をtimestamp付きで保存します。
AkamaiのstatusはControl Plane外の理由でも変化し得るため、Controllerは毎回観測結果を事実として扱います。

Rust表現はforward-compatibleにし、未知の文字列を失わない形にします。
既知statusにはAkamai APIの語彙をそのまま使い、独自名へ無理に変換しません。

### Host state

Host stateは上位layerに対する意味を表します。
正確なenumは実装直前に決めますが、次を区別します。

```text
Pending
  HostClaimはあるがLinode作成を開始していない

Provisioning
  Linode作成、boot、GNU/Linux bootstrapを観測している

Ready
  Linodeがrunningで、Host Agentを含め上位layerへ渡せる

Released
  Claimから解放された

Idle
  正常なpolicyにより短時間再利用可能として保持されている

Deleting
  正常lifecycleとしてLinode削除を行っている

Failed
  自動処理を停止したterminal failure。既知の課金resourceがない、または別controllerの判断待ち

Critical
  外部状態、課金、data safety、削除失敗などにより人間の対応が必要
```

`running`だけでHostを`Ready`にしません。将来はHost Agentのidentity、health、bootstrap revisionも必要です。

## Normal lifecycle deletion

Host controllerは**正常なlifecycle**に限ってLinodeを削除します。

- Claimが解放された
- idle retention policyが削除を選択した
- 将来Data layerが削除を許可した
- 対象Linode IDとownershipが確定している

削除APIの成功応答だけでは完了とせず、後続観測でLinodeの不存在を確認します。
Akamai公式文書では、Linodeを削除すると関連するdisk、configuration、interfaceも削除され、Linodeのbillingが停止します。

## Terminal and critical failures

複雑なself-healingを目的にしません。
不確実な外部操作や不整合は、短いbounded observationの後にterminal stateへ移します。

### Creation outcome unknown

1. mutation前にHost ID、Claim ID、Type ID、ownership metadataを永続化する
2. create responseが不明ならcreateを繰り返さない
3. label/tagとinventoryで短時間観測する
4. 対応Linodeが見つかれば通常処理へ戻る
5. 見つからなければ`Critical: CreationOutcomeUnknown`としてHost controllerのmutationを停止する

Host controllerは「回復のため」に推測でLinodeを削除したり、新しいLinodeを作ったりしません。

### State mismatch

Linode ID、ownership、Type ID、観測状態が期待と一致しない場合は短時間再観測します。
収束しなければterminal/criticalとして記録し、人間またはCost controllerへ委ねます。

### Deletion failure

通常削除のAPI rejection、response unknown後も存在が続く状態、resource lockなどで削除できない状態はCriticalです。
Host controllerは無期限に削除を繰り返しません。

critical recordには少なくとも次を残します。

- Host ID、Claim ID、Linode ID
- Type ID
- reasonと最初の発生時刻
- 最後のAkamai observation
- 最後に実行したmutation
- operatorが確認すべき情報

将来DiscordなどのinterfaceはCriticalを最優先で通知します。

## Cost controller

失敗状態の課金resourceを長期間残さないため、Host controllerとは別の**Cost controller**を将来追加します。
最初は別processではなく、同じ`control-plane` daemon内の独立controllerとします。

Cost controllerの責務:

- Control Plane所有tagを持つLinode inventoryを継続監視する
- terminal/critical状態で一定時間を超えたbillable resourceを検出する
- active Claim、normal deletion、data protection holdを持つresourceを除外する
- policyが許可したresourceだけをforce-deleteする
- 削除結果を観測する
- 削除できなければCriticalを更新し、人間へ通知する

Cost controllerはHostのrepair、replacement、bootstrapを行いません。
一般的なcloud cost optimizerでもありません。

自動force-deleteの最低条件:

```text
exact ownership verified
AND terminal state age >= configured grace period
AND no active HostClaim
AND no data-safety hold
AND automatic cost disposal enabled
```

削除APIが失敗した場合はcriticalです。
無期限のmutation loopにせず、bounded attempt後はoperator interventionを必要とします。

## Data safety precedence

将来Minecraft dataを扱う場合、costよりdata safetyを優先します。

- durable backup/handoff成功: Host deletionを許可できる
- backup失敗または結果不明: `DataProtectionHold`を設定し、自動削除を禁止する
- backup failure: Critical alert
- DataProtectionHold中はCost controllerも削除しない

passwordless restic repositoryを継続し、backupの成否をHostの削除可否へ明示的に接続します。

## Billing reason

Akamai Cloudはresourceがpowered offでもaccount上に存在する限り課金し、追加課金を止めるには削除が必要です。
多くのserviceは使用時間を時間単位へ切り上げます。
そのため正常なidle retentionと、異常resourceのCost controllerは別々に必要です。

## Consequences

採用するもの:

- Akamai-native HostClaimとController
- exact Linode Type ID
- raw Linode observationと抽象Host stateの二層
- normal lifecycle mutationとcritical recoveryの分離
- future Cost controller
- fake Akamai service for deterministic tests

採用しないもの:

- provider-neutral Host model
- generic provider plugin
- CPU/RAM/storageからの自動plan selection
- terminal failureを隠す無期限retry
- Host controllerによる推測的cleanup
- ownershipが不明なresourceの自動削除

## Official references

- Linode API: <https://techdocs.akamai.com/linode-api/reference/api>
- Get a Linode and status: <https://techdocs.akamai.com/linode-api/reference/get-linode-instance>
- Create a Linode: <https://techdocs.akamai.com/linode-api/reference/post-linode-instance>
- Delete a Linode: <https://techdocs.akamai.com/linode-api/reference/delete-linode-instance>
- List/Get Linode Types: <https://techdocs.akamai.com/linode-api/reference/get-linode-types>
- Account events: <https://techdocs.akamai.com/linode-api/reference/get-events>
- Billing: <https://techdocs.akamai.com/cloud-computing/docs/understanding-how-billing-works>
- Billing FAQ: <https://techdocs.akamai.com/cloud-computing/docs/billing-faqs>
