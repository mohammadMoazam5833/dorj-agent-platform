"""Prometheus metrics (RED for the API/proxy + workspace lifecycle). Served on a separate port."""
from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("nova_http_requests_total", "API/proxy requests", ["route", "method", "code"])
HTTP_LATENCY = Histogram("nova_http_request_seconds", "API/proxy request latency", ["route"],
                         buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
PROXY_LATENCY = Histogram("nova_proxy_request_seconds", "Time spent proxying to a workspace (excl. cold start)",
                          buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
WS_START = Histogram("nova_workspace_start_seconds", "Workspace create/scale-up until Ready",
                     ["kind"], buckets=(1, 2.5, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180))
WS_EVENTS = Counter("nova_workspace_events_total", "Workspace lifecycle events", ["event"])
WS_ACTIVE = Gauge("nova_workspaces", "Workspaces by state", ["state"])
WS_SOCKETS = Gauge("nova_websocket_connections", "Open proxied WebSocket connections")
LLM_KEY_EVENTS = Counter("nova_llm_key_events_total", "LiteLLM virtual key operations", ["event"])
TUNNELS = Gauge("nova_workspace_tunnels", "Connected customer PCs (reverse workspace tunnels)")
LOGINS = Counter("nova_logins_total", "Desktop sign-ins", ["result"])
