# mc-control-plane

Rustで新しいControl Plane基盤を設計・実装するprojectです。
repository名は当面そのまま使用しますが、project名、binary名、crate名はまだ確定していません。

既存のPython実装を移植するprojectではありません。Python実装と実環境検証から得られた知見は利用しますが、
開発中のAPI、protocol、database、configuration、名称との後方互換性は保証しません。

## Current focus

現在の中期目標は、**Host layerをControl Planeから完全に管理できること**です。

- 上位layerが必要なHostを宣言できる
- controllerが要求と実状態を継続的にreconcileする
- 必要数のHostをAkamai Cloud / Linode上に確保する
- Host上の常駐daemonとControl Planeが安全に通信する
- 解放されたHostを再利用可能な状態へ戻す
- policyに従ってidle保持または削除する
- process restartや一時的な外部障害があっても収束を再開する
- operatorはRPC clientだけを使用し、databaseやproviderを直接操作しない

Minecraft workload、backup、snapshotなどの上位layerは、この基盤が成立した後に個別に設計します。

## Documentation

設計と計画の入口は[docs/README.md](docs/README.md)です。

文書は、実装より大幅に先行して詳細を固定しない方針です。次に実装する範囲に必要な内容だけを決め、
それ以外は未確定事項として残します。

## Previous implementation

Python prototypeはGit履歴と`python-prototype-reference-2026-07-23` tagから参照できます。
現在のworking treeには残しません。
