# Documentation

このdirectoryは、新しいRust実装について合意した方針と、現在取り組む範囲を共有するためのものです。

## Documents

- [Project direction](project-direction.md): 変えない基本方針と現在のscope
- [Host control milestone](host-control-milestone.md): 中期目標の定義
- [Next steps](next-steps.md): 直近の実装順序
- [Terminology and naming](terminology.md): 用語と命名の方針
- [Open questions](open-questions.md): まだ決めない事項
- [Architecture decisions](decisions/README.md): 確定した判断
- [Python prototype](history/python-prototype.md): 旧実装から引き継ぐ知見

## Documentation policy

- 文書は、実装する前に必要な判断を共有するために書く。
- 遠い将来の実装詳細を先に仕様化しない。
- まだ検証していない内容を確定事項として書かない。
- 一般的な用語、protocol、library、formatを優先し、独自概念を必要以上に増やさない。
- 判断を確定した場合だけADRへ記録する。
- 実装開始後、codeとtestが文書より新しい場合はcodeとtestを事実として文書を更新する。
- 開発中は後方互換性より、現在の最適な設計を優先する。
