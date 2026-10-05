"""Render NovaAgent's own download page (NovaAgent scripts/download-site/index.html) for this deployment
and gather the files it links: the desktop client built by desktop/build-client.sh, the nova CLI wheel,
the organisation root CA and SHA256SUMS.

    python3 render.py <NovaAgent src> <out dir> <server URL>

Edits are pattern-based (file names, version, server address), so a newer upstream page keeps working;
anything that no longer matches fails loudly.
"""
import hashlib
import json
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
src, out, server = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3].rstrip("/")
ver = json.loads((src / "package.json").read_text())["version"]
dist = ROOT / "desktop/dist"
files = {"exe": f"NovaAgent-Setup-{ver}-x64.exe", "deb": f"NovaAgent-{ver}-amd64.deb",
         "appimage": f"NovaAgent-{ver}-x86_64.AppImage"}
out.mkdir(parents=True, exist_ok=True)
for f in files.values():
    if not (dist / f).exists():
        sys.exit(f"missing {dist / f}: run desktop/build-client.sh first")
    shutil.copy2(dist / f, out / f)
wheels = sorted((ROOT / "download/dist").glob("nova_cli-*.whl"))
extras = []
if wheels:
    shutil.copy2(wheels[-1], out / wheels[-1].name)
    extras.append(wheels[-1].name)
shutil.copy2(ROOT / "desktop/org-root-ca.crt", out / "isigpu-root-ca.crt")
extras.append("isigpu-root-ca.crt")
for f in ROOT.joinpath("download").glob("vazirmatn-*.woff2"):
    shutil.copy2(f, out / f.name)

html = (src / "scripts/download-site/index.html").read_text(encoding="utf-8")
sizes = [(out / f).stat().st_size / 1e6 for f in files.values()]
host = server.split("://", 1)[-1]


def sub(pattern, repl, *, required=True, count=0):
    global html
    html, n = re.subn(pattern, repl, html, count=count)
    if required and n == 0:
        sys.exit(f"download page: pattern not found: {pattern}")


ver_re = r"\d+\.\d+\.\d+"
sub(rf"/nova/NovaAgent-Client-{ver_re}-Setup\.exe", files["exe"])
sub(rf"/nova/NovaAgent-Client-{ver_re}-amd64\.deb", files["deb"])
sub(rf"/nova/NovaAgent-Client-{ver_re}-x86_64\.AppImage", files["appimage"])
sub(rf"sudo dpkg -i NovaAgent-Client-{ver_re}-amd64\.deb", f"sudo apt install ./{files['deb']}")
sub(rf"NovaAgent-Client-{ver_re}-Setup\.exe", files["exe"])
sub(rf"NovaAgent-Client-{ver_re}-x86_64\.AppImage", files["appimage"])
sub(rf"v{ver_re}", f"v{ver}")
sub(rf'<b class="ltr">{ver_re}</b>', f'<b class="ltr">{ver}</b>')
sub(r"\d+–\d+ MB", f"{min(sizes):.0f}–{max(sizes):.0f} MB")
sub(r"\b\d{1,3}(?:\.\d{1,3}){3}:\d+\b", host)
sub(r'کافی است فایل <code class="ltr">remote-client\.json</code>[^<]*<code[^>]*>[^<]*</code>|کافی است فایل <code class="ltr">remote-client\.json</code>[^<]*',
    'از منوی <b>NovaAgent ← خروج از حساب</b> خارج شوید؛ برای سرور دیگر نسخه‌ی مخصوص آن سرور را از همان سرور دانلود کنید.',
    required=False)
# self-hosted Vazirmatn (customers' machines need not reach Google Fonts)
sub(r"<style>\n", "<style>\n@font-face{font-family:'Vazirmatn';src:url(vazirmatn-400.woff2) format('woff2');font-weight:400;font-display:swap}\n"
    "@font-face{font-family:'Vazirmatn';src:url(vazirmatn-600.woff2) format('woff2');font-weight:600 800;font-display:swap}\n", count=1)
if wheels:
    faq_cli = (f'<details>\n      <summary>خط فرمان (CLI) هم دارد؟</summary>\n      <div class="a">بله — <code class="ltr">nova</code> برای ترمینال و CI. '
               f'نصب: <code class="ltr">pip install {server}/download/{wheels[-1].name}</code> سپس <code class="ltr">nova login</code>. '
               f'گواهی ریشه‌ی سازمان: <a class="ltr" href="isigpu-root-ca.crt">isigpu-root-ca.crt</a>.</div>\n    </details>\n    ')
    sub(r"<details>\s*<summary>[^<]*به اشتراک AI", faq_cli + "<details>\n      <summary>به اشتراک AI", required=False, count=1)
assert "172.16.40.188" not in html
(out / "index.html").write_text(html, encoding="utf-8")
names = sorted(list(files.values()) + extras)
(out / "SHA256SUMS").write_text("".join(f"{hashlib.sha256((out / n).read_bytes()).hexdigest()}  {n}\n" for n in names))
print(f"rendered NovaAgent {ver} download page -> {out}")
