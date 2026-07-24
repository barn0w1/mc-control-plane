# Project direction

## Purpose

Control Planeは、上位layerから提示されたresource需要を保存し、controllerによって実際のresourceを要求状態へ収束させます。
最初の対象はHostです。Minecraft、workload、data managementはHost layerが成立した後に追加します。

## Foundation

```text
control
  JSON-RPC client
      |
      v
control-plane
  RPC boundary
  persistent state
  controllers
  provider integration
      ^
      |
host-agent
  future Host observation and restricted execution
```

`control-plane`は一つのRust daemonとして動作します。内部の責務はmoduleと明確なownership boundaryに分けますが、
初期段階でnetwork serviceへ細分化しません。

すべてのinterfaceはRPC clientです。CLI、将来のBotやWeb interface、Host daemonはdatabaseへ直接接続せず、
provider APIやcontroller内部実装も直接呼びません。

## Control model

上位componentは「Linodeを作成する」と命令しません。必要なHostを`HostClaim`として提示します。
Host controllerは、保存されたClaimと観測したHostを比較し、必要なHost数へ継続的に収束します。

このmodelでは、process再起動や同じreconciliationの再実行を通常動作として扱います。
外部操作の結果が不明な場合は、無条件に再実行せず、外部状態を再観測してから判断します。

## Scope discipline

現在確定しているのは、Rust、単一daemon、RPC client、JSON-RPC 2.0、controller model、HostClaim、SQLiteです。

次の詳細は必要になるまで固定しません。

- Host transportとmTLSの具体的profile
- certificate lifecycle
- Linode client library
- Host idle retentionの計算式
- command deliveryとjournal schema
- 完全なdatabase schema
- 将来のData、Workload、Server layerのresource model

## Data direction

将来のData layerでも、Python prototypeで有効性を確認したpasswordless restic repositoryを継続します。
restic passwordをsecurity boundaryとせず、object storage credentialとresource isolationでaccessを管理します。
