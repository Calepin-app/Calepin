"""Assemble la démo en ligne (site statique) : python3 demo/build.py <dossier de sortie>

  index.html, fr.html   la page de Calepin (anglais, français) + shim.js, qui la fait répondre sans serveur
  bundle.json           ce que Pyodide installe dans son disque virtuel : le code de pipeline/, les relevés
                        fictifs de sample/statements et ce que le LLM en a tiré (demo/enregistre)
Lancé par la GitHub Action .github/workflows/demo.yml à chaque push ; aucun LLM nécessaire.
"""
import base64
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/pyodide.js"
out = Path(sys.argv[1] if len(sys.argv) > 1 else "_site")
tmp = Path(tempfile.mkdtemp())  # dossier des données vide : importer revue ne touche à rien de réel
os.environ.update(BANQUE_DOSSIER=str(tmp), BANQUE_CONF=str(tmp / "conf.json"), BANQUE_LANG="en")
sys.path.insert(0, str(ROOT / "pipeline"))

import i18n  # noqa: E402
import revue  # noqa: E402

shutil.rmtree(out, ignore_errors=True)
out.mkdir(parents=True)

files = {}
for f in (ROOT / "pipeline").rglob("*"):
    if f.suffix in (".py", ".json") and "__pycache__" not in f.parts:
        files[f"/calepin/{f.relative_to(ROOT).as_posix()}"] = f
for f in ROOT.glob("categories.exa*.txt"):
    files[f"/calepin/{f.name}"] = f
files["/calepin/demo/demo.py"] = DEMO / "demo.py"
for f in (ROOT / "sample" / "statements").glob("*.csv"):
    files[f"/demo/data/{f.name}"] = f
for f in (DEMO / "enregistre").iterdir():
    files["/calepin/demo/analyse.json" if f.name == "analyse.json" else f"/demo/travail/{f.name}"] = f
bundle = {k: base64.b64encode(v.read_bytes()).decode() for k, v in sorted(files.items())}
(out / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")

for name in ("shim.js", "demo.css"):
    shutil.copy2(DEMO / name, out / name)
for lang, page in (("en", "index.html"), ("fr", "fr.html")):
    i18n.LANG = lang
    texts = {k[5:]: v for k, v in i18n.catalog("demo.").items()}
    head = (f'<script src="{PYODIDE}"></script>'
            f'<script>const DEMO = {json.dumps({"lang": lang, **texts}, ensure_ascii=False)};</script>'
            '<script src="shim.js"></script><link rel="stylesheet" href="demo.css">')
    html = revue.render_page()
    assert "<head>" in html
    (out / page).write_text(html.replace("<head>", "<head>" + head, 1), encoding="utf-8")
(out / ".nojekyll").write_text("")
shutil.rmtree(tmp)
print(f"{out}: {len(bundle)} files in bundle.json")
