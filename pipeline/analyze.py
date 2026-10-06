"""Calculs déterministes (le LLM ne fait aucun calcul).

Chaque nœud de l'arborescence cumule sa descendance. Côté Dépenses, les montants sont comptés en
positif et un remboursement classé dans une dépense vient en déduction (comme dans GnuCash).
"""
from collections import defaultdict
from datetime import timedelta
from statistics import mean, median

from i18n import money, tr
from taxonomy import BUDGET, INCOME, OFF_BUDGET, SPENDING, WEALTH, ancestors, depth, leaf, load, root


def month(d):
    return f"{d.year}-{d.month:02d}"


def analyze(tx, span=None):
    """span : (début, fin) de la période analysée, si elle est plus courte que les relevés."""
    tax = load()
    real = [t for t in tx if root(t["category"]) in BUDGET]
    wealth = [t for t in tx if root(t["category"]) in WEALTH]
    off = [t for t in tx if root(t["category"]) == OFF_BUDGET]  # seulement pour le tableau détaillé
    months = sorted({month(t["date"]) for t in tx})
    first, last = span or (min(t["date"] for t in tx), max(t["date"] for t in tx))
    partial = partial_months(first, last)
    full = [m for m in months if m not in partial] or months

    nodes = defaultdict(lambda: defaultdict(float))
    # Dépenses, Actif, Passif et Hors budget sont comptés en argent sorti des comptes (positif = versé)
    for t in real + wealth + off:
        v = t["amount"] if root(t["category"]) == INCOME else -t["amount"]
        for p in ancestors(t["category"]):
            nodes[p][month(t["date"])] += v
    nodes = {p: dict(v) for p, v in nodes.items()}
    total = {p: sum(v.values()) for p, v in nodes.items()}
    avg = {p: sum(v.get(m, 0) for m in full) / len(full) for p, v in nodes.items()}

    # flux de trésorerie bruts de l'actif et du passif : sorties (versements) et entrées (retraits, emprunts)
    def flows(sign):
        fl = defaultdict(lambda: defaultdict(float))
        for t in wealth:
            if t["amount"] * sign > 0:
                for p in ancestors(t["category"]):
                    fl[p][month(t["date"])] += abs(t["amount"])
        return {"total": {p: sum(v.values()) for p, v in fl.items()},
                "avg": {p: sum(v.get(m, 0) for m in full) / len(full) for p, v in fl.items()}}

    rec = recurring(real + wealth, last)
    rec_names = {r["merchant"].lower() for r in rec}
    return {
        "tax": tax, "first": first, "last": last, "months": months, "partial": partial, "full": full,
        "n_tx": len({t["id"] for t in tx}), "n_excluded": len(tx) - len(real) - len(wealth), "n_wealth": len(wealth),
        "nodes": nodes, "total": total, "avg": avg,
        "income": nodes.get(INCOME, {}), "spending": nodes.get(SPENDING, {}),
        "recurring": rec,
        "top": sorted((t for t in real if t["amount"] < 0 and root(t["category"]) == SPENDING
                       and t["merchant"].lower() not in rec_names), key=lambda t: t["amount"])[:12],
        "rises": rises(nodes, full),
        "wealth_out": flows(-1), "wealth_in": flows(1),
    }


def partial_months(first, last):
    out = set()
    if first.day > 3:
        out.add(month(first))
    if (last + timedelta(days=1)).month == last.month:  # relevé arrêté avant la fin du mois
        out.add(month(last))
    return out


def recurring(tx, last):
    """Dépenses mensuelles récurrentes : même marchand, ≥ 3 mois, montant stable, écart ~1 mois."""
    groups = defaultdict(list)
    for t in tx:
        if t["amount"] < 0:
            groups[t["merchant"].lower()].append(t)
    out = []
    for g in groups.values():
        g.sort(key=lambda t: t["date"])
        ms = {month(t["date"]) for t in g}
        if len(ms) < 3 or len(g) > len(ms) * 1.5:
            continue
        amounts = [-t["amount"] for t in g]
        # montant stable d'un mois sur l'autre (une hausse de tarif ponctuelle est tolérée)
        steps = list(zip(amounts, amounts[1:]))
        if sum(abs(b - a) <= max(2, 0.3 * a) for a, b in steps) < 0.8 * len(steps):
            continue
        gaps = [(b["date"] - a["date"]).days for a, b in zip(g, g[1:])]
        if not 24 <= median(gaps) <= 38:
            continue
        typ = amounts[-1]
        out.append({
            "merchant": g[-1]["merchant"], "category": g[-1]["category"], "monthly": typ,
            "yearly": typ * 12, "since": g[0]["date"], "last": g[-1]["date"],
            "active": (last - g[-1]["date"]).days <= 45,
            "change": amounts[-1] - amounts[0] if abs(amounts[-1] - amounts[0]) > 0.5 else 0,
        })
    return sorted(out, key=lambda r: (not r["active"], -r["yearly"]))


def rises(nodes, full):
    """Postes de dépense (2 premiers niveaux) dont le dernier mois complet dépasse nettement
    la moyenne des 3 précédents. Si un enfant explique l'essentiel de la hausse du parent,
    seul l'enfant est gardé."""
    if len(full) < 4:
        return []
    cur, prev = full[-1], full[-4:-1]
    found = {}
    for p, v in nodes.items():
        if root(p) != SPENDING or not 1 <= depth(p) <= 2:
            continue
        base = mean(v.get(m, 0) for m in prev)
        now = v.get(cur, 0)
        if now - base > 50 and now > 1.3 * base:
            found[p] = {"category": p, "month": cur, "now": now, "base": base}
    out = [r for p, r in found.items()
           if not any(q.startswith(p + ":") and found[q]["now"] - found[q]["base"] > 0.7 * (r["now"] - r["base"])
                      for q in found)]
    return sorted(out, key=lambda r: r["base"] - r["now"])


def tree_lines(a, branch, max_depth):
    """(chemin, profondeur) d'une branche, frères triés par montant décroissant ; nœuds vides omis."""
    tax, out = a["tax"], []

    def walk(p):
        if p not in a["total"]:
            return
        out.append((p, depth(p)))
        if depth(p) < max_depth:
            for c in sorted(tax.children[p], key=lambda c: -a["total"].get(c, 0)):
                walk(c)
    walk(branch)
    return out


def summary_for_llm(a):
    """Résumé agrégé envoyé au LLM local pour le commentaire (aucune ligne brute)."""
    lab, m = a["tax"].label, money
    lines = [tr("sum.period", start=a["first"], end=a["last"], n=len(a["full"])),
             tr("sum.totals", inc=m(a["total"].get(INCOME, 0)), out=m(a["total"].get(SPENDING, 0))),
             tr("sum.by_month")]
    lines += [f"  {mo} : {m(a['income'].get(mo, 0))} / {m(a['spending'].get(mo, 0))}"
              + (tr("sum.partial") if mo in a["partial"] else "") for mo in a["months"]]
    for branch in (INCOME, SPENDING):
        lines.append(tr("sum.branch", branch=lab(branch)))
        lines += [f"{'  ' * d}{leaf(p)} : {m(a['total'][p])}, {m(a['avg'][p])}{tr('sum.per_month')}"
                  for p, d in tree_lines(a, branch, 2)[1:]]
    for branch in WEALTH:
        if branch in a["total"]:
            lines.append(tr("sum.wealth", branch=lab(branch)))
            lines += [f"{'  ' * d}{leaf(p)} : {m(a['total'][p])}, {m(a['avg'][p])}{tr('sum.per_month')}"
                      for p, d in tree_lines(a, branch, 2)]
    lines.append(tr("sum.recurring"))
    lines += [f"  {r['merchant']} ({leaf(r['category'])}) : {m(r['monthly'], 2)}{tr('sum.per_month')}"
              + ("" if r["active"] else tr("sum.stopped"))
              + (tr("sum.change", v=m(r["change"], 2)) if r["change"] else "") for r in a["recurring"]]
    lines.append(tr("sum.top"))
    lines += [f"  {t['date']} {t['merchant']} ({leaf(t['category'])}) : {m(-t['amount'])}" for t in a["top"]]
    if a["rises"]:
        lines.append(tr("sum.rises"))
        lines += [tr("sum.rise", cat=lab(r["category"]), month=r["month"], now=m(r["now"]), base=m(r["base"]))
                  for r in a["rises"]]
    return "\n".join(lines)
