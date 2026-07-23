# Host control milestone

## Goal

中期目標は、Control PlaneがHostの需要から実resourceの終了までを継続的に管理できることです。

Hostは、上位処理の手順の一部として直接作成するものではありません。
上位layerが必要な実行環境を要求し、Host controllerがその要求を満たします。

## Conceptual flow

```text
Host demand appears
        ↓
find a compatible available Host
        ↓ not found
provision a Host through the provider
        ↓
Host daemon joins and reports readiness
        ↓
allocate the Host to the demand
        ↓
demand is released
        ↓
return the Host to a reusable state
        ↓
reuse it or terminate it according to policy
```

要求が複数存在する場合は、controllerが必要数のHostを確保します。
要求が消えたことは、即時削除命令を意味しません。Host subsystemが再利用可能性、idle policy、providerの課金条件などを考慮して管理します。

## Ownership boundaries

- Host subsystemだけがHostの内部identityとlifecycleを所有する。
- Provider integrationだけがLinode resourceの作成、観測、削除を行う。
- 上位layerはLinode IDやprovider APIを扱わない。
- Host上の常駐daemonだけが、そのHostのOSやlocal runtimeを観測・操作する。
- CLIはRPCを通じて要求と状態を扱うだけである。

Hostの内部identityとLinode resource IDは別のものとして扱います。

## Completion criteria

このmilestoneは、少なくとも次を満たしたときに完了とします。

- operatorがHost要求を作成・削除できる。
- controllerが要求数へ自動的に収束する。
- 複数要求から複数Hostを重複なく確保できる。
- Control Plane再起動後もstateを失わずreconciliationを再開できる。
- provider APIのtimeoutや結果不明を安全に再観測できる。
- 所有していないprovider resourceを削除しない。
- Host上のdaemonがControl Planeへ接続し、identityと状態を報告できる。
- Hostの解放、idle保持、再利用、削除をHost subsystemが管理できる。
- 通常操作にSSHやCloud Manager上の手作業を必要としない。
- operatorが、要求、割当、Host、provider、Host daemonの状態を区別して確認できる。

## Not decided here

この文書では、次の詳細を固定しません。

- resourceとdatabase tableの完全なschema
- RPC method一覧とpayload
- mTLSとCAの具体的な実装
- Host daemonへのcommand delivery方式
- idle retentionの計算方法
- Linode API client library
- Rust crateの細かな分割

これらは、該当部分を実装する直前に、必要なtestとfailure caseを含めて決めます。
