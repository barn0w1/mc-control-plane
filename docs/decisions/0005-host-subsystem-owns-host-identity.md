# ADR-0005: Host subsystemがHost identityを所有する

- Status: Accepted
- Date: 2026-07-23

## Context

Hostは単なるLinode API responseではない。Provider resource、Host daemon identity、allocation、health、
idle retentionを一つのlifecycleとして管理する必要がある。

Linode IDをdomain identityにすると、resource replacement、stale `mccp-hostd`、certificate reuse、provider detailの漏洩を防ぎにくい。

## Decision

Host subsystemが独自のHost IDとincarnationを発行する。

- Host IDとLinode external IDを分離する。
- 一台のHostは一つのactive Claimへ排他的にallocationする。
- Host subsystemだけがallocation、idle、reuse、terminationを決める。
- Provider subsystemだけがLinode resourceを操作する。
- `mccp-hostd` certificateをHost IDとincarnationへbindingする。

## Consequences

### Positive

- Provider resource replacementとHost identity lifecycleを明確に区別できる。
- stale machine、certificate、commandをfenceできる。
- 上位layerからLinode detailを隠せる。

### Negative

- HostとProviderResourceを対応させる永続modelが必要。
- identity、incarnation、allocation generationの整合性検証が増える。
