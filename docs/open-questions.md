# Open questions

最初のworkspaceに必要なtoolchain、library、local RPC、SQLite、HostClaim specは決定済みです。
詳細は[Implementation foundation](implementation-foundation.md)と[HostClaim specification](host-claim-spec.md)を参照してください。

質問を解決するときは、既存の標準と成熟したlibraryを先に調査し、実装直前に必要な範囲だけを決めます。

## During the first implementation

- dedicated Unix socket上で`POST /rpc`以外を拒否することをどのtransport testで固定するか
- SQLx checked queryへの移行とoffline metadata生成をどのcommitで行うか
- `Failed` Hostをoperator retry、automatic replacement、Claim recreationのどれで回復させるか

fake provider fault injectionは、最初の実装ではtest-only APIとしてControl Plane process内のtestから使用します。operator RPCには公開しません。

これらは基本方針を変更せず、実装を進めながら決められます。

## Before Linode integration

Akamai Cloudを正式な実providerとし、runtimeで差し替える汎用provider plugin systemは作りません。fake testのためのprivate infrastructure boundaryだけを残します。方向は[Host management direction](host-management-direction.md)を参照してください。

実装前に決める事項:

- official SDK、generated client、direct HTTP clientのどれを使用するか
- allowed plan family/type policyをconfigurationでどう表現するか
- system reserved CPU、memory、storageの初期値
- ownership metadataとresource discoveryをLinode label/tagへどう符号化するか
- bounded convergence windowとcleanup deadline
- Linode type catalogとpriceをどの頻度でrefreshするか
- account-level ownership inventoryをどのintervalで実行するか

## Before host-agent communication

- Host transportをHTTPS request/response、long polling、streamingのどれにするか
- HTTP/2を継続するか、Host通信に別transportが必要か
- mTLS certificate profile、発行、rotation、失効
- enrollmentとHost identityのbinding
- command deliveryとlocal journalの最小protocol
- Host Agentが報告するcapabilityとhealth model

## Before idle Host reuse

- ClaimとHostのallocationを独立resourceへ分離する必要があるか
- compatibilityをCPU/memory/storage以外の何で判定するか
- 再利用前のsanitization contract
- Linodeの課金境界をどのprovider timestampから判断するか
- idle保持時間、最大台数、削除margin

## Before the Data layer

- passwordless restic repositoryへのtemporary credential発行方法
- repository namespaceとownership
- snapshot verificationとretention
- workloadへ提示するstorage requirementとsnapshot size estimateの関係
