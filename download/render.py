"""Render the NovaAgent download page + gather the artifacts it links into an output dir.
python3 render.py <out-dir> <server-url>   (reads desktop/dist, the CLI wheel in dist/, the root CA)"""
import hashlib, json, pathlib, shutil, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
out, server = pathlib.Path(sys.argv[1]), sys.argv[2]
ver = json.loads((ROOT / "desktop/app/package.json").read_text())["version"]
files = {"EXE": f"NovaAgent-Setup-{ver}-x64.exe", "DEB": f"NovaAgent-{ver}-amd64.deb",
         "APPIMAGE": f"NovaAgent-{ver}-x86_64.AppImage"}
out.mkdir(parents=True, exist_ok=True)
for f in files.values():
    shutil.copy2(ROOT / "desktop/dist" / f, out / f)
wheels = sorted((ROOT / "download/dist").glob("nova_cli-*.whl"))
if not wheels:
    sys.exit("CLI wheel missing: pip wheel ./cli --no-deps -w download/dist")
shutil.copy2(wheels[-1], out / wheels[-1].name)
shutil.copy2(ROOT / "desktop/app/isigpu-root-ca.crt", out / "isigpu-root-ca.crt")
for f in ROOT.joinpath("download").glob("vazirmatn-*.woff2"):
    shutil.copy2(f, out / f.name)
sizes = [(out / f).stat().st_size / 1e6 for f in files.values()]
html = (ROOT / "download/index.template.html").read_text()
for k, v in {**files, "VERSION": ver, "SERVER": server.replace("https://", ""), "CLI_URL": f"{server}/download/{wheels[-1].name}",
             "SIZES": f"{min(sizes):.0f}–{max(sizes):.0f} MB"}.items():
    html = html.replace("{{%s}}" % k, v)
assert "{{" not in html, html[html.index("{{"):html.index("{{") + 40]
(out / "index.html").write_text(html)
sums = [f"{hashlib.sha256((out / f).read_bytes()).hexdigest()}  {f}" for f in sorted(list(files.values()) + [wheels[-1].name, "isigpu-root-ca.crt"])]
(out / "SHA256SUMS").write_text("\n".join(sums) + "\n")
print(f"rendered v{ver} -> {out}")
