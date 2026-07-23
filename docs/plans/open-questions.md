# Open questions

未確定事項を暗黙のimplementation detailにしないため、この文書で追跡します。

## Protocol and transport

1. Host profileをHTTP/1.1 long polling、HTTP/2、WebSocketのどれから開始するか。
2. JSON-RPC batchとnotificationを禁止するか。
3. OpenRPCをRust型から生成するか、schema-firstにするか。
4. Unix domain socket上のJSON message framingをHTTPにするか、length-prefixにするか。
5. Host observationとcommand resultを一つの`hostd.exchange`へまとめるか、methodを分けるか。

## PKI

1. RustのCA、X.509、TLS libraryを何にするか。
2. Online intermediate private keyをfilesystem、OS key store、外部secret storeのどこへ置くか。
3. Leaf certificate lifetimeとrotation window。
4. Revocationをshort lifetimeだけで扱うか、明示deny listも持つか。
5. Development CA bootstrap UX。

## Persistence

1. SQLite accessを同期thread poolとするか、async wrapperを使うか。
2. Controller leaseをrow lock相当、compare-and-swap、single schedulerのどれで実装するか。
3. Audit tableの保持期間。
4. Resource rowを削除するか、tombstoneを保持するか。

## Host provisioning

1. Linode APIは公式Rust SDKが十分か、generated/direct HTTP clientを使うか。
2. `mccp-hostd` artifact配布をimage、package repository、Control Plane endpointのどれにするか。
3. Bootstrap integrityをchecksum、signature、image bakingのどこまで行うか。
4. Host clock synchronizationをどのbaselineで検証するか。

## Host reuse

1. 初期checkpointで異なるowner間のHost再利用を許可するか。
2. Sanitizationの最低保証。
3. billing boundaryの基準時刻をproviderのどのfieldから取るか。
4. deletion safety marginのdefault。
5. Idle poolの最大台数とHostClassごとのpolicy。

## Operations

1. Control Plane node自体のdeployment方法。
2. Unix socket以外のoperator authentication。
3. Configuration reloadを許可するか、restartのみとするか。
4. Metrics、tracing、log formatの初期scope。

各questionは実装直前にdecisionが必要なものと、実測後に決めるものへ分類します。
