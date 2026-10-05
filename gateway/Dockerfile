# NovaAgent customer-workspace gateway: NovaAgent's scripts/customer-workspace* + local-tools-sidecar
# protocol modules (copied in by build.sh from the NovaAgent source) + the registry sync wrapper.
FROM 172.16.50.89:5000/library/node:24-slim
WORKDIR /app
COPY scripts/ ./scripts/
COPY nova-gateway.mjs ./
ENV NOVAAGENT_CUSTOMER_WORKSPACE_HOST=0.0.0.0 NOVAAGENT_CUSTOMER_WORKSPACE_PORT=18766 NODE_ENV=production
USER 1000
EXPOSE 18766
CMD ["node", "nova-gateway.mjs"]
