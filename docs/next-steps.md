# Next steps

## Immediate scope

直近では、Host control milestone全体を一度に実装しません。
まず、controller modelが成立する最小の縦方向の流れを作ります。

## Step 1: Naming and minimal vocabulary

Rust workspaceを作る前に、次だけを決めます。

- projectまたはbinaryに使用する短いname
- Control Plane daemonのbinary名
- Host上のdaemonのbinary名
- operator CLIのbinary名
- Host需要を表すresource名

`mccp*`は確定名として扱いません。repository名を変更する必要もありません。

## Step 2: Rust workspace and process skeleton

最小のworkspaceを作り、次の三つを起動可能にします。

- Control Plane daemon
- Host daemon
- operator CLI

この段階ではHostやLinodeを操作しません。
configuration、logging、shutdownなども、最初のRPCを成立させるために必要な最小限だけ実装します。

## Step 3: Minimal RPC path

operator CLIからControl Plane daemonへJSON-RPC requestを送り、typed responseを受け取れるようにします。

最初のmethodはversionとprocess情報を返す程度で十分です。
目的は、CLIがdaemonの内部moduleやdatabaseへ直接依存しない境界を最初に固定することです。

Transportとschema toolingは、このstepを実装する直前に比較して決めます。

## Step 4: Host demand with a fake provider

永続化されたHost需要を作成し、controllerがfake provider上のHost状態へ収束させます。

ここで確認することは次です。

- desired stateとobserved stateを分けられること
- controllerを繰り返し実行しても結果が壊れないこと
- 複数要求を扱えること
- daemon再起動後に再開できること
- CLIから要求と状態を確認できること

Cloud APIやHost daemonより先に、resource ownershipとreconciliation modelを検証します。

## Following work

上記が成立した後、次を一段ずつ追加します。

1. Linode provider integration
2. Host daemonのbootstrap、identity、通信
3. Host allocationとrelease
4. idle保持、再利用、safe termination
5. Host control milestoneのlive acceptance

各段階の詳細仕様は、その段階を開始する前に作成します。
