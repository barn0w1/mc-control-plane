# ADR-0003: Hostをcontrollerとreconciliationで管理する

- Status: Accepted
- Date: 2026-07-24

## Decision

上位layerは必要なHostを永続的な需要として提示します。
Host controllerは、需要と観測状態を比較し、必要なHostの確保、割当、解放、再利用、削除へ継続的に収束させます。

上位layerからLinode作成・削除の命令列を直接送る設計にはしません。

## Reason

Host lifecycleとprovider固有のfailureをHost subsystemへ閉じ込め、上位layerを粗結合にできます。
Process restartや一時的な障害も、同じreconciliation modelで扱えます。
