"""Catégorisation des libellés par le LLM local, avec cache et corrections manuelles."""
import csv
import json
from collections import Counter
import re

from config import CATEGORIES_FILE, CORRECTIONS_FILE, GROUPS_FILE, NOTES_FILE, OVERRIDES_FILE
from llm import PipelineError, chat
from i18n import tr
from taxonomy import load

BATCH = 40

# Consignes du LLM : locales/<langue>.json, clé prompt.categorize ({notes} puis {paths})


# Catégories de la première version (liste à un niveau) -> chemin dans l'arbre par défaut.
# Les entrées converties sont marquées « migrated » et recatégorisées finement avec --affiner.
LEGACY = {
    "Courses alimentaires": "Dépenses:Alimentation:Courses",
    "Restaurants & cafés": "Dépenses:Alimentation:Restaurants & cafés",
    "Logement": "Dépenses:Logement",
    "Énergie & eau": "Dépenses:Logement:Énergie & eau",
    "Télécom & internet": "Dépenses:Abonnements:Téléphone & internet",
    "Transports": "Dépenses:Transports",
    "Voiture": "Dépenses:Transports",
    "Santé": "Dépenses:Santé",
    "Assurances": "Dépenses:Finances:Autres assurances",
    "Abonnements numériques": "Dépenses:Abonnements:Streaming & logiciels",
    "Loisirs & sorties": "Dépenses:Loisirs",
    "Voyages": "Dépenses:Loisirs:Voyages & hébergement",
    "Shopping": "Dépenses:Achats",
    "Maison & équipement": "Dépenses:Achats:Maison & déco",
    "Enfants & éducation": "Dépenses:Famille",
    "Impôts & taxes": "Dépenses:Finances:Impôts & taxes",
    "Frais bancaires": "Dépenses:Finances:Frais bancaires",
    "Dons": "Dépenses:Finances:Dons",
    "Retraits espèces": "Dépenses:Retraits espèces",
    "Revenus": "Revenus:Autres revenus",
    "Remboursements": "Dépenses:Divers",
    "Épargne & placements": "Actif:Épargne & placements",
    "Transferts internes": "Hors budget:Transferts internes",
    "Autres": "Dépenses:Divers",
}


def migrate(cache, valid):
    n = 0
    for v in cache.values():
        new = LEGACY.get(v.get("category"))
        if v.get("category") not in valid and new in valid:
            v["category"], v["migrated"] = new, True
            n += 1
    return n


def load_cache(tax, status=lambda m: None):
    """Cache libellé -> catégorie, converti depuis l'ancienne liste et aligné sur categories.txt."""
    cache = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8")) if CATEGORIES_FILE.exists() else {}
    if (n := migrate(cache, set(tax.assignable()))):
        CATEGORIES_FILE.write_text(json.dumps(cache, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        status(tr("cat.migrated", n=n))
    return reconcile(cache, tax, status)


def reconcile(cache, tax, status=lambda m: None):
    """Suit les changements de categories.txt : une catégorie déplacée est retrouvée par son nom ;
    une catégorie renommée ou supprimée libère ses libellés, qui repassent par le LLM."""
    moved, dropped, out = 0, 0, {}
    for k, v in cache.items():
        path = tax.remap(v.get("category"))
        if path is None:
            dropped += 1
            continue
        if path != v["category"]:
            v = {**v, "category": path}
            moved += 1
        out[k] = v
    if moved or dropped:
        CATEGORIES_FILE.write_text(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        status(tr("cat.reconciled", moved=moved, dropped=dropped))
    return out


def schema(tax):
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "category": {"type": "string", "enum": [tax.label(p) for p in tax.assignable()]},
                        "merchant": {"type": "string"},
                    },
                    "required": ["id", "category", "merchant"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


DATES = re.compile(r"\b\d{1,2}[/.-]\d{1,2}([/.-]\d{2,4})?\b")
NOISE = re.compile(
    r"\b[A-Z]*\d[A-Z0-9]{5,}\b"                # références, n° de carte ou de mandat
    r"|\bX{2,}\d*\b"
    r"|(?<=\s)\d+([.,]\d+)?(\s*(EUR|€))?(?=\s|$)"  # nombres isolés (montants, n° d'arrondissement)
)


def normalize(label):
    s = NOISE.sub(" ", DATES.sub(" ", " " + label.upper()) + " ")
    return " ".join(s.split())[:80]


def matches(motif, text):
    """Motif simple ou variantes séparées par « | » (AMAZON|AMZN), casse ignorée."""
    return any(m.strip() and m.strip().upper() in text.upper() for m in motif.split("|"))


def load_groups():
    return json.loads(GROUPS_FILE.read_text(encoding="utf-8")) if GROUPS_FILE.exists() else []


def load_notes():
    return [l.strip() for l in NOTES_FILE.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if NOTES_FILE.exists() else []


def notes_block():
    notes = load_notes()
    return (tr("cat.notes_head") + "\n" + "\n".join(f"- {n}" for n in notes) + "\n") if notes else ""


_groups = {"mtime": None, "list": []}


def regroup(norm, raw=""):
    """Applique les regroupements : un libellé contenant le motif devient le nom du marchand."""
    mtime = GROUPS_FILE.stat().st_mtime if GROUPS_FILE.exists() else None
    if mtime != _groups["mtime"]:
        _groups.update(mtime=mtime, list=load_groups())
    for g in _groups["list"]:
        if matches(g["motif"], norm + " " + raw):  # libellé complet : toutes ses lignes, pas seulement le début
            return g["nom"].upper()
    return norm


def key(t):
    return f"{'C' if t['amount'] > 0 else 'D'}|{regroup(normalize(t['label']), t['label'])}"


def inherit(tx, cache):
    """Un regroupement ajouté à la main dans regroupements.json crée un libellé encore inconnu : il hérite
    de la catégorie majoritaire (en opérations) de ses libellés d'origine, manuelle si l'un l'était.
    Renvoie True si le cache a changé."""
    names = {g["nom"].upper(): g["nom"] for g in load_groups()}
    votes = {}
    for t in tx:
        k = key(t)
        src = cache.get(k[:2] + normalize(t["label"]))
        if k in cache or k[2:] not in names or not src:
            continue
        v = votes.setdefault(k, [Counter(), False])
        v[0][src["category"]] += 1
        v[1] = v[1] or bool(src.get("manual"))
    for k, (c, manual) in votes.items():
        cache[k] = {"category": c.most_common(1)[0][0], "merchant": names[k[2:]], **({"manual": True} if manual else {})}
    if votes:
        CATEGORIES_FILE.write_text(json.dumps(cache, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return bool(votes)


def load_corrections(tax):
    """corrections.csv : motif;catégorie — si le libellé contient le motif, la catégorie est imposée.
    La catégorie est un chemin (Dépenses:Loisirs:Sport) ou un nom unique (Sport)."""
    if not CORRECTIONS_FILE.exists():
        CORRECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CORRECTIONS_FILE.write_text(tr("cat.corrections_header"), encoding="utf-8")
    rules = []
    for row in csv.reader(CORRECTIONS_FILE.read_text(encoding="utf-8").splitlines(), delimiter=";"):
        if len(row) >= 2 and row[0].strip() and not row[0].startswith("#"):
            path = tax.resolve(row[1])
            if path:
                rules.append((row[0].strip().upper(), path))
    return rules


def ask(prompt, sch, chunk):
    """Catégorise un lot ; en cas de réponse invalide ou tronquée, recommence par moitiés.
    Un libellé seul qui échoue encore est laissé de côté (classé Divers)."""
    lines = "\n".join(f"{j}\t{tr('cat.credit') if k[0] == 'C' else tr('cat.debit')}\t{k[2:]}" for j, k in enumerate(chunk))
    try:
        res = chat(prompt, lines, schema=sch, max_tokens=60 * len(chunk) + 200, timeout=300)
    except PipelineError:
        if len(chunk) == 1:
            return {}
        mid = len(chunk) // 2
        return {**ask(prompt, sch, chunk[:mid]), **ask(prompt, sch, chunk[mid:])}
    return {chunk[i["id"]]: {"category": i["category"], "merchant": i["merchant"].strip()}
            for i in res["items"] if 0 <= i["id"] < len(chunk)}


def categorize(tx, status, refine=False):
    tax = load()
    valid = set(tax.assignable())
    cache = load_cache(tax, status)
    renamed = {k: v["merchant"] for k, v in cache.items() if v.get("merchant_manual")}
    if refine:
        cache = {k: v for k, v in cache.items() if not v.get("migrated") or v.get("manual")}
    inherit(tx, cache)
    todo = sorted({key(t) for t in tx} - cache.keys())
    status(tr("cat.todo", n=len(todo), known=len(cache)))
    labels = {tax.label(p): p for p in tax.assignable()}  # chemins tels qu'écrits dans categories.txt
    prompt, sch = tr("prompt.categorize").format(paths="\n".join(labels), notes=notes_block()), schema(tax)
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        for k, v in ask(prompt, sch, chunk).items():
            v["category"] = labels.get(v["category"], v["category"])
            if v["category"] in valid:
                cache[k] = {**v, "merchant": renamed[k], "merchant_manual": True} if k in renamed else v
        CATEGORIES_FILE.write_text(json.dumps(cache, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        status(tr("cat.progress", done=min(i + BATCH, len(todo)), n=len(todo)))
    apply(tx, tax, cache)
    missing = sum(t["origin"] == "défaut" for t in tx)
    if missing:
        status(tr("cat.missing", n=missing))
    return tx


def load_overrides():
    return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8")) if OVERRIDES_FILE.exists() else {}


def apply(tx, tax, cache):
    """Priorité : opération corrigée à la main > libellé corrigé à la main > corrections.csv > LLM."""
    rules, overrides = load_corrections(tax), load_overrides()
    for t in tx:
        t["key"] = key(t)
        c = cache.get(t["key"])
        if c:
            t["category"], t["origin"] = c["category"], ("manuel" if c.get("manual") else
                                                          "converti" if c.get("migrated") else "modèle")
        else:
            t["category"], t["origin"] = tax.fallback(t["amount"]), "défaut"
        t["merchant"] = (c or {}).get("merchant") or normalize(t["label"])[:30].title()
        if t["origin"] != "manuel":
            up = t["label"].upper() + " " + t["key"][2:]
            for pat, path in rules:
                if matches(pat, up):
                    t["category"], t["origin"] = path, "règle"
                    break
        o = overrides.get(t["id"])
        t["split"] = None
        if isinstance(o, list):  # ventilation : [{"category", "amount"}], montants signés dont la somme = l'opération
            t["split"] = [{"category": tax.remap(x["category"]) or tax.fallback(x["amount"]), "amount": x["amount"]}
                          for x in o]
            t["category"], t["origin"] = max(t["split"], key=lambda x: abs(x["amount"]))["category"], "ventilé"
        elif tax.remap(o):
            t["category"], t["origin"] = tax.remap(o), "opération"
    return tx


def expand(tx):
    """Remplace chaque opération ventilée par ses parts (même date, même libellé) pour les calculs."""
    return [{**t, "amount": x["amount"], "category": x["category"], "split": None} if x else t
            for t in tx for x in (t.get("split") or [None])]
