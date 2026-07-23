#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
	echo "run this installer with sudo" >&2
	exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/../.." && pwd)"
cli="${repo_dir}/.venv/bin/mc-control-plane"

if ! id opc >/dev/null 2>&1; then
	echo "the internal deployment expects the existing opc service account" >&2
	exit 1
fi
if [[ ! -x "${cli}" ]]; then
	echo "missing ${cli}; run 'uv sync --frozen' in the repository first" >&2
	exit 1
fi
agent_version="$(
	"${repo_dir}/.venv/bin/python" -c \
		'from mc_control_plane.application.host_protocol import HOST_AGENT_VERSION; print(HOST_AGENT_VERSION)'
)"
wheel="${repo_dir}/dist/host-agent/mccp_host_agent-${agent_version}-py3-none-any.whl"
if [[ ! -f "${wheel}" ]]; then
	echo "missing ${wheel}; build the Host agent wheel first" >&2
	exit 1
fi

install -d -o opc -g opc -m 0700 /var/lib/mc-control-plane
install -d -o root -g opc -m 0750 /etc/mc-control-plane
install -d -o root -g root -m 0755 /opt/mc-control-plane/artifacts
install -o root -g root -m 0644 "${wheel}" /opt/mc-control-plane/artifacts/host-agent.whl
ln -sfn "${cli}" /usr/local/bin/mc-control-plane

for unit in mc-control-plane-host-api.service mc-control-plane-reconciler.service mc-control-plane.target; do
	install -o root -g root -m 0644 "${script_dir}/${unit}" "/etc/systemd/system/${unit}"
done

if [[ ! -e /etc/mc-control-plane/config.toml ]]; then
	install -o root -g opc -m 0640 \
		"${script_dir}/config.example.toml" /etc/mc-control-plane/config.toml
fi
if [[ ! -e /etc/mc-control-plane/secrets.env ]]; then
	install -o root -g opc -m 0640 \
		"${script_dir}/secrets.env.example" /etc/mc-control-plane/secrets.env
fi
if [[ ! -e /etc/mc-control-plane/host-bootstrap.key ]]; then
	"${cli}" host-bootstrap-key-create /etc/mc-control-plane/host-bootstrap.key
	chown opc:opc /etc/mc-control-plane/host-bootstrap.key
	chmod 0600 /etc/mc-control-plane/host-bootstrap.key
fi

systemctl daemon-reload
echo "installed files without starting services"
echo "edit /etc/mc-control-plane/config.toml and secrets.env, install key files, then run:"
echo "  sudo -u opc env LINODE_TOKEN='<token>' /usr/local/bin/mc-control-plane node-check"
echo "  sudo systemctl enable --now mc-control-plane.target"
