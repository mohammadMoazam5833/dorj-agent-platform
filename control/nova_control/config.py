"""Settings from the environment (12-factor). Secrets come from mounted Kubernetes Secrets."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    workspace_namespace: str = field(default_factory=lambda: _env("NOVA_WS_NAMESPACE", "nova-ws"))
    control_namespace: str = field(default_factory=lambda: _env("NOVA_NAMESPACE", "nova"))
    public_url: str = field(default_factory=lambda: _env("NOVA_PUBLIC_URL", "https://nova.isigpu.local"))
    gateway_url: str = field(default_factory=lambda: _env("NOVA_GATEWAY_URL", "http://nova-gateway.nova.svc.cluster.local:18766"))
    registry_token: str = field(default_factory=lambda: _env("NOVA_REGISTRY_TOKEN"))
    keycloak_url: str = field(default_factory=lambda: _env("NOVA_KEYCLOAK_URL", "http://keycloak.auth.svc.cluster.local"))
    keycloak_realm: str = field(default_factory=lambda: _env("NOVA_REALM", "nova"))
    desktop_client_id: str = field(default_factory=lambda: _env("NOVA_DESKTOP_CLIENT_ID", "nova-desktop"))
    desktop_client_secret: str = field(default_factory=lambda: _env("NOVA_DESKTOP_CLIENT_SECRET"))
    admin_group: str = field(default_factory=lambda: _env("NOVA_ADMIN_GROUP", "nova-admins"))
    reasoning_effort_map: str = field(default_factory=lambda: _env("NOVA_REASONING_EFFORT_MAP"))
    vision_models: str = field(default_factory=lambda: _env("NOVA_VISION_MODELS", "qwen3,glm"))
    image_models: str = field(default_factory=lambda: _env("NOVA_IMAGE_MODELS", "qwen3"))
    image_text_model: str = field(default_factory=lambda: _env("NOVA_IMAGE_TEXT_MODEL", "qwen3"))
    ocr_concurrency: int = field(default_factory=lambda: int(_env("NOVA_OCR_CONCURRENCY", "4")))
    ocr_timeout_seconds: float = field(default_factory=lambda: float(_env("NOVA_OCR_TIMEOUT_SECONDS", "60")))
    admin_secret: str = field(default_factory=lambda: _env("NOVA_ADMIN_SECRET"))
    email_domain: str = field(default_factory=lambda: _env("NOVA_EMAIL_DOMAIN", "isigpu.local"))
    prometheus_url: str = field(default_factory=lambda: _env("NOVA_PROMETHEUS_URL", "http://kps-kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"))
    admin_page: str = field(default_factory=lambda: _env("NOVA_ADMIN_PAGE", "/srv/nova-admin/nova-admin.mjs"))
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


settings = Settings()
