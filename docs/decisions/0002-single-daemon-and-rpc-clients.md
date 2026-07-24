# ADR-0002: 単一Control Plane daemonとRPC clientを使用する

- Status: Accepted
- Date: 2026-07-24

## Context

旧実装ではHost API、reconciler、CLIが同じstateへ異なる経路で接続し、ownershipとconfigurationが複雑になりました。
初期systemに独立deploymentや複数writerは必要ありません。

## Decision

`control-plane`を一つのRust daemonとして動かし、SQLite stateの唯一のapplication ownerにします。
`control`、`host-agent`、将来のinterfaceはすべてRPC clientとし、databaseやproviderへ直接接続しません。

内部責務はmoduleで分離します。必要性が実証されるまでnetwork serviceへ分割しません。

## Consequences

state mutation、configuration、logging、lifecycleが一つにまとまります。
一方、daemon停止中は通常操作できず、一つのprocess failureがすべてのcontrollerへ影響します。
