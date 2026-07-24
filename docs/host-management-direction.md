# Host management direction

- Status: Direction
- Scope: Host control milestone

この文書は、Host管理を完成させるための設計方針をまとめます。
具体的なAPI、database schema、timeout値、Linode clientの選択を固定する実装仕様ではありません。

## Hostの意味

`Host`は、workloadを実行する土台となる、一つの管理されたGNU/Linux実行環境です。
仮想machineでも物理machineでも同じ概念として扱えますが、このprojectで実際にprovisionする対象はAkamai CloudのLinodeです。

一つの`HostClaim`は、一つの排他的なHostを要求します。
複数Claimを一台のHostへpackingするschedulerは作りません。

割当中の関係は次です。

```text
HostClaim 1 ── 1 Host ── 1 provider compute resource
```

Host上で複数processやcontainerが動くことはありますが、それはHostClaimのpackingではありません。
一台のHostをどのworkloadがどう使うかは、将来のWorkload layerが管理します。

## Controller model

### Conceptual model

Host controllerは、eventを受けたときだけ起動するjobではありません。
Control Plane daemonの稼働中、Host subsystemの状態に継続的な責任を持つnon-terminating control loopです。

Controllerは毎回、保存されたdesired stateと最新のobserved stateを読み、現在必要な一つの変更だけを行います。
この処理を何度繰り返しても、同じ最終状態へ収束するlevel-triggered reconciliationを基本とします。

```text
loop while control-plane is running:
    read desired HostClaims
    observe managed Hosts and provider resources
    calculate drift
    apply at most one safe external change
    persist observation and status
```

### Events are hints, not semantics

RPCによるClaim変更、Host Agentの報告、provider observationの完了などは、Controllerへ早く処理を促すhintとして使用できます。
一方、eventの配送成功を正しさの前提にしません。

通知を失っても、Controllerは自律的な再観測によって最終的に差分を発見します。
そのため、`wake`はdomain model、RPC、resource statusへ露出させません。

実装内部では、CPUを消費するbusy loopを避けるため、event notification、timer、periodic scan、next observation deadlineを組み合わせて構いません。
これらはController schedulerの内部mechanismです。

### Retry is not a resource concept

`retry`をHostやHostClaimのlifecycleとして扱いません。
一時的な通信失敗があれば、Controllerは次の観測時点で再び状態を評価します。

必要に応じて内部schedulerが次の実行時刻やattempt数を持つことはできますが、operatorが理解すべき状態は次です。

- provisioning中
- ready
- deletion中
- observationが一時的に不明
- terminal failure
- cleanupが完了していないcritical state

現在の実装にある`Notify`、wake limit、retry scheduleは最初のvertical sliceの実装詳細です。
Host managementを実cloudへ接続する前に、この方針に合わせて命名とstatus exposureを見直します。

## Akamai Cloud integration boundary

### Akamai Cloudを正式なproviderとする

このprojectは、実質的にAkamai Cloud専用として設計します。
将来使う予定のないcloud providerを想定した共通最小APIや、runtimeでproviderを差し替えるplugin systemは作りません。

これにより、次をAkamai Cloudに最適化できます。

- Linode type selection
- regionとcapacity availability
- labelとtagによるownership
- interfaces、VPC、Cloud Firewall
- Metadata serviceとcloud-init
- billing model
- API error classification

### Internal seamは残す

Akamai Cloud専用であっても、Host controllerへLinode HTTP detailを直接埋め込みません。
Control Plane内部に小さなsemantic infrastructure boundaryを置きます。
Linode API endpointを一対一で写す薄いwrapperではなく、Host lifecycleに必要な意味へ変換するadapterです。

このboundaryの目的は次の二つだけです。

1. ControllerとLinode API clientの責務を分ける
2. fake implementationで決定的なtestを行う

これはpublic plugin contractではありません。
semantic versioning、third-party implementation、dynamic loading、provider selection configurationは提供しません。

概念上、boundaryは次のoperationだけを持ちます。

```text
resolve(requirements, provisioning policy) -> resolved plan
observe(host identity) -> provider observation
create(host identity, resolved plan) -> submitted or outcome unknown
remove(owned provider resource) -> submitted or outcome unknown
```

実際のRust trait、型、errorはLinode integrationを実装する直前に決めます。

### Controllerへ公開しないprovider detail

次はHostClaim specへ含めず、Control Plane deploymentのprovisioning policyとして管理します。

- Akamai region
- allowed Linode type family
- VPCとsubnet
- Cloud Firewall
- public/private interface構成
- image、kernel、disk layout
- cloud-init / Metadata user data
- Host Agent artifactとbootstrap revision
- tagsとlabel format
- SSH access policy

これらはconfiguration fileやsecret referenceから読みます。
ハードコードはしませんが、Claimごとに自由指定させる必要もありません。

HostClaimが要求するのは、引き続きworkloadへ提供するminimum allocatable capacityです。

- vCPU count
- memory bytes
- local storage bytes

provider plan IDは、requirementsとdeployment policyからinfrastructure componentが解決し、create前にControl Plane内部stateへ固定します。

## Ownership and observation

すべてのprovider compute resourceは、Control PlaneのHost identityを復元できるownership metadataを持たなければなりません。
Akamai Cloudでは、決定的なlabelと複数tagを使用する方向です。

最低限、次を区別できる必要があります。

- このControl Plane deploymentが所有するresourceか
- どのHost IDに対応するか
- 現在のHost incarnationに対応するか

provider resource IDだけを破壊操作の根拠にしません。

Host controllerは個別Hostのreconciliationに加え、Control Plane所有tagを持つLinodeのinventoryを定期的に確認する方向とします。
これは、response loss、databaseとのdrift、orphaned billable resourceを検出する単純な安全網です。
独立した汎用orphan recovery systemへ発展させる必要はありません。

## Bounded uncertainty

Cloud APIは、requestが失敗したように見えても外部変更が成功している可能性があります。
一方、無期限に複雑な回復処理を続けることも目標にしません。

基本方針は次です。

1. mutation前にHost identityとresolved planを永続化する
2. mutationのresponseが不明なら同じmutationを直ちに繰り返さない
3. ownership metadataで外部状態を観測する
4. 短いbounded convergence window内で期待状態を確認する
5. 期限内に確認できなければterminal failureとして扱う
6. Control Planeが確実に所有すると判断できる課金resourceはcleanupする
7. ownershipが曖昧なresourceは破壊せず、critical alertにする

`timeout`はcontrollerの責任放棄ではありません。
Provisioningをterminal failureにした後も、既知の課金resourceが残っていればcleanup責任は継続します。

## Failure and cleanup policy

### Provisioning failure

Hostが規定時間内にusableにならない場合、そのHost attemptはterminal failureです。
同じHostを無期限に修復し続けません。

Control Planeが所有を確認できるprovider resourceは削除し、必要なら上位resourceが新しいHostを要求できる状態にします。
自動replacementを行うかは、HostClaim lifecycleを実装するときに決めます。

### Provider state mismatch

期待するplan、ownership、resource identityとprovider observationが一致しない場合、短い再観測期間を設けます。
一致しなければHostをterminal failureとします。

- ownershipを確実に確認できる: resourceをcleanupする
- ownershipが曖昧: 削除せずcritical alertを発生させる

安全性のため、未知のresourceを推測で削除しません。

### Deletion failure

課金resourceの削除要求が失敗する、または不存在を確認できない状態はcriticalです。
Control PlaneはHost recordをfinalizeせず、所有情報とcleanup stateを保持します。

critical alertを発生させたうえで、観測によりresourceがまだ存在すると確認できる限り、controlled cleanupを継続します。
削除できていないresourceをdatabaseから忘れません。

### Future data safety boundary

将来Minecraft dataを扱う場合、billable Hostを削除してよい条件は、必要なbackupまたはdurable data handoffが成功していることです。

- backup成功: workloadを停止し、課金resourceをcleanupしてよい
- backup失敗または結果不明: Host削除を止め、critical alertを発生させる
- provider削除失敗: dataが安全でもcritical alertを発生させる

backup失敗を通常の自動回復として隠しません。
operatorと将来のDiscord interfaceへ最優先で通知すべき状態です。

## Host lifecycle target

正確なenumは実装直前に決めますが、完成形では次の意味を区別します。

```text
Requested
  HostClaimは存在するがHostがまだない

Provisioning
  provider resourceの確保とGNU/Linux bootstrapを行っている

Ready
  Host Agentを含め、上位layerへ渡せる

Released
  Claimから解放された

Idle
  policyにより短時間保持され、再利用可能

Deleting
  provider resourceの削除と不存在確認を行っている

Failed
  Host attemptがterminal failureになった

CleanupCritical
  課金resourceまたは安全上重要なcleanupを完了できていない
```

`Failed`と`CleanupCritical`を分けることが重要です。
Hostの作成に失敗したことと、課金resourceを削除できていないことはseverityが異なります。

## References and lessons

### Kubernetes controllers

KubernetesはControllerを、cluster stateを継続的に監視し、current stateをdesired stateへ近づけるcontrol loopとして説明しています。
このprojectでもevent handlerではなく継続責任を持つcontrollerとして扱います。

- <https://kubernetes.io/docs/concepts/architecture/controller/>
- <https://pkg.go.dev/sigs.k8s.io/controller-runtime/pkg/cache>

### Cluster API InfraMachine

Cluster APIの`InfraMachine`は、physicalまたはvirtualなprovider-specific machine instanceのlifecycleを管理します。
上位のMachineとprovider固有のinfrastructure lifecycleを分離する考え方は、HostClaim/HostとAkamai integrationの境界に近いです。

- <https://cluster-api.sigs.k8s.io/developer/providers/contracts/infra-machine>

### Crossplane managed resources

Crossplaneは、external resourceのcreate responseが失われるとresource leakが起こり得ることを明示し、external identityを保存して再観測します。
このfailure caseはPython prototypeでも確認済みであり、単純化しても無視できません。

- <https://docs.crossplane.io/latest/managed-resources/managed-resources/>

### Karpenter NodeClaim and NodeClass

Karpenterは、一つのcompute instance要求を`NodeClaim`として扱い、provider固有設定を`NodeClass`へ分離しています。
HostClaimへnetworkやimageを入れず、deployment provisioning policyへ置く考え方の参考になります。

- <https://karpenter.sh/docs/concepts/nodeclaims/>
- <https://karpenter.sh/docs/concepts/nodeclasses/>

### Kubernetes finalizers and Karpenter termination

Kubernetes finalizerは、外部resource cleanupが完了するまで管理resourceを削除しない仕組みです。
KarpenterもNodeClaim削除時にcloud instanceをterminateしてからfinalizerを外します。
Control PlaneのHost deletionも同じ原則を使用します。

- <https://kubernetes.io/docs/concepts/overview/working-with-objects/finalizers/>
- <https://karpenter.sh/docs/concepts/nodeclaims/>

## Resulting direction

Host managementの完成形について、次を方針とします。

- Hostは一つのGNU/Linux実行環境であり、一つのClaimが排他的に使用する
- Host packing schedulerは作らない
- Controllerは常時責任を持つlevel-triggered control loopとして扱う
- event、wake、timer、attemptは内部scheduler detailに限定する
- Akamai Cloudを正式な実providerとし、汎用provider plugin systemは作らない
- fake providerのために小さなprivate infrastructure boundaryを残す
- Claimはcapacityだけを指定し、networkやbootstrapはdeployment policyが所有する
- 外部操作の不確実性はbounded observationで解決し、無期限の回復workflowを作らない
- terminal failure後は、所有を確認できる課金resourceを優先的にcleanupする
- deletion不能、ownership ambiguity、将来のbackup failureはcritical alert対象とする

この方向を実コードへ反映する作業は、Linode integrationの実装計画を立てる段階で個別に行います。
