# RPC protocol

## 1. Scope

すべてのinterfaceは`mccpd`のRPC clientです。

- `mccpctl`はdatabaseやproviderへ直接接続しない。
- 将来のDiscord BotやWeb backendも同じapplication RPCを使う。
- `mccp-hostd`はHost専用RPC surfaceだけを使う。
- RPC methodはapplication boundaryであり、内部repository APIをそのまま公開しない。

## 2. Application protocol

JSON-RPC 2.0をapplication message envelopeとして採用する方針です。
Method、parameter、result、error schemaはOpenRPC documentとして生成・検証します。

References:

- <https://www.jsonrpc.org/specification>
- <https://spec.open-rpc.org/>

JSON-RPCが定義しない次の性質は、このprojectのprotocol profileで定義します。

- transport
- authentication and authorization
- request size limit
- timeout and deadline
- idempotency
- durable command delivery
- schema versioning
- error taxonomy

## 3. RPC surfaces

一つの巨大なmethod namespaceにしません。

```text
system.*
host_class.*
host_claim.*
host.*
activity.*
identity.*
hostd.*
```

Host daemon用のmethodは、通常のoperator clientから呼べないauthorization scopeに分離します。

初期候補:

```text
system.get_info
host_class.create
host_class.get
host_class.list
host_claim.create
host_claim.get
host_claim.release
host.get
host.list
activity.get
hostd.enroll
hostd.exchange
```

Method名とpayloadは実装前にOpenRPC draftで確定します。

## 4. Transport profiles

### Local operator profile

`mccpctl`と同一nodeの`mccpd`間はUnix domain socketを第一候補とします。
OS file permissionとpeer credentialを利用し、network portを不要にします。

### Remote client profile

Remote interfaceはHTTPS上のJSON-RPCを候補とします。認証方式と公開範囲は将来決定します。

### Host profile

最初の実装候補は、HTTPS上のrequest/responseとmTLSです。
`mccp-hostd`から外向きに`hostd.exchange`を繰り返し、observation、完了結果、次commandを交換します。

WebSocket、HTTP/2 streaming、HTTP/3/QUICは、継続接続が実際に必要になった場合の選択肢とします。
最初からraw TCP framingや独自QUIC application protocolを作りません。

Transportの最終決定はADR-0006で追跡します。

## 5. Durable commands

JSON-RPC request IDと、Host上の副作用を識別するcommand IDを分離します。

- JSON-RPC `id`: 一回の通信requestとresponseの対応
- `command_id`: 再配送されても同じ副作用として扱う永続identity

Host commandはat-least-onceで配送される可能性があります。
`mccp-hostd`はlocal journalへcommand ID、canonical request digest、resultを保存します。

同じcommand IDについて:

- 同じdigestなら保存済みresultを返す。
- 異なるdigestならprotocol violationとして拒否する。
- 実行中なら同時に再実行しない。

## 6. Command design

Host RPCは任意shell commandを受け付けません。

Rustのtagged enumで閉じたcommand集合を定義します。

```text
HostCommand
  Inspect
  PrepareFixture
  RemoveFixture
  Reboot
  future: RestoreData
  future: ApplyWorkload
  future: StartWorkload
  future: StopWorkload
```

Path、unit名、container名、credential scopeなどを自由文字列のまま渡さず、Host側policyで検証します。

## 7. Error model

JSON-RPC標準errorに、project固有のstructured dataを載せます。

```text
code
message
 data:
   error_code
   category
   retryable
   operator_action_required
   resource_id
   correlation_id
```

Human-readable messageを制御判断に使いません。安定した`error_code`とcategoryを使います。

## 8. Compatibility during development

最初のstable releaseまではprotocol後方互換性を保証しません。
`mccpd`と`mccp-hostd`は同一workspaceの共有型を使用し、互換性shimを作りません。

一方、接続時にはprotocol revisionとsoftware build identityを交換し、不一致を明確なerrorとして停止します。
暗黙のbest effort parsingは行いません。
