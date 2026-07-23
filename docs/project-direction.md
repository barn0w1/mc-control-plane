# Project direction

## Purpose

複数のresource layerを、それぞれ明確なownerを持つcontrollerとして管理し、依存関係を通じて連携させる
Control Planeを構築します。

最初に扱うresource layerはHostです。Data、workload、Minecraft serverなどは、Host layerの上に後から追加します。

## Foundation

### Rust core

Control Plane daemonとHost上の常駐daemonはRustで実装します。
安全性、明確な型、予測可能なfailure handlingを基盤側で優先します。

### One Control Plane daemon

Control Planeのstate、controller、provider integration、RPC serverは一つのdaemonが所有します。
論理的なmoduleは分離しますが、必要性が確認される前に複数serviceへ分割しません。

### RPC-only interfaces

CLIや将来のinterfaceはControl PlaneのRPC clientです。
interfaceはdatabase、Linode API、controller内部へ直接アクセスしません。

### Controller and reconciliation

上位layerは、下位layerへ手続き的な命令列を送るのではなく、必要なresourceまたはcapacityを永続的な要求として提示します。
controllerは要求された状態と観測した状態を比較し、繰り返し実行可能なreconciliationによって収束させます。

この考え方はKubernetes controllerなどで一般的に使われるcontrol-loop modelを参考にしますが、
Kubernetes互換のAPIや完全な模倣を目的にはしません。

### Standards before custom protocols

既存の標準、広く利用される用語、成熟したlibraryで十分な場合は、それを優先します。
独自仕様は、このproject固有の要件を標準だけでは表現できない場合に限定します。

RPC envelopeにはJSON-RPC 2.0を採用します。Transport、authentication、schema生成などは、
必要になる段階で個別に決めます。

### No development compatibility

最初のstable releaseまでは、RPC、database、configuration、binary名、resource名を含め、
開発中のversion間の後方互換性を保証しません。
不要になった設計や実装は置き換え、compatibility layerを追加しません。

## Later data layer principle

Data layerは現在の実装scopeではありません。ただし、Python prototypeで有用性を確認した次の方針は継続します。

- resticをsnapshot repository formatとして使用する。
- restic repositoryは`--insecure-no-password`を使用し、password protectionに依存しない。
- repositoryへのaccess controlとconfidentiality boundaryは、object storage側のcredentialとresource isolationで管理する。

具体的なcredential発行、repository配置、retention、restore検証はData layerを実装する前に設計します。

## References

- [Kubernetes: Controllers](https://kubernetes.io/docs/concepts/architecture/controller/)
- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
