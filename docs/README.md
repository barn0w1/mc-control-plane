# Documentation

このdirectoryは、新しいRust実装に対する設計上の正本です。
コードと文書が食い違う場合は、実装開始後はcodeとacceptance testを優先し、差異を文書へ戻します。

## Specification

- [Architecture](specification/architecture.md)
- [Resource model](specification/resource-model.md)
- [Controller and claim model](specification/controller-model.md)
- [Host management](specification/host-management.md)
- [RPC protocol](specification/rpc.md)
- [Identity and PKI](specification/identity-and-pki.md)
- [Persistence and consistency](specification/persistence.md)
- [Failure model](specification/failure-model.md)
- [Security model](specification/security.md)
- [Terminology](specification/terminology.md)

## Plans

- [Roadmap](plans/roadmap.md)
- [Host control checkpoint](plans/checkpoint-host-control.md)
- [Implementation sequence](plans/implementation-sequence.md)
- [Validation strategy](plans/validation-strategy.md)
- [Open questions](plans/open-questions.md)

## Decisions

- [ADR index](decisions/README.md)

ADRは採用した判断と、その時点での理由を記録します。検討中の事項は`Proposed`とし、
実装前に必ず確定させる必要はありません。判断を変更するときは過去のADRを書き換えて歴史を消すのではなく、
新しいADRで`Superseded`にします。

## History

- [Python prototypeから得た知見](history/python-prototype.md)
