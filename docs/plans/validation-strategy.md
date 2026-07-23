# Validation strategy

## 1. Test layers

### Unit tests

- resource transition
- compatibility matching
- retry policy
- billing boundary calculation
- certificate identity parsing
- command digest and journal behavior
- provider status normalization

External I/Oとwall clockはtraitまたはexplicit dependencyで置換可能にします。

### Scenario tests

SQLiteを含む実際のstorageとfake external adapterを使用します。

- multiple HostClaim allocation
- mccpd restart between every step
- create outcome unknown
- delete outcome unknown
- stale `mccp-hostd` response
- certificate expiration
- failed sanitization
- idle reuse and expiration

### Contract tests

- JSON-RPC request and response fixtures
- OpenRPC schema conformity
- SQLite migration invariants
- cloud-init/bootstrap output
- provider request construction
- certificate SAN and chain validation

### Live acceptance

課金と実credentialを伴うtestは通常testから分離し、明示的なflagと一意なownership identityを必要とします。

## 2. Fault injection

少なくとも次の境界でfailureを注入します。

- external call前
- external call成功後、result保存前
- result保存後、next state更新前
- `mccp-hostd` command受信後、実行前
- side effect完了後、journal保存前
- journal保存後、result送信前
- certificate更新中
- Host releaseとsanitizationの間

## 3. Property-based testing candidates

- ID、generation、fencing tokenのserialization
- Host lifecycleの不正transition拒否
- retry scheduleの上限
- arbitrary RPC payloadがpanicを起こさないこと
- path and identifier validation
- command replay invariants

## 4. Security validation

- secret typeのDebug/Display redaction
- log captureでsecret absenceを確認
- malformed certificateとwrong SANを拒否
- oversized RPC payloadを拒否
- unknown command variantを拒否
- ownership mismatchでdeleteを呼ばない
- stale allocation resultを状態へ反映しない

## 5. Live acceptance safety

- 作成前にaccount、region、plan、expected maximum costを表示する。
- project ownerの明示確認を必要とする。
- resourceへsystem、test run、expiryのownership metadataを付与する。
- cleanupはownership完全一致時だけ行う。
- cleanup失敗時にresource identityと手動確認手順を出力する。
- test成功条件にprovider上の不存在確認を含める。
