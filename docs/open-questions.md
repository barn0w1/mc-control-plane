# Open questions

現在の実装に必要になるまで、次の詳細は未確定とします。
質問を解決するときは、既存の標準や成熟したlibraryを先に調査します。

## Before implementing the first workspace

- Rust toolchain versionとMSRVをどう設定するか
- JSON-RPC 2.0 server/clientにどのmaintained libraryを使用するか
- HTTP over Unix domain socketにどのHTTP stackを使用するか
- async runtime、logging、CLI parsing、SQLite libraryを何にするか
- 最初のHostClaim specに本当に必要なfieldは何か
- fake providerの独立状態をtestでどのように表現するか

これらは最初の実装を開始する直前に決定します。

## Before Linode integration

- official SDK、generated client、direct HTTP clientのどれを使用するか
- ownership metadataとresource discoveryをどう表現するか
- create/deleteのoutcome-unknownをどのprovider情報で解決するか

## Before host-agent communication

- Host transportをHTTPS long polling、HTTP/2、WebSocketなどのどれにするか
- mTLSのcertificate profile、発行、rotation、失効をどう設計するか
- enrollmentとHost identityをどうbindingするか
- command deliveryとlocal journalの最小protocolは何か

## Before idle Host reuse

- どの条件をHost compatibilityとして扱うか
- 再利用前に何をsanitizationするか
- Linodeの課金境界をどの時刻から判断するか
- idle保持時間、最大台数、削除marginをどう設定するか

## Before the Data layer

- passwordless restic repositoryへのtemporary credential発行方法
- repository namespaceとownership
- snapshot verificationとretention
