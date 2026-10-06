import asyncio

from nova_control import tenants, workspaces
from nova_control.identity import Identity
from nova_control.main import logout


def test_logout_rotates_token_and_rolls_workspace(monkeypatch):
    ident = Identity(email="alice@isigpu.local", slug="alice", groups=("nova-admins",))
    rotated = []
    restarted = []

    async def fake_restart(received):
        restarted.append(received)

    monkeypatch.setattr(tenants, "rotate_token", lambda slug: rotated.append(slug))
    monkeypatch.setattr(workspaces, "restart", fake_restart)
    resp = asyncio.run(logout(ident))

    assert resp.body == b'{"ok":true}'
    assert rotated == [ident.slug] and restarted == [ident]
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith('nova_t="') and "Max-Age=0" in cookie
