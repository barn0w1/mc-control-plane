# Documentation

このdirectoryは、project全体の方針、checkpoint、Architecture Decision Record、履歴の入口です。

## Read first

1. [Project direction](project-direction.md)
2. [Checkpoints](checkpoints/README.md)
3. [Current checkpoint: Host Control System v1](checkpoints/host-control-v1/README.md)
4. [Terminology](terminology.md)
5. [Architecture Decision Records](decisions/README.md)

## Directory structure

```text
docs/
  README.md
  project-direction.md
  terminology.md
  checkpoints/
    README.md
    host-control-v1/
      README.md
      architecture.md
      implementation.md
      open-questions.md
  decisions/
  history/
```

`docs/`直下には、複数checkpointを通して参照する文書だけを置きます。
特定checkpointの目標、設計、実装状況、未決事項は、そのcheckpoint directoryへまとめます。

## Other references

- [Python prototypeから得た知見](history/python-prototype.md)

## Documentation rules

- codeとtestが文書と矛盾した場合、開発中はcodeとtestを事実として文書を更新する
- 実装していない構想を完成した仕様のように書かない
- checkpoint固有の文書を`docs/`直下へ増やさない
- 実装詳細は、その実装を開始する直前に決める
- 既存の標準、protocol、libraryで十分な場合は独自仕様を作らない
- stable releaseまでは後方互換性を設計上の制約にしない
