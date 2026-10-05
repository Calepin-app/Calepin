"""Assistant de catégorisation (onglet « Assistant » de la page de révision).

L'utilisateur écrit en langage naturel ; le LLM local voit les libellés concernés et propose des
actions (regrouper, catégoriser, créer une catégorie, retenir une consigne). Chaque action est
prévisualisée puis appliquée à la demande ; appliquée, elle devient une règle permanente.
"""
import csv
import io
import json
import re
from collections import Counter

import arbre
from categorize import load_groups, load_notes, matches, normalize
from config import CATEGORIES_FILE, CORRECTIONS_FILE, GROUPS_FILE, NOTES_FILE
from llm import chat, ensure_server
from i18n import money, tr
from taxonomy import leaf

MAX_LABELS = 40
HISTORY = 6

# Consignes du LLM : locales/<langue>.json, clé prompt.assistant ({paths}, {notes}, {labels})

SCHEMA = {
    "type": "object",
    "properties": {
        "reponse": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["regrouper", "categoriser", "creer_categorie", "consigne"]},
                    "motif": {"type": ["string", "null"]},
                    "nom": {"type": ["string", "null"]},
                    "categorie": {"type": ["string", "null"]},
                    "parent": {"type": ["string", "null"]},
                    "texte": {"type": ["string", "null"]},
                },
                "required": ["type", "motif", "nom", "categorie", "parent", "texte"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reponse", "actions"],
    "additionalProperties": False,
}

STOP = {"LES", "DES", "UNE", "POUR", "DANS", "AVEC", "SONT", "QUI", "QUE", "CES", "SUR", "PAR", "TOUS",
        "TOUTES", "EST", "PAS", "MAIS", "MES", "MON", "LEUR", "ELLES", "ILS", "CELA", "FAUT", "COMME",
        "CATEGORIE", "CATÉGORIE", "LIBELLE", "LIBELLÉ", "LIBELLÉS", "OPERATIONS", "OPÉRATIONS", "AUSSI", "MÊME",
        "THE", "AND", "ARE", "ALL", "FOR", "WITH", "THIS", "THAT", "THESE", "FROM", "INTO", "SHOULD", "MY",
        "CATEGORY", "LABEL", "LABELS", "TRANSACTIONS", "ALSO", "SAME", "NOT", "BUT"}


def relevant(groups, messages):
    """Libellés à montrer au LLM : ceux qui contiennent un mot des derniers messages, sinon les plus gros."""
    text = " ".join(m["content"] for m in messages[-3:] if m["role"] == "user").upper()
    words = {w for w in re.findall(r"[A-ZÀ-Ü0-9*.'-]{3,}", text) if w not in STOP}
    hits = [g for g in groups if any(w in g["label"] or w in g["merchant"].upper() for w in words)]
    rest = [g for g in sorted(groups, key=lambda g: -abs(g["total"])) if g not in hits]
    return (sorted(hits, key=lambda g: -abs(g["total"])) + rest)[:MAX_LABELS]


def converse(state, messages):
    ensure_server()
    data = state.data()
    labels = "\n".join(f"{g['label']} | {g['merchant']} | {state.tax.label(g['category'])} | {len(g['ops'])} | {money(g['total'])}"
                       for g in relevant(data["groups"], messages))
    notes = load_notes()
    system = tr("prompt.assistant").format(paths="\n".join(state.tax.label(p) for p in state.tax.assignable()), labels=labels,
                           notes=(tr("asst.notes_head") + "\n" + "\n".join(f"- {n}" for n in notes) + "\n\n") if notes else "")
    history = "\n\n".join(f"{tr('asst.user') if m['role'] == 'user' else tr('asst.assistant')}: {m['content']}"
                          for m in messages[-HISTORY:])
    for temperature in (0.2, 0.0):
        res = chat(system, history, schema=SCHEMA, temperature=temperature)
        if not any(malformed(a) for a in res.get("actions", [])):
            break
    actions = [a for a in (clean(state, a) for a in res.get("actions", []) if not malformed(a)) if a]
    for a in actions:
        a["apercu"] = preview(state, data["groups"], a)
    return {"reponse": res.get("reponse", ""), "actions": actions}


def malformed(a):
    """Le modèle glisse parfois du JSON dans une valeur texte : on écarte ces actions."""
    return any(isinstance(v, str) and re.search(r'[{}"]|\'\s*[,}]', v) for v in a.values())


def clean(state, a):
    t = a.get("type")
    if t in ("regrouper", "categoriser") and not (a.get("motif") or "").strip():
        return None
    out = {"type": t}
    if a.get("motif"):
        alts = [normalize(m) or m.strip().upper() for m in a["motif"].split("|") if m.strip()]
        out["motif"] = "|".join(dict.fromkeys(alts))
    if t == "regrouper":
        out["nom"] = (a.get("nom") or out["motif"].split("|")[0].title()).strip()
    elif t == "categoriser":
        out["categorie"] = state.tax.resolve(a.get("categorie") or "") or (a.get("categorie") or "")
    elif t == "creer_categorie":
        par = state.tax.resolve(a.get("parent") or "") or (a.get("parent") or "")
        if not a.get("nom"):
            return None
        out.update(parent=par, nom=a["nom"].strip())
    elif t == "consigne":
        if not (a.get("texte") or "").strip():
            return None
        out["texte"] = a["texte"].strip()
    else:
        return None
    return out


def matching(groups, motif):
    """Libellés dont au moins une opération contient le motif (libellé nettoyé ou intitulé complet)."""
    return [g for g in groups if matches(motif, g["label"]) or any(matches(motif, o["label"]) for o in g["ops"])]


def preview(state, groups, a):
    """Effet de l'action, calculé sans rien modifier."""
    t = a["type"]
    if t in ("regrouper", "categoriser"):
        m = matching(groups, a["motif"])
        info = {"libelles": len(m), "operations": sum(len(g["ops"]) for g in m),
                "total": round(sum(g["total"] for g in m), 2), "exemples": [g["label"] for g in m[:6]]}
        if t == "categoriser" and a["categorie"] not in state.tax.paths:
            info["erreur"] = tr("asst.cat_missing")
        if not m:
            info["erreur"] = tr("asst.no_match")
        return info
    if t == "creer_categorie":
        path = f"{a['parent']}:{a['nom']}"
        if a["parent"] not in state.tax.paths:
            return {"erreur": tr("asst.no_parent")}
        return {"erreur": tr("asst.exists")} if path in state.tax.paths else {"chemin": state.tax.label(path)}
    return {}


def save_cache(cache):
    CATEGORIES_FILE.write_text(json.dumps(cache, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def apply(state, a):
    """Applique une action (déjà nettoyée) et renvoie un message."""
    t = a.get("type")
    groups = state.data()["groups"]
    if t == "regrouper":
        motif, nom = a["motif"], a["nom"].strip()
        m = matching(groups, motif)
        if not m:
            raise arbre.TreeError(tr("asst.no_match"))
        rules = [g for g in load_groups() if g["motif"] != motif]
        GROUPS_FILE.write_text(json.dumps([{"motif": motif, "nom": nom}] + rules, indent=1, ensure_ascii=False), encoding="utf-8")
        # le libellé fusionné hérite de la catégorie majoritaire (en nombre d'opérations) de chaque sens
        for sens in ("D", "C"):
            part = [g for g in m if g["key"][0] == sens]
            if not part:
                continue
            votes = Counter()
            for g in part:
                votes[g["category"]] += len(g["ops"])
            state.cache[f"{sens}|{nom.upper()}"] = {
                "category": votes.most_common(1)[0][0], "merchant": nom,
                "manual": any(g["origin"] == "manuel" for g in part)}
        save_cache(state.cache)
        return tr("asst.grouped", n=len(m), name=nom)
    if t == "categoriser":
        motif, path = a["motif"], state.tax.resolve(a["categorie"])
        if not path or ":" not in path:
            raise arbre.TreeError(tr("asst.unknown_cat"))
        m = matching(groups, motif)
        # libellé entièrement concerné : corrigé à la main ; sinon la règle ne vise que ses opérations concernées
        for g in m:
            if not (matches(motif, g["label"]) or all(matches(motif, o["label"]) for o in g["ops"])):
                continue
            e = state.cache.setdefault(g["key"], {"merchant": g["merchant"]})
            e.update(category=path, manual=True)
            e.pop("migrated", None)
        save_cache(state.cache)
        existing = CORRECTIONS_FILE.read_text(encoding="utf-8") if CORRECTIONS_FILE.exists() else ""
        buf = io.StringIO()
        csv.writer(buf, delimiter=";", lineterminator="\n").writerow([motif, path])
        CORRECTIONS_FILE.write_text(existing.rstrip("\n") + "\n" + buf.getvalue(), encoding="utf-8")
        return tr("asst.categorized", n=len(m), cat=leaf(path))
    if t == "creer_categorie":
        arbre.add(a["parent"], a["nom"])
        return tr("asst.created", name=a["nom"])
    if t == "consigne":
        notes = load_notes()
        if a["texte"] not in notes:
            NOTES_FILE.write_text("\n".join(notes + [a["texte"]]) + "\n", encoding="utf-8")
        return tr("asst.noted")
    raise arbre.TreeError(tr("asst.unknown_action"))


def memory(tax):
    rules = []
    if CORRECTIONS_FILE.exists():
        for line in CORRECTIONS_FILE.read_text(encoding="utf-8").splitlines():
            row = next(csv.reader([line], delimiter=";"), [])
            if len(row) >= 2 and row[0].strip() and not line.startswith("#"):
                rules.append({"motif": row[0], "categorie": tax.label(tax.resolve(row[1])) if tax.resolve(row[1]) else f"{row[1]} ({tr('asst.unknown')})"})
    return {"regroupements": load_groups(), "regles": rules, "consignes": load_notes()}


def forget(kind, index):
    if kind == "regroupements":
        g = load_groups()
        del g[index]
        GROUPS_FILE.write_text(json.dumps(g, indent=1, ensure_ascii=False), encoding="utf-8")
    elif kind == "consignes":
        n = load_notes()
        del n[index]
        NOTES_FILE.write_text("\n".join(n) + ("\n" if n else ""), encoding="utf-8")
    elif kind == "regles":
        lines = CORRECTIONS_FILE.read_text(encoding="utf-8").splitlines()
        idx = [i for i, l in enumerate(lines)
               if not l.startswith("#") and len(next(csv.reader([l], delimiter=";"), [])) >= 2
               and l.split(";")[0].strip()]
        del lines[idx[index]]
        CORRECTIONS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
