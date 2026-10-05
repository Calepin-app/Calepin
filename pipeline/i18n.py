"""Interface language (French / English).

The language is stored in .banque.json ("langue"); by default it follows the macOS language.
BANQUE_LANG overrides it (tests). Strings live in locales/<lang>.json, as flat "dotted.keys";
a missing key falls back to French, then to the key itself.
"""
import json
import locale
import os
import subprocess
import sys
from pathlib import Path

from config import read_conf, save_conf

LANGS = ("fr", "en")
_DIR = Path(__file__).with_name("locales")


def system_lang():
    """Langue du système : réglage macOS, sinon locale (Windows, Linux)."""
    code = ""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=3).stdout
            code = out.split('"')[1]
        except (OSError, IndexError, subprocess.SubprocessError):
            pass
    if not code:
        code = locale.getlocale()[0] or os.environ.get("LANG", "")
    return "fr" if code.lower().startswith("fr") else "en"


def configured_lang():
    if os.environ.get("BANQUE_LANG") in LANGS:
        return os.environ["BANQUE_LANG"]
    lang = read_conf().get("langue")
    return lang if lang in LANGS else system_lang()


def set_lang(lang):
    """Remembers the language in .banque.json (keeps the other settings)."""
    global LANG
    save_conf(langue=lang)
    LANG = lang


_CAT = {lang: json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8")) for lang in LANGS}
LANG = configured_lang()


def tr(key, **kw):
    s = _CAT[LANG].get(key) or _CAT["fr"].get(key) or key
    return s.format(**kw) if kw else s


def catalog(prefix):
    """Every string whose key starts with prefix, for the page's JavaScript."""
    return {k: v for k, v in {**_CAT["fr"], **_CAT[LANG]}.items() if k.startswith(prefix)}


def fdate(d, short=False):
    """Date au format de la langue : 26/09/2026 ou 2026-09-26 (court : 26/09/26 ou 26-09-26)."""
    if LANG == "fr":
        return d.strftime("%d/%m/%y" if short else "%d/%m/%Y")
    return d.strftime("%d %b %y" if short else "%Y-%m-%d")


def money(x, dec=0):
    """Montant : « 1 035,50 € » en français, « €1,035.50 » en anglais."""
    s = f"{x:,.{dec}f}"
    if LANG == "fr":
        return s.replace(",", "\u202f").replace(".", ",") + "\u00a0€"  # espaces insécables, comme le rapport
    return ("-€" + s[1:]) if s.startswith("-") else "€" + s


MONTHS = {"fr": "janv. févr. mars avr. mai juin juil. août sept. oct. nov. déc.".split(),
          "en": "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()}
