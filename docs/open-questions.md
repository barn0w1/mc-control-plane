# Open questions

未確定事項は、実装を始める時点で必要なものだけを決めます。

## Decide before creating the Rust workspace

1. projectとbinaryの名称
2. Host需要resourceの名称
3. 最初のworkspaceに含めるcrateまたはpackageの最小単位

## Decide before the first RPC implementation

1. local operator CLIとControl Plane daemonのtransport
2. JSON-RPC schemaをRust型から生成するか、schema-firstにするか
3. 使用するJSON-RPC library、または小さなprotocol adapterの範囲

## Decide before persistent reconciliation

1. SQLite access libraryとmigration方法
2. resource revisionとconcurrent updateの扱い
3. controller wake-upとretryをどこまで永続化するか

## Decide later

- Linode API client library
- Host enrollmentとmTLS PKI
- Host daemon command delivery
- idle retentionとbilling boundaryの具体的policy
- Data layer、restic credential、snapshot、restore、retention
- Workload layerとMinecraft-specific model

「Decide later」にある項目は、現在のfoundation実装を止める理由にはしません。
