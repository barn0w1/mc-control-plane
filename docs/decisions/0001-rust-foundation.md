# ADR-0001: Rustでfoundationを新しく構築する

- Status: Accepted
- Date: 2026-07-24

## Context

Python prototypeはcloud resource、Host control、restic、Minecraft lifecycleの検証に有用でした。
一方、動的schema、大きなCLI責務、複数processによるstate ownershipなどをそのまま基盤にしたくありません。
既存systemは運用されておらず、互換性を維持する必要もありません。

## Decision

Control Plane daemon、Host daemon、operator CLIをRustで新しく実装します。
Python codeを逐語的に移植せず、failure case、不変条件、実機検証結果を参考資料として利用します。

## Consequences

Rustの型、ownership、enum、exhaustive matchingをresource stateとprotocol boundaryへ利用できます。
一方、旧prototypeの機能を一時的に失い、新しい基盤を小さなvertical sliceから再構築する必要があります。
