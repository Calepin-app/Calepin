"""Fabrique le paquet d'installation : python3 .github/pack.py Calepin-v0.1.0.zip

Un dossier Calepin/ avec seulement ce qu'il faut pour utiliser l'application (code, lanceurs, modèles de
catégories, relevés fictifs, README, licence), en gardant les lanceurs exécutables (droits git).
"""
import subprocess
import sys
import zipfile

KEEP = ("pipeline/", "sample/", "Calepin.command", "Calepin.bat", "calepin.sh", "categories.example.txt",
        "categories.exemple.txt", "README.md", "README.fr.md", "LICENSE")
out = sys.argv[1]
index = subprocess.run(["git", "ls-files", "-s"], capture_output=True, text=True, check=True).stdout
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for line in index.splitlines():
        meta, path = line.split("\t", 1)
        if not path.startswith(KEEP):
            continue
        info = zipfile.ZipInfo("Calepin/" + path, date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = (0o100755 if meta.startswith("100755") else 0o100644) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(path, "rb") as f:
            z.writestr(info, f.read())
print(out, len(z.namelist()), "files")
