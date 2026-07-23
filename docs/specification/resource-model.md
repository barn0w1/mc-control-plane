# Resource model

## 1. Common shape

永続resourceは可能な範囲で共通の形を持ちます。

```text
metadata
  id
  created_at
  updated_at
  generation

spec
  requested state

status
  observed_generation
  phase
  conditions
  last_observed_at
```

`spec`は要求、`status`は観測結果です。controllerは`status`を更新しますが、要求を勝手に書き換えません。

`generation`はspec変更ごとに増加します。`observed_generation`により、statusがどのspecを観測したものか判断します。

## 2. Conditions

単一の`phase`だけでは複数の独立した状態を表現できません。重要な性質はconditionとして保持します。

```text
Condition
  type
  status: true | false | unknown
  reason
  message
  observed_generation
  last_transition_at
```

Hostに想定するconditionの例:

- `ProviderReady`
- `BootstrapReady`
- `AgentConnected`
- `Healthy`
- `Allocated`
- `Reusable`
- `Terminating`

`reason`は機械処理可能な安定したcode、`message`は人間向け説明です。

## 3. Foundation resources

### HostClass

確保するHostの互換条件を定義します。

```text
HostClass
  id
  provider: linode
  region
  instance_type
  image
  firewall
  bootstrap_revision
  hostd_revision
  security_profile
```

これは汎用cloud abstractionではありません。最初はLinode固有のtyped specを許容し、provider detailを
上位layerへ漏らさないことだけを保証します。

### HostClaim

上位layerからHost subsystemへ提示される、一台の排他的Host要求です。

```text
HostClaim
  id
  owner
  host_class_id
  lifecycle_policy
  requested_at
  deletion_requested_at
```

初期実装では一つのClaimが一台のHostを要求します。複数台必要なら複数Claimを作ります。
`count`を持つ集合resourceにはしません。個々の割当、failure、解放を独立して扱うためです。

### Host

Host subsystemが所有する論理Host identityです。

```text
Host
  id
  host_class_id
  incarnation
  phase
  created_at
  idle_since
  last_observed_at
```

`Host.id`はLinode IDではありません。provider resourceを作り直す場合は、新しいHostを作成します。
同じIDを別のmachineへ再利用しません。

### HostAllocation

HostClaimとHostの排他的な対応を表します。

```text
HostAllocation
  id
  claim_id
  host_id
  generation
  fencing_token
  allocated_at
  released_at
```

古いcommandや遅延した`mccp-hostd` responseは、現在の`generation`と`fencing_token`が一致しなければ拒否します。

### ProviderResource

Linode API上のresourceを表します。

```text
ProviderResource
  id
  host_id
  provider: linode
  external_id
  ownership_identity
  observed_state
  created_at
  deleted_at
```

Host controllerはprovider APIのresponse shapeを直接扱いません。Provider controllerまたはadapterが
provider固有状態を正規化します。

### HostIdentity

`mccp-hostd`が提示するidentityとcertificate lifecycleを管理します。

```text
HostIdentity
  host_id
  incarnation
  certificate_serial
  issued_at
  expires_at
  revoked_at
```

Host、certificate、RPC parameter、allocation generationの一致を検証します。

### Activity

外部副作用の一回の試行を永続化します。

```text
Activity
  id
  kind
  subject_id
  idempotency_key
  attempt
  state
  outcome
  started_at
  completed_at
  retry_at
```

詳細は[Failure model](failure-model.md)を参照してください。

## 4. Ownership rules

- Host subsystemだけが`Host.id`を発行する。
- Provider subsystemだけがprovider resourceの作成・削除を行う。
- Identity subsystemだけがHost certificateを発行・失効する。
- 上位layerは`HostClaim`を作成・削除するが、Hostを直接削除しない。
- `mccp-hostd`は自分のHost以外のresourceを参照・操作できない。
- Interface clientはdatabase entityを直接変更せず、RPC methodを呼ぶ。

## 5. Deletion

削除は即時のrow removalではありません。

1. resourceへdeletion intentを記録する。
2. controllerが依存resourceと外部resourceを安全に処理する。
3. finalizer相当の条件が満たされたらresourceを消去またはtombstone化する。

HostClaimの削除はHost削除を直接意味しません。Allocationを解放し、Host retention policyへ制御を渡します。
