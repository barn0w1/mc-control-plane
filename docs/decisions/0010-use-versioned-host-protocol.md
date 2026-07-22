# ADR-0010: versioned closed Host protocolとat-least-once配送を使用する

- Status: Accepted
- Date: 2026-07-22

## Context

Host agentはroot権限でsystemd、Quadlet、将来のsnapshotを操作する。Control Planeやnetworkが再起動しても
commandを失わず、同じcommandの再送で二重実行による状態分裂を起こさない必要がある。一方、汎用RPC、
任意shell、任意unit本文はHost agentをremote shellへ変え、入力検証と復旧を困難にする。

## Decision

Host protocol v1を固定JSON schemaとして実装し、互換versionが一致するagentだけを`connected`とする。
この状態は認証済みpollを表し、Hostの`ready`はcapabilityとservice observationから別に判定する。

- endpointは`POST /v1/host/enroll`と`POST /v1/host/poll`だけとする。
- commandは列挙済みの高水準actionと空のversioned payloadだけを許可する。
- commandはControl Plane DBを正本としてat-least-once配送する。
- agentは実行前にlocal SQLite journalへcommand IDとcanonical request digestを保存する。
- terminal resultをControl Planeが受領するまで再送し、同じcommand IDと異なる内容は拒否する。
- commandにはRun、Operation、step、payload version、deadlineを持たせる。
- heartbeatはboot ID、agent/protocol version、OS/package capability、systemd service
  stateを持つ。
- HTTP redirectでcredentialを別originへ送らず、非loopback Host APIはTLSなしで起動しない。

enrollment応答を失った場合にだけ、同じenrollment token、agent ID、agent credentialの組み合わせを
冪等に受け付ける。別agent IDまたは別credentialによる二回目の使用は拒否する。この例外により、
tokenを消費した直後のresponse lossでSSH復旧が必要になることを避ける。

## Consequences

- process再起動とnetwork再送を通常フローとしてtestできる。
- protocol surfaceが小さく、Hostで実行可能な操作をcode reviewできる。
- local journalとControl Plane command recordという二つの記録を保守する必要がある。
- schema追加時はprotocol versionまたはpayload versionの互換規則を明示する必要がある。
- protocol v1の固定語彙にはGate 2 fixture、Gate 4 data、Gate 5 Minecraft actionをmigrationで追加する。
  任意shellや任意Quadlet本文は引き続き受け付けない。
