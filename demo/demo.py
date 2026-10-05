"""Démo en ligne : le code de pipeline/ exécuté tel quel dans le navigateur (Pyodide).

shim.js détourne les appels /api/... de la page vers handle(). Le disque virtuel contient les relevés
fictifs (/demo/data) et ce que le LLM local a produit sur eux (/demo/travail, enregistré par record.py).
Ce qui demande un LLM (import, assistant, analyse d'une autre période) répond par un message.
"""
import json
import os
import sys

os.environ.update(BANQUE_DOSSIER="/demo", BANQUE_CONF="/demo/conf.json")
sys.path.insert(0, "/calepin/pipeline")

import i18n  # noqa: E402
from i18n import tr  # noqa: E402

NEEDS_LLM = {"/api/import", "/api/assistant", "/api/modele",
             "/api/dossier", "/api/dossier/exemple", "/api/dossier/parcourir"}
state = analysis = Call = None


def start(lang):
    global state, analysis, Call
    i18n.LANG = lang
    import revue

    class Call(revue.Routes):
        def __init__(self, path):
            self.path, self.out = path, None

        def send(self, body, ctype="application/json", code=200):
            self.out = [code, ctype, body]

    revue.Routes.state = state = revue.State()
    with open("/calepin/demo/analyse.json", encoding="utf-8") as f:
        analysis = json.load(f)


def answer(obj):
    return json.dumps([200, "application/json", json.dumps(obj, ensure_ascii=False)])


def handle(method, path, body):
    req = json.loads(body or "{}")
    if method == "GET":
        if path == "/api/import":
            return answer({"running": False, "ok": None, "lines": []})
        if path == "/api/modeles":
            return answer({"models": [], "current": "", "error": tr("demo.no_llm")})
        if path == "/api/dossier":
            return answer({"path": tr("demo.folder"), "default": False})
    elif path in NEEDS_LLM:
        return answer({"ok": False, "message": tr("demo.no_llm")})
    elif path == "/api/analyse":
        if req.get("du") or req.get("au"):
            return answer({"ok": False, "message": tr("demo.analysis_period")})
        return answer({"ok": True, "html": analysis[i18n.LANG]})
    call = Call(path)
    call.route_get() if method == "GET" else call.route_post(req)
    return json.dumps(call.out or [404, "application/json", "{}"])
