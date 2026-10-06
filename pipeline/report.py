"""Rapport HTML autonome (aucune ressource externe)."""
import json
from html import escape
from analyze import tree_lines
import i18n
from i18n import money as eur, tr
from taxonomy import ASSET, INCOME, LIABILITY, OFF_BUDGET, SPENDING, WEALTH, depth, leaf

TAX = None  # arborescence du rapport en cours (affichage des racines telles qu'écrites)


def mlabel(m, year=False):
    y, mo = m.split("-")
    return i18n.MONTHS[i18n.LANG][int(mo) - 1] + (f" {y[2:]}" if year else "")


def fdate(d):
    return i18n.fdate(d, short=True)


def lab(path):
    return TAX.label(path) if TAX else path


FLOW_JS = r"""
// Histogramme des flux : revenus vers le haut, dépenses vers le bas, catégories empilées
// (les plus grosses près de l'axe), courbe du solde. Calculé dans la page à partir des sommes mensuelles.
(() => {
const D = JSON.parse(document.getElementById('flowdata').textContent);
const box = document.getElementById('flow'), legend = document.getElementById('flowlegend');
const inX = document.getElementById('fx'), inP = document.getElementById('fp'), inC = document.getElementById('fc');
try { const s = JSON.parse(localStorage.getItem('flow') || '{}');
  if (s.x) inX.value = s.x; if (s.p !== undefined) inP.checked = s.p; if (s.c !== undefined) inC.checked = s.c; } catch (e) {}
const leaf = p => p.split(':').pop();
const under = (q, p) => q.startsWith(p + ':');
const sum = o => Object.values(o).reduce((s, v) => s + v, 0);
const LOC = LANG === 'fr' ? 'fr-FR' : 'en-US';
const fmt = v => { const n = Math.round(v) || 0, s = Math.abs(n).toLocaleString(LOC);
  return LANG === 'fr' ? n.toLocaleString(LOC) + '\u00a0€' : (n < 0 ? '-€' : '€') + s; };
// 8 couleurs pour les deux côtés réunis : jamais la même couleur en haut et en bas
const SLOTS = [1, 2, 3, 4, 5, 6, 7, 8];

function build(side, x, promote, slots) {
  const N = D[side], root = D.roots[side], R = sum(N[root] || {}), thr = x / 100 * Math.abs(R);
  const tot = p => sum(N[p] || {}), depth = p => p.split(':').length - 1;
  const cand = Object.keys(N).filter(p => p !== root && (promote || depth(p) === 1) && tot(p) > 0 && tot(p) >= thr);
  let series = [];
  for (const p of cand) {
    // sous-catégories promues directement sous p (sans autre promue entre les deux)
    const desc = cand.filter(q => under(q, p) && !cand.some(r => r !== q && under(q, r) && under(r, p)));
    const vals = {};
    for (const m of D.months) vals[m] = (N[p][m] || 0) - desc.reduce((s, q) => s + (N[q][m] || 0), 0);
    const t = sum(vals);
    if (desc.length && t < thr) continue;  // reste trop petit : versé dans « Autres »
    series.push({path: p, name: desc.length ? leaf(p) + ' ' + I18N['rep.js.rest'] : leaf(p), vals, total: t});
  }
  series.sort((a, b) => b.total - a.total);
  series = series.slice(0, slots.length);
  series.forEach((s, i) => s.color = `var(--c${slots[i]})`);
  const other = {};
  for (const m of D.months) other[m] = (N[root][m] || 0) - series.reduce((s, x) => s + x.vals[m], 0);
  if (Math.abs(sum(other)) >= 1) series.push({path: root, name: I18N['rep.js.other'], other: true, vals: other, total: sum(other), color: 'var(--cother)'});
  return series;
}

function nice(v) {
  const raw = v / 3, mag = 10 ** Math.floor(Math.log10(raw || 1));
  return [1, 2, 2.5, 5, 10].map(k => k * mag).find(s => s >= raw) || 10 * mag;
}

function draw() {
  const x = Math.min(50, Math.max(1, +inX.value || 10)), promote = inP.checked, showCum = inC.checked;
  try { localStorage.setItem('flow', JSON.stringify({x, p: promote, c: showCum})); } catch (e) {}
  const inc = build('income', x, promote, SLOTS.slice(0, 3)), M = D.months;
  const sp = build('spending', x, promote, SLOTS.slice(inc.filter(c => !c.other).length));
  const pos = v => Math.max(0, v);
  const contra = m => inc.concat(sp).filter(c => c.vals[m] <= -0.5);
  const incTot = m => inc.reduce((s, c) => s + pos(c.vals[m]), 0), spTot = m => sp.reduce((s, c) => s + pos(c.vals[m]), 0);
  const solde = m => (D.income[D.roots.income][m] || 0) - (D.spending[D.roots.spending][m] || 0);
  const cum = {}; M.reduce((s, m) => cum[m] = s + solde(m), 0);
  const lines = M.map(solde).concat(showCum ? M.map(m => cum[m]) : []);
  const hi = Math.max(...M.map(incTot), ...lines, 0), lo = Math.max(...M.map(spTot), ...lines.map(v => -v), 0);
  const step = nice(Math.max(hi + lo, 1) / 3);
  const top = Math.max(hi * 1.04, step / 2), bot = Math.max(lo * 1.08, step / 4);  // échelle au plus près des données
  const W = 900, H = 600, L = 64, R = 12, T = 12, B = 30, ph = H - T - B;
  const y = v => T + ph * (top - v) / (top + bot);
  const gw = (W - L - R) / M.length, bw = Math.min(30, gw * 0.7);
  const cx = i => L + gw * i + gw / 2;
  const out = [`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${I18N['rep.js.aria']}">`];
  for (let v = -step * Math.floor(bot / step); v <= top + 1e-6; v += step) {
    out.push(`<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`,
             `<text class="ax" x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${fmt(v)}</text>`);
  }
  const every = Math.ceil(M.length / 12);
  M.forEach((m, i) => {
    const part = D.partial.includes(m) ? ' opacity=".45"' : '';
    let up = 0, dn = 0;
    for (const c of inc) { const v = pos(c.vals[m]); if (v) out.push(`<rect x="${cx(i) - bw / 2}" y="${y(up + v)}" width="${bw}" height="${y(up) - y(up + v)}" fill="${c.color}" stroke="var(--surface)" stroke-width="1"${part}/>`); up += v; }
    for (const c of sp) { const v = pos(c.vals[m]); if (v) out.push(`<rect x="${cx(i) - bw / 2}" y="${y(-dn)}" width="${bw}" height="${y(-dn - v) - y(-dn)}" fill="${c.color}" stroke="var(--surface)" stroke-width="1"${part}/>`); dn += v; }
    // catégorie à contre-sens ce mois-ci (ex. remboursements > dépenses) : non dessinée, barre inexacte
    if (contra(m).length) out.push(`<text class="warn" x="${cx(i)}" y="${y(-dn) + 16}" text-anchor="middle">⚠</text>`);
    if (i % every === 0) {
      const d = new Date(m + '-01');
      out.push(`<text class="ax" x="${cx(i)}" y="${H - 8}" text-anchor="middle">${d.toLocaleDateString(LOC, {month: 'short'})}${d.getMonth() === 0 || i === 0 ? ' ' + String(d.getFullYear()).slice(2) : ''}</text>`);
    }
  });
  // axe 0 par-dessus les barres
  out.push(`<line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)" stroke-width="2"/>`);
  if (showCum) {
    out.push(`<polyline fill="none" stroke="var(--cum)" stroke-width="2.5" stroke-dasharray="7 4" points="${M.map((m, i) => `${cx(i)},${y(cum[m])}`).join(' ')}"/>`);
    M.forEach((m, i) => out.push(`<circle cx="${cx(i)}" cy="${y(cum[m])}" r="3.5" fill="var(--cum)" stroke="var(--surface)" stroke-width="2"/>`));
  }
  out.push(`<polyline fill="none" stroke="var(--ink)" stroke-width="2" points="${M.map((m, i) => `${cx(i)},${y(solde(m))}`).join(' ')}"/>`);
  M.forEach((m, i) => out.push(`<circle cx="${cx(i)}" cy="${y(solde(m))}" r="4" fill="var(--ink)" stroke="var(--surface)" stroke-width="2"/>`));
  M.forEach((m, i) => out.push(`<rect class="hit" data-i="${i}" x="${L + gw * i}" y="${T}" width="${gw}" height="${ph}" fill="transparent"/>`));
  out.push('</svg><div class="ftip" hidden></div>');
  box.innerHTML = out.join('');
  const item = c => `<span title="${c.path === D.roots.income || c.path === D.roots.spending ? I18N['rep.js.below'] : c.path.replace(/:/g, ' › ')}"><i style="background:${c.color}"></i>${c.name}</span>`;
  legend.innerHTML = `<div><b>${I18N['rep.js.income']}</b>${inc.map(item).join('')}</div><div><b>${I18N['rep.js.expenses']}</b>${sp.map(item).join('')}</div>`
    + `<div><span><i class="line"></i>${I18N['rep.js.balance']}</span>${showCum ? `<span><i class="line cum"></i>${I18N['rep.js.cum_long']}</span>` : ''}<span class="muted">${I18N['rep.js.partial']}</span>${M.some(m => contra(m).length) ? `<span class="muted"><b class="warn">⚠</b> ${I18N['rep.js.inexact']}</span>` : ''}</div>`;
  const tip = box.querySelector('.ftip');
  box.querySelectorAll('.hit').forEach(h => {
    h.addEventListener('mousemove', e => {
      const m = M[+h.dataset.i], d = new Date(m + '-01');
      const rows = (list, sign) => list.filter(c => Math.abs(c.vals[m]) >= 0.5).map(c =>
        `<tr${c.vals[m] < 0 ? ' class="neg"' : ''}><td><i style="background:${c.color}"></i>${c.vals[m] < 0 ? '⚠ ' : ''}${c.name}</td><td>${fmt(sign * c.vals[m])}</td></tr>`).join('');
      tip.innerHTML = `<b>${d.toLocaleDateString(LOC, {month: 'long', year: 'numeric'})}${D.partial.includes(m) ? ' ' + I18N['rep.js.partial_short'] : ''}</b>
        <table>${rows(inc, 1)}${rows(sp, -1)}<tr class="tot"><td>${I18N['rep.js.balance']}</td><td>${fmt(solde(m))}</td></tr><tr><td>${I18N['rep.js.cum']}</td><td>${fmt(cum[m])}</td></tr></table>${contra(m).length ?
        '<div class="wnote">⚠ ' + I18N['rep.js.contra'] + ' ' + contra(m).map(c => c.name).join(', ') + '</div>' : ''}`;
      tip.hidden = false;
      const r = box.getBoundingClientRect(), w = tip.offsetWidth;
      tip.style.left = Math.min(Math.max(0, e.clientX - r.left + 14), r.width - w) + 'px';
      tip.style.top = (e.clientY - r.top + 14) + 'px';
    });
    h.addEventListener('mouseleave', () => tip.hidden = true);
  });
}
inX.addEventListener('input', draw); inP.addEventListener('change', draw); inC.addEventListener('change', draw); draw();
})();
"""


def flow_block(a):
    """Section de l'histogramme des flux (données mensuelles par catégorie, dessin en JavaScript)."""
    data = {"months": a["months"], "partial": sorted(a["partial"]), "roots": {"income": INCOME, "spending": SPENDING},
            "income": {p: v for p, v in a["nodes"].items() if p.split(":")[0] == INCOME},
            "spending": {p: v for p, v in a["nodes"].items() if p.split(":")[0] == SPENDING}}
    js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (f"<section><h2>{tr('rep.flow')}</h2>"
            f"<div class='fctl'><label>{tr('rep.threshold')} <input type='number' id='fx' min='1' max='50' value='10'> {tr('rep.of_branch')}</label>"
            f"<label><input type='checkbox' id='fp'> {tr('rep.detail_sub')}</label>"
            f"<label><input type='checkbox' id='fc' checked> {tr('rep.cumulative')}</label></div>"
            "<div id='flowlegend' class='flegend'></div><div id='flow' class='scroll fbox'></div>"
            f"<script type='application/json' id='flowdata'>{js}</script><script>{FLOW_JS}</script></section>")




def dual(avg, total):
    """Montant mensuel moyen ou total de la période, selon la case « Ramener au mois » du rapport."""
    return f'<span class="vm">{eur(avg)}<small>{tr("rep.per_month")}</small></span><span class="vt">{eur(total)}</span>'


def pretty(path):
    return escape(lab(path).replace(":", " › "))


FLOW_NAMES = {
    "out": {ASSET: "rep.to_assets", LIABILITY: "rep.to_liabilities"},
    "in": {ASSET: "rep.from_assets", LIABILITY: "rep.from_liabilities"},
}


def category_tree(a, branch, flow=None):
    """Arbre repliable : chaque nœud montre sa moyenne mensuelle et sa part du total affiché.
    flow ("out" ou "in") ajoute les flux de trésorerie de l'actif et du passif à la branche."""
    tax, T, A = a["tax"], dict(a["total"]), dict(a["avg"])
    tops = [c for c in tax.children[branch] if c in T]
    if flow:
        fl = a["wealth_out" if flow == "out" else "wealth_in"]
        T.update(fl["total"]), A.update(fl["avg"])
        tops += [r for r in WEALTH if fl["total"].get(r)]
    total = sum(T[c] for c in tops) or 1
    scale = max([A.get(c, 0) for c in tops] + [1])
    names = {k: tr(v) for k, v in FLOW_NAMES.get(flow, {}).items()}

    def row(p):
        v, avg = T[p], A[p]
        return (f'<span class="crow" title="{pretty(p)} : {tr("rep.tree_tip", total=eur(v), avg=eur(avg), pct=f"{v / total:.0%}")}">'
                f'<span class="cname">{escape(names.get(p, leaf(p)))}</span>'
                f'<span class="ctrack"><span class="cbar" style="width:{max(0, 100 * avg / scale):.1f}%"></span></span>'
                f'<span class="cval">{dual(avg, v)}</span>'
                f'<span class="cpct">{v / total:.0%}</span></span>')

    def node(p):
        kids = sorted((c for c in tax.children[p] if c in T), key=lambda c: -T[c])
        if not kids:
            return f'<div class="leaf">{row(p)}</div>'
        # un nœud qui porte lui-même des opérations en plus de ses enfants : ligne « (direct) »
        direct = A[p] - sum(A[c] for c in kids)
        direct_t = T[p] - sum(T[c] for c in kids)
        extra = (f'<div class="leaf"><span class="crow direct"><span class="cname">{tr("rep.undetailed")}</span>'
                 f'<span class="ctrack"></span><span class="cval">{dual(direct, direct_t)}</span>'
                 f'<span class="cpct"></span></span></div>') if abs(direct) >= 1 else ""
        return f'<details><summary>{row(p)}</summary><div class="kids">{"".join(node(c) for c in kids)}{extra}</div></details>'

    return "".join(node(c) for c in sorted(tops, key=lambda c: -T[c]))


def cat_table(a):
    months = a["months"]
    head = "".join(f"<th>{mlabel(m, True)}{'*' if m in a['partial'] else ''}</th>" for m in months)
    rows = (tree_lines(a, SPENDING, 2)[1:] + tree_lines(a, INCOME, 1)
            + [x for b in (ASSET, LIABILITY, OFF_BUDGET) for x in tree_lines(a, b, 1)])
    maxv = max([a["nodes"][p].get(m, 0) for p, d in rows if d == 2 for m in months] + [1])
    body = []
    for p, d in rows:
        cells = []
        for m in months:
            v = a["nodes"][p].get(m, 0)
            alpha = 0 if d != 2 or v <= 0 else 0.08 + 0.5 * min(1, v / maxv) ** 0.6
            cells.append(f'<td style="--a:{alpha:.2f}">{eur(v) if round(v) else "·"}</td>')
        cls = "lvl1" if d == 1 else ("lvl0" if d == 0 else "")
        body.append(f"<tr class='{cls}'><th style='padding-left:{8 + 16 * max(0, d - 1)}px' title='{pretty(p)}'>"
                    f"{escape(lab(p).rsplit(':', 1)[-1])}</th>{''.join(cells)}<td class='tot'>{eur(a['avg'][p])}</td><td class='tot'>{eur(a['total'][p])}</td></tr>")
    return (f'<div class="scroll"><table class="heat"><thead><tr><th></th>{head}<th>{tr("rep.avg_month")}</th><th>{tr("rep.total")}</th></tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table></div>")


def recurring_table(a):
    if not a["recurring"]:
        return f"<p class='muted'>{tr('rep.no_recurring')}</p>"
    rows = []
    for r in a["recurring"]:
        chg = f" <span class='chg'>{'+' if r['change'] > 0 else ''}{eur(r['change'], 2)} {tr('rep.since_start')}</span>" if r["change"] else ""
        rows.append(f"<tr class='{'' if r['active'] else 'off'}'><td>{escape(r['merchant'])}{chg}</td>"
                    f"<td class='muted' title='{pretty(r['category'])}'>{escape(leaf(r['category']))}</td><td class='num'>{eur(r['monthly'], 2)}</td>"
                    f"<td class='num'>{eur(r['yearly'])}</td>"
                    f"<td class='muted'>{tr('rep.active') if r['active'] else tr('rep.stopped_on', date=fdate(r['last']))}</td></tr>")
    tot = sum(r["yearly"] for r in a["recurring"] if r["active"])
    tot_m = sum(r["monthly"] for r in a["recurring"] if r["active"])
    return (f"<div class='scroll'><table><thead><tr><th>{tr('rep.merchant')}</th><th>{tr('rep.category')}</th><th class='num'>{tr('rep.per_month_col')}</th>"
            f"<th class='num'>{tr('rep.per_year')}</th><th>{tr('rep.state')}</th></tr></thead><tbody>{''.join(rows)}</tbody>"
            f"<tfoot><tr><td colspan='2'>{tr('rep.recurring_total')}</td><td class='num'>{eur(tot_m, 2)}</td>"
            f"<td class='num'>{eur(tot)}</td><td></td></tr></tfoot></table></div>")


def top_table(a):
    rows = "".join(
        f"<tr><td class='muted'>{fdate(t['date'])}</td><td>{escape(t['merchant'])}</td>"
        f"<td class='muted' title='{pretty(t['category'])}'>{escape(leaf(t['category']))}</td><td class='num'>{eur(-t['amount'])}</td></tr>" for t in a["top"])
    return f"<div class='scroll'><table><tbody>{rows}</tbody></table></div>"


def rises_block(a):
    if not a["rises"]:
        return ""
    items = "".join(
        f"<li><b>{escape(r['category'].split(':', 1)[1].replace(':', ' › '))}</b> : "
        f"{tr('rep.rise', now=eur(r['now']), month=mlabel(r['month'], True), base=eur(r['base']))}</li>" for r in a["rises"])
    return f"<section><h2>{tr('rep.rises')}</h2><ul>{items}</ul></section>"


def comment_block(comment):
    if not comment:
        return ""
    obs = "".join(f"<li>{escape(x)}</li>" for x in comment.get("observations", []))
    sug = "".join(f"<li>{escape(x)}</li>" for x in comment.get("suggestions", []))
    return (f"<section class='comment'><h2>{tr('rep.analysis')}</h2><ul>{obs}</ul>"
            + (f"<h3>{tr('rep.ideas')}</h3><ul>{sug}</ul>" if sug else "")
            + f"<p class='muted small'>{tr('rep.analysis_note')}</p></section>")


def wealth_block(a, wealth):
    if not wealth:
        return ""
    trees = "".join(f"<h3>{escape(lab(b))} · {dual(a['avg'][b], a['total'][b])}</h3><div class='tree'>{category_tree(a, b)}</div>"
                    for b in wealth)
    return (f"<section><h2>{tr('rep.wealth')}</h2><p class='muted small'>{tr('rep.wealth_note')}</p>"
            f"{trees}</section>")


def render(a, comment, generated):
    global TAX
    TAX = a["tax"]
    full = a["full"]
    n = len(full)
    fin = sum(a["income"].get(m, 0) for m in full)
    fout = sum(a["spending"].get(m, 0) for m in full)
    tin, tout = a["total"].get(INCOME, 0), a["total"].get(SPENDING, 0)
    both = lambda label, avg, total: (f'<span class="vm">{tr("rep.kpi_month", label=label)}</span><span class="vt">{tr("rep.kpi_period", label=label)}</span>',
                                      f'<span class="vm">{eur(avg)}</span><span class="vt">{eur(total)}</span>')
    kpis = [
        both(tr("rep.income"), fin / n, tin),
        both(tr("rep.expenses"), fout / n, tout),
        both(tr("rep.balance"), (fin - fout) / n, tin - tout),
    ]
    wealth = [b for b in WEALTH if b in a["total"]]
    if wealth:
        kpis += [both(tr(FLOW_NAMES["out"][b]), a["avg"][b], a["total"][b]) for b in wealth]
    kpi_html = "".join(f"<div class='kpi'><span>{k}</span><b>{v}</b></div>" for k, v in kpis)
    L = json.dumps(i18n.catalog("rep.js."), ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang="{i18n.LANG}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{tr("app.name")}</title><style>{CSS}</style></head><body><main>
<script>const LANG = "{i18n.LANG}", I18N = {L};</script>
<header><h1>{tr("app.name")}</h1>
<label class="permois"><input type="checkbox" id="permois" checked> {tr("rep.per_month_toggle")}</label>
<p class="muted">{tr("rep.subtitle", start=i18n.fdate(a['first']), end=i18n.fdate(a['last']), n=a['n_tx'], months=n, generated=generated)}</p></header>
<div class="kpis">{kpi_html}</div>
<div id="analyse">{comment_block(comment)}</div>
{flow_block(a)}
<section><h2>{tr("rep.where_to")}</h2><p class="muted small"><span class="vm">{tr("rep.avg_full")}</span><span class="vt">{tr("rep.period_total")}</span> {tr("rep.where_to_note")}</p>
<div class="tree">{category_tree(a, SPENDING, "out")}</div></section>
<section><h2>{tr("rep.where_from")}</h2><div class="tree">{category_tree(a, INCOME, "in")}</div></section>
{wealth_block(a, wealth)}
<section><h2>{tr("rep.recurring")}</h2>{recurring_table(a)}</section>
{rises_block(a)}
<section><h2>{tr("rep.top")}</h2><p class='muted small'>{tr("rep.top_note")}</p>{top_table(a)}</section>
<section class="long"><h2>{tr("rep.detail")}</h2><p class="muted small">{tr("rep.partial_star")} {tr("rep.detail_sign")}</p>{cat_table(a)}</section>
<footer class="muted small">{tr("rep.footer", n=a['n_excluded'], branch=lab(OFF_BUDGET))}{tr("rep.footer_wealth", n=a['n_wealth']) if a['n_wealth'] else ""}.</footer>
<script>
// « Ramener au mois » : moyennes mensuelles (coché) ou totaux de la période ; choix mémorisé
const box = document.getElementById('permois');
try {{ if (localStorage.getItem('permois') === '0') box.checked = false; }} catch (e) {{}}
const apply = () => {{ document.body.classList.toggle('totaux', !box.checked);
  try {{ localStorage.setItem('permois', box.checked ? '1' : '0'); }} catch (e) {{}} }};
box.addEventListener('change', apply); apply();
</script><script>{COPY_JS}</script><script>{PRINT_JS}</script></main></body></html>"""


PRINT_JS = r"""
// Export PDF (impression) : chaque tableau est mis à l'échelle pour occuper exactement la largeur utile
// d'une page A4 (réduit s'il est plus large, étiré s'il est plus étroit) ; tout est rétabli après.
(() => {
const TARGET = 680;  // largeur utile d'une page A4 en px CSS, moins les marges de section
addEventListener('beforeprint', () => {
  document.querySelectorAll('section table').forEach(t => {
    if (t.closest('.ftip')) return;
    t.style.zoom = ''; t.style.width = 'max-content';
    const w = t.getBoundingClientRect().width;
    if (w > TARGET) t.style.zoom = (TARGET / w).toFixed(3);
    else t.style.width = '100%';
  });
});
addEventListener('afterprint', () => document.querySelectorAll('section table').forEach(t => { t.style.zoom = ''; t.style.width = ''; }));
})();
"""


COPY_JS = r"""
// Bouton « Copier pour Excel » au-dessus de chaque tableau : texte tabulé, montants convertis en nombres
// (« 1 035,50 € » → 1035,50 ; « · » → vide), lisibles tels quels par un Excel en français.
(() => {
const num = t => {
  let s = t.replace(/[\u00a0\u202f\s]/g, '').replace(/\u2212/g, '-');
  if (LANG !== 'fr') { s = s.replace(/^(-?)€/, '$1').replace(/,/g, ''); }  // €1,035.50 -> 1035.50
  if (s === '·') return '';
  const m = s.match(LANG === 'fr' ? /^([+-]?\d+(?:,\d+)?)(€|%)?$/ : /^([+-]?\d+(?:\.\d+)?)(%)?$/);
  return m ? m[1] + (m[2] === '%' ? '%' : '') : t.trim();
};
const tsv = table => [...table.rows].map(r => [...r.cells].map(c => {
  const t = num(c.innerText.replace(/\s+/g, ' ').trim());
  return [t, ...Array(Math.max(0, c.colSpan - 1)).fill('')].join('\t');
}).join('\t')).join('\n');
async function copy(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) {}
  const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.append(ta); ta.select(); const ok = document.execCommand('copy'); ta.remove(); return ok;
}
document.querySelectorAll('section table').forEach(table => {
  if (table.closest('.ftip')) return;
  const anchor = table.closest('.scroll') || table, b = document.createElement('button');
  b.className = 'copy'; b.type = 'button'; b.textContent = I18N['rep.js.copy'];
  b.addEventListener('click', async () => {
    const ok = await copy(tsv(table));
    b.textContent = ok ? I18N['rep.js.copied'] : I18N['rep.js.copy_failed']; setTimeout(() => b.textContent = I18N['rep.js.copy'], 1800);
  });
  anchor.before(b);
});
})();
"""


CSS = """
button.copy{font:inherit;font-size:12px;padding:3px 10px;margin:0 0 6px;border:1px solid var(--grid);border-radius:6px;
background:var(--page);color:var(--ink2);cursor:pointer}button.copy:hover{color:var(--ink);border-color:var(--base)}
.fctl{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:var(--ink2);margin-bottom:8px;align-items:center}
.fctl input[type=number]{width:52px;font:inherit;padding:2px 4px}
.flegend{font-size:12px;color:var(--ink2);margin-bottom:8px}.flegend div{display:flex;gap:4px 14px;flex-wrap:wrap;margin-bottom:3px}
.flegend b{font-weight:600;min-width:70px}.flegend i,.ftip i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.flegend i.line{height:2px;width:16px;background:var(--ink);vertical-align:3px}
svg text.warn,b.warn,.ftip tr.neg td,.wnote{fill:var(--warn);color:var(--warn)}svg text.warn{font-size:13px;font-weight:700}
.wnote{margin-top:6px;max-width:260px;white-space:normal}
.flegend i.line.cum{background:repeating-linear-gradient(90deg,var(--cum) 0 6px,transparent 6px 9px);height:3px}
.fbox{position:relative}.fbox svg{min-width:640px}svg text.ax{font-size:11px;fill:var(--muted)}
.ftip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--grid);border-radius:8px;padding:8px 10px;
font-size:12px;box-shadow:0 4px 14px #0002;z-index:2;min-width:180px}.ftip table{margin-top:4px}
.ftip td{padding:1px 4px;border:0}.ftip td:last-child{text-align:right;font-variant-numeric:tabular-nums}.ftip tr.tot td{border-top:1px solid var(--grid);font-weight:600}
[hidden]{display:none!important}
body:not(.totaux) .vt,body.totaux .vm{display:none}
.kpi b span{font:inherit;color:inherit;display:inline}body:not(.totaux) .kpi b .vt,body.totaux .kpi b .vm{display:none}
.permois{float:right;font-size:13px;display:flex;gap:6px;align-items:center;cursor:pointer;margin-top:6px}

:root{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--base:#c3c2b7;--s1:#2a78d6;--s2:#eb6834;--heat:42,120,214;color-scheme:light;
--c1:#2a78d6;--c2:#eb6834;--c3:#1baf7a;--c4:#eda100;--c5:#e87ba4;--c6:#008300;--c7:#4a3aa7;--c8:#e34948;--cother:#b5b4ad;--cum:#4a3aa7;--warn:#b86e00}
@media (prefers-color-scheme:dark){:root{--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--base:#383835;--s1:#3987e5;--s2:#d95926;--heat:57,135,229;color-scheme:dark;
--c1:#3987e5;--c2:#d95926;--c3:#199e70;--c4:#c98500;--c5:#d55181;--c6:#008300;--c7:#9085e9;--c8:#e66767;--cother:#5d5c57;--cum:#b3abf5;--warn:#eda100}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 20px 64px}
h1{margin:0;font-size:28px}h2{font-size:17px;margin:0 0 12px}h3{font-size:15px;margin:16px 0 6px}
.muted{color:var(--muted)}.small{font-size:13px}
section,.kpi{background:var(--surface);border:1px solid var(--grid);border-radius:12px}
section{padding:20px;margin-top:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:20px}
.kpi{padding:14px 16px}.kpi span{display:block;color:var(--ink2);font-size:13px}.kpi b{font-size:24px;font-variant-numeric:tabular-nums}
.comment ul{margin:0;padding-left:20px}.comment li{margin:4px 0}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:var(--ink2);margin-bottom:8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
i.s1{background:var(--s1)}i.s2{background:var(--s2)}
.scroll{overflow-x:auto}
svg{width:100%;min-width:560px;height:auto;display:block}
svg .grid{stroke:var(--grid);stroke-width:1}svg .base{stroke:var(--base);stroke-width:1}
svg .axis{fill:var(--muted);font-size:11px}
svg path.s1{fill:var(--s1)}svg path.s2{fill:var(--s2)}
svg path.partial{opacity:.45}svg path:hover{opacity:.75}
.tree details>summary{list-style:none;cursor:pointer}.tree summary::-webkit-details-marker{display:none}
.tree summary .cname::before{content:"▸";display:inline-block;width:14px;color:var(--muted);transition:transform .15s}
.tree details[open]>summary .cname::before{transform:rotate(90deg)}
.tree .leaf .cname{padding-left:14px}
.kids{margin-left:18px;border-left:1px solid var(--grid);padding-left:6px}.crow.direct .cname{color:var(--muted);font-style:italic}
.heat tr.lvl1 th,.heat tr.lvl1 td{font-weight:600;background:none}.heat tr.lvl0 th,.heat tr.lvl0 td{font-weight:700;background:none;border-top:2px solid var(--base)}
.heat tbody tr th,.heat tbody tr.lvl1 th,.heat tbody tr.lvl0 th{background:var(--surface);z-index:1}  /* colonne figée opaque, y compris pour les catégories mères */
.crow{display:grid;grid-template-columns:minmax(120px,190px) 1fr 110px 44px;gap:10px;align-items:center;padding:4px 0;font-size:14px}
.crow:hover{background:color-mix(in srgb,var(--grid) 45%,transparent);border-radius:6px}
.ctrack{height:12px}.cbar{display:block;height:100%;background:var(--s1);border-radius:0 4px 4px 0;min-width:2px}
.cval,.cpct{text-align:right;font-variant-numeric:tabular-nums}.cval small{color:var(--muted);margin-left:2px}.cpct{color:var(--ink2)}
@media (max-width:560px){.crow{grid-template-columns:1fr 90px 40px}.ctrack{grid-column:1/-1;order:5}}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:6px 8px;border-bottom:1px solid var(--grid);text-align:left;white-space:nowrap}
thead th{color:var(--ink2);font-weight:600;font-size:12px}
.num,.heat td{text-align:right;font-variant-numeric:tabular-nums}
tfoot td{font-weight:600;border-bottom:0}
tr.off td{color:var(--muted);text-decoration:line-through;text-decoration-color:var(--base)}
.chg{font-size:12px;color:var(--ink2);margin-left:6px}
.heat td{background:rgba(var(--heat),var(--a));font-size:13px}.heat td.tot{background:none;font-weight:600}
.heat tbody th{font-weight:500;position:sticky;left:0;background:var(--surface)}
footer{margin-top:20px}

/* ---- impression / export PDF : A4, fond blanc, sections non coupées, contenus à la largeur de la page */
@page{size:A4;margin:12mm 11mm}
@media print{
:root{--page:#fff;--surface:#fff;--ink:#0b0b0b;--ink2:#52514e;--muted:#6f6e69;--grid:#d9d8d1;--base:#c3c2b7;
--s1:#2a78d6;--s2:#eb6834;--heat:42,120,214;--c1:#2a78d6;--c2:#eb6834;--c3:#1baf7a;--c4:#eda100;--c5:#e87ba4;--c6:#008300;
--c7:#4a3aa7;--c8:#e34948;--cother:#b5b4ad;--cum:#4a3aa7;--warn:#b86e00;color-scheme:light}
*{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{background:#fff}
main{max-width:none;padding:0;margin:0}
.permois,.fctl,button.copy,.ftip,.tree summary::before{display:none!important}
section,.kpi{border-radius:6px}
section{padding:10px 12px;margin-top:8px;break-inside:avoid}
section.long{break-inside:auto}
h1{font-size:20px}h2{font-size:15px;break-after:avoid}h3{break-after:avoid}
.kpis{margin-top:10px;gap:8px}.kpi{padding:8px 10px}.kpi b{font-size:18px}
.scroll{overflow:visible}
.fbox svg{min-width:0;width:100%;height:auto}
thead{display:table-header-group}tr{break-inside:avoid}
.heat tbody th{position:static}
}
"""
