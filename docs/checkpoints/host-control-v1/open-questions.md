# Host Control System v1 open questions

決定済みの大きな方針は[Architecture](architecture.md)にあります。
以下は、各vertical sliceを実装する直前に決定する事項です。

質問を解決するときは、既存の標準、Akamaiの公式API、成熟したRust libraryを先に調査し、必要な範囲だけを決めます。

## Akamai-native local model

- raw Linode statusを表すforward-compatible Rust型
- public Host resourceにどのAkamai fieldを公開するか
- Critical recordとoperator acknowledgement model
- current test-only retry fieldsをtarget DB modelでどこまで残すか
- dedicated Unix socket上で`POST /rpc`以外を拒否するtransport test
- SQLx checked queryとoffline metadataを導入する時点

## Real Akamai integration

- official OpenAPIからclientを生成するか、direct HTTP clientを実装するか
- Akamai API tokenの権限とsecret loading
- fixed region、image、Cloud Firewall、VPC/interface、disk encryptionのtyped configuration
- Host ID、Claim ID、deployment IDをlabel/tagへどう符号化するか
- account inventoryのpaginationとrate-limit handling
- create outcome unknownのobservation window
- normal deleteのobservation window

## Host Agent communication

- HTTPS request/response、long polling、streamingのどれを使用するか
- HTTP/2を継続するか、別transportが必要か
- mTLS certificate profile、発行、rotation、失効
- enrollmentとHost identityのbinding
- command deliveryとlocal journalの最小protocol
- Host Agentが報告するcapabilityとhealth model

## Idle Host reuse

- ClaimとHostのallocationを独立resourceへ分離する必要があるか
- compatibilityをLinode Type ID、bootstrap revision、network/security configurationのどこまで一致させるか
- 再利用前のsanitization contract
- Linodeの課金境界をどのAkamai timestampから判断するか
- idle保持時間、最大台数、削除margin

## CostController

- failed Hostのgrace period
- unknown creation outcomeのgrace period
- automatic force deletionのdefault
- one-shot disposal intentとoperator acknowledgement
- owned Linode inventoryのscan interval
- orphan resourceをControl Plane recordへ取り込む方法
- Critical alertのRPC representation

## Later Data layer dependency

- `DataProtectionHold`をどのResourceが所有するか
- backup成功をHost/CostControllerへどう通知するか
- passwordless restic repositoryへのtemporary credential発行方法
- repository namespace、snapshot verification、retention
