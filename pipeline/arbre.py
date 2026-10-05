"""Édition de l'arborescence depuis la page de révision.

Chaque opération réécrit categories.txt et reporte le changement de chemin sur les libellés
(categories.json), les corrections d'opérations (operations.json) et corrections.csv : un renommage,
un déplacement ou une suppression ne fait perdre aucune correction.
"""
import csv
import io
import json

from config import CATEGORIES_FILE, CORRECTIONS_FILE, OVERRIDES_FILE, TAXONOMY_FILE
from i18n import tr
from taxonomy import ROOTS, SEP, Taxonomy, leaf, load, parent

def protected(tax):
    """Catégories de repli utilisées quand rien d'autre ne convient : ni renommables ni supprimables."""
    return set(tax.fallbacks().values())


class TreeError(Exception):
    pass


def under(p, node):
    return p == node or p.startswith(node + SEP)


def check_name(name):
    name = " ".join((name or "").split())
    if not name or SEP in name or name.startswith("#"):
        raise TreeError(tr("tree.bad_name"))
    return name


def check_editable(tax, path):
    if path not in tax.paths:
        raise TreeError(tr("tree.not_found"))
    if path in ROOTS or path in protected(tax):
        raise TreeError(tr("tree.locked", name=tax.label(path).rsplit(SEP, 1)[-1]))


def write_tree(paths):
    """Réécrit categories.txt en conservant les commentaires d'en-tête et l'ordre des frères."""
    kids = {}
    for p in paths:
        kids.setdefault(parent(p), [])
        if p not in kids[parent(p)]:
            kids[parent(p)].append(p)
    lines = []
    names = load().root_names  # racines réécrites telles qu'elles étaient (français ou anglais)

    def walk(p, d):
        lines.append("  " * d + (names.get(p, p) if d == 0 else leaf(p)))
        for c in kids.get(p, []):
            walk(c, d + 1)
    for i, r in enumerate(kids.get("", [])):
        if i:
            lines.append("")
        walk(r, 0)
    head = []
    for l in TAXONOMY_FILE.read_text(encoding="utf-8").splitlines():
        if l.strip() and not l.startswith("#"):
            break
        head.append(l)
    text = "\n".join(head).rstrip() + "\n\n" + "\n".join(lines) + "\n"
    Taxonomy(text)  # lève une erreur si l'arbre produit est invalide
    TAXONOMY_FILE.write_text(text, encoding="utf-8")


def propagate(old_tax, f):
    """Applique la transformation de chemins f à tout ce qui référence une catégorie."""
    if CATEGORIES_FILE.exists():
        cache = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
        for v in cache.values():
            v["category"] = f(v["category"])
        CATEGORIES_FILE.write_text(json.dumps(cache, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    if OVERRIDES_FILE.exists():
        ov = {k: [{**x, "category": f(x["category"])} for x in v] if isinstance(v, list) else f(v)
              for k, v in json.loads(OVERRIDES_FILE.read_text(encoding="utf-8")).items()}
        OVERRIDES_FILE.write_text(json.dumps(ov, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    if CORRECTIONS_FILE.exists():
        out = []
        for line in CORRECTIONS_FILE.read_text(encoding="utf-8").splitlines():
            row = next(csv.reader([line], delimiter=";"), [])
            path = old_tax.resolve(row[1]) if len(row) >= 2 and not line.startswith("#") else None
            if path and f(path) != path:
                buf = io.StringIO()
                csv.writer(buf, delimiter=";", lineterminator="").writerow([row[0], f(path), *row[2:]])
                line = buf.getvalue()
            out.append(line)
        CORRECTIONS_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def add(parent_path, name):
    tax = load()
    if parent_path not in tax.paths:
        raise TreeError(tr("tree.no_parent"))
    new = parent_path + SEP + check_name(name)
    if new in tax.paths:
        raise TreeError(tr("tree.exists"))
    write_tree(tax.paths + [new])


def rename(path, name):
    tax = load()
    check_editable(tax, path)
    new = parent(path) + SEP + check_name(name)
    if new in tax.paths:
        raise TreeError(tr("tree.sibling_exists"))
    f = lambda p: new + p[len(path):] if under(p, path) else p
    write_tree([f(p) for p in tax.paths])
    propagate(tax, f)


def move(path, dest):
    tax = load()
    check_editable(tax, path)
    if dest not in tax.paths or under(dest, path):
        raise TreeError(tr("tree.bad_dest"))
    new = dest + SEP + leaf(path)
    if new in tax.paths:
        raise TreeError(tr("tree.dest_exists"))
    f = lambda p: new + p[len(path):] if under(p, path) else p
    write_tree([f(p) for p in tax.paths if not under(p, path)] + [f(p) for p in tax.paths if under(p, path)])
    propagate(tax, f)


def target_after_delete(tax, path):
    """Où vont les libellés d'une catégorie supprimée : son parent, ou la catégorie de repli de la branche."""
    par = parent(path)
    if SEP in par:
        return par
    for p in (*protected(tax), *tax.children[par]):
        if parent(p) == par and not under(p, path) and p in tax.paths:
            return p
    raise TreeError(tr("tree.last_child", name=tax.label(par)))


def delete(path):
    tax = load()
    check_editable(tax, path)
    target = target_after_delete(tax, path)
    write_tree([p for p in tax.paths if not under(p, path)])
    propagate(tax, lambda p: target if under(p, path) else p)
    return target
