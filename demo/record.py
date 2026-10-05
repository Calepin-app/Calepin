"""Enregistre ce que le LLM local produit sur les relevés fictifs de sample/statements, pour la démo en ligne.

    python3 demo/record.py      (LM Studio lancé ; à relancer si les relevés fictifs ou les consignes changent)

Travaille dans un dossier temporaire et ne lit que sample/statements, jamais le dossier des données.
Résultat dans demo/enregistre/ : formats appris, catégories des libellés, arbre, quelques exemples de
ventilation, commentaire et validation, et l'analyse de toute la période (analyse.json, fr et en).
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo" / "enregistre"
work = Path(tempfile.mkdtemp())
(work / "data").mkdir()
(work / "travail").mkdir()
for f in (ROOT / "sample" / "statements").glob("*.csv"):
    shutil.copy2(f, work / "data" / f.name)
os.environ.update(BANQUE_DOSSIER=str(work), BANQUE_CONF=str(work / "conf.json"), BANQUE_LANG="en")
sys.path.insert(0, str(ROOT / "pipeline"))

import i18n  # noqa: E402
import revue  # noqa: E402
from categorize import categorize, expand  # noqa: E402
from ingest import ingest  # noqa: E402
from llm import ensure_server  # noqa: E402
from run import build_analysis  # noqa: E402

ensure_server()
categorize(ingest(print), print)

# quelques exemples, pour que la démo montre ventilation, commentaire et validation
st = revue.State()
salary = next(g for g in st.data()["groups"] if "SALAIRE" in g["label"])
ops = sorted(salary["ops"], key=lambda o: o["date"], reverse=True)
taxes = next(p for p in st.tax.assignable() if p.endswith(":Taxes"))
st.set_split(ops[0]["id"], [{"category": salary["category"], "amount": ops[0]["amount"] + 500},
                            {"category": taxes, "amount": -500}])
st.set_note(ops[1]["id"], "Includes the annual bonus")
st.data()
st.validate([o["id"] for o in ops[2:]], True)

analysis = {}
for lang in i18n.LANGS:
    print(f"analysis ({lang})…", flush=True)
    i18n.LANG = lang
    st.data()
    analysis[lang] = build_analysis(expand(st.tx), "", "")

shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
for f in (work / "travail").iterdir():
    if f.suffix in (".json", ".txt", ".csv"):
        shutil.copy2(f, OUT / f.name)
(OUT / "analyse.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=1), encoding="utf-8")
shutil.rmtree(work)
print("ok:", ", ".join(sorted(f.name for f in OUT.iterdir())))
