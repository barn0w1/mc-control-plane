# Failure model

この文書は、すべてのcheckpointとControllerに共通するfailure handlingの方針です。
個別Controllerが独自のretry state machineやcleanup workflowを作らず、同じ分類と記録方法を使用します。

## Principles

- 正常な入力拒否、resource不存在、既知のlifecycle transitionと、予測不能な外部failureを区別する
- 予測不能な外部failureを無期限に自動回復しない
- 外部mutationを推測で繰り返さない
- affected resourceへの自動mutationを停止し、永続的なIncidentとして記録する
- Control Plane全体は可能な限り動作を継続し、他resourceの管理とoperator RPCを維持する
- 強制cleanupやcost enforcementはControl Planeの責務に含めない
- actual Rust panicはprogram bugまたは内部不変条件違反に限定する

## Error classes

### Expected outcome

通常のdomain処理として表現できる結果です。

例:

- HostClaimのType ID形式が不正
- 同じClaim IDに異なるspecが送られた
- get対象が存在しない
- 削除済みLinodeを観測して`Absent`と判断した
- operatorにより明示的に拒否された操作

Expected outcomeはtyped RPC error、resource condition、または通常のstate transitionとして返します。
Incidentは作成しません。

### Fatal external failure

Control Planeが正常なlifecycleとして解決できない外部failureです。

例:

- Akamai APIの予期しないnon-success response
- authenticationまたはauthorization failure
- transport error、timeout、response decode failure
- mutation requestの結果が不明
- ownership metadataの不一致
- 期待したLinode identityまたはstateとの矛盾
- normal lifecycleのdelete request failure
- Host Agentのidentity contradiction

Fatal external failureが発生した場合:

1. errorをtyped valueとして上位へ返す
2. affected resourceまたはsubsystemを`Critical`にする
3. common Incident storeへ永続化する
4. affected scopeへの自動mutationを停止する
5. read-only observationに必要な情報を保持する
6. operatorが原因を解消し、明示的にIncidentをresolveするまで自動再開しない

Controllerはfailureを隠すための独自retry workflow、replacement、forced deletionを行いません。

### Internal invariant violation

Control Plane自身のbug、またはprocess内で安全な継続を保証できない状態です。

例:

- 到達不能であるべきstateへ到達した
- memory上のidentity mappingが内部不変条件と矛盾した
- 型とdatabase constraintで保証したはずの条件が破られた

この場合はactual Rust `panic!`を許可します。
process supervisorによる再起動を前提とし、panic hookは構造化logへ最低限の情報を出します。

panic中のdatabase writeは安全性を保証できないため、panic hookからIncident tableへ書き込むことを正しさの前提にしません。
再起動後に永続stateから矛盾を検出できる場合は、通常のController処理でFatal Incidentとして記録します。

## Fatal Incident

運用上の「panicに相当する解決不能なerror」は、actual panicではなく`FatalIncident`として永続化します。

初期modelでは、少なくとも次の情報を持ちます。

```text
FatalIncident
  id
  state
  severity
  subsystem
  resource_kind
  resource_id
  operation
  code
  summary
  details
  occurred_at
  acknowledged_at
  resolved_at
```

### Identity and lifecycle

- `id`: UUIDv7
- `state`: `Open`、`Acknowledged`、`Resolved`
- `severity`: 初期は`Critical`のみ
- `subsystem`: `host-controller`、`akamai-api`、`host-agent`など
- `resource_kind`と`resource_id`: resource-specificでない場合はnull
- `operation`: `observe-linode`、`create-linode`、`delete-linode`など
- `code`: machine-readableで安定したreason
- `summary`: operator向けの短い説明
- `details`: redactedされたstructured data

Incident recordへAPI token、certificate private key、temporary credential、完全なHTTP authorization headerを保存してはいけません。

### Acknowledgement and resolution

- acknowledgementは人間が確認したことだけを表し、自動処理を再開しない
- resolutionは外部原因が解消され、resourceを再評価してよいことを表す
- Incidentのresolve方法とController再開の正確なtransactionは、実装直前に決める
- Discord Botなどの外部interfaceは将来JSON-RPCからIncidentを読み取り、人間へ通知できる

## Scope of a failure

failureは可能な限り狭いscopeへ閉じ込めます。

- 一つのLinodeだけに関係するfailure: そのHostをCriticalにする
- Akamai API credential failureなどaccount-wideなfailure: Akamai integration subsystem全体のmutationを停止する
- database integrityやprocess invariant failure: processをpanicさせる

一つのHostのfailureで、無関係なHostの観測やoperator RPCまで停止させません。

## External mutation rule

外部mutationは、成功したと確認できない場合でも無条件に再送しません。

- request前に必要なidentityとintentを永続化する
- definite successだけを正常stateへ反映する
- definite rejectionまたはtransport uncertaintyはFatal Incidentにする
- response loss後の自動的なcreate/delete再送は行わない
- operator調査に必要なrequest identityと最後のobservationを残す

read-only observationを一度追加で行い事実を記録することはできますが、それを無期限のrecovery loopにはしません。

## Deletion and cost

Claim解放やIdle retention終了による**正常lifecycleのLinode削除**はHost Controllerの責務です。

Fatal Incident発生後のforced deletion、stale billing resourceのcleanup、account全体のcost enforcementはControl Planeの責務ではありません。
必要であれば、別repository・別processの外部programがAkamai Cloudを独立監視します。
そのprogramはHost Control System v1の完成条件でも、Control Planeの安全性前提でもありません。

## Rust mapping

Rustの公式error handling modelに合わせます。

- anticipated runtime failureは`Result<T, E>`で表す
- external API errorもboundaryでtyped errorとして受け取る
- Control Planeが回復しないと決めた場合は、その`Err`をFatal Incidentへ昇格する
- `panic!`はbugと内部不変条件違反に限定する

References:

- <https://doc.rust-lang.org/book/ch09-00-error-handling.html>
- <https://doc.rust-lang.org/book/ch09-01-unrecoverable-errors-with-panic.html>
- <https://doc.rust-lang.org/core/macro.panic.html>
