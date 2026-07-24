# ADR-0006: Data repositoryにpasswordless resticを使用する

- Status: Accepted
- Date: 2026-07-24

## Context

Python prototypeの実機検証から、restic repositoryをpasswordなしで利用し、object storageへのaccess credentialをsecurity boundaryとする構成が有用だと分かりました。
restic passwordを別途配布・保存することは、現在のthreat modelではsecurityよりsecret管理の複雑さを増やします。

## Decision

将来のData layerでは、restic repositoryをpasswordless modeで作成・利用します。
repositoryのconfidentialityとintegrityをrestic passwordに依存させません。

access controlは、object storage credential、resource isolation、最小権限、credential lifetime、auditで管理します。
repositoryへのread accessを持つ主体はdataを読める前提とします。

## Consequences

Hostへrestic passwordを配布する必要がなくなり、一時credentialを中心とした単純なaccess modelを構築できます。
一方、object storage credentialの漏洩はdata accessへ直結するため、発行scopeとlifetimeを厳密に管理する必要があります。
