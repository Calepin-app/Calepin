"""Page de révision des catégories : python3 pipeline/revue.py

Mini-serveur local (127.0.0.1 uniquement) : les opérations sont regroupées par libellé, on corrige la
catégorie d'un libellé (vaut pour toutes ses opérations, présentes et futures) ou d'une opération
précise. Chaque changement est enregistré aussitôt. N'appelle pas le LLM.
"""
import json
from html import escape
import os
import re
import sys
import threading
import traceback
import urllib.request
import webbrowser
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import arbre
import assistant
import dossier
from categorize import apply, categorize, expand, inherit, load_cache, load_overrides, normalize
from config import SCHEMAS_FILE, BASE_DIR, CATEGORIES_FILE, COMMENTS_FILE, DATA_DIR, save_conf, DEFAULT_BASE, PRIVATE_DIR, ERROR_LOG, OVERRIDES_FILE, REPORT_FILE, TAXONOMY_FILE, VALID_FILE
from ingest import ingest
import i18n
from i18n import tr
import llm
from llm import PipelineError, ensure_server
from run import build_analysis, build_report
from taxonomy import load

PORT = int(os.environ.get("BANQUE_PORT", 8765))
PAIR_DAYS = 5


def assignment(t):
    """Affectation actuelle d'une opération (catégorie, ou ventilation complète)."""
    return json.dumps(t["split"], ensure_ascii=False, sort_keys=True) if t.get("split") else t["category"]


def load_valid():
    return json.loads(VALID_FILE.read_text(encoding="utf-8")) if VALID_FILE.exists() else {}


class Import:
    """Import lancé depuis la page (bouton « Mettre à jour ») : lecture des relevés et catégorisation des
    nouveaux libellés dans un fil à part ; la page suit la progression."""
    running, ok, lines = False, None, []
    lock = threading.Lock()

    @classmethod
    def start(cls):
        with cls.lock:
            if cls.running:
                return False
            cls.running, cls.ok, cls.lines = True, None, []
        threading.Thread(target=cls.run, daemon=True).start()
        return True

    @classmethod
    def run(cls):
        say = cls.lines.append
        try:
            if not DATA_DIR.is_dir():
                raise PipelineError(tr("run.no_data_dir", path=DATA_DIR))
            PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
            ensure_server()
            tx = ingest(say)
            say(tr("run.after_dedup", n=len(tx)))
            tx = expand(categorize(tx, say))
            valid = load_valid()
            say(tr("run.import_done", n=len({x["id"] for x in tx} - valid.keys())))
            cls.ok = True
        except PipelineError as e:
            say(tr("run.failed", stage=tr("stage.import"), msg=e))
            cls.ok = False
        except Exception as e:  # message possiblement porteur de données : type seulement
            ERROR_LOG.write_text(traceback.format_exc(), encoding="utf-8")
            say(tr("run.failed", stage=tr("stage.import"), msg=tr("srv.error", kind=type(e).__name__, log=ERROR_LOG.name)))
            cls.ok = False
        finally:
            cls.running = False


class State:
    def __init__(self):
        self.seen, self.error = None, None
        self.tx, self.pairs, self.cache, self.tax, self.skipped = [], set(), {}, None, 0
        self.refresh()

    def reload(self):
        self.tax = load()
        self.cache = load_cache(self.tax)

    @staticmethod
    def stamp():
        """Empreinte des relevés et des fichiers que « Mettre à jour » peut modifier pendant que la page tourne."""
        files = sorted(DATA_DIR.glob("*.csv")) + [SCHEMAS_FILE, CATEGORIES_FILE, TAXONOMY_FILE]
        return tuple((f.name, f.stat().st_mtime) for f in files if f.exists())

    def refresh(self):
        """Relit relevés et catégories s'ils ont changé depuis le dernier chargement (nouvel import).
        Dossier introuvable ou vide : la page s'ouvre quand même et propose d'en choisir un."""
        if not DATA_DIR.is_dir():
            self.error = tr("run.no_data_dir", path=DATA_DIR)
            return
        st = self.stamp()
        if st == self.seen and not self.error:
            return
        inputs = lambda st: [x for x in st if x[0].endswith(".csv") or x[0] == SCHEMAS_FILE.name]  # relevés et formats appris
        csvs = inputs(st)
        try:
            if self.seen is None or self.error or csvs != inputs(self.seen):
                self.tx = ingest(lambda m: None, learn=False) if any(x[0].endswith(".csv") for x in csvs) else []
                self.skipped = getattr(ingest, "skipped", 0)
                self.pairs = transfer_pairs(self.tx)
            self.reload()
            self.error = None
        except PipelineError as e:
            self.error = str(e)
        self.seen = self.stamp()

    def data(self):
        self.refresh()
        if self.error:
            return {"error": self.error, "groups": [], "paths": [], "fallbacks": []}
        inherit(self.tx, self.cache)
        apply(self.tx, self.tax, self.cache)
        valid = load_valid()
        notes = json.loads(COMMENTS_FILE.read_text(encoding="utf-8")) if COMMENTS_FILE.exists() else {}
        groups = defaultdict(list)
        for t in self.tx:
            groups[t["key"]].append(t)
        out = []
        for k, ops in groups.items():
            c = self.cache.get(k) or {}
            ops.sort(key=lambda t: t["date"], reverse=True)
            out.append({
                "key": k, "label": k[2:], "credit": k[0] == "C",
                "merchant": ops[0]["merchant"],
                "category": c.get("category") or self.tax.fallback(ops[0]["amount"]),
                "origin": "manuel" if c.get("manual") else "converti" if c.get("migrated") else "modèle" if c else "défaut",
                "total": round(sum(t["amount"] for t in ops), 2),
                "ops": [{"id": t["id"], "date": t["date"].isoformat(), "label": t["label"], "amount": t["amount"],
                         "source": t["source"], "category": t["category"], "origin": t["origin"],
                         "pair": t["id"] in self.pairs, "split": t.get("split"),
                         "ok": valid.get(t["id"]) == assignment(t), "note": notes.get(t["id"], "")} for t in ops],
            })
        paths = [{"path": p, "depth": p.count(":"),
                  "label": self.tax.label(p),
                  "locked": p in arbre.ROOTS or p in arbre.protected(self.tax)} for p in self.tax.paths]
        return {"groups": out, "paths": paths, "fallbacks": list(self.tax.fallbacks().values()),
                "notice": tr("srv.new_files", n=self.skipped) if self.skipped else ""}

    def set_label(self, k, category, merchant=None):
        entry = self.cache.setdefault(k, {"merchant": normalize(k[2:])[:30].title()})
        entry.update(category=category, manual=True)
        entry.pop("migrated", None)
        if merchant:
            entry["merchant"] = merchant
        CATEGORIES_FILE.write_text(json.dumps(self.cache, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def rename(self, k, merchant):
        """Renomme le marchand d'un libellé, sans toucher à sa catégorie."""
        entry = self.cache.setdefault(k, {"category": next(t["category"] for t in self.tx if t["key"] == k)})
        entry.update(merchant=merchant, merchant_manual=True)
        CATEGORIES_FILE.write_text(json.dumps(self.cache, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def set_op(self, op_id, category):
        ov = load_overrides()
        if category:
            ov[op_id] = category
        else:
            ov.pop(op_id, None)
        OVERRIDES_FILE.write_text(json.dumps(ov, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def set_note(self, op_id, text):
        notes = json.loads(COMMENTS_FILE.read_text(encoding="utf-8")) if COMMENTS_FILE.exists() else {}
        text = " ".join((text or "").split())[:500]
        if text:
            notes[op_id] = text
        else:
            notes.pop(op_id, None)
        COMMENTS_FILE.write_text(json.dumps(notes, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def validate(self, ids, ok):
        """Valide (ou dévalide) l'affectation actuelle des opérations données."""
        valid, cur = load_valid(), {t["id"]: t for t in self.tx}
        for i in ids:
            if ok and i in cur:
                valid[i] = assignment(cur[i])
            else:
                valid.pop(i, None)
        VALID_FILE.write_text(json.dumps(valid, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def set_split(self, op_id, parts):
        """Ventile une opération ; parts vide = supprime la ventilation."""
        ov = load_overrides()
        if not parts:
            ov.pop(op_id, None)
        else:
            op = next((t for t in self.tx if t["id"] == op_id), None)
            valid = set(self.tax.assignable())
            parts = [{"category": x["category"], "amount": round(float(x["amount"]), 2)} for x in parts]
            if not op or len(parts) < 2 or any(x["category"] not in valid for x in parts):
                raise ValueError(tr("srv.split_invalid"))
            if abs(sum(x["amount"] for x in parts) - op["amount"]) > 0.005:
                raise ValueError(tr("srv.split_sum"))
            ov[op_id] = parts
        OVERRIDES_FILE.write_text(json.dumps(ov, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def transfer_pairs(tx):
    """Opérations probablement miroir l'une de l'autre : même montant, signes opposés,
    libellés différents, à ≤ PAIR_DAYS jours d'écart."""
    by_amount = defaultdict(list)
    for t in tx:
        by_amount[round(abs(t["amount"]), 2)].append(t)
    ids = set()
    for amt, ts in by_amount.items():
        if amt < 10 or len(ts) < 2:
            continue
        for a in ts:
            for b in ts:
                if (a["amount"] < 0 < b["amount"] and a["label"] != b["label"]
                        and abs((a["date"] - b["date"]).days) <= PAIR_DAYS):
                    ids.update((a["id"], b["id"]))
    return ids


class Routes:
    """Réponses aux requêtes de la page, indépendantes du transport : le serveur local (Handler) ou la
    démo en ligne (demo/, le même code exécuté dans le navigateur). Fournir path, state et send()."""
    state: State = None

    def route_get(self):
        if self.path == "/":
            self.send(render_page(), "text/html")
        elif self.path == "/api/data":
            self.send(json.dumps(self.state.data(), ensure_ascii=False))
        elif self.path == "/api/ping":
            self.send('{"app": "calepin"}')
        elif self.path == "/api/import":
            self.send(json.dumps({"running": Import.running, "ok": Import.ok, "lines": Import.lines[-12:]}, ensure_ascii=False))
        elif self.path == "/api/dossier":
            self.send(json.dumps({"path": str(BASE_DIR), "default": BASE_DIR == DEFAULT_BASE}, ensure_ascii=False))
        elif self.path == "/api/modeles":
            self.send(json.dumps(model_list(), ensure_ascii=False))
        elif self.path == "/api/memoire":
            self.send(json.dumps(assistant.memory(self.state.tax) if self.state.tax else {"regroupements": [], "regles": [], "consignes": []}, ensure_ascii=False))
        else:
            self.send("{}", code=404)

    def route_post(self, req):
        if self.path == "/api/import":
            return self.send(json.dumps({"ok": Import.start()}))
        if self.path == "/api/dossier/parcourir":
            return self.send(json.dumps({"path": dossier.choose() or ""}, ensure_ascii=False))
        if self.path in ("/api/dossier", "/api/dossier/exemple"):
            ok, msg = dossier.make_demo() if self.path.endswith("exemple") else dossier.set_folder(req.get("path") or "")
            self.send(json.dumps({"ok": ok, "message": msg}, ensure_ascii=False))
            if ok:  # les chemins sont fixés au démarrage : l'application redémarre sur le nouveau dossier
                threading.Timer(0.5, restart).start()
            return
        if self.path == "/api/langue" and req.get("lang") in i18n.LANGS:
            i18n.set_lang(req["lang"])
            return self.send('{"ok": true}')
        if Import.running:  # pas de modification pendant un import (il réécrit les mêmes fichiers)
            return self.send(json.dumps({"ok": False, "message": tr("srv.import_running")}, ensure_ascii=False))
        if self.path == "/api/modele" and isinstance(req.get("model"), str) and req["model"]:
            save_conf(modele=req["model"])
            return self.send('{"ok": true}')
        self.state.refresh()  # un import a pu modifier les fichiers depuis l'affichage de la page
        if self.state.error:
            return self.send(json.dumps({"ok": False, "message": self.state.error}, ensure_ascii=False))
        valid = set(self.state.tax.paths)
        if self.path == "/api/libelle" and req.get("category") in valid:
            self.state.set_label(req["key"], req["category"], req.get("merchant"))
        elif self.path == "/api/operation" and (req.get("category") in valid or req.get("category") is None):
            self.state.set_op(req["id"], req.get("category"))
        elif self.path == "/api/marchand" and (req.get("merchant") or "").strip() and req.get("key"):
            self.state.data()
            self.state.rename(req["key"], " ".join(req["merchant"].split())[:60])
        elif self.path == "/api/commentaire" and isinstance(req.get("id"), str):
            self.state.set_note(req["id"], req.get("text"))
        elif self.path == "/api/valider" and isinstance(req.get("ids"), list):
            self.state.data()  # affectations à jour
            self.state.validate(req["ids"], bool(req.get("ok")))
        elif self.path == "/api/ventilation":
            try:
                self.state.set_split(req["id"], req.get("parts"))
            except (ValueError, TypeError, KeyError) as e:
                msg = str(e) if isinstance(e, ValueError) else tr("srv.split_invalid")
                return self.send(json.dumps({"ok": False, "message": msg}, ensure_ascii=False))
        elif self.path.startswith("/api/assistant") or self.path == "/api/memoire/oublier":
            try:
                if self.path == "/api/assistant":
                    out = assistant.converse(self.state, req.get("messages", []))
                elif self.path == "/api/assistant/appliquer":
                    out = {"message": assistant.apply(self.state, req["action"])}
                else:
                    assistant.forget(req["kind"], int(req["index"]))
                    out = {"message": tr("srv.forgotten")}
            except (PipelineError, arbre.TreeError) as e:
                self.send(json.dumps({"ok": False, "message": str(e)}, ensure_ascii=False))
                return
            self.state.reload()
            self.send(json.dumps({"ok": True, **out}, ensure_ascii=False))
            return
        elif self.path == "/api/arbre":
            try:
                op, msg = req.get("op"), tr("srv.tree_saved")
                if op == "ajouter":
                    arbre.add(req["path"], req["name"])
                elif op == "renommer":
                    arbre.rename(req["path"], req["name"])
                elif op == "deplacer":
                    arbre.move(req["path"], req["dest"])
                elif op == "supprimer":
                    msg = tr("srv.deleted", dest=self.state.tax.label(arbre.delete(req["path"])).replace(":", " › "))
                else:
                    raise arbre.TreeError(tr("asst.unknown_action"))
            except arbre.TreeError as e:
                self.send(json.dumps({"ok": False, "message": str(e)}, ensure_ascii=False))
                return
            self.state.reload()
            self.send(json.dumps({"ok": True, "message": msg}, ensure_ascii=False))
            return
        elif self.path == "/api/analyse":
            # commentaire du LLM seul, inséré par la page dans le rapport déjà affiché
            try:
                self.state.data()
                ensure_server()
                du, au = ((req.get(k) or "") if re.fullmatch(r"(\d{4}-\d{2})?", req.get(k) or "") else "" for k in ("du", "au"))
                out = {"ok": True, "html": build_analysis(expand(self.state.tx), du, au)}
            except PipelineError as e:
                out = {"ok": False, "message": str(e)}
            except Exception as e:  # message possiblement porteur de données : type seulement
                ERROR_LOG.write_text(traceback.format_exc(), encoding="utf-8")
                out = {"ok": False, "message": tr("srv.error", kind=type(e).__name__, log=ERROR_LOG.name)}
            self.send(json.dumps(out, ensure_ascii=False))
            return
        elif self.path == "/api/rapport":
            # rapport calculé à la volée sur la période ; commentaire du LLM seulement sur demande (lent)
            try:
                self.state.data()
                if req.get("comment"):
                    ensure_server()
                du, au = ((req.get(k) or "") if re.fullmatch(r"(\d{4}-\d{2})?", req.get(k) or "") else "" for k in ("du", "au"))
                html = build_report(expand(self.state.tx), du, au, bool(req.get("comment")))
                REPORT_FILE.write_text(html, encoding="utf-8")
                out = {"ok": True, "html": html}
            except PipelineError as e:
                out = {"ok": False, "message": str(e)}
            except Exception as e:  # message possiblement porteur de données : type seulement
                ERROR_LOG.write_text(traceback.format_exc(), encoding="utf-8")
                out = {"ok": False, "message": tr("srv.error", kind=type(e).__name__, log=ERROR_LOG.name)}
            self.send(json.dumps(out, ensure_ascii=False))
            return
        else:
            self.send('{"ok": false}', code=400)
            return
        self.send('{"ok": true}')


class Handler(Routes, BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self.route_get()

    def do_POST(self):
        self.route_post(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"))


PAGE = r"""<!doctype html><html lang="{{lang}}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{{app.name}}</title>
<style>
:root{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--accent:#2a78d6;--warn:#b86e00;--pos:#006300;--hl:#fff4d6;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--accent:#3987e5;--warn:#eda100;--pos:#0ca30c;--hl:#3a3220;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,sans-serif}
main{max-width:1150px;margin:0 auto;padding:20px 16px 80px}
header{position:sticky;top:0;z-index:2;background:var(--page);padding:12px 0;border-bottom:1px solid var(--grid)}
h1{font-size:20px;margin:0 0 10px}
[hidden]{display:none!important}
.tools{float:right;display:flex;gap:6px;align-items:center}.tools button{font-size:13px;padding:4px 10px}
#lang,#model{font-size:12px;padding:2px 4px}#model{max-width:230px}
.hd{font-size:12px;font-weight:600;color:var(--muted)}.row.hd{padding:10px 13px 0}.op.hd{padding-top:8px}
.hd .hc{width:260px}.hd .ghost{visibility:hidden;display:flex;gap:8px}.hd [data-sort]{cursor:pointer;user-select:none}.hd [data-sort]:hover,.hd .on{color:var(--ink)}
.panel{clear:both;background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:10px 14px;margin:10px 0}
.panel pre{margin:6px 0 0;font-size:12px;white-space:pre-wrap;color:var(--ink2);max-height:220px;overflow:auto}
.panel.warn{border-color:var(--warn);color:var(--warn)}
#folderbox .rr{display:flex;gap:8px;flex-wrap:wrap}#folderpath{flex:1 1 320px;min-width:0}
.per{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.per label{display:flex;gap:4px;align-items:center}
#report .bar{margin-bottom:10px}#rframe{width:100%;border:1px solid var(--grid);border-radius:10px;background:var(--page);min-height:400px}
.seg{display:inline-flex;border:1px solid var(--grid);border-radius:8px;overflow:hidden}.seg button{border:0;border-radius:0;padding:6px 10px}
.seg button.on{background:var(--accent);color:#fff}
.flat{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:4px 12px 8px;margin-top:8px}
.flat .mh{font-weight:600;font-size:13px;color:var(--ink2);padding:10px 0 4px;border-bottom:1px solid var(--grid);text-transform:capitalize}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.checks{display:flex;gap:12px;flex-wrap:wrap}.checks label{display:flex;gap:4px;align-items:center;cursor:pointer}
input,select,button{font:inherit;color:inherit;background:var(--surface);border:1px solid var(--grid);border-radius:8px;padding:6px 10px}
input[type=search]{flex:1;min-width:200px}
button{cursor:pointer}button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.muted{color:var(--muted)}.small{font-size:12px}
.help{margin:10px 0 0;color:var(--ink2);font-size:13px}
.g{background:var(--surface);border:1px solid var(--grid);border-radius:10px;margin-top:8px}
.row{display:grid;grid-template-columns:22px 20px minmax(0,1fr) 60px 110px minmax(200px,300px) 80px;gap:10px;align-items:center;padding:8px 12px}
.row .tog{cursor:pointer;color:var(--muted);user-select:none}
.lab{overflow:hidden}.lab b{font-weight:600;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lab .small{display:block;overflow-wrap:anywhere}
#tip{position:fixed;z-index:50;max-width:min(560px,calc(100vw - 32px));padding:8px 10px;border-radius:6px;background:var(--ink);
color:var(--page);font-size:12px;line-height:1.4;white-space:pre-wrap;overflow-wrap:anywhere;pointer-events:none;box-shadow:0 4px 14px #0003}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.pos{color:var(--pos)}
select.cat{width:100%}
.badge{font-size:11px;padding:2px 6px;border-radius:999px;border:1px solid var(--grid);color:var(--ink2);text-align:center}
.badge.converti,.badge.défaut{color:var(--warn);border-color:var(--warn)}
.badge.manuel,.badge.opération,.badge.ventilé{color:var(--accent);border-color:var(--accent)}
.ops{border-top:1px solid var(--grid);padding:4px 12px 8px 76px}  /* opérations en retrait sous le libellé */
.split{padding:0 0 6px 126px;font-size:12px}
.note{padding:0 0 6px 126px;font-size:12px;color:var(--ink2);cursor:pointer;white-space:pre-wrap}
.noted{display:flex;gap:6px;padding:0 0 8px 126px}.noted input{flex:1;min-width:0}
button.vent.has{border-color:var(--accent);color:var(--accent)}
.sped{margin:4px 0 10px 126px;padding:10px;border:1px solid var(--grid);border-radius:8px;background:var(--surface)}
.spl{display:grid;grid-template-columns:minmax(200px,340px) 120px 70px;gap:8px;align-items:center;margin-bottom:6px}
.spl input{text-align:right}.sped .bar{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
button.vent{font-size:11px;padding:2px 6px}
button.ren{border:0;background:none;padding:0 4px;margin-left:4px;color:var(--muted);font-size:12px;cursor:pointer;opacity:0}
.row:hover button.ren,button.ren:focus{opacity:1}@media (hover:none){button.ren{opacity:1}}
input.rename{font:inherit;font-weight:600;padding:1px 4px;width:100%}
input.ok{width:16px;height:16px;margin:0;accent-color:var(--pos);cursor:pointer}
.g.done{opacity:.7}.op.done .l{color:var(--muted)}
.op{display:grid;grid-template-columns:20px 86px minmax(0,1fr) 110px auto;gap:10px;align-items:center;padding:4px 0;font-size:13px}
.op .l{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.opc{display:flex;gap:8px;align-items:center}.opc select{width:260px}.opc .badge{min-width:64px}
/* fenêtre moyenne : le libellé garde toute la largeur, menu et boutons passent en dessous */
@media (max-width:1100px){.op .opc{grid-column:3/-1}.opc select{flex:1 1 auto;width:auto;min-width:0}}
.op.pair .l::before{content:"⇄ ";color:var(--accent);font-weight:700}
.g.haspair .lab b::after{content:" ⇄";color:var(--accent)}
.more{display:block;margin:14px auto}
#chat{margin-top:10px}
#msgs{display:flex;flex-direction:column;gap:10px}
.msg{max-width:85%;padding:10px 14px;border-radius:12px;background:var(--surface);border:1px solid var(--grid);white-space:pre-wrap}
.msg.user{align-self:flex-end;background:var(--accent);color:#fff;border-color:var(--accent)}
.msg.wait{color:var(--muted);font-style:italic}
.act{margin-top:8px;padding:8px 10px;border:1px solid var(--grid);border-radius:8px;background:var(--page);white-space:normal}
.act .ex{font-size:12px;color:var(--muted);margin-top:4px}.act .err{color:var(--warn);font-size:12px}
.act .btns{display:flex;gap:6px;margin-top:6px}.act.done{opacity:.6}.act button{padding:3px 10px;font-size:12px}
#ask{display:flex;gap:8px;margin-top:14px;position:sticky;bottom:calc(8px + env(safe-area-inset-bottom,0px))}
#ask textarea{flex:1;font:inherit;color:inherit;background:var(--surface);border:1px solid var(--grid);border-radius:8px;padding:8px 10px;resize:vertical}
.rf{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:10px 14px;margin-bottom:12px}
.rf h3{margin:0 0 8px;font-size:14px}.rr{display:flex;gap:8px;flex-wrap:wrap}.rr input{flex:1 1 200px;min-width:0}
.rr select{flex:1 1 220px;min-width:0}.prev{font-size:12px;color:var(--muted);margin-top:6px}
.prev ul{margin:4px 0 0;padding-left:18px}.prev .warn{color:var(--warn)}
#rulelist{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:4px 14px 10px}
#rulelist h4{margin:10px 0 4px;font-size:13px;color:var(--ink2)}
#mem{margin-top:16px;background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:10px 14px}
#mem summary{cursor:pointer;font-weight:600}#memlist h4{margin:10px 0 4px;font-size:13px;color:var(--ink2)}
.mi{display:flex;justify-content:space-between;gap:10px;padding:3px 0;font-size:13px;border-bottom:1px solid var(--grid)}
.mi button{padding:1px 8px;font-size:12px}
.tabs{display:flex;gap:4px;margin-bottom:10px}.tabs button{border-radius:8px 8px 0 0;border-bottom:none;background:none;color:var(--ink2)}
.tabs button.on{background:var(--surface);color:var(--ink);font-weight:600}
#tree{background:var(--surface);border:1px solid var(--grid);border-radius:10px;margin-top:10px;padding:6px 0}
.node{display:grid;grid-template-columns:minmax(0,1fr) 90px 120px 390px;gap:10px;align-items:center;padding:5px 12px}
.node:hover{background:color-mix(in srgb,var(--grid) 40%,transparent)}
.node .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.node.root .nm{font-weight:700;font-size:15px;margin-top:6px}
.node .acts{display:flex;justify-content:flex-end;gap:4px;opacity:.25;transition:opacity .1s}.node:hover .acts,.node .acts:focus-within{opacity:1}
.node .acts button{padding:2px 8px;font-size:12px}.node .acts select{font-size:12px;padding:2px 6px;max-width:260px}
@media (max-width:760px){.node{grid-template-columns:1fr 100px}.node .n{display:none}.node .acts{grid-column:1/-1;opacity:1;flex-wrap:wrap}}
#toast{position:fixed;bottom:calc(16px + env(safe-area-inset-bottom,0px));left:50%;transform:translateX(-50%);background:var(--ink);color:var(--page);padding:8px 14px;border-radius:8px;opacity:0;transition:opacity .2s;pointer-events:none}
#toast.on{opacity:.92}
@media (max-width:760px){.row{grid-template-columns:22px 20px 1fr 100px}.row .n,.row .badge{display:none}.row select{grid-column:3/-1}
.split,.sped,.note,.noted{margin-left:0;padding-left:0}.spl{grid-template-columns:1fr 100px 40px}
.op{grid-template-columns:20px 1fr 100px}.op .d,.op .badge{display:none}.op .opc{grid-column:1/-1}.ops{padding-left:36px}}
</style></head><body><main>
<header><h1>{{app.name}}</h1>
<span class="tools"><button id="bImport" title="{{ui.import.tip}}">⟳ {{ui.import}}</button>
<button id="bFolder" title="{{ui.folder.tip}}">📁 {{ui.folder}}</button>
<select id="model" title="{{ui.model.tip}}" hidden></select>
<select id="lang" title="Langue / Language"><option value="fr">FR</option><option value="en">EN</option></select></span>
<div id="jobbox" class="panel" hidden><b id="jobtitle"></b><pre id="joblog"></pre><button id="jobclose" hidden>{{ui.close}}</button></div>
<div id="folderbox" class="panel" hidden><b>{{ui.folder}}</b>
  <p class="small muted">{{ui.folder.help}}</p>
  <div class="rr"><input id="folderpath" autocomplete="off"><button id="fBrowse">{{ui.folder.browse}}</button>
  <button class="primary" id="fApply">{{ui.folder.apply}}</button><button id="fClose">{{ui.close}}</button></div>
  <p class="small muted">{{ui.folder.demo.help}} <button id="fDemo">{{ui.folder.demo}}</button></p>
  <p id="foldermsg" class="small"></p></div>
<div id="errbox" class="panel warn" hidden></div>
<nav class="tabs"><button data-tab="ops" class="on">{{ui.tab.ops}}</button><button data-tab="tree">{{ui.tab.tree}}</button><button data-tab="rules">{{ui.tab.rules}}</button><button data-tab="chat">{{ui.tab.chat}}</button><button data-tab="report">{{ui.tab.report}}</button></nav>
<div id="opsbar"><div class="bar">
<span class="seg" id="view"><button data-v="groups" class="on">{{ui.view.groups}}</button><button data-v="flat">{{ui.view.flat}}</button></span>
<button id="expand" title="{{ui.expand.tip}}">{{ui.expand}}</button>
<input type="search" id="q" placeholder="{{ui.search}}">
<select id="f" title="{{ui.f.cat.tip}}"></select>
<span class="checks">
<label class="small" title="{{ui.f.todo.tip}}"><input type="checkbox" id="fTodo"> {{ui.f.todo}}</label>
<label class="small" title="{{ui.f.check.tip}}"><input type="checkbox" id="fCheck"> {{ui.f.check}}</label>
<label class="small" title="{{ui.f.pair.tip}}"><input type="checkbox" id="fPair"> {{ui.f.pair}}</label>
</span>
<span class="per" title="{{ui.f.period.tip}}"><label class="small">{{ui.from}} <input type="month" id="fDu"></label>
<label class="small">{{ui.to}} <input type="month" id="fAu"></label></span>
</div>
<p class="help">{{ui.help.ops}} <span id="count" class="muted"></span></p>
</div>
<p id="treehelp" class="help" hidden>{{ui.help.tree}}</p>
</header>
<div id="ops"><div id="list"></div><button class="more" id="more" hidden>{{ui.more}}</button></div>
<div id="tree" hidden></div>
<div id="report" hidden>
  <div class="bar"><label class="small">{{ui.from}} <input type="month" id="rDu"></label><label class="small">{{ui.to}} <input type="month" id="rAu"></label>
  <button class="primary" id="rGo">{{ui.show}}</button>
  <button id="rLLM" title="{{ui.llm.tip}}">{{ui.llm}}</button>
  <button id="rPDF" title="{{ui.pdf.tip}}">{{ui.pdf}}</button>
  <span id="rState" class="small muted"></span></div>
  <iframe id="rframe" title="{{ui.tab.report}}" allow="clipboard-write"></iframe>
</div>
<div id="rules" hidden>
  <p class="help">{{ui.help.rules}}</p>
  <form class="rf" data-type="regrouper"><h3>{{ui.r.group}}</h3>
    <div class="rr"><input name="motif" placeholder="{{ui.r.pattern_ex1}}" autocomplete="off">
    <input name="nom" placeholder="{{ui.r.name_ex}}" autocomplete="off"><button class="primary">{{ui.add}}</button></div>
    <div class="prev"></div></form>
  <form class="rf" data-type="categoriser"><h3>{{ui.r.classify}}</h3>
    <div class="rr"><input name="motif" placeholder="{{ui.r.pattern_ex2}}" autocomplete="off">
    <select name="categorie" class="rcat"></select><button class="primary">{{ui.add}}</button></div>
    <div class="prev"></div></form>
  <form class="rf" data-type="consigne"><h3>{{ui.r.note}}</h3>
    <div class="rr"><input name="texte" placeholder="{{ui.r.note_ex}}" autocomplete="off">
    <button class="primary">{{ui.add}}</button></div></form>
  <div id="rulelist" class="mem"></div>
</div>
<div id="chat" hidden>
  <div id="msgs"><div class="msg bot">{{ui.chat.intro}}</div></div>
  <form id="ask"><textarea id="say" rows="2" placeholder="{{ui.chat.ph}}"></textarea>
  <button class="primary">{{ui.send}}</button></form>
  <details id="mem"><summary>{{ui.memory}}</summary><div id="memlist"></div></details>
</div>
<div id="toast"></div><div id="tip" hidden></div>
<script>
const LANG = '{{lang}}', I18N = /*I18N*/{};
const T = (k, v = {}) => (I18N[k] || k).replace(/\{(\w+)\}/g, (_, x) => v[x] ?? '');
const LOC = LANG === 'fr' ? 'fr-FR' : 'en-US';
const fd = d => LANG === 'fr' ? d.split('-').reverse().join('/') : d;  // date affichée
let D, shown = 60, open = new Set();
let VIEW = 'groups';  // 'groups' : par libellé ; 'flat' : chronologique
let NE = null;  // opération dont le commentaire est en cours d'édition
let SP = null;  // ventilation en cours : {id, amount, split, lines: [{category, amount}]}, la ligne 0 prend le reste
const $ = s => document.querySelector(s);
const eur = x => x.toLocaleString(LOC, {style: 'currency', currency: 'EUR'});
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(m) { const t = $('#toast'); t.textContent = m; t.classList.add('on'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), 2200); }
let OPTS = '';
function buildFilter() {
  const f = $('#f'), v = f.value;
  f.innerHTML = `<option value="">${T('ui.all_cats')}</option>` + D.paths.map(p =>
    `<option value="${esc(p.path)}">${'\u00a0\u00a0\u00a0\u00a0'.repeat(p.depth)}${esc(p.label.split(':').pop())}</option>`).join('');
  f.value = D.paths.some(p => p.path === v) ? v : '';
}
let LBL = {}, LABEL = {};
function buildOpts() {
  LBL = Object.fromEntries(D.paths.filter(p => p.depth > 0).map(p =>
    [p.path, `${p.label.split(':').pop()}  — ${p.label.split(':').slice(-2, -1)[0]}`]));
  LABEL = Object.fromEntries(D.paths.map(p => [p.path, p.label]));
  RANK = Object.fromEntries(D.paths.map((p, i) => [p.path, i]));
  OPTS = D.paths.filter(p => p.depth > 0).map(p =>
    `<option value="${esc(p.path)}">${'\u00a0\u00a0\u00a0\u00a0'.repeat(p.depth - 1)}${esc(p.label.split(':').pop())}  — ${esc(p.label.split(':').slice(-2, -1)[0])}</option>`).join('');
}
// une liste par ligne avec toutes les catégories rend la page très lente : chaque menu n'a d'abord que
// sa valeur, la liste complète est insérée au premier clic ou focus
function sel(cls, value, extra = '') {
  const txt = value ? LBL[value] || value : extra.includes('data-op') ? T('ui.as_label') : '';
  return `<select class="${cls} lazy" data-v="${esc(value)}" ${extra}><option value="${esc(value)}" selected>${esc(txt)}</option></select>`;
}
function fillSelect(e) {
  const s = e.target.closest && e.target.closest('select.lazy'); if (!s) return;
  const v = s.dataset.v, first = s.options[0].textContent;
  s.innerHTML = (s.hasAttribute('data-op') ? `<option value="">${esc(v ? T('ui.as_label') : first)}</option>` : '') + OPTS;
  s.value = v; s.classList.remove('lazy');
}
document.addEventListener('pointerdown', fillSelect, true);
document.addEventListener('focusin', fillSelect, true);
// recherche : le libellé du regroupement, ou une opération (intitulé complet, montant, date)
const groupHit = (g, q) => g.label.toLowerCase().includes(q) || g.merchant.toLowerCase().includes(q);
const opHit = (o, q) => o.label.toLowerCase().includes(q) || (o.note || '').toLowerCase().includes(q) || o.date.split('-').reverse().join('/').includes(q)
  || eur(o.amount).replace(/\s/g, '').includes(q.replace(/\s/g, '')) || String(Math.abs(o.amount)).includes(q);
// un regroupement trouvé par une de ses opérations s'ouvre tout seul
const closed = new Set();
const isOpen = g => !closed.has(g.key) && (open.has(g.key) || shownOps(g).length < g.ops.length);
// filtres : catégorie (liste) et cases, combinés ; une opération est affichée si elle satisfait tout
const under = (p, node) => p === node || p.startsWith(node + ':');
function criteria() {
  return {q: $('#q').value.trim().toLowerCase(), cat: $('#f').value, du: $('#fDu').value, au: $('#fAu').value,
          todo: $('#fTodo').checked, pair: $('#fPair').checked, check: $('#fCheck').checked};
}
function opOk(g, o, c) {
  return (!c.du || o.date.slice(0, 7) >= c.du) && (!c.au || o.date.slice(0, 7) <= c.au)
    && (!c.q || groupHit(g, c.q) || opHit(o, c.q))
    && (!c.cat || under(o.category, c.cat) || (o.split || []).some(x => under(x.category, c.cat)))
    && (!c.todo || !o.ok) && (!c.pair || o.pair);
}
// tri par colonne : clic sur un en-tête, un second clic inverse l'ordre ; mémorisé pour chaque vue
let SORT = {groups: {k: 'amount', d: -1}, flat: {k: 'date', d: -1}}, RANK = {};
try { Object.assign(SORT, JSON.parse(localStorage.getItem('tri') || '{}')); } catch (e) {}
const cmp = (a, b) => typeof a === 'string' ? a.localeCompare(b, LOC, {sensitivity: 'base'}) : a - b;
const rank = c => RANK[c] ?? 1e9;  // les catégories dans l'ordre de l'arbre
const GKEY = {ok: g => g.ops.filter(o => o.ok).length / g.ops.length, label: g => g.merchant, n: g => g.ops.length,
  amount: g => Math.abs(g.total), cat: g => rank(g.category), origin: g => T('ui.origin.' + g.origin)};
const OKEY = {ok: r => +!!r.o.ok, date: r => r.o.date, label: r => r.o.label, amount: r => Math.abs(r.o.amount),
  cat: r => rank(r.o.origin === 'opération' ? r.o.category : r.g.category)};
function sortBy(list, keys, s) {
  const f = keys[s.k] || Object.values(keys)[0];
  return list.map(x => [f(x), x]).sort((a, b) => s.d * cmp(a[0], b[0])).map(x => x[1]);
}
function th(k, txt, cls = '') {
  const s = SORT[VIEW], on = s.k === k;
  return `<span class="${cls}${on ? ' on' : ''}" data-sort="${k}" title="${T('ui.sort.tip')}">${txt}${on ? (s.d > 0 ? ' ▲' : ' ▼') : ''}</span>`;
}
$('#list').addEventListener('click', e => {
  const h = e.target.closest('[data-sort]'); if (!h) return;
  const k = h.dataset.sort, s = SORT[VIEW];
  SORT[VIEW] = {k, d: s.k === k ? -s.d : ['label', 'cat', 'origin', 'ok'].includes(k) ? 1 : -1};
  try { localStorage.setItem('tri', JSON.stringify(SORT)); } catch (err) {}
  render();
});
function shownOps(g) { const c = criteria(); return g.ops.filter(o => opOk(g, o, c)); }
function filtered() {
  const c = criteria();
  return D.groups.filter(g => (!c.check || g.origin === 'converti' || g.origin === 'défaut' || D.fallbacks.includes(g.category))
    && g.ops.some(o => opOk(g, o, c)));
}
function opRow(o, g) {
  return `
      <div class="op ${o.pair ? 'pair' : ''} ${o.ok ? 'done' : ''}">
        <input type="checkbox" class="ok" data-vok="${o.id}" title="${T('ui.ok.tip')}" ${o.ok ? 'checked' : ''}>
        <span class="d muted">${fd(o.date)}</span>
        <span class="l" data-full="${esc(o.label)}">${esc(o.label)}</span>
        <span class="num ${o.amount > 0 ? 'pos' : ''}">${eur(o.amount)}</span>
        <span class="opc">${sel('cat', o.origin === 'opération' ? o.category : '', `data-op="${o.id}"`)
          .replace(T('ui.as_label'), `${esc((LABEL[g.category] || g.category).split(':').pop())} ${T('ui.label_suffix')}`)}
        <span class="badge ${o.origin}">${T('ui.origin.' + o.origin)}</span>
        <button class="vent" data-vent="${o.id}" title="${T('ui.split.tip')}">${T('ui.split')}</button>
        <button class="vent ${o.note ? 'has' : ''}" data-note="${o.id}" title="${o.note ? T('ui.note.edit') : T('ui.note.add')}">💬</button></span>
      </div>${NE === o.id ? `<div class="noted"><input class="notein" value="${esc(o.note)}" maxlength="500" placeholder="${T('ui.note.ph')}">
        <button class="primary" data-nsave>${T('ui.save')}</button><button data-ncancel>${T('ui.cancel')}</button>${o.note ? `<button data-ndel>${T('ui.delete')}</button>` : ''}</div>`
      : o.note ? `<div class="note" data-note="${o.id}" title="${T('ui.note.click')}">💬 ${esc(o.note)}</div>` : ''}${SP && SP.id === o.id ? spEditor() : o.split ? `<div class="split muted">↳ ${o.split.map(x =>
        `${esc((LABEL[x.category] || x.category).split(':').pop())} ${eur(x.amount)}`).join(' · ')}</div>` : ''}`;
}
// vue chronologique : toutes les opérations filtrées, sans regroupement, les plus récentes d'abord
function renderFlat(gs) {
  const rows = sortBy(gs.flatMap(g => shownOps(g).map(o => ({o, g}))).sort((a, b) => b.o.date.localeCompare(a.o.date)),
    OKEY, SORT.flat), byDate = SORT.flat.k === 'date';
  const all = D.groups.flatMap(g => g.ops), nok = all.filter(o => o.ok).length;
  $('#count').textContent = T('ui.count.flat', {n: rows.length, ok: nok, all: all.length});
  let m = '';
  $('#list').innerHTML = `<div class="flat"><div class="op hd">${th('ok', '✓')}${th('date', T('ui.col.date'), 'd')}`
    + `${th('label', T('ui.col.op'))}${th('amount', T('ui.col.amount'), 'num')}<span class="opc">${th('cat', T('ui.col.cat'), 'hc')}`
    + `<span class="ghost"><span class="badge">${T('ui.origin.modèle')}</span><button class="vent">${T('ui.split')}</button><button class="vent">💬</button></span></span></div>` + rows.slice(0, shown).map(({o, g}) => {
    const head = byDate && o.date.slice(0, 7) !== m ? `<div class="mh">${new Date(o.date.slice(0, 7) + '-01').toLocaleDateString(LOC, {month: 'long', year: 'numeric'})}</div>` : '';
    m = o.date.slice(0, 7);
    return `${head}<div data-key="${esc(g.key)}">${opRow(o, g)}</div>`;
  }).join('') + '</div>';
  $('#more').hidden = rows.length <= shown;
}
function render() {
  if (!D) return;
  $('#expand').hidden = VIEW === 'flat';
  if (VIEW !== 'flat') { const gs = filtered().slice(0, shown); $('#expand').textContent = gs.length && gs.every(isOpen) ? T('ui.collapse') : T('ui.expand'); }
  if (VIEW === 'flat') return renderFlat(filtered());
  const gs = sortBy(filtered().sort((a, b) => Math.abs(b.total) - Math.abs(a.total)), GKEY, SORT.groups);
  const all = D.groups.flatMap(g => g.ops), nok = all.filter(o => o.ok).length;
  $('#count').textContent = T('ui.count.groups', {n: gs.length, ok: nok, all: all.length});
  $('#list').innerHTML = `<div class="row hd"><span></span>${th('ok', '✓')}${th('label', T('ui.col.label'))}`
    + `${th('n', T('ui.col.n'), 'num')}${th('amount', T('ui.col.amount'), 'num')}${th('cat', T('ui.col.cat'))}${th('origin', T('ui.col.origin'))}</div>`
    + gs.slice(0, shown).map(g => `
  <div class="g ${g.ops.some(o => o.pair) ? 'haspair' : ''} ${g.ops.every(o => o.ok) ? 'done' : ''}" data-key="${esc(g.key)}">
    <div class="row">
      <span class="tog">${isOpen(g) ? '▾' : '▸'}</span>
      <input type="checkbox" class="ok" data-gok title="${T('ui.gok.tip')}"
        ${g.ops.every(o => o.ok) ? 'checked' : g.ops.some(o => o.ok) ? 'data-mixed' : ''}>
      <span class="lab"><b>${esc(g.merchant)}<button class="ren" data-ren title="${T('ui.rename.tip')}">✎</button></b><span class="small muted">${esc(g.label)}</span></span>
      <span class="n num muted">${T('ui.ops_n', {n: g.ops.length})}</span>
      <span class="num ${g.total > 0 ? 'pos' : ''}">${eur(g.total)}</span>
      ${sel('cat', g.category, 'data-label')}
      <span class="badge ${g.origin}">${T('ui.origin.' + g.origin)}</span>
    </div>
    ${isOpen(g) ? `<div class="ops">${shownOps(g).length < g.ops.length ? `<div class="small muted">${T('ui.search_hits', {n: shownOps(g).length, all: g.ops.length})}</div>` : ''}${shownOps(g).map(o => opRow(o, g)).join('')}</div>` : ''}
  </div>`).join('');
  $('#more').hidden = gs.length <= shown;
  document.querySelectorAll('[data-mixed]').forEach(x => x.indeterminate = true);
}
async function saveNote(text) {
  const id = NE; NE = null;
  await post('/api/commentaire', {id, text});
  toast(text.trim() ? T('ui.t.note_saved') : T('ui.t.note_deleted'));
  const y = scrollY; await load(); scrollTo(0, y);
}
document.addEventListener('keydown', e => {
  if (!e.target.matches || !e.target.matches('.notein')) return;
  if (e.key === 'Enter') saveNote(e.target.value);
  if (e.key === 'Escape') { NE = null; render(); }
});
function spRest() {
  const r = SP.amount - SP.lines.slice(1).reduce((s, l) => s + (Number(l.amount) || 0), 0);
  return SP.lines[0].amount = Math.round(r * 100) / 100;
}
function spSel(i, v) {
  return `<select class="cat" data-i="${i}"><option value="">${T('ui.category_ph')}</option>${OPTS}</select>`
    .replace(`value="${esc(v)}"`, `value="${esc(v)}" selected`);
}
function spEditor() {
  return `<div class="sped">
    <div class="spl">${spSel(0, SP.lines[0].category)}<span class="num sp-rest">${eur(spRest())}</span><span class="small muted">${T('ui.split.rest')}</span></div>
    ${SP.lines.slice(1).map((l, j) => `<div class="spl">${spSel(j + 1, l.category)}
      <input type="number" step="0.01" data-i="${j + 1}" value="${l.amount || ''}" placeholder="-600,00">
      <button class="sp-del" data-i="${j + 1}" title="${T('ui.split.del')}">✕</button></div>`).join('')}
    <div class="bar"><button class="sp-add">${T('ui.split.add')}</button><button class="primary sp-save">${T('ui.save')}</button>
      <button class="sp-cancel">${T('ui.cancel')}</button>${SP.split ? `<button class="sp-clear">${T('ui.split.clear')}</button>` : ''}</div>
    <p class="help">${T('ui.split.help')}</p></div>`;
}
async function load() { reportStale = true; D = await (await fetch('/api/data')).json();
  $('#errbox').hidden = !D.error && !D.notice; $('#errbox').textContent = D.error ? D.error + ' ' + T('ui.folder.fix') : D.notice || '';
  if (D.error && $('#folderbox').hidden) openFolder(); buildOpts(); buildFilter(); render(); }
async function post(url, body) {
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  return r.json();
}
$('#list').addEventListener('click', async e => {
  const b = e.target;
  if (b.hasAttribute('data-ren')) {
    const g = D.groups.find(g => g.key === b.closest('.g').dataset.key), holder = b.closest('b');
    holder.innerHTML = `<input class="rename" value="${esc(g.merchant)}">`;
    const inp = holder.querySelector('input'); inp.focus(); inp.select();
    let done = false;
    const finish = async save => {
      if (done) return; done = true;
      const v = inp.value.trim();
      if (save && v && v !== g.merchant) {
        await post('/api/marchand', {key: g.key, merchant: v});
        toast(T('ui.t.renamed')); const y = scrollY; await load(); scrollTo(0, y);
      } else render();
    };
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') finish(true); if (e.key === 'Escape') finish(false); });
    inp.addEventListener('blur', () => finish(true));
    return;
  }
  if (b.dataset.note) { NE = b.dataset.note; render(); const i = document.querySelector('.notein'); if (i) { i.focus(); i.select(); } return; }
  if (b.hasAttribute('data-ncancel')) { NE = null; return render(); }
  if (b.hasAttribute('data-nsave') || b.hasAttribute('data-ndel')) return saveNote(b.hasAttribute('data-ndel') ? '' : document.querySelector('.notein').value);
  if (b.dataset.vent) {
    const o = D.groups.flatMap(g => g.ops).find(o => o.id === b.dataset.vent);
    SP = {id: o.id, amount: o.amount, split: !!o.split,
          lines: o.split ? o.split.map(x => ({...x})) : [{category: o.category, amount: o.amount}, {category: '', amount: 0}]};
    return render();
  }
  if (b.classList.contains('sp-add')) { SP.lines.push({category: '', amount: 0}); return render(); }
  if (b.classList.contains('sp-del')) { SP.lines.splice(+b.dataset.i, 1); return render(); }
  if (b.classList.contains('sp-cancel')) { SP = null; return render(); }
  if (b.classList.contains('sp-save') || b.classList.contains('sp-clear')) {
    const clear = b.classList.contains('sp-clear');
    spRest();
    if (!clear && (SP.lines.length < 2 || SP.lines.some(l => !l.category)))
      return toast(T('ui.t.split_need'));
    const r = await post('/api/ventilation', {id: SP.id, parts: clear ? null : SP.lines});
    if (r.ok === false) return toast(r.message);
    toast(clear ? T('ui.t.split_del') : T('ui.t.split_ok'));
    SP = null; const y = scrollY; await load(); scrollTo(0, y); return;
  }
  if (!b.classList.contains('tog')) return;
  const k = e.target.closest('.g').dataset.key;
  const g = D.groups.find(g => g.key === k);
  if (isOpen(g)) { open.delete(k); closed.add(k); } else { closed.delete(k); open.add(k); }
  render();
});
$('#list').addEventListener('change', async e => {
  const s = e.target;
  if (s.closest('.sped')) {
    SP.lines[+s.dataset.i][s.matches('select') ? 'category' : 'amount'] = s.matches('select') ? s.value : Number(s.value) || 0;
    return;
  }
  if (s.matches('input.ok')) {
    const g = D.groups.find(g => g.key === s.closest('[data-key]').dataset.key);
    const ops = s.hasAttribute('data-gok') ? shownOps(g) : g.ops.filter(o => o.id === s.dataset.vok);  // pendant une recherche : seulement les opérations affichées
    const r = await post('/api/valider', {ids: ops.map(o => o.id), ok: s.checked});
    if (r.ok === false) return toast(T('ui.t.save_failed'));
    ops.forEach(o => o.ok = s.checked);
    return render();
  }
  if (!s.matches('select')) return;
  if (s.hasAttribute('data-label')) {
    await post('/api/libelle', {key: s.closest('.g').dataset.key, category: s.value});
    toast(T('ui.t.label_saved'));
  } else {
    await post('/api/operation', {id: s.dataset.op, category: s.value || null});
    toast(s.value ? T('ui.t.op_saved') : T('ui.t.op_reset'));
  }
  const y = scrollY; await load(); scrollTo(0, y);
});
$('#list').addEventListener('input', e => {
  const s = e.target; if (!s.closest('.sped') || !s.matches('input')) return;
  SP.lines[+s.dataset.i].amount = Number(s.value) || 0;
  s.closest('.sped').querySelector('.sp-rest').textContent = eur(spRest());
});
// bulle immédiate avec le texte complet des libellés tronqués
document.addEventListener('mouseover', e => {
  const el = e.target.closest('[data-full]'), tip = $('#tip');
  if (!el || el.scrollWidth <= el.clientWidth) return tip.hidden = true;
  tip.textContent = el.dataset.full; tip.hidden = false;
  const r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.max(16, Math.min(r.left, innerWidth - w - 16)) + 'px';
  tip.style.top = (r.bottom + 6 + h > innerHeight ? r.top - h - 6 : r.bottom + 6) + 'px';
});
addEventListener('scroll', () => $('#tip').hidden = true, {passive: true});
$('#expand').addEventListener('click', () => {
  const gs = filtered().slice(0, shown), all = gs.every(isOpen);
  for (const g of gs) {
    if (all) { open.delete(g.key); closed.add(g.key); } else { closed.delete(g.key); open.add(g.key); }
  }
  render();
});
$('#view').addEventListener('click', e => {
  const v = e.target.dataset.v; if (!v) return;
  VIEW = v; shown = 60;
  document.querySelectorAll('#view button').forEach(b => b.classList.toggle('on', b === e.target));
  try { localStorage.setItem('vue', v); } catch (err) {}
  render();
});
try { const v = localStorage.getItem('vue'); if (v === 'flat') document.querySelector('#view [data-v=flat]').click(); } catch (err) {}
$('#q').addEventListener('input', () => { shown = 60; closed.clear(); render(); });
['#f', '#fTodo', '#fCheck', '#fPair', '#fDu', '#fAu'].forEach(s => $(s).addEventListener('change', () => { shown = 60; closed.clear(); render(); }));
$('#more').addEventListener('click', () => { shown += 60; render(); });
// ---- onglet Rapport : calculé à la volée sur la période ; commentaire du LLM à la demande
let reportStale = true, analysis = null;  // analysis : {period, html} de la dernière analyse du LLM
const rPeriod = () => $('#rDu').value + '|' + $('#rAu').value;
try { const p = JSON.parse(localStorage.getItem('periodeRapport') || '{}'); $('#rDu').value = p.du || ''; $('#rAu').value = p.au || ''; } catch (e) {}
['#rDu', '#rAu'].forEach(s => $(s).addEventListener('change', () => {
  try { localStorage.setItem('periodeRapport', JSON.stringify({du: $('#rDu').value, au: $('#rAu').value})); } catch (e) {}
}));
function fitFrame() { const f = $('#rframe'); f.style.height = f.contentDocument.documentElement.scrollHeight + 20 + 'px'; }
function showAnalysis() {
  const doc = $('#rframe').contentDocument, slot = doc && doc.getElementById('analyse');
  if (!slot || !analysis || analysis.period !== rPeriod()) return;
  slot.innerHTML = analysis.html; fitFrame();
}
async function loadReport() {
  const go = $('#rGo'); go.disabled = true; $('#rState').textContent = T('ui.computing');
  const r = await post('/api/rapport', {du: $('#rDu').value, au: $('#rAu').value});
  go.disabled = false; $('#rState').textContent = '';
  if (!r.ok) return toast(r.message);
  reportStale = false;
  const f = $('#rframe');
  f.onload = () => { fitFrame(); showAnalysis(); };  // une analyse de la même période est réinsérée
  f.srcdoc = r.html;
}
async function loadAnalysis() {
  if (reportStale) await loadReport();
  const llm = $('#rLLM'), period = rPeriod(); llm.disabled = true;
  $('#rState').textContent = T('ui.analysing');
  const doc = $('#rframe').contentDocument, slot = doc && doc.getElementById('analyse');
  if (slot) { slot.innerHTML = `<section class="comment"><h2>${T('ui.analysis')}</h2><p class="muted">${T('ui.analysing')}</p></section>`; fitFrame(); }
  const r = await post('/api/analyse', {du: $('#rDu').value, au: $('#rAu').value});
  llm.disabled = false; $('#rState').textContent = '';
  if (!r.ok) { if (slot) slot.innerHTML = ''; return toast(r.message); }
  analysis = {period, html: r.html}; showAnalysis();
}
$('#rGo').addEventListener('click', () => loadReport());
$('#rLLM').addEventListener('click', loadAnalysis);
$('#rPDF').addEventListener('click', () => { const w = $('#rframe').contentWindow; if (w && w.document.body) w.print(); });
// ---- onglet Catégories
let tab = 'ops', moving = null;
document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => {
  tab = b.dataset.tab;
  document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('on', x === b));
  $('#ops').hidden = $('#opsbar').hidden = tab !== 'ops';
  $('#tree').hidden = $('#treehelp').hidden = tab !== 'tree';
  $('#chat').hidden = tab !== 'chat';
  $('#rules').hidden = tab !== 'rules';
  $('#report').hidden = tab !== 'report';
  if (tab === 'report' && reportStale) loadReport(false);
  if (tab === 'chat' || tab === 'rules') loadMem();
  if (tab === 'rules') document.querySelectorAll('.rcat').forEach(s => { const v = s.value; s.innerHTML = `<option value="">${T('ui.category_ph')}</option>` + OPTS; s.value = v; });
  history.replaceState(null, '', {tree: '#categories', chat: '#assistant', rules: '#regles', report: '#rapport'}[tab] || '#');
  tab === 'tree' ? renderTree() : render();
}));
function subtreeStats() {
  const st = {};
  const add = (cat, n, amt) => { const parts = cat.split(':');
    for (let i = 1; i <= parts.length; i++) { const p = parts.slice(0, i).join(':');
      st[p] = st[p] || {labels: 0, amount: 0}; st[p].labels += n; st[p].amount += amt; } };
  for (const g of D.groups) { add(g.category, 1, 0); for (const o of g.ops) add(o.category, 0, o.amount); }
  return st;
}
function renderTree() {
  const st = subtreeStats();
  $('#tree').innerHTML = D.paths.map(p => {
    const s = st[p.path] || {labels: 0, amount: 0}, name = p.label.split(':').pop();
    const acts = moving === p.path
      ? `<select class="dest"><option value="">${T('ui.tree.move_to')}</option>${D.paths.filter(d =>
          d.path !== p.path.split(':').slice(0, -1).join(':') && d.path !== p.path && !d.path.startsWith(p.path + ':'))
          .map(d => `<option value="${esc(d.path)}">${'\u00a0\u00a0'.repeat(d.depth)}${esc(d.label.split(':').pop())}</option>`).join('')}</select>
         <button data-a="annuler">${T('ui.cancel')}</button>`
      : `<button data-a="ajouter">${T('ui.tree.add')}</button>${p.locked ? '' :
         `<button data-a="renommer">${T('ui.tree.rename')}</button><button data-a="deplacer">${T('ui.tree.move')}</button><button data-a="supprimer">${T('ui.delete')}</button>`}`;
    return `<div class="node ${p.depth ? '' : 'root'}" data-path="${esc(p.path)}" style="padding-left:${12 + 22 * p.depth}px">
      <span class="nm">${esc(name)}</span>
      <span class="n num muted small">${s.labels ? T('ui.tree.labels', {n: s.labels}) : ''}</span>
      <span class="num small ${s.amount > 0 ? 'pos' : ''}">${s.amount ? eur(s.amount) : ''}</span>
      <span class="acts">${acts}</span></div>`;
  }).join('');
}
async function treeOp(body) {
  const r = await post('/api/arbre', body);
  toast(r.message); moving = null;
  if (r.ok) await load();
  renderTree();
}
$('#tree').addEventListener('click', e => {
  const a = e.target.dataset.a; if (!a) return;
  const path = e.target.closest('.node').dataset.path, name = (LABEL[path] || path).split(':').pop();
  if (a === 'ajouter') { const n = prompt(T('ui.tree.new', {name})); if (n) treeOp({op: 'ajouter', path, name: n}); }
  if (a === 'renommer') { const n = prompt(T('ui.tree.newname', {name}), name); if (n && n !== name) treeOp({op: 'renommer', path, name: n}); }
  if (a === 'deplacer') { moving = path; renderTree(); }
  if (a === 'annuler') { moving = null; renderTree(); }
  if (a === 'supprimer' && confirm(T('ui.tree.confirm_del', {name})))
    treeOp({op: 'supprimer', path});
});
$('#tree').addEventListener('change', e => {
  if (e.target.matches('select.dest') && e.target.value)
    treeOp({op: 'deplacer', path: e.target.closest('.node').dataset.path, dest: e.target.value});
});
// ---- onglet Assistant
const convo = [];
const path = p => esc((LABEL[p] || p || '').replace(/:/g, ' › '));
function describe(a) {
  if (a.type === 'regrouper') return T('ui.a.group', {motif: esc(a.motif), nom: esc(a.nom)});
  if (a.type === 'categoriser') return T('ui.a.classify', {motif: esc(a.motif), cat: path(a.categorie)});
  if (a.type === 'creer_categorie') return T('ui.a.create', {cat: path(a.parent) + ' › ' + esc(a.nom)});
  return T('ui.a.note', {texte: esc(a.texte)});
}
function actCard(a, i) {
  const p = a.apercu || {};
  const eff = p.libelles !== undefined ? `<div class="ex">${T('ui.a.effect', {l: p.libelles, o: p.operations, t: eur(p.total)})}${
    p.exemples && p.exemples.length ? ' — ' + p.exemples.map(esc).join(' · ') : ''}</div>` : '';
  return `<div class="act" data-i="${i}">${describe(a)}${eff}${p.erreur ? `<div class="err">⚠ ${esc(p.erreur)}</div>` : ''}
    <div class="btns"><button data-do="apply">${T('ui.apply')}</button><button data-do="skip">${T('ui.skip')}</button></div></div>`;
}
function addMsg(role, html) {
  const d = document.createElement('div'); d.className = 'msg ' + role; d.innerHTML = html;
  $('#msgs').append(d); d.scrollIntoView({block: 'end', behavior: 'smooth'}); return d;
}
$('#say').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#ask').requestSubmit(); } });
$('#ask').addEventListener('submit', async e => {
  e.preventDefault();
  const text = $('#say').value.trim(); if (!text) return;
  $('#say').value = ''; convo.push({role: 'user', content: text}); addMsg('user', esc(text));
  const w = addMsg('bot wait', T('ui.thinking'));
  const r = await post('/api/assistant', {messages: convo});
  w.remove();
  if (!r.ok) { addMsg('bot', '⚠ ' + esc(r.message)); convo.pop(); return; }
  convo.push({role: 'assistant', content: r.reponse});
  const m = addMsg('bot', esc(r.reponse) + r.actions.map(actCard).join(''));
  m._actions = r.actions;
});
$('#msgs').addEventListener('click', async e => {
  const b = e.target.dataset.do; if (!b) return;
  const card = e.target.closest('.act'), a = card.closest('.msg')._actions[+card.dataset.i];
  if (b === 'skip') { card.remove(); return; }
  e.target.disabled = true;
  const r = await post('/api/assistant/appliquer', {action: a});
  toast(r.message);
  if (r.ok) {
    card.classList.add('done'); card.querySelector('.btns').innerHTML = `<span class="small">${T('ui.applied')}</span>`;
    convo.push({role: 'assistant', content: T('ui.applied_log', {x: card.textContent.split('\n')[0].trim()})});
    await load(); loadMem();
  } else e.target.disabled = false;
});
// ---- onglet Règles (sans LLM)
const variants = m => m.split('|').map(x => x.trim().toUpperCase()).filter(Boolean);
function ruleMatches(motif) {
  const vs = variants(motif);
  return vs.length ? D.groups.filter(g => vs.some(v => g.label.includes(v) || g.ops.some(o => o.label.toUpperCase().includes(v)))) : [];
}
function rulePreview(f) {
  const box = f.querySelector('.prev'); if (!box) return;
  const motif = f.motif.value; if (!motif.trim()) { box.innerHTML = ''; return; }
  const m = ruleMatches(motif).sort((a, b) => Math.abs(b.total) - Math.abs(a.total));
  if (!m.length) { box.innerHTML = `<span class="warn">${T('ui.r.no_match')}</span>`; return; }
  const n = m.reduce((s, g) => s + g.ops.length, 0), tot = m.reduce((s, g) => s + g.total, 0);
  box.innerHTML = `${T('ui.a.effect', {l: m.length, o: n, t: eur(tot)})}<ul>${m.slice(0, 8).map(g =>
    `<li>${esc(g.label)} — ${path(g.category)}</li>`).join('')}${m.length > 8 ? '<li>…</li>' : ''}</ul>`;
}
$('#rules').addEventListener('input', e => { const f = e.target.closest('.rf'); if (f && e.target.name === 'motif') rulePreview(f); });
$('#rules').addEventListener('submit', async e => {
  e.preventDefault();
  const f = e.target, type = f.dataset.type, a = {type};
  if (type === 'consigne') { a.texte = f.texte.value.trim(); if (!a.texte) return; }
  else {
    a.motif = variants(f.motif.value).join('|');
    if (!a.motif) return toast(T('ui.t.empty_pattern'));
    if (type === 'regrouper') { a.nom = f.nom.value.trim() || a.motif.split('|')[0]; }
    else { a.categorie = f.categorie.value; if (!a.categorie) return toast(T('ui.t.pick_cat')); }
  }
  const r = await post('/api/assistant/appliquer', {action: a});
  toast(r.message);
  if (r.ok) { f.reset(); rulePreview(f); await load(); loadMem(); }
});
async function loadMem() {
  const m = await (await fetch('/api/memoire')).json();
  const sec = (title, kind, items, fmt) => items.length ? `<h4>${title}</h4>` + items.map((x, i) =>
    `<div class="mi"><span>${fmt(x)}</span><button data-kind="${kind}" data-i="${i}">${T('ui.forget')}</button></div>`).join('') : '';
  $('#memlist').innerHTML = $('#rulelist').innerHTML = (sec(T('ui.m.groups'), 'regroupements', m.regroupements, x => `« ${esc(x.motif)} » → ${esc(x.nom)}`)
    + sec(T('ui.m.rules'), 'regles', m.regles, x => `« ${esc(x.motif)} » → ${path(x.categorie)}`)
    + sec(T('ui.m.notes'), 'consignes', m.consignes, esc)) || `<p class="muted small">${T('ui.m.empty')}</p>`;
}
document.addEventListener('click', async e => {
  const k = e.target.dataset.kind; if (!k || !e.target.closest('#memlist, #rulelist')) return;
  const r = await post('/api/memoire/oublier', {kind: k, index: +e.target.dataset.i});
  toast(r.message); await load(); loadMem();
});
$('#lang').value = LANG;
// ---- mise à jour (import des relevés) depuis la page
async function pollImport() {
  const r = await (await fetch('/api/import')).json();
  $('#jobbox').hidden = false;
  $('#jobtitle').textContent = r.running ? T('ui.import.running') : r.ok ? T('ui.import.done') : T('ui.import.failed');
  $('#joblog').textContent = r.lines.join('\n');
  $('#bImport').disabled = r.running; $('#jobclose').hidden = r.running;
  if (r.running) return setTimeout(pollImport, 1500);
  if (r.ok) { const y = scrollY; await load(); scrollTo(0, y); }
}
$('#bImport').addEventListener('click', async () => { await post('/api/import', {}); pollImport(); });
$('#jobclose').addEventListener('click', () => $('#jobbox').hidden = true);
fetch('/api/import').then(r => r.json()).then(r => { if (r.running) pollImport(); });
// ---- dossier des données
async function openFolder() {
  const r = await (await fetch('/api/dossier')).json();
  $('#folderpath').value = r.path; $('#foldermsg').textContent = ''; $('#folderbox').hidden = false;
}
$('#bFolder').addEventListener('click', openFolder);
$('#fClose').addEventListener('click', () => $('#folderbox').hidden = true);
$('#fBrowse').addEventListener('click', async e => {
  e.target.disabled = true;
  const r = await post('/api/dossier/parcourir', {});
  e.target.disabled = false;
  if (r.path) $('#folderpath').value = r.path;
});
async function switchFolder(url, body) {
  const r = await post(url, body);
  $('#foldermsg').textContent = r.message;
  if (!r.ok) return;
  $('#foldermsg').textContent += ' — ' + T('ui.folder.restarting');
  // l'application redémarre sur le nouveau dossier : on attend qu'elle réponde, puis on recharge
  const wait = async () => { try { await fetch('/api/ping'); location.reload(); } catch (e) { setTimeout(wait, 700); } };
  setTimeout(wait, 1500);
}
$('#fApply').addEventListener('click', () => switchFolder('/api/dossier', {path: $('#folderpath').value.trim()}));
$('#fDemo').addEventListener('click', () => switchFolder('/api/dossier/exemple', {}));
// modèle du LLM : la liste des modèles installés dans LM Studio (sans attendre : LM Studio peut démarrer)
async function loadModels() {
  const r = await (await fetch('/api/modeles')).json(), s = $('#model');
  if (r.error || !r.models.length) { s.hidden = true; return; }
  const ids = r.models.map(m => m.id);
  s.innerHTML = (r.current ? '' : `<option value="">${T('ui.model.choose')}</option>`)
    + (r.current && !ids.includes(r.current) ? `<option value="${esc(r.current)}">${esc(r.current)} (${T('ui.model.missing')})</option>` : '')
    + r.models.map(m => `<option value="${esc(m.id)}">${esc(m.id)}${m.loaded ? ' ●' : ''}</option>`).join('');
  s.value = r.current; s.disabled = !!r.fixed; s.hidden = false;
}
$('#model').addEventListener('change', async e => {
  const r = await post('/api/modele', {model: e.target.value});
  if (r.ok) toast(T('ui.t.model', {m: e.target.value})); else { toast(r.message); loadModels(); }
});
loadModels().catch(() => {});
$('#lang').addEventListener('change', async e => { await post('/api/langue', {lang: e.target.value}); location.reload(); });
load().then(() => { const t = {'#categories': 'tree', '#assistant': 'chat', '#regles': 'rules', '#rapport': 'report'}[location.hash];
  if (t) document.querySelector(`[data-tab=${t}]`).click(); });
</script></main></body></html>"""


def render_page():
    """La page dans la langue choisie : textes {{clé}} remplacés, catalogue de l'interface pour le JavaScript."""
    cat = i18n.catalog("ui.")
    html = PAGE.replace("/*I18N*/{}", json.dumps(cat, ensure_ascii=False).replace("</", "<\\/"))
    html = html.replace("{{lang}}", i18n.LANG).replace("{{app.name}}", escape(tr("app.name")))
    return re.sub(r"\{\{(ui\.[\w.]+)\}\}", lambda m: cat.get(m.group(1), m.group(1)), html)


def model_list():
    """Modèles installés dans LM Studio et modèle en cours, pour la liste de l'en-tête de la page."""
    try:
        ensure_server()
        ms = llm.models()
    except PipelineError as e:
        return {"models": [], "current": "", "error": str(e)}
    try:
        current = llm.model()
    except PipelineError:
        current = ""  # plusieurs modèles et aucun choisi : la page invite à choisir
    return {"models": ms, "current": current, "fixed": bool(llm.LLM_MODEL)}


def restart():
    """Relance l'application (nouveau dossier des données) ; la page se recharge d'elle-même."""
    args = [a for a in sys.argv if a != "--no-browser"] + ["--no-browser"]
    os.execv(sys.executable, [sys.executable] + args)


def already_running(url):
    try:
        return "calepin" in urllib.request.urlopen(url + "api/ping", timeout=2).read().decode()
    except (OSError, ValueError):
        return False


def main():
    url = f"http://127.0.0.1:{PORT}/"
    if already_running(url):  # déjà lancée : on rouvre simplement la page
        print(tr("srv.already", url=url))
        webbrowser.open(url)
        return
    Handler.state = State()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(tr("srv.folder", path=BASE_DIR), flush=True)
    print(tr("srv.running", url=url), flush=True)
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
