# Terminology

用語はcode、RPC、database、documentで同じ意味に使用します。

| Term | Meaning |
| --- | --- |
| `mccpd` | Control Plane本体のRust daemon |
| `mccp-hostd` | 管理対象Hostに常駐するRust daemon |
| `mccpctl` | Operator向けRPC CLI client |
| Resource | subsystemがidentityとlifecycleを所有する永続オブジェクト |
| Spec | Resourceに要求された状態 |
| Status | Controllerが観測した状態 |
| Condition | Resourceの独立した性質を表すtyped status |
| Claim | 上位layerが下位layerへ提示するresource要求 |
| Controller | Resourceを観測し、要求状態へ収束させるcomponent |
| Activity | 外部副作用の一回のdurableな実行記録 |
| Host | workloadを実行できる一台の論理実行環境 |
| HostClass | Hostの互換条件とprovider spec |
| HostClaim | 上位layerが要求する一台の排他的Host |
| HostAllocation | HostClaimとHostの現在の割当 |
| Host ID | Host subsystemが発行する内部identity |
| Provider resource ID | Linode APIが発行する外部identity |
| Incarnation | Host identityの一回の生成。古いmachineやcertificateをfenceする値 |
| Fencing token | 古いallocationや遅延commandを拒否する単調なidentity |
| Idle Host | allocationがなく、policyにより一時保持されるHost |
| Sanitization | Hostを安全に再利用可能な状態へ戻す処理 |
| Enrollment | 新しい`mccp-hostd`がHost identity certificateを取得する初回手続き |
| Observation | providerまたはHostから取得した時刻付き状態 |
| OutcomeUnknown | 外部副作用が発生したか断定できない状態 |

## Avoided terms

### Agent

単独では意味が広すぎるため、software名は`mccp-hostd`、実行環境はHostと呼びます。
文脈上必要な場合のみ「Host agent」を説明語として使います。

### Run

command実行、test run、server稼働期間を混同しやすいため、将来のserver稼働期間には`Session`を候補とします。

### Operation

粒度が不明確なため、永続resource workflowは`Job`、外部副作用は`Activity`として区別する方針です。
Host checkpointでは主にControllerとActivityを使用します。
