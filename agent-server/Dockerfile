# NovaAgent agent-server (one pod per customer): stock openhands-agent-server from PyPI (the version the
# NovaAgent UI pins, config/defaults.json) + NovaAgent's sidecar_bridge (agent tools run on the
# customer's own PC through the reverse workspace gateway) + nova_server_patches.
FROM 172.16.50.89:5000/python:3.12-slim
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG AGENT_SERVER_VERSION=1.38.0
RUN apt-get update && apt-get install -y --no-install-recommends git tmux curl ca-certificates procps \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} ${PIP_TRUSTED_HOST:+--trusted-host "$PIP_TRUSTED_HOST"} \
      "openhands-agent-server==${AGENT_SERVER_VERSION}" "openhands-sdk==${AGENT_SERVER_VERSION}" \
      "openhands-tools==${AGENT_SERVER_VERSION}" "agent-client-protocol<0.11" "fastmcp>=3,<4"
COPY sidecar_bridge /opt/nova/tools/sidecar_bridge
COPY nova_server_patches /opt/nova/tools/nova_server_patches
# NovaAgent's agent identity (NovaAgent scripts/deploy/SOUL.md, staged by k8s-deploy script 140)
COPY SOUL.md /opt/nova/SOUL.md
RUN useradd -m -u 10001 -s /bin/bash openhands
ENV PYTHONPATH=/opt/nova/tools PYTHONUNBUFFERED=1 LITELLM_LOCAL_MODEL_COST_MAP=True
USER 10001
WORKDIR /home/openhands
EXPOSE 8000
ENTRYPOINT ["agent-server", "--host", "0.0.0.0", "--port", "8000", "--import-modules", "sidecar_bridge,nova_server_patches"]
