"""NovaAgent's admin panel page, taken from NovaAgent's own scripts/admin/nova-admin.mjs (the PAGE
template literal it serves), with the few edits the Kubernetes deployment needs. Each edit asserts
its anchor, so an upstream change shows up as an error instead of a silently wrong panel.

- sign in with an organisation account (realm nova, group nova-admins) instead of one shared password
- passwords live in Active Directory: the per-user "password" button becomes "sign out devices"
  (rotates that user's tenant token); the panel-password change button is removed
- new users are AD usernames (no portal password)
"""
from __future__ import annotations

import functools
import re

from .config import settings

EDITS = [
    ('<input id="pw" type="password" placeholder="رمز مدیریت" autofocus onkeydown="if(event.key===\\\'Enter\\\')doLogin()">',
     '<input id="un" type="text" placeholder="نام کاربری سازمانی" autofocus dir="ltr" onkeydown="if(event.key===\\\'Enter\\\')document.getElementById(\\\'pw\\\').focus()">'
     '<div style="height:8px"></div><input id="pw" type="password" placeholder="گذرواژه" dir="ltr" onkeydown="if(event.key===\\\'Enter\\\')doLogin()">'),
    ('post("/api/login",{password:pw})', 'post("/api/login",{username:document.getElementById("un").value.trim(),password:pw})'),
    ('renderLogin(r.j.error||"رمز اشتباه است")', 'renderLogin(r.j.error||"نام کاربری یا گذرواژه نادرست است")'),
    ('\'<button class="ghost mini" onclick="changePw()">تغییر رمز</button>\'+', ''),
    ("(t.hasPortalUser?'تغییر رمز':'رمز')", "'خروج دستگاه‌ها'"),
    ('''function tPw(n){
  var p=prompt("رمز پورتال برای "+n+" (حداقل ۴ کاراکتر):");
  if(p==null)return;
  if(p.length<4){toast("رمز کوتاه است");return}
  post("/api/tenants/"+n+"/password",{password:p}).then(function(r){
    toast(r.j.ok?"رمز "+r.j.username+" ثبت شد ✓":("خطا: "+(r.j.error||r.s)));loadTenants();
  });
}''', '''function tPw(n){
  if(!confirm("همه‌ی دستگاه‌های "+n+" از حساب خارج شوند؟ (توکن کاربر عوض می‌شود و باید دوباره وارد شود)"))return;
  post("/api/tenants/"+n+"/rotate",{}).then(function(r){
    toast(r.j.ok?"دستگاه‌های "+r.j.username+" خارج شدند ✓":("خطا: "+(r.j.error||r.s)));loadTenants();
  });
}'''),
    ('placeholder="نام کاربری مثلاً node3"', 'placeholder="نام کاربری Active Directory (مثلاً ali)"'),
    ('<span class="mut">رمز، همان لاگین پورتال NovaAgent است</span>', '<span class="mut">کاربران با حساب سازمانی (Active Directory) وارد اپ می‌شوند</span>'),
    ('<th>پورت</th>', '<th>Workspace</th>'),
]


@functools.lru_cache(maxsize=1)
def render() -> str:
    src = open(settings.admin_page, encoding="utf-8").read()
    a = src.index("const PAGE = `") + len("const PAGE = `")
    page = src[a:src.index("`;\n", a)]
    # evaluate the JS template literal's escapes (the PAGE has no ${} interpolations)
    if "${" in page:
        raise RuntimeError("nova-admin PAGE gained template interpolations; update adminpage.py")
    page = re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), page, flags=re.S)
    fav = re.search(r'const FAVICON =\s*"([^"]*)"', src)
    page = page.replace("__FAVICON__", fav.group(1) if fav else "")
    for old, new in EDITS:
        if old not in page:
            raise RuntimeError(f"nova-admin page anchor not found: {old[:70]!r}")
        page = page.replace(old, new, 1)
    # the new-user form has no password field any more
    page = re.sub(r'<input id="ntpw"[^>]*>', '<input id="ntpw" type="hidden" value="">', page, count=1)
    return page
