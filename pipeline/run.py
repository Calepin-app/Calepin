"""Point d'entrée : python3 pipeline/run.py [--open] [--no-comment] [--affiner]

La sortie console ne contient que des compteurs et des noms de fichiers, jamais de libellés ni de
montants. Le détail d'une erreur imprévue va dans private/derniere_erreur.log.
"""
import json
import sys
import traceback
import webbrowser
import calendar
from datetime import date, datetime

from analyze import analyze, summary_for_llm
from categorize import categorize, expand
from i18n import fdate, tr
from config import DATA_DIR, ERROR_LOG, PRIVATE_DIR, REPORT_FILE, VALID_FILE
from ingest import ingest
from llm import PipelineError, chat, ensure_server
from report import comment_block, render

# Consigne du LLM pour l'analyse : locales/<langue>.json, clé prompt.comment

COMMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {"type": "array", "items": {"type": "string"}},
        "suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["observations", "suggestions"],
    "additionalProperties": False,
}


LOG = []  # --log : la sortie console est aussi écrite dans travail/dernier_passage.log


def status(msg):
    print(msg, flush=True)
    LOG.append(msg)


def option(argv, name):
    return next((a.split("=", 1)[1] for a in argv if a.startswith(f"--{name}=")), None)


def period(du, au):
    """AAAA-MM[-JJ] : un mois seul va du 1er (du) ou jusqu'au dernier jour (au) ; vide = sans limite."""
    def parse(s, end):
        try:
            y, m, *d = (int(x) for x in s.split("-"))
            return date(y, m, d[0] if d else calendar.monthrange(y, m)[1] if end else 1)
        except (ValueError, TypeError):
            raise PipelineError(tr("run.bad_date", s=s))
    return parse(du, False) if du else None, parse(au, True) if au else None


def select_period(tx, du, au, status=lambda m: None):
    """Opérations de la période et bornes effectives (None si toute la période)."""
    start, end = period(du, au)
    span = None
    if start or end:
        lo, hi = min(t["date"] for t in tx), max(t["date"] for t in tx)
        start, end = max(start or lo, lo), min(end or hi, hi)
        tx = [t for t in tx if start <= t["date"] <= end]
        if not tx:
            raise PipelineError(tr("run.empty_period"))
        span = (start, end)
        status(tr("run.period", start=fdate(start), end=fdate(end)))
    return tx, span


def build_analysis(tx, du=None, au=None):
    """Commentaire du LLM sur la période, en bloc HTML à insérer dans un rapport déjà affiché."""
    a = analyze(*select_period(tx, du, au))
    return comment_block(chat(tr("prompt.comment"), summary_for_llm(a), schema=COMMENT_SCHEMA, temperature=0.3))


def build_report(tx, du=None, au=None, comment=False, status=lambda m: None):
    """HTML du rapport sur la période (opérations déjà catégorisées et ventilées)."""
    a = analyze(*select_period(tx, du, au, status))
    text = None
    if comment:
        status(tr("run.writing_comment"))
        text = chat(tr("prompt.comment"), summary_for_llm(a), schema=COMMENT_SCHEMA, temperature=0.3)
    now = datetime.now()
    return render(a, text, f"{fdate(now)} {now:%H:%M}")


def main(argv):
    stage = "stage.start"
    try:
        if not DATA_DIR.is_dir():
            raise PipelineError(tr("run.no_data_dir", path=DATA_DIR))
        PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
        stage = "stage.llm"
        ensure_server()
        stage = "stage.read"
        tx = ingest(status)
        status(tr("run.after_dedup", n=len(tx)))
        stage = "stage.categorize"
        tx = expand(categorize(tx, status, refine="--affiner" in argv))
        if "--import" in argv:  # mise à jour : import et catégorisation seulement, le rapport se génère depuis la page
            valid = json.loads(VALID_FILE.read_text(encoding="utf-8")) if VALID_FILE.exists() else {}
            status(tr("run.import_done", n=len({x['id'] for x in tx} - valid.keys())))
            return 0
        stage = "stage.report"
        html = build_report(tx, option(argv, "du"), option(argv, "au"), "--commentaire" in argv, status)
        REPORT_FILE.write_text(html, encoding="utf-8")
        status(tr("run.report", path=REPORT_FILE))
        if "--open" in argv:
            webbrowser.open(REPORT_FILE.as_uri())
        return 0
    except PipelineError as e:
        status(tr("run.failed", stage=tr(stage), msg=e))
    except Exception as e:  # message possiblement porteur de données : on ne l'affiche pas
        ERROR_LOG.write_text(traceback.format_exc(), encoding="utf-8")
        tb = traceback.extract_tb(e.__traceback__)[-1]
        status(tr("run.failed", stage=tr(stage), msg=tr("run.crash", kind=type(e).__name__,
               where=f"{tb.filename.rsplit('/', 1)[-1]}:{tb.lineno}", log=ERROR_LOG.name)))
    return 1


if __name__ == "__main__":
    code = main(sys.argv[1:])
    if "--log" in sys.argv and PRIVATE_DIR.is_dir():
        (PRIVATE_DIR / "dernier_passage.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    sys.exit(code)
