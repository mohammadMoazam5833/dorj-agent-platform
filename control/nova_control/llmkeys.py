"""LLM key broker: one LiteLLM virtual key per user, created with the gateway's admin API.
The key lives only in the user's workspace Secret; budgets/rate limits/models are enforced by
LiteLLM, and every call is attributable to the user (user_id + metadata)."""
import httpx

from .config import settings
from .metrics import LLM_KEY_EVENTS


class LLMKeyError(RuntimeError):
    pass


def _headers() -> dict:
    if not settings.litellm_master_key:
        raise LLMKeyError("NOVA_LITELLM_MASTER_KEY is not configured")
    return {"Authorization": f"Bearer {settings.litellm_master_key}"}


async def create_user_key(email: str, slug: str) -> str:
    body = {
        "user_id": email,
        "key_alias": f"nova-{slug}",
        "models": list(settings.allowed_models),
        # no "tags": LiteLLM OSS rejects them (Enterprise-only); user_id + alias + metadata attribute spend
        "metadata": {"product": "nova", "workspace": slug},
    }
    if settings.monthly_budget_usd > 0:
        body["max_budget"] = settings.monthly_budget_usd
        body["budget_duration"] = "30d"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{settings.litellm_url}/key/generate", json=body, headers=_headers())
    if r.status_code == 400 and "already exists" in r.text.lower():
        # alias taken by an earlier workspace of this user: rotate it
        await delete_user_key(slug)
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{settings.litellm_url}/key/generate", json=body, headers=_headers())
    if r.status_code >= 300:
        LLM_KEY_EVENTS.labels("error").inc()
        raise LLMKeyError(f"LiteLLM /key/generate {r.status_code}: {r.text[:200]}")
    LLM_KEY_EVENTS.labels("created").inc()
    return r.json()["key"]


async def delete_user_key(slug: str) -> None:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{settings.litellm_url}/key/delete", json={"key_aliases": [f"nova-{slug}"]}, headers=_headers())
    LLM_KEY_EVENTS.labels("deleted" if r.status_code < 300 else "delete_error").inc()


async def user_spend(slug: str) -> dict:
    """Spend for the user's nova key, as LiteLLM reports it (USD). Keys are looked up by alias:
    /key/generate with a user_id does not create a LiteLLM user, so /user/info would 404."""
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{settings.litellm_url}/key/list", headers=_headers(),
                        params={"key_alias": f"nova-{slug}", "return_full_object": "true"})
    if r.status_code >= 300:
        return {"available": False}
    keys = [k for k in r.json().get("keys", []) if isinstance(k, dict)]
    if not keys:
        return {"available": True, "spend_usd": 0.0, "max_budget": None, "models": []}
    k = keys[0]
    return {"available": True, "spend_usd": round(float(k.get("spend") or 0), 6),
            "max_budget": k.get("max_budget"), "budget_reset_at": k.get("budget_reset_at"),
            "models": k.get("models") or []}


async def models() -> list:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{settings.litellm_url}/v1/models", headers=_headers())
    ids = [m.get("id") for m in (r.json().get("data") or [])] if r.status_code < 300 else []
    allowed = set(settings.allowed_models)
    return [m for m in ids if m in allowed]
