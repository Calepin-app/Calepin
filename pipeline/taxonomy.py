"""Category tree, read from categories.txt. A node is identified by its path
(« Dépenses:Logement:Énergie & eau »); the implicit root is the empty path.

Internally the five roots always carry their French names (stable keys in every saved file); a tree
written with English roots (Income, Expenses…) is accepted, and shown/written back as written."""
from config import TAXONOMY_FILE, TAXONOMY_SAMPLES
import i18n
from i18n import tr
from llm import PipelineError

SEP = ":"
INCOME, SPENDING, OFF_BUDGET = "Revenus", "Dépenses", "Hors budget"
ASSET, LIABILITY = "Actif", "Passif"  # facultatives : flux qui constituent ou réduisent le patrimoine
ROOTS = (INCOME, SPENDING, ASSET, LIABILITY, OFF_BUDGET)
BUDGET = (INCOME, SPENDING)
WEALTH = (ASSET, LIABILITY)
ALIASES = {"Income": INCOME, "Expenses": SPENDING, "Assets": ASSET, "Liabilities": LIABILITY, "Off-budget": OFF_BUDGET}
# catégories de repli (« Divers » / « Autres revenus » ou leur équivalent anglais)
FALLBACK_LEAVES = {SPENDING: ("Divers", "Miscellaneous"), INCOME: ("Autres revenus", "Other income")}


class Taxonomy:
    def __init__(self, text):
        self.paths = []  # ordre du fichier
        self.root_names = {}  # racine interne -> nom écrit dans le fichier
        stack = []       # (indentation, nom)
        for n, raw in enumerate(text.splitlines(), 1):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            name = raw.strip()
            if SEP in name:
                raise PipelineError(tr("tax.colon", n=n, sep=SEP))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if not stack:
                canon = ALIASES.get(name, name)
                if canon not in ROOTS:
                    raise PipelineError(tr("tax.unknown_root", n=n, name=name))
                self.root_names[canon] = name
                name = canon
            stack.append((indent, name))
            path = SEP.join(s[1] for s in stack)
            if path in self.paths:
                raise PipelineError(tr("tax.duplicate", n=n, path=path))
            self.paths.append(path)
        for root in (INCOME, SPENDING, OFF_BUDGET):
            if root not in self.paths:
                raise PipelineError(tr("tax.missing_root", root=root))
        self.children = {p: [] for p in ["", *self.paths]}
        for p in self.paths:
            self.children[parent(p)].append(p)

    def assignable(self):
        return [p for p in self.paths if SEP in p]

    def label(self, path):
        """Chemin tel qu'écrit dans categories.txt (racine anglaise si le fichier est en anglais)."""
        r, _, rest = path.partition(SEP)
        r = self.root_names.get(r, r)
        return f"{r}{SEP}{rest}" if rest else r

    def resolve(self, s):
        """Chemin complet (racine interne ou telle qu'écrite), ou nom de catégorie s'il est unique ; sinon None."""
        parts = [x.strip() for x in s.replace(">", SEP).replace("›", SEP).split(SEP)]
        names = {v: k for k, v in self.root_names.items()}
        parts[0] = names.get(parts[0], ALIASES.get(parts[0], parts[0]))
        s = SEP.join(parts)
        if s in self.paths:
            return s
        hits = [p for p in self.paths if p.endswith(SEP + s)]
        return hits[0] if len(hits) == 1 else None

    def remap(self, path):
        """Chemin toujours valide ; sinon, catégorie déplacée dans l'arbre (même nom, unique) ; sinon None."""
        if path in self.paths and SEP in path:
            return path
        moved = self.resolve(leaf(path or ""))
        return moved if moved and SEP in moved else None

    def fallbacks(self):
        """Catégories de repli existantes : {racine: chemin}."""
        out = {}
        for r, leaves in FALLBACK_LEAVES.items():
            hit = next((f"{r}{SEP}{x}" for x in leaves if f"{r}{SEP}{x}" in self.paths), None)
            out[r] = hit or next((p for p in self.children[r]), r)
        return out

    def fallback(self, amount):
        return self.fallbacks()[SPENDING if amount < 0 else INCOME]


def parent(path):
    return path.rsplit(SEP, 1)[0] if SEP in path else ""


def ancestors(path):
    """Le nœud lui-même et tous ses parents jusqu'à la racine de branche."""
    parts = path.split(SEP)
    return [SEP.join(parts[:i]) for i in range(len(parts), 0, -1)]


def root(path):
    return path.split(SEP, 1)[0]


def leaf(path):
    return path.rsplit(SEP, 1)[-1]


def depth(path):
    return path.count(SEP)


def load():
    if not TAXONOMY_FILE.exists() and TAXONOMY_FILE.parent.is_dir():  # premier lancement : modèle de la langue
        sample = TAXONOMY_SAMPLES.get(i18n.LANG, TAXONOMY_SAMPLES["fr"])
        TAXONOMY_FILE.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
    return Taxonomy(TAXONOMY_FILE.read_text(encoding="utf-8"))
