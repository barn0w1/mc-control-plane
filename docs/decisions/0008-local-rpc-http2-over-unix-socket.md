# ADR-0008: Local operator RPCにHTTP/2 over Unix domain socketを使用する

- Status: Accepted
- Date: 2026-07-24

## Context

Operator CLIとControl Plane daemonのlocal通信には、filesystem permissionを利用できるUnix domain socketが適しています。
独自message framingは作りたくありません。HTTP/1.1、HTTP/2、HTTP/3を比較すると、HTTP/3はQUICとTLSを前提としlocal IPCには不要です。
HTTP/2は標準化されたbinary framingとstream lifecycleを持ち、Hyperとjsonrpseeが対応しています。

## Decision

`control`と`control-plane`のlocal RPCは、JSON-RPC 2.0をHTTP/2 request/responseとしてUnix domain socket上で運びます。
HTTP/2はprior knowledgeで開始し、HTTP/1.1 fallback、WebSocket、JSON-RPC batch、notificationは最初の実装で提供しません。

local transportではTLSを使用せず、socket filesystem permissionをaccess boundaryとします。
Host Agentやremote interfaceのtransportはこのADRでは決めません。

## Consequences

独自framingを作らず、Tokio/Hyper/Tower/jsonrpsee stackを利用できます。
一方、jsonrpseeの通常HTTP clientはUnix socketを直接扱わないため、transport adapterを明確なmoduleとして実装する必要があります。
