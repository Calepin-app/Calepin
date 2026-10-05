# Calepin

*[English version](README.md)*

Analyse de relevés bancaires **100 % locale** : les transactions ne sont lues que par un LLM qui tourne
sur la machine (LM Studio, avec le modèle de son choix). Le résultat est un rapport HTML autonome.

**Vos relevés bancaires, catégorisés et mis en graphiques par une IA qui ne quitte pas votre ordinateur.**

**[▶ Essayer la démo en ligne](https://calepin-app.github.io/Calepin/fr.html)** : la vraie application, dans le
navigateur, sur des relevés fictifs, avec les réponses du LLM local enregistrées à l'avance.

<p align="center"><img src="docs/demo.gif" alt="Tour de Calepin : opérations, règles, catégories et rapport, sur des relevés fictifs" width="820"></p>

Un petit outil personnel, écrit presque entièrement par une IA (Claude) sous ma direction, que je
trouve bien pratique pour mes propres comptes. Sans grande prétention : il marche sur mon Mac avec
les exports de mes banques. Retours et suggestions bienvenus, en particulier sous Windows et Linux.

<table>
<tr>
<td width="50%"><b>Révision des opérations</b> : coches, ventilations, commentaires.<br><img src="docs/screenshots/transactions.png" alt="Opérations regroupées par libellé"></td>
<td width="50%"><b>Règles sans LLM</b>, avec aperçu en direct.<br><img src="docs/screenshots/rules.png" alt="Onglet Règles"></td>
</tr>
<tr>
<td><b>Où va l'argent / d'où il vient</b><br><img src="docs/screenshots/report-trees.png" alt="Arbres des dépenses et revenus"></td>
<td><b>Détail par catégorie et par mois</b><br><img src="docs/screenshots/report-detail.png" alt="Tableau catégorie × mois"></td>
</tr>
</table>

<sub>Captures réalisées sur des relevés fictifs, interface en anglais (sélecteur FR / EN dans la page).</sub>

## Principe

| Rôle | Qui |
|---|---|
| Lire les CSV, dédoublonner, calculer | Python (déterministe) |
| Reconnaître le format d'un CSV inconnu, catégoriser les libellés, rédiger le commentaire | LLM local |
| Écrire et maintenir le code | Claude Code, **sans jamais accéder au dossier des données** |

Le LLM ne fait **aucun calcul** : tous les chiffres du rapport viennent de Python.

**Langue** : français ou anglais, au choix dans la page (sélecteur FR / EN en haut à droite) ; par
défaut la langue de macOS. Elle s'applique à la page, au rapport, à la console et aux consignes du
LLM. L'arborescence peut avoir des racines françaises (Revenus, Dépenses…) ou anglaises (Income,
Expenses…) : au premier lancement, le modèle `categories.exemple.txt` ou `categories.example.txt` est
copié selon la langue.

## Prérequis

- Python 3.9+ (bibliothèque standard uniquement)
- [LM Studio](https://lmstudio.ai) avec au moins un modèle de conversation qui suit bien les consignes
  et gère la sortie structurée ; développé avec `mistralai/mistral-small-3.2` (~13,5 Go). Un modèle
  plus petit va plus vite mais catégorise moins bien.
- Plusieurs modèles installés : choisir dans la liste en haut de la page (● = chargé en mémoire) ;
  un seul modèle, ou un seul chargé, est pris d'office. `BANQUE_LLM_MODEL` impose un modèle.
- Le script démarre le serveur LM Studio (`lms server start`) s'il ne tourne pas ; le modèle se
  charge à la première requête et se décharge après 1 h d'inactivité.

## Utilisation

Lancer Calepin avec le lanceur de son système : la page s'ouvre dans le navigateur (ou se rouvre si
Calepin tourne déjà). La fenêtre de terminal fait tourner l'application : la fermer (ou Ctrl+C) pour
l'arrêter.

| Système | Lanceur |
|---|---|
| macOS | double-clic sur **`Calepin.command`** (la première fois : clic droit → Ouvrir) |
| Windows | double-clic sur **`Calepin.bat`** |
| Linux | **`./calepin.sh`** |

**Essayer d'abord avec des relevés fictifs** : 📁 Dossier des données → **Essayer avec des relevés
fictifs** crée `~/Calepin demo` avec quelques exports inventés (deux formats, deux comptes), puis
cliquer sur ⟳ Mettre à jour. Revenir ensuite à son propre dossier par 📁 Dossier des données.

1. Déposer les exports CSV dans `data/` du dossier des données (`./private/data` par défaut ;
   **📁 Dossier des données** l'affiche ou le change).
2. Cliquer sur **⟳ Mettre à jour** dans la page : import des nouveaux relevés et catégorisation des
   nouveaux libellés par le LLM, avec la progression affichée dans la page.
3. Valider et corriger, puis consulter l'onglet **Rapport** (période du / au mémorisée, sans LLM) ;
   **Analyse par le LLM** insère constats et pistes (1 à 4 min) ; **Exporter en PDF** ouvre
   l'impression.

Développé et testé sur macOS ; les lanceurs Windows et Linux sont fournis mais pas encore testés.
Python 3.9+ suffit (bibliothèque standard uniquement) ; sur Windows ou Linux, LM Studio charge la
version GGUF du modèle.

En ligne de commande (identique partout) : `python3 pipeline/revue.py` (l'application),
`python3 pipeline/run.py --import` (import seul), `python3 pipeline/run.py --open` (import + rapport).

| Option de `run.py` | Effet |
|---|---|
| `--import` | import et catégorisation seulement, sans rapport |
| `--open` | ouvre le rapport à la fin |
| `--commentaire` | ajoute le commentaire rédigé par le LLM (lent) |
| `--du=2025-01` / `--au=2025-12` | limite le rapport à une période (mois, ou jour : `2025-01-15`) |
| `--affiner` | recatégorise finement les libellés convertis depuis l'ancienne liste à un niveau |

Premier passage : ~20 min (tous les libellés passent par le LLM). Ensuite, seuls les nouveaux
marchands sont envoyés au modèle.

## Catégories

Le plus simple : onglet **Catégories** de la page de révision (voir plus bas) : ajouter, renommer,
déplacer, supprimer, avec report automatique sur tous les libellés et corrections concernés.
Une catégorie supprimée verse son contenu dans sa mère. « Divers » et « Autres revenus » (catégories de
repli) et les trois racines ne sont pas modifiables.

L'arborescence est stockée dans **`travail/categories.txt`** (créé au premier lancement à partir du
modèle `categories.exemple.txt`), à la GnuCash : deux espaces d'indentation = une
sous-catégorie, profondeur libre.

```
Dépenses
  Logement
    Énergie & eau
```

- Trois racines obligatoires : **Revenus**, **Dépenses**, **Hors budget** (exclu des totaux :
  virements entre ses propres comptes courants, paiement du relevé de carte).
- Deux racines facultatives, **Actif** et **Passif**, pour les flux qui touchent au patrimoine :
  versements sur l'épargne, achat immobilier, prêt accordé ; déblocage ou remboursement anticipé
  d'emprunt. Exclues du budget, elles ont leur section « Patrimoine » dans le rapport (argent versé
  net ; négatif = retiré ou emprunté). Si le relevé du compte d'épargne est aussi dans `data/`, classer
  en Actif un seul des deux côtés du virement, l'autre en Transferts internes.
- Chaque nœud cumule toute sa descendance.
- Un remboursement (CPAM, mutuelle, avoir) est classé dans la dépense qu'il rembourse et vient
  en déduction.
- **Ajouter** une catégorie : les libellés déjà classés ne bougent pas ; le LLM l'utilise pour les
  nouveaux libellés, `--affiner` pour les conversions, la page de révision pour le reste.
- **Déplacer** une catégorie (même nom, ailleurs dans l'arbre) : ses libellés et corrections la
  suivent, à condition que le nom reste unique dans l'arbre.
- **Renommer ou supprimer à la main dans le fichier** : ses libellés repassent par le LLM au passage
  suivant, **corrections manuelles comprises** ; les règles de `corrections.csv` qui la visent sont
  ignorées. D'où l'intérêt de passer par l'onglet Catégories.
- Après une modification, relancer la page de révision si elle est ouverte.
- Pas de `:` dans les noms (séparateur des chemins, ex. `Dépenses:Logement:Énergie & eau`).

### Corriger une catégorie

**Page** (`127.0.0.1:8765` uniquement ; en-tête : **⟳ Mettre à jour**, **📁 Dossier des données**, liste des modèles du LLM, sélecteur **FR / EN**) :

- les opérations sont regroupées par libellé ; un clic sur un en-tête de colonne trie (un second
  clic inverse ; les catégories suivent l'ordre de l'arbre), mémorisé pour chaque vue ;
- **Chronologique** (bouton en haut à gauche) : toutes les opérations à plat, par défaut des plus
  récentes aux plus anciennes, par mois, avec catégorie, coche de validation et ventilation ; mêmes filtres ;
- le menu d'une ligne change la catégorie du **libellé** : toutes ses opérations, y compris futures ;
- ▸ déplie le libellé pour corriger une **opération** seule (« — comme le libellé — » annule) ;
- **Ventiler** (sur une opération dépliée) la répartit sur plusieurs catégories, par exemple un
  salaire : Salaire +3 500, Impôts −600, Frais remboursés +100. Montants signés comme sur le relevé ;
  la première ligne prend le reste pour que le total égale l'opération. Stocké dans
  `travail/operations.json` ;
- **💬** (sur une opération) : commentaire libre, affiché sous l'opération et trouvé par la recherche ;
  Entrée enregistre, Échap annule. Stocké dans `travail/commentaires.json` ;
- **coches de validation** : cocher une opération confirme son affectation ; la coche du libellé
  valide toutes ses opérations (tiret : validation partielle). Le filtre **À valider** ne montre que
  les libellés ayant des opérations non cochées : après l'ajout d'un relevé, seules les nouvelles
  opérations y apparaissent. Une validation tombe d'elle-même si l'affectation de l'opération change
  ensuite (catégorie, règle, ventilation). Stocké dans `travail/validees.json` ;
- filtre **À vérifier** : libellés convertis de l'ancienne liste, non classés ou en Divers ;
- filtre **Transferts probables ⇄** : même montant en sens inverse à ≤ 5 jours d'écart avec un
  libellé différent, typiquement courant ↔ épargne ↔ carte, à passer en « Hors budget » ;
- filtre **du / au** : limite la liste à une période (indispensable sur un long historique : la
  liste reste rapide) ;
- onglet **Rapport** : voir plus haut.

**Règles** (onglet de la page de révision, ou `#regles`) : créer à la main, sans LLM, un
regroupement (motif → marchand), une règle de catégorie (motif → catégorie) ou une consigne. Les
libellés touchés s'affichent pendant la frappe du motif ; les règles existantes sont listées dessous
avec un bouton Oublier. Mêmes fichiers que l'assistant.

**Assistant** (onglet de la page de révision, ou `#assistant`) : décrire en langage naturel ce qui
ne va pas. Le LLM local voit les libellés concernés et propose des actions, chacune avec un aperçu
(libellés et opérations touchés, exemples) et un bouton Appliquer :

- **regrouper** `motif` → `nom` : les variantes d'un même marchand fusionnent en un seul libellé
  (`AMAZON|AMZN` → Amazon ; `|` sépare les variantes). Le libellé fusionné prend la catégorie
  majoritaire. Stocké dans `travail/regroupements.json`, appliqué aussi aux relevés futurs.
  Le fichier s'édite aussi à la main (liste de `{"motif": …, "nom": …}`, casse ignorée, motif cherché
  n'importe où dans le libellé nettoyé, premier motif trouvé gagnant) : recharger la page suffit ;
- **catégoriser** `motif` → catégorie : corrige les libellés actuels et ajoute la règle à
  `corrections.csv` pour les futurs ;
- **créer une catégorie** (à appliquer avant de catégoriser vers elle) ;
- **consigne** : règle générale ajoutée au prompt de catégorisation automatique
  (`travail/consignes.txt`).

Tout ce qui a été retenu est listé dans « Mémoire », avec un bouton Oublier. Compter 1 à 3 min par
réponse sur un MacBook Air M3.

Priorité : opération corrigée > libellé corrigé > `corrections.csv` > LLM. Les corrections manuelles
ne sont jamais écrasées, même par `--affiner`. Stockage : `travail/categories.json` (libellés,
`"manual": true`) et `travail/operations.json` (opérations).

**Règles par motif** : dans `travail/corrections.csv`, une règle par ligne `motif;catégorie`. Le motif est cherché dans le
libellé (casse ignorée), la catégorie est un chemin complet ou un nom seul s'il est unique :

```
LIVRET A;Épargne & placements
DECATHLON;Dépenses:Loisirs:Sport
```

Les corrections priment sur le LLM et s'appliquent à chaque passage.

## Contenu du rapport

- Entrées, dépenses et solde, puis versements vers l'actif et le passif : moyennes mensuelles (mois complets) ou totaux de la période, au choix
- Commentaire et pistes rédigés par le LLM à partir des chiffres calculés
- Flux par catégorie : histogramme mensuel, revenus vers le haut et dépenses vers le bas, catégories
  empilées (les plus grosses près de l'axe), courbe du solde. Seuil réglable (10 % par défaut) : une
  catégorie plus petite que ce pourcentage de sa branche va dans « Autres ». « Détailler les
  sous-catégories importantes » affiche à part toute sous-catégorie au-dessus du seuil ; le reste de sa
  catégorie mère reste affiché s'il atteint lui aussi le seuil, sinon il va dans « Autres »
- Arbre dépliable des dépenses et des revenus : moyenne mensuelle et part du total
- Abonnements et prélèvements récurrents : actifs ou arrêtés, variation de tarif, coût annuel
- Hausses du dernier mois complet par rapport aux 3 précédents
- Plus grosses dépenses ponctuelles (hors récurrents)
- Tableau catégorie × mois

## Fichiers

```
<dossier des données>/   par défaut ./private ; ailleurs possible (cf. « Dossier des données »)
  data/                  relevés CSV bruts
  travail/               tout ce qui en est dérivé
    formats.json           « carte » de chaque format de CSV reconnu
    categories.json        cache libellé → catégorie + nom de marchand
    categories.txt         arborescence des catégories (modifiable aussi depuis la page)
    corrections.csv        règles de correction par motif
    operations.json        corrections d'opérations individuelles et ventilations
    validees.json          opérations dont l'affectation a été validée
    commentaires.json      commentaires libres sur des opérations
    regroupements.json     variantes de libellés fusionnées (motif → marchand)
    consignes.txt          consignes retenues pour le LLM
    rapport.html           dernier rapport affiché
    dernier_passage.log    sortie du dernier « Mettre à jour »
    derniere_erreur.log    trace complète de la dernière erreur imprévue
.banque.json             chemin du dossier des données (local, exclu de git)
categories.exemple.txt   modèle d'arborescence, copié dans travail/ au premier lancement
Calepin.command / Calepin.bat / calepin.sh   lanceurs macOS / Windows / Linux
pipeline/
  run.py                 point d'entrée
  revue.py               page (opérations, catégories, règles, assistant, rapport)
  dossier.py             choix du dossier des données
  arbre.py               édition de l'arborescence avec report des changements
  assistant.py           dialogue avec le LLM et application des actions
  ingest.py              reconnaissance des formats, lecture, dédoublonnage par compte
  categorize.py          catégorisation par le LLM, cache, corrections
  taxonomy.py            lecture de l'arborescence
  analyze.py             calculs
  report.py              génération du HTML
  llm.py                 client de l'API locale de LM Studio
  config.py              chemins et réglages
.gitignore               exclut ./private, .banque.json et les réglages locaux de git
.claude/settings.json    interdit à Claude Code de lire ./private (et l'ancien ./data)
.claude/settings.local.json   idem pour le dossier choisi s'il est ailleurs (local, exclu de git)
```

## Dossier des données

Tout ce qui est personnel est dans un seul dossier : `data/` (relevés bruts, à alimenter) et
`travail/` (le reste). Par défaut `./private`, exclu de git. Pour le placer ailleurs (dossier
sauvegardé, synchronisé…) : le déplacer en entier, puis cliquer sur **📁 Dossier des données** dans
la page et le désigner (Parcourir… ou chemin collé) ; Calepin redémarre dessus. Le choix est mémorisé
dans `.banque.json` ; s'il est introuvable, la page le signale et propose d'en choisir un, au lieu de
recréer un dossier vide. `python3 pipeline/dossier.py
--afficher` donne le dossier actuel. L'ancienne organisation (`./data` et `./private` à plat) est
convertie automatiquement au premier lancement.

## Fonctionnement

**Formats.** Pour un CSV jamais vu, le LLM lit les 25 premières lignes et renvoie une carte du
format : ligne d'en-tête, séparateur, colonnes date / libellé / montant (ou débit + crédit), virgule
décimale. Python la valide en parsant tout le fichier (≥ 90 % de lignes lues), la corrige si
l'en-tête est décalé, puis la mémorise. Un format connu est reconnu à sa ligne d'en-tête, même si le
préambule change de longueur. Les libellés sur plusieurs lignes (Crédit Agricole) sont gérés.

**Signes.** Montant négatif = sortie d'argent. Pour les formats à colonne de montant unique où
les achats sont positifs (Amex), le signe majoritaire est pris pour celui des dépenses.

**Dédoublonnage.** Les relevés d'un même format sont d'abord regroupés par compte : deux relevés
viennent du même compte s'ils partagent la plupart des opérations de leur période commune (exports qui
se chevauchent). Dans un même compte, une opération (date, montant, libellé) est comptée autant de fois
qu'elle apparaît au maximum dans un même fichier : les chevauchements ne créent pas de doublons, deux
cafés identiques le même jour restent deux cafés. Entre comptes différents, les opérations
s'additionnent : deux virements identiques émis le même jour par deux comptes sont bien deux virements.

**Catégorisation.** Les libellés sont normalisés (dates, références, numéros retirés), puis envoyés
par lots de 40 au LLM, avec le sens (débit / crédit) mais sans montant. La réponse est contrainte par
un schéma JSON qui n'autorise que les chemins de `categories.txt`.

**Récurrents.** Même marchand sur ≥ 3 mois, écart médian de 24 à 38 jours, montant stable d'un mois
sur l'autre (une hausse de tarif est tolérée). Actif si vu dans les 45 derniers jours.

## Confidentialité

- La console n'affiche que des noms de fichiers et des compteurs. Une erreur imprévue n'affiche que
  son type et sa ligne de code ; la trace complète (qui peut contenir des données) va dans
  `travail/derniere_erreur.log`.
- Les formats sont nommés `n°1`, `n°2`… : aucun nom venant des fichiers n'est affiché.
- Pour faire évoluer le code, Claude Code travaille sur des relevés fictifs générés à l'identique.
  En cas de problème sur un vrai fichier, lui transmettre uniquement la ligne `ÉCHEC …`.

## Dépannage

| Message | Que faire |
|---|---|
| `Serveur LM Studio injoignable` | ouvrir LM Studio, ou `~/.lmstudio/bin/lms server start` |
| `LM Studio a répondu HTTP 404` | le modèle choisi n'est plus installé : en choisir un autre dans la page |
| `… modèles installés dans LM Studio : choisir …` | choisir un modèle dans la liste en haut de la page |
| `Format non reconnu pour <fichier>` | format que le LLM n'a pas su lire : décrire sa structure avec des valeurs inventées |
| `Le format connu ne permet plus de lire <fichier>` | la banque a changé son export : supprimer l'entrée dans `travail/formats.json` |
| macOS refuse d'ouvrir `Calepin.command` | clic droit → Ouvrir → confirmer |
| `relevé(s) dans un format pas encore reconnu` | cliquer sur ⟳ Mettre à jour : le LLM apprend le format |

## Automatisation (facultatif)

Pour importer automatiquement, lancer `python3 pipeline/run.py --import` depuis le planificateur du
système (launchd ou cron sur macOS/Linux, Planificateur de tâches sur Windows). Exemple launchd
(macOS) dans le [README anglais](README.md#automation-optional).

## Licence

Copyright 2026 The Calepin authors. Distribué sous [licence Apache 2.0](LICENSE).
