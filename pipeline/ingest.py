"""Lecture des relevés CSV, quel que soit leur format.

Le LLM local lit l'en-tête et quelques lignes d'un fichier inconnu et renvoie une « carte » du format
(ligne d'en-tête, séparateur, colonnes). La carte est ensuite validée en parsant tout le fichier et
mise en cache : un format déjà vu ne repasse plus par le LLM.
"""
import csv
import hashlib
import io
import json
import re
from collections import Counter
from datetime import datetime

from config import DATA_DIR, SCHEMAS_FILE
from i18n import tr
from llm import PipelineError, chat

PREVIEW_LINES = 25

FORMAT_SCHEMA = {
    "type": "object",
    "properties": {
        "header_line": {"type": "integer"},
        "delimiter": {"type": "string", "enum": [";", ",", "\t", "|"]},
        "date_column": {"type": "string"},
        "date_format": {"type": "string"},
        "label_columns": {"type": "array", "items": {"type": "string"}},
        "amount_column": {"type": ["string", "null"]},
        "debit_column": {"type": ["string", "null"]},
        "credit_column": {"type": ["string", "null"]},
        "decimal_comma": {"type": "boolean"},
    },
    "required": ["header_line", "delimiter", "date_column", "date_format",
                 "label_columns", "amount_column", "debit_column", "credit_column", "decimal_comma"],
    "additionalProperties": False,
}

FORMAT_PROMPT = """Tu analyses le début d'un export CSV de relevé bancaire ou de carte de crédit.
Les lignes sont numérotées à partir de 0 (le préfixe "N| " ne fait pas partie du fichier).
Il peut y avoir des lignes d'introduction (titulaire, solde...) avant la vraie ligne d'en-tête.
Réponds en JSON :
- header_line : numéro de la ligne qui contient les noms de colonnes
- delimiter : séparateur de colonnes
- date_column : nom exact de la colonne de date d'opération
- date_format : format strftime Python de cette date (ex. %d/%m/%Y)
- label_columns : noms exacts des colonnes décrivant l'opération (libellé, description, marchand...)
- amount_column : nom exact de la colonne de montant unique signé, sinon null
- debit_column / credit_column : noms exacts si débits et crédits sont dans deux colonnes séparées, sinon null
- decimal_comma : true si les montants utilisent la virgule décimale (1 234,56)
Les noms de colonnes doivent être recopiés exactement comme dans la ligne d'en-tête."""


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


def split_header(line, delim):
    return [c.strip() for c in next(csv.reader([line], delimiter=delim))]


def parse_amount(s, decimal_comma):
    s = (s or "").strip().replace(" ", "").replace(" ", "").replace(" ", "")
    s = s.replace("€", "").replace("EUR", "")
    if not s:
        return None
    if decimal_comma:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    if s.endswith("-"):
        s = "-" + s[:-1]
    try:
        return float(s)
    except ValueError:
        return None


def parse_rows(text, fmt):
    """Renvoie (transactions, lignes_rejetées). Montant < 0 = sortie d'argent."""
    lines = text.splitlines(keepends=True)
    body = "".join(lines[fmt["header_line"]:])
    reader = csv.DictReader(io.StringIO(body), delimiter=fmt["delimiter"])
    reader.fieldnames = [c.strip() for c in reader.fieldnames]
    tx, rejected = [], 0
    for row in reader:
        try:
            d = datetime.strptime((row.get(fmt["date_column"]) or "").strip(), fmt["date_format"]).date()
        except ValueError:
            rejected += 1
            continue
        label = " ".join(" ".join((row.get(c) or "") for c in fmt["label_columns"]).split())
        if fmt["amount_column"]:
            amt = parse_amount(row.get(fmt["amount_column"]), fmt["decimal_comma"])
        else:
            deb = parse_amount(row.get(fmt["debit_column"]), fmt["decimal_comma"]) if fmt["debit_column"] else None
            cre = parse_amount(row.get(fmt["credit_column"]), fmt["decimal_comma"]) if fmt["credit_column"] else None
            amt = None if deb is None and cre is None else abs(cre or 0) - abs(deb or 0)
        if amt is None or not label:
            rejected += 1
            continue
        tx.append({"date": d, "label": label, "amount": amt})
    return tx, rejected


def validate(text, fmt):
    """Vérifie la carte en parsant tout le fichier ; corrige au passage une ligne d'en-tête décalée."""
    lines = text.splitlines()
    needed = [fmt["date_column"], *fmt["label_columns"]]
    needed += [c for c in (fmt["amount_column"], fmt["debit_column"], fmt["credit_column"]) if c]
    if not fmt["label_columns"] or not (fmt["amount_column"] or fmt["debit_column"] or fmt["credit_column"]):
        return None
    has_cols = lambda i: set(needed) <= set(split_header(lines[i], fmt["delimiter"]))
    if not (0 <= fmt["header_line"] < len(lines) and has_cols(fmt["header_line"])):
        found = [i for i in range(min(len(lines), PREVIEW_LINES * 2)) if has_cols(i)]
        if not found:
            return None
        fmt["header_line"] = found[0]
    tx, rejected = parse_rows(text, fmt)
    if not tx or rejected > 0.1 * (len(tx) + rejected) + 3:
        return None
    return tx, rejected


def fingerprint(header_line):
    return hashlib.sha256(" ".join(header_line.split()).lower().encode()).hexdigest()[:16]


def find_known(text, schemas):
    """Reconnaît un format déjà vu par sa ligne d'en-tête, où qu'elle soit (le préambule varie)."""
    for i, line in enumerate(text.splitlines()[:PREVIEW_LINES * 2]):
        fmt = schemas.get(fingerprint(line))
        if fmt:
            return {**fmt, "header_line": i}
    return None


def detect(text, name):
    preview = "\n".join(f"{i}| {l}" for i, l in enumerate(text.splitlines()[:PREVIEW_LINES]))
    last = None
    for attempt in range(2):
        user = f"Fichier « {name} » :\n{preview}"
        if last:
            user += "\n\nUne première réponse ne permettait pas de lire le fichier ; vérifie la ligne d'en-tête et les noms de colonnes."
        fmt = chat(FORMAT_PROMPT, user, schema=FORMAT_SCHEMA)
        last = fmt
        if validate(text, fmt):
            return fmt
    raise PipelineError(tr("ingest.unknown_format", name=name))


def load_schemas():
    return json.loads(SCHEMAS_FILE.read_text(encoding="utf-8")) if SCHEMAS_FILE.exists() else {}


def ingest(status, learn=True):
    """Lit tous les CSV de DATA_DIR ; renvoie la liste dédoublonnée des transactions.
    learn=False (page) : un CSV au format inconnu est laissé de côté au lieu d'interroger le LLM ;
    ingest.skipped donne leur nombre."""
    ingest.skipped = 0
    schemas = load_schemas()
    read = []  # (format, Counter des opérations du fichier)
    files = sorted(DATA_DIR.glob("*.csv"))
    if not files:
        raise PipelineError(tr("ingest.no_csv", path=DATA_DIR))
    for f in files:
        text = decode(f.read_bytes())
        fmt = find_known(text, schemas)
        new = fmt is None
        if new and not learn:
            ingest.skipped += 1
            continue
        if new:
            fmt = detect(text, f.name)
            fmt["fingerprint"] = fingerprint(text.splitlines()[fmt["header_line"]])
            known = schemas.get(fmt["fingerprint"])
            fmt["name"] = known["name"] if known else f"n°{len(schemas) + 1}"
            schemas[fmt["fingerprint"]] = fmt
        result = validate(text, fmt)
        if not result:
            raise PipelineError(tr("ingest.format_changed", name=f.name))
        tx, rejected = result
        read.append((fmt["fingerprint"], Counter((t["date"], round(t["amount"], 2), t["label"]) for t in tx)))
        status(tr("ingest.file", name=f.name, n=len(tx), rejected=rejected, fmt=fmt["name"])
               + (tr("ingest.new") if new else ""))
    if learn:  # en lecture seule (page), rien de nouveau à mémoriser
        SCHEMAS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCHEMAS_FILE.write_text(json.dumps(schemas, indent=2, ensure_ascii=False), encoding="utf-8")
    inst = {s["fingerprint"]: s["name"] for s in schemas.values()}
    per_key = Counter()
    for fp in {fp for fp, _ in read}:
        groups = accounts([c for f, c in read if f == fp])
        # groupes de relevés qui couvrent une même période sans partager leurs opérations : comptes distincts
        spans = [(min(k[0] for c in g for k in c), max(k[0] for c in g for k in c)) for g in groups if any(g)]
        distinct = sum(any(i != j and a[0] <= b[1] and b[0] <= a[1] for j, b in enumerate(spans)) for i, a in enumerate(spans))
        if distinct > 1:
            status(tr("ingest.accounts", fmt=inst[fp], n=distinct))
        for g in groups:
            # même compte : une opération compte autant de fois qu'elle apparaît au plus dans un même relevé
            best = Counter()
            for c in g:
                best |= c
            for (d, amt, label), n in best.items():
                per_key[(fp, d, amt, label)] += n  # comptes différents : leurs opérations s'additionnent
    out = []
    for (fp, d, amt, label), n in per_key.items():
        h = hashlib.sha256(f"{fp}|{d}|{amt:.2f}|{label}".encode()).hexdigest()[:12]
        out += [{"id": f"{h}-{i}", "date": d, "amount": amt, "label": label, "source": inst[fp], "fp": fp}
                for i in range(n)]
    return sort_signs(out, schemas)


def same_account(a, b):
    """Deux relevés du même format viennent du même compte s'ils partagent la plupart des opérations
    de leur période commune (relevés qui se chevauchent) ; deux comptes différents n'en partagent presque pas."""
    lo = max(min(k[0] for k in a), min(k[0] for k in b))
    hi = min(max(k[0] for k in a), max(k[0] for k in b))
    if lo > hi:
        return False
    ca = Counter({k: n for k, n in a.items() if lo <= k[0] <= hi})
    cb = Counter({k: n for k, n in b.items() if lo <= k[0] <= hi})
    common = sum((ca & cb).values())
    return common >= 1 and common >= 0.5 * min(sum(ca.values()), sum(cb.values()))


def accounts(counters):
    """Regroupe les relevés d'un même format par compte (union des chevauchements)."""
    parent = list(range(len(counters)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(counters)):
        for j in range(i + 1, len(counters)):
            if counters[i] and counters[j] and same_account(counters[i], counters[j]):
                parent[find(i)] = find(j)
    groups = {}
    for i, c in enumerate(counters):
        groups.setdefault(find(i), []).append(c)
    return list(groups.values())


def sort_signs(tx, schemas):
    """Formats à montant unique : certains émetteurs de carte (ex. Amex) notent les achats en positif.
    On retient le signe majoritaire comme celui des dépenses."""
    for s in schemas.values():
        if not s["amount_column"]:
            continue
        mine = [t for t in tx if t["fp"] == s["fingerprint"]]
        if mine and sum(t["amount"] > 0 for t in mine) > len(mine) / 2:
            for t in mine:
                t["amount"] = -t["amount"]
    return sorted(tx, key=lambda t: t["date"])
