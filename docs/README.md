# Documentation

このdirectoryは、現在の設計判断と直近の実装計画の正本です。
将来の実装詳細を早期に固定せず、実装を始める直前に必要な判断だけを追加します。

## Current documents

- [Project direction](project-direction.md)
- [Host control milestone](host-control-milestone.md)
- [Host management direction](host-management-direction.md)
- [Current implementation](current-implementation.md)
- [Implementation foundation](implementation-foundation.md)
- [HostClaim specification](host-claim-spec.md)
- [First implementation plan](first-implementation-plan.md)
- [Terminology](terminology.md)
- [Open questions](open-questions.md)
- [Architecture Decision Records](decisions/README.md)
- [Python prototypeから得た知見](history/python-prototype.md)

## Documentation rules

- codeとtestが文書と矛盾した場合、開発中はcodeとtestを事実として文書を更新する
- 実装していない構想を完成した仕様のように書かない
- 実装詳細は、その実装を開始する直前に決める
- 既存の標準、protocol、libraryで十分な場合は独自仕様を作らない
- stable releaseまでは後方互換性を設計上の制約にしない
