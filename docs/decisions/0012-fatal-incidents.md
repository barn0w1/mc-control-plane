# ADR-0012: 予測不能な外部failureをFatal Incidentとして記録する

- Status: Accepted
- Date: 2026-07-24
- Supersedes: ADR-0011

## Context

Akamai API error、external mutationの結果不明、ownership mismatch、delete failureなどへControllerごとのretry、repair、replacement、forced cleanupを追加すると、state machineが複雑になり予測不能な動作と誤削除riskが増えます。

一方、actual Rust `panic!`でdaemon全体を終了すると、他resourceの管理、operator RPC、errorの永続化を継続できません。
Rustのerror handling modelでも、external runtime failureは`Result`で表し、`panic!`はprogram bugや内部不変条件違反に使用するのが自然です。

Cost enforcementはControl Planeの正しさとは別の関心事です。

## Decision

すべてのControllerで共通するFatal Incident modelを導入します。

- expected rejectionやnormal absenceはtyped domain resultとして扱う
- 予測不能な外部failureはtyped errorとして受け取る
- common Incident storeへFatal Incidentを永続化する
- affected resourceまたはsubsystemを`Critical`にする
- affected scopeへの自動mutationを停止する
- operatorが明示的にresolveするまで自動再開しない
- Controller固有の無期限retry、replacement、forced deletionを実装しない
- actual `panic!`はprogram bugまたは内部不変条件違反に限定する

Claim解放やIdle retention終了によるnormal deletionはHost Controllerの責務として維持します。
Fatal Incident後のforced deletion、stale billing resource cleanup、account-wide cost monitoringはControl Planeのscope外とします。
必要なら別repository・別processの独立programがAkamai Cloudを監視します。

## Consequences

failure behaviorがController間で一貫し、状態機械と自動mutationを小さく保てます。
Control Planeは一つのresource failure後も他resourceとoperator RPCを継続できます。
Discord Botなどは将来JSON-RPCからIncidentを監視できます。

一方、Fatal Incidentは人間の対応が必要です。
Control Planeはcost leakを自動解消せず、外部cost management programも完成条件や安全性前提に含めません。
