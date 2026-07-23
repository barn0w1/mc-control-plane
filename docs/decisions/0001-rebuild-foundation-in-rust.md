# ADR-0001: Rustでfoundationを新しく構築する

- Status: Accepted
- Date: 2026-07-23

## Context

Python prototypeにより、Linode lifecycle、outbound Host control、durable orchestration、restic data lifecycle、
Minecraft lifecycleを実環境で検証できた。一方で、CLI責務、動的protocol schema、process分割、概念の混在により、
基盤としての安全性と一貫性に不安がある。

Projectは開発中であり、既存API、database、protocol、deploymentとの互換性を維持する必要がない。

## Decision

Control Plane foundationをRustで新しく実装する。

- `mccpd`、`mccp-hostd`、`mccpctl`をRustで実装する。
- Python codeを逐語的に移植しない。
- 旧実装はGit tagとhistoryへ保存する。
- 旧実装からはfailure case、invariant、acceptance resultを引き継ぐ。
- 新しいresource model、RPC、identity、persistenceを先に定義する。

## Consequences

### Positive

- closed enum、ownership、error型、exhaustive matchingでprotocolとstate transitionを表現できる。
- privileged Host componentでmemory safetyと予測可能なerror handlingを得られる。
- 互換層に制約されず、概念を整理できる。

### Negative

- Python prototypeの機能を一時的に失う。
- Rustの開発速度とecosystem選定に学習・実装costがある。
- 実装完了まで利用可能なsystemが存在しない期間が生じる。

## Alternatives

### Python prototypeを段階的にrefactorする

旧境界とdatabaseを維持する圧力が強く、根本的なmodel変更が難しいため採用しない。

### PythonとRustを長期間併用する

二つのprotocol modelと運用経路を維持するcostが高いため、移行互換目的では採用しない。
