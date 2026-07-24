# Cost control direction

- Status: Future direction
- Scope: billable Akamai resources left by terminal Host errors

Cost controllerは、Host controllerが正常に管理できなくなった課金resourceを、設定された猶予期間後に処分するための独立controllerです。
Host lifecycleの実装を複雑なself-healing systemへ変えずに、cost leakだけを限定的に防ぎます。

## Why separate it

Host controllerの主目的はHostClaimを正常なHostへ収束させることです。
terminal/critical状態でVMを推測的に削除すると、failure handling、data safety、cost policyがHost lifecycleへ混在します。

Cost controllerを分けることで:

- Host controllerはterminal errorで安全に停止できる
- forced deletionは明示的なpolicyの下だけで行える
- data protection holdを一か所で強制できる
- cost-related alertとauditを分離できる

初期deploymentでは同じ`control-plane` process内のtask/controllerとし、別serviceにはしません。

## Inputs

- Host/Claim terminal status
- Linode IDとownership tags
- raw Akamai observation
- resource creation time
- failure/critical transition time
- active Claimの有無
- `DataProtectionHold`
- configured grace period
- force-delete enable flag

## Eligibility

正常、active、観測不能だけのresourceは削除しません。
最低限、次をすべて満たす必要があります。

```text
Control Plane ownership is exact
terminal or critical state is recorded
configured grace period elapsed
no active claim references the Host
no normal lifecycle operation is running
no data protection hold exists
```

orphan inventoryについては、ownership tagだけで即削除せず、Control Plane recordへ取り込みCriticalとして提示してからpolicyを適用します。

## Action model

1. eligibilityをtransactionで確定し、disposal intentを記録する
2. exact Linode IDへdeleteを一度要求する
3. bounded window内でGet/Listを使い不存在を観測する
4. 不存在ならdisposedとして記録する
5. API rejection、resource lock、または期限後も存在する場合はCriticalを更新する
6. operator acknowledgementまたはpolicy generation変更までmutationを停止する

無期限の自動delete retryは行いません。

## Safety

- ownershipが曖昧なら削除禁止
- DataProtectionHoldがあれば削除禁止
- healthy/Ready/Idle Hostは対象外
- active Claimがあれば対象外
- Cost controllerがClaimを作成、置換、修復しない
- Cost controllerがbackup成功を推測しない

## Policy shape

具体schemaは実装前に決めますが、最低限次を想定します。

```text
CostControlPolicy
  enabled
  failed_host_grace_period
  unknown_creation_grace_period
  automatic_force_delete
```

`automatic_force_delete`はdeployment単位の明示設定とし、default値は実装時に決めます。

## Alerts

次はCritical alertです。

- Linode deletion APIがdefinitiveに失敗した
- delete responseが不明で、期限後もresourceが存在する
- resource lockにより削除できない
- ownership metadataが矛盾している
- DataProtectionHoldとcost deadlineが競合している

将来のDiscord interfaceは、Host lifecycleの通常statusとは別にCost/Critical alertを通知します。
