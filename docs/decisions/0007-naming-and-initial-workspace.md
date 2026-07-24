# ADR-0007: 名称と最初のCargo workspace境界を定める

- Status: Accepted
- Date: 2026-07-24

## Context

`mccpctl`のような名称は入力しにくく、Minecraftをsystem全体の名称へ固定する必要もありません。
同時に、最初から多数の`core`、`domain`、`storage` crateへ分割すると、まだ存在しない境界を固定してしまいます。

## Decision

名称を次のように定めます。

| Role | Name |
| --- | --- |
| System | Control Plane |
| Central daemon | `control-plane` |
| Host-resident daemon | `host-agent` |
| Operator CLI | `control` |
| Persistent Host demand | `HostClaim` |

repository名`mc-control-plane`は変更しません。

最初のCargo workspaceは次の四packageだけで開始します。

```text
control-plane-protocol
control-plane
host-agent
control-cli
```

`control-plane-protocol`はRPC wire contractだけを共有します。
Control Plane内部のresource model、controller、storage、providerは`control-plane` package内のmoduleから開始します。
独立したdeployment、dependency、reuse boundaryが確認された場合だけcrateを追加します。

## Consequences

binaryの役割とCLI入力が分かりやすくなり、Host daemonのsecurity boundaryを独立packageとして保てます。
一方、名称が一般的で外部softwareと衝突する可能性はあります。Stable release前であるため、実際の問題になれば変更します。
