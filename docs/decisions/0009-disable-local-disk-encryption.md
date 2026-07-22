# ADR-0009: 一時Linodeのlocal disk encryptionを無効にする

- Status: Accepted
- Date: 2026-07-22

## Context

Execution Hostのroot diskはRun中の作業領域であり、長期的な復旧点はR2上のrestic snapshotである。
Minecraft worldのsaveやrestoreはdisk I/Oを使い、Akamaiの公式資料はlocal disk
encryptionがCPU overheadを増やし、実効throughputを下げる可能性があるとしている。

一方、local disk encryptionは物理driveが取り外し、廃棄された場合のdata at restを保護する。
実行中のVM侵害、過剰な権限、logへのsecret出力を防ぐ機能ではない。

## Decision

- Control Planeが作るLinodeには`disk_encryption="disabled"`を明示する。provider既定値へ依存しない。
- 作成後のprovider observationでも`disk_encryption=disabled`を確認する。
- disk encryptionを無効化できないdistributed regionは、現在のExecution Host対象外とする。
- API token、enrollment token、R2 temporary credentialは短命かつ必要時だけ渡し、root diskへ長期保存
  しない。このsecret管理要件をdisk encryptionの代替として緩和しない。

## Consequences

### Positive

- encryption由来のCPU overheadとthroughput低下を避けられる。
- ephemeral root diskというdata lifecycleと、性能を優先する小規模Minecraft用途に一致する。
- create requestと観測値が一致し、accountやproviderの既定値変更に影響されない。

### Negative

- providerの物理drive取り外し、廃棄時にroot disk内容を暗号化で保護できない。
- sensitive dataを恒常的に扱う用途へ、そのまま転用できない。
- distributed regionはdisk encryptionを無効化できないため選択できない。

## Reconsider when

- root diskへ機密情報や長期credentialを保存する要件が生じる。
- 実測でencryptionの性能差が無視でき、data-at-rest保護を優先するようになる。
- Akamaiがdisk encryptionの実装または性能特性を変更する。
- distributed regionを使用する必要が生じる。

## References

- [Akamai: Disk encryption](https://techdocs.akamai.com/cloud-computing/docs/local-disk-encryption)
- [Akamai API: Create a Linode](https://techdocs.akamai.com/linode-api/reference/post-linode-instance)
