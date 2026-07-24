# Host Control System v1 open questions

決定済みの大きな方針は[Architecture](architecture.md)にあります。
共通のerror classificationは[Failure model](../../failure-model.md)を正本とします。
以下は、各vertical sliceを実装する直前に決定する事項です。

質問を解決するときは、既存の標準、Akamaiの公式API、成熟したRust libraryを先に調査し、必要な範囲だけを決めます。

## Akamai-native local model

- raw Linode statusを表すforward-compatible Rust型
- public Host resourceにどのAkamai fieldを公開するか
- common Incident tableのmigrationとindex
- `FatalIncident.code`の最初のstable vocabulary
- Incident acknowledgementとresolutionのtransaction
- resolved Incident後にaffected Controllerを再評価する方法
- current test-only retry fieldsを削除する時点
- dedicated Unix socket上で`POST /rpc`以外を拒否するtransport test
- SQLx checked queryとoffline metadataを導入する時点

## Real Akamai integration

- official OpenAPIからclientを生成するか、direct HTTP clientを実装するか
- Akamai API tokenの権限とsecret loading
- fixed region、image、Cloud Firewall、VPC/interface、disk encryptionのtyped configuration
- Host ID、Claim ID、deployment IDをlabel/tagへどう符号化するか
- account inventoryのpagination
- HTTP status、Akamai error object、transport errorをtyped errorへ変換する方法
- resource-local IncidentとAkamai subsystem-wide Incidentの判定基準

## Host Agent communication

- HTTPS request/response、long polling、streamingのどれを使用するか
- HTTP/2を継続するか、別transportが必要か
- mTLS certificate profile、発行、rotation、失効
- enrollmentとHost identityのbinding
- command deliveryとlocal journalの最小protocol
- Host Agentが報告するcapabilityとhealth model
- Host Agent failureをresource-local Fatal Incidentへ昇格する条件

## Idle Host reuse

- ClaimとHostのallocationを独立resourceへ分離する必要があるか
- compatibilityをLinode Type ID、bootstrap revision、network/security configurationのどこまで一致させるか
- 再利用前のsanitization contract
- Linodeの課金境界をどのAkamai timestampから判断するか
- idle保持時間、最大台数、削除margin
- OpenまたはAcknowledged Incidentを持つHostを再利用対象から除外するquery

## Incident interface

- `incident.list`、`incident.get`、`incident.acknowledge`、`incident.resolve`のRPC shape
- Discord Botなどのmonitorがpollingするためのcursorまたは`updated_since`
- Incident detailsからsecretを確実にredactするboundary
- operator noteをIncident recordに持つか、別event logへ分けるか
- global Incidentが存在する間のAkamai mutation gate

## Later Data layer dependency

- backup failureをFatal Incidentへ昇格するresource scope
- backup成功をHost normal deletionへどう伝えるか
- passwordless restic repositoryへのtemporary credential発行方法
- repository namespace、snapshot verification、retention
