"""Chemins et réglages.

Toutes les données vivent dans un seul dossier (« dossier des données »), qui peut être hors du projet :
  <dossier>/data/     relevés CSV bruts
  <dossier>/travail/  tout ce qui en est dérivé (formats, catégories, corrections, rapport…)
Son chemin est mémorisé dans .banque.json à la racine du projet (exclu de git) ; par défaut ./private.
Pour le changer : bouton « Dossier des données » de la page, ou python3 pipeline/dossier.py.
BANQUE_DOSSIER, ou BANQUE_DATA / BANQUE_PRIVATE, permettent de tester sur des données fictives.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONF = Path(os.environ.get("BANQUE_CONF", ROOT / ".banque.json"))  # BANQUE_CONF : tests
DEFAULT_BASE = ROOT / "private"


def read_conf():
    """Réglages locaux (.banque.json) : dossier, langue, modele."""
    try:
        conf = json.loads(LOCAL_CONF.read_text(encoding="utf-8"))
        return conf if isinstance(conf, dict) else {}
    except (OSError, ValueError):
        return {}


def save_conf(**values):
    """Met à jour des réglages en gardant les autres."""
    conf = read_conf()
    conf.update(values)
    LOCAL_CONF.write_text(json.dumps(conf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def configured_base():
    try:
        return Path(read_conf()["dossier"]).expanduser()
    except (KeyError, TypeError):
        return DEFAULT_BASE


TESTING = any(k in os.environ for k in ("BANQUE_DOSSIER", "BANQUE_DATA", "BANQUE_PRIVATE", "BANQUE_CONF"))
BASE_DIR = Path(os.environ.get("BANQUE_DOSSIER") or configured_base())
DATA_DIR = Path(os.environ.get("BANQUE_DATA", BASE_DIR / "data"))
PRIVATE_DIR = Path(os.environ.get("BANQUE_PRIVATE", BASE_DIR / "travail"))


def migrate_layout():
    """Ancienne organisation (./data et ./private à plat) -> ./private/data et ./private/travail.
    Simple déplacement de fichiers, sans les lire ; uniquement sur le vrai dossier par défaut."""
    old_data = ROOT / "data"
    if TESTING or BASE_DIR != DEFAULT_BASE or (BASE_DIR / "travail").exists():
        return
    if not old_data.exists() and not DEFAULT_BASE.exists():
        return
    (BASE_DIR / "travail").mkdir(parents=True)
    if DEFAULT_BASE.exists():
        for f in DEFAULT_BASE.iterdir():
            if f.name not in ("data", "travail"):
                f.rename(BASE_DIR / "travail" / f.name)
    if old_data.exists() and not (BASE_DIR / "data").exists():
        old_data.rename(BASE_DIR / "data")


migrate_layout()

SCHEMAS_FILE = PRIVATE_DIR / "formats.json"         # carte de chaque format CSV
CATEGORIES_FILE = PRIVATE_DIR / "categories.json"   # cache libellé -> catégorie
CORRECTIONS_FILE = PRIVATE_DIR / "corrections.csv"  # corrections manuelles (prioritaires)
VALID_FILE = PRIVATE_DIR / "validees.json"          # opérations dont la catégorie a été validée
COMMENTS_FILE = PRIVATE_DIR / "commentaires.json"  # commentaire libre par opération
OVERRIDES_FILE = PRIVATE_DIR / "operations.json"    # catégorie imposée à une opération précise
GROUPS_FILE = PRIVATE_DIR / "regroupements.json"    # motif -> marchand unique (variantes de libellés)
NOTES_FILE = PRIVATE_DIR / "consignes.txt"          # consignes retenues pour le LLM, une par ligne
REPORT_FILE = PRIVATE_DIR / "rapport.html"
ERROR_LOG = PRIVATE_DIR / "derniere_erreur.log"

LLM_URL = os.environ.get("BANQUE_LLM_URL", "http://127.0.0.1:1234/v1/chat/completions")
LLM_MODEL = os.environ.get("BANQUE_LLM_MODEL")  # sinon : choix de la page, dans .banque.json (llm.model())
LMS_BIN = Path.home() / ".lmstudio" / "bin" / ("lms.exe" if os.name == "nt" else "lms")

# arborescence des catégories : vit dans private/ avec les autres données ; categories.exemple.txt sert de
# modèle au premier lancement (un ancien categories.txt à la racine y est déplacé tel quel)
TAXONOMY_FILE = Path(os.environ.get("BANQUE_TAXONOMY", PRIVATE_DIR / "categories.txt"))
TAXONOMY_SAMPLES = {"fr": ROOT / "categories.exemple.txt", "en": ROOT / "categories.example.txt"}
if not TAXONOMY_FILE.exists() and TAXONOMY_FILE.parent.parent.exists():  # dossier introuvable : rien à créer
    TAXONOMY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if (ROOT / "categories.txt").exists() and not TESTING:  # ancien emplacement, jamais pour un dossier de test
        (ROOT / "categories.txt").rename(TAXONOMY_FILE)
    # sinon taxonomy.load() le crée à partir du modèle de la langue choisie
