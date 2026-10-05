"""Choisit le dossier des données (relevés + fichiers de travail) et le mémorise dans .banque.json.

Utilisé par la page (bouton « Dossier des données ») ; aussi en ligne de commande :
  python3 pipeline/dossier.py              ouvre un sélecteur de dossier
  python3 pipeline/dossier.py <chemin>     utilise ce chemin
  python3 pipeline/dossier.py --afficher   affiche le dossier actuel

Le dossier contient data/ (relevés CSV) et travail/ (le reste) ; ils sont créés s'ils manquent.
Pour déplacer les données : déplacer le dossier entier, puis le choisir ici.
Ajoute aussi à .claude/settings.local.json l'interdiction pour Claude Code de lire ce dossier.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from config import BASE_DIR, DEFAULT_BASE, ROOT, save_conf
from i18n import tr

CLAUDE_LOCAL = ROOT / ".claude" / "settings.local.json"
SAMPLE = ROOT / "sample" / "statements"  # relevés fictifs pour essayer Calepin
DEMO = Path.home() / "Calepin demo"


def current():
    return str(BASE_DIR) + (tr("dir.default") if BASE_DIR == DEFAULT_BASE else "")


def choose():
    """Sélecteur de dossier natif : AppleScript sur macOS, Tk ailleurs (dans un processus à part)."""
    if sys.platform == "darwin":
        cmd = ["osascript", "-e", f'POSIX path of (choose folder with prompt "{tr("dir.prompt")}")']
    else:
        cmd = [sys.executable, "-c", "import tkinter, tkinter.filedialog as f; r = tkinter.Tk(); r.withdraw(); "
               "r.attributes('-topmost', True); print(f.askdirectory(title=%r) or '')" % tr("dir.prompt")]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=600).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def protect(path):
    """Interdit à Claude Code de lire ou modifier le dossier, où qu'il soit (règles locales, hors git)."""
    p = str(path).rstrip("/\\")
    rules = [f"Read(/{p}/**)", f"Edit(/{p}/**)",
             *(f"Bash({c} {p}/*)" for c in ("cat", "head", "tail", "less")), f"Bash(grep * {p}/*)"]
    try:
        conf = json.loads(CLAUDE_LOCAL.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        conf = {}
    deny = conf.setdefault("permissions", {}).setdefault("deny", [])
    deny += [r for r in rules if r not in deny]
    CLAUDE_LOCAL.parent.mkdir(exist_ok=True)
    CLAUDE_LOCAL.write_text(json.dumps(conf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def set_folder(chosen):
    """Valide et mémorise le dossier ; renvoie (ok, message). Prend effet au redémarrage de l'application."""
    path = Path(chosen).expanduser().resolve()
    if not path.is_dir():
        return False, tr("dir.not_found", path=path)
    missing = [d for d in ("data", "travail") if not (path / d).is_dir()]
    if len(missing) == 2 and any(path.iterdir()):
        return False, tr("dir.not_empty", path=path)
    for d in missing:
        (path / d).mkdir()
    save_conf(dossier=str(path))
    protect(path)
    return True, tr("srv.folder", path=path) + (tr("dir.created", dirs=", ".join(missing)) if missing else "")


def make_demo():
    """Dossier d'essai hors du projet (~/Calepin demo), garni des relevés fictifs ; renvoie (ok, message)."""
    (DEMO / "data").mkdir(parents=True, exist_ok=True)
    for f in SAMPLE.glob("*.csv"):
        if not (DEMO / "data" / f.name).exists():
            shutil.copy2(f, DEMO / "data" / f.name)
    return set_folder(DEMO)


def main(argv):
    if argv[:1] == ["--afficher"]:
        print(tr("srv.folder", path=current()))
        return 0
    chosen = argv[0] if argv else choose()
    if not chosen:
        print(tr("dir.none"))
        return 1
    ok, msg = set_folder(chosen)
    print(msg)
    if ok:
        print(tr("dir.restart"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
