import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from nova_control import billing, tenants, workspaces
from nova_control.identity import Identity

ALICE = {"name": "alice", "email": "alice@isigpu.local", "token": "a" * 48, "groups": [],
         "disabled": False, "created": 1730000000, "display_name": "Alice"}
ADMIN = {"name": "admin", "email": "admin@isigpu.local", "token": "b" * 48, "groups": ["nova-admins"],
         "disabled": False, "created": 1730000000, "display_name": "Admin"}


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(tenants, "all_customers", lambda fresh=False: [dict(ALICE), dict(ADMIN)])


def ident(token="a" * 48):
    c = tenants.by_token(token)
    return tenants.identity_of(c)


async def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=__import__("nova_control.main", fromlist=["app"]).app),
                             base_url="http://test", headers={"X-Session-API-Key": "a" * 48})


def test_nonstream_usage_is_extracted_from_openai_payload():
    usage = billing.usage_from_payload({"usage": {"prompt_tokens": 11, "completion_tokens": 7}})
    assert usage == {"in_tokens": 11, "out_tokens": 7}
    assert billing.usage_from_payload({"usage": None}) is None


def test_stream_usage_is_extracted_from_final_sse_chunk():
    raw = "\n".join([
        'data: {"id":"1","choices":[{"delta":{"content":"hello"}}]}',
        'data: {"id":"1","choices":[],"usage":{"prompt_tokens":100,"completion_tokens":40}}',
        "data: [DONE]",
    ])
    assert billing.usage_from_sse(raw) == {"in_tokens": 100, "out_tokens": 40}
    # A chunk boundary must not lose the final usage object.
    assert billing.usage_from_sse(raw.encode()[:-9]) == {"in_tokens": 100, "out_tokens": 40}


def test_usage_summary_is_grouped_by_model_and_day():
    h = {"usage": [
        {"model": "qwen3", "tokens_in": 10, "tokens_out": 5, "cost_toman": 0.125, "created": "2026-10-14T10:00:00"},
        {"model": "glm", "tokens_in": 100, "tokens_out": 10, "cost_toman": 1.1, "created": "2026-10-14T11:00:00"},
    ]}
    s = billing.usage_summary(h)
    assert s["tokens"] == 125 and s["models"]["qwen3"]["tokens_out"] == 5
    assert s["daily"] == [{"day": "2026-10-14", "tokens": 125, "cost_toman": 1.225}]


def test_billing_endpoints_proxy_to_internal_and_do_not_forward_return_url(monkeypatch):
    import asyncio
    seen = []

    async def fake_purchase(http, received, plan_id):
        seen.append((received.slug, plan_id))
        return {"redirect_url": "http://ipg.test/IPG?Token=x", "reference": "DA1"}

    async def run():
        monkeypatch.setattr(billing, "purchase", fake_purchase)
        async with await client() as c:
            return await c.post("/api/billing/purchase", json={"plan_id": "a200", "return_url": "https://evil"})

    r = asyncio.run(run())
    assert r.status_code == 200 and seen == [("alice", "a200")]


def test_prefs_are_validated_and_saved_per_account(monkeypatch):
    import asyncio
    saved = {}

    def fake_save(name, updates):
        saved[name] = updates
        return {"language": updates["language"], "theme": updates["theme"], "default_model": updates["default_model"]}

    async def run():
        monkeypatch.setattr(tenants, "set_preferences", fake_save)
        async with await client() as c:
            return (await c.put("/api/me/prefs", json={"language": "en", "theme": "light", "default_model": "glm"}),
                    await c.put("/api/me/prefs", json={"language": "xx", "theme": "light", "default_model": "glm"}))

    ok, bad = asyncio.run(run())
    assert ok.status_code == 200 and ok.json()["preferences"]["language"] == "en"
    assert bad.status_code == 422 and saved == {"alice": {"language": "en", "theme": "light", "default_model": "glm"}}


def test_enforcement_blocks_normal_user_but_admins_are_exempt(monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from types import SimpleNamespace
    monkeypatch.setattr(billing, "settings", SimpleNamespace(agent_billing_enforce=True))

    async def free_wallet(http, received):
        return {"plan": "free", "credit_toman": 0}

    async def run():
        monkeypatch.setattr(billing, "wallet", free_wallet)
        try:
            await billing.ensure_new_completion_allowed(None, ident())
        except HTTPException as exc:
            return exc
        return None

    exc = asyncio.run(run())
    assert exc is not None and exc.status_code == 402
    asyncio.run(billing.ensure_new_completion_allowed(None, ident("b" * 48)))


def test_no_enforcement_keeps_existing_users_working(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    monkeypatch.setattr(billing, "settings", SimpleNamespace(agent_billing_enforce=False))

    async def fail_wallet(http, received):
        raise AssertionError("wallet must not be called")

    async def run():
        monkeypatch.setattr(billing, "wallet", fail_wallet)
        await billing.ensure_new_completion_allowed(None, ident())

    asyncio.run(run())


def test_llm_key_is_resolved_without_returning_or_logging_the_secret():
    class FakeSecret:
        def __init__(self):
            self.metadata = SimpleNamespace(name="ws-alice", labels={"nova.isigpu.local/user": "alice"})
            self.data = {"llm-api-key": base64.b64encode(b"litellm-key").decode()}

    class FakeCore:
        def list_namespaced_secret(self, namespace, label_selector):
            assert namespace and "component=workspace" in label_selector
            return SimpleNamespace(items=[FakeSecret()])

    original = workspaces._api_pair
    workspaces._api_pair = (FakeCore(), object())
    try:
        assert workspaces.slug_for_llm_key("litellm-key") == "alice"
        assert workspaces.slug_for_llm_key("wrong") is None
    finally:
        workspaces._api_pair = original


def test_enforcement_caches_an_active_plan_but_not_a_missing_one(monkeypatch):
    import asyncio
    monkeypatch.setattr(billing, "settings", SimpleNamespace(agent_billing_enforce=True))
    monkeypatch.setattr(billing, "_allowed_until", {})
    answers = [{"plan": "free", "credit_toman": 0}, {"plan": "a200", "credit_toman": 150000.0}]
    calls = []

    async def wallet(http, received):
        calls.append(1)
        return answers[min(len(calls), len(answers)) - 1]

    async def unavailable(http, received):
        raise HTTPException(503, "down")

    async def run():
        monkeypatch.setattr(billing, "wallet", wallet)
        with pytest.raises(HTTPException) as blocked:
            await billing.ensure_new_completion_allowed(None, ident())
        assert blocked.value.status_code == 402
        await billing.ensure_new_completion_allowed(None, ident())          # bought: allowed at once
        await billing.ensure_new_completion_allowed(None, ident())          # cached, no third wallet call
        assert len(calls) == 2
        billing._allowed_until.clear()
        monkeypatch.setattr(billing, "wallet", unavailable)
        await billing.ensure_new_completion_allowed(None, ident())          # billing outage: not blocked

    asyncio.run(run())
