"""Settings from the environment (12-factor). Secrets come from mounted Kubernetes Secrets."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    workspace_namespace: str = field(default_factory=lambda: _env("NOVA_WS_NAMESPACE", "nova-ws"))
    agent_server_image: str = field(default_factory=lambda: _env("NOVA_AGENT_SERVER_IMAGE"))
    agent_server_port: int = field(default_factory=lambda: int(_env("NOVA_AGENT_SERVER_PORT", "8000")))
    storage_class: str = field(default_factory=lambda: _env("NOVA_WS_STORAGE_CLASS", "ceph-block"))
    workspace_storage: str = field(default_factory=lambda: _env("NOVA_WS_STORAGE", "20Gi"))
    workspace_cpu: str = field(default_factory=lambda: _env("NOVA_WS_CPU", "2"))
    workspace_memory: str = field(default_factory=lambda: _env("NOVA_WS_MEMORY", "4Gi"))
    idle_minutes: int = field(default_factory=lambda: int(_env("NOVA_WS_IDLE_MINUTES", "30")))
    start_timeout_s: int = field(default_factory=lambda: int(_env("NOVA_WS_START_TIMEOUT", "180")))
    litellm_url: str = field(default_factory=lambda: _env("NOVA_LITELLM_URL", "http://litellm-internal.vllm.svc.cluster.local:4000"))
    litellm_master_key: str = field(default_factory=lambda: _env("NOVA_LITELLM_MASTER_KEY"))
    default_model: str = field(default_factory=lambda: _env("NOVA_DEFAULT_MODEL", "qwen3"))
    allowed_models: tuple = field(default_factory=lambda: tuple(m for m in _env("NOVA_ALLOWED_MODELS", "qwen3").split(",") if m))
    monthly_budget_usd: float = field(default_factory=lambda: float(_env("NOVA_MONTHLY_BUDGET_USD", "0") or 0))
    web_root: str = field(default_factory=lambda: _env("NOVA_WEB_ROOT", "/srv/nova-web"))
    email_domain: str = field(default_factory=lambda: _env("NOVA_EMAIL_DOMAIN", "isigpu.local"))
    trust_identity_headers: bool = field(default_factory=lambda: _env("NOVA_TRUST_IDENTITY_HEADERS", "true") == "true")


settings = Settings()
