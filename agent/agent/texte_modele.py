"""Ce que le modele relit de ses tours passes, et ce qu'il ecrit a l'utilisateur.

Pourquoi (mesure live du 2026-09-26, defaut D1 de la fiche de mesure) : au
tour « Oui, je veux uniquement le bati dans le perimetre communal d'Aix… », le
modele a ecrit dans son TEXTE

    > **`get_project_info`** — pour récupérer le layer_id…

au lieu d'emettre un appel d'outil. Le tour s'est arrete apres iter=0, sans
action, et l'utilisateur est reste devant « Rédaction de la réponse… 5 s ».
Cause : le contenu assistant stocke en base est le RENDU de l'interface
(bloc `<details class="agent-reasoning">`, puis une ligne de citation par
outil appele), et les 20 derniers messages repartaient tels quels au modele.
Il imitait ce format.

Ce module regroupe trois fonctions pures :

- `contenu_assistant_pour_le_modele` : le texte final destine a
  l'utilisateur, precede d'un memo factuel des actions du tour, redige pour
  ne ressembler a aucune syntaxe d'appel. Ce qui est stocke pour l'affichage
  ne change pas : la transformation se fait en construisant les messages ;
- `appel_ecrit_en_texte` : reconnait un appel d'outil ecrit en texte, pour
  relancer le modele une fois au lieu de clore le tour sur du vide ;
- `retirer_emojis` : defaut D5, le modele semait des emojis (triangle
  d'alerte, pastilles de couleur) dans ses reponses a des utilisateurs grand
  public. Les chiffres ne sont jamais touches ;
- `NettoyeurSortie` / `humaniser` : garde-fou de sortie du lot 3. Malgre la
  regle de prompt « aucun nom d'outil », le modele a ecrit a l'utilisateur
  (mesure live du 2026-09-26, apres le lot 2) « il faut effectuer un
  decoupage (`clip_to_study_zone`) » et « l'emprise rectangulaire (la
  `bbox`) ». Les noms d'outils connus deviennent leur libelle courant, le
  mot « bbox » devient « rectangle de la zone », de facon deterministe et
  en flux.
"""
from __future__ import annotations

import json
import re

# ── Nettoyage du rendu de l'interface ───────────────────────────────────────

# Le raisonnement insere par `reasoning_details_html` : masque dans le chat,
# mais relu par le modele il pesait lourd et n'apportait rien de verifie.
_RAISONNEMENT = re.compile(
    r'<details class="agent-reasoning">.*?</details>\s*', re.DOTALL,
)

# Le marqueur de point de retour pose avant un outil mutant.
_MARQUEUR_CKPT = re.compile(r"<!--ckpt:[^>]*-->\s*")

# Une ligne d'appel telle que l'interface la rend (`> **`nom`** — `k=v``),
# suivie eventuellement du bloc de resultat que le chat replie. C'est
# exactement ce que le modele a recopie le 2026-09-26.
_LIGNE_APPEL_RENDUE = re.compile(
    r"^[ \t]*>[ \t]*\*\*`[^`\n]+`\*\*[^\n]*\n?"
    r"(?:[ \t]*\n)*"
    r"(?:[ \t]*```[^\n]*\n.*?\n[ \t]*```[ \t]*\n?)?",
    re.MULTILINE | re.DOTALL,
)

# Le lien de livrable garde son information (l'URL), sans l'emoji ni le gras
# qui en faisaient un gabarit a imiter.
# U+1F4CE : le trombone que pose `chat_stream` devant le lien.
_LIEN_LIVRABLE = re.compile("\U0001F4CE" r"\s*\*\*Livrable publie\*\*\s*:")

_LIGNES_VIDES = re.compile(r"\n{3,}")

# Debut du memo des actions : sert aussi a reconnaitre un memo recopie par le
# modele au lieu d'agir (cf. `appel_ecrit_en_texte`).
MEMO_PREFIXE = "[Mémo interne, non affiché"


def texte_final(contenu: str | None) -> str:
    """Le texte que l'utilisateur lit, sans raisonnement ni rendu d'outils.

    Idempotente : l'appliquer deux fois donne le meme resultat.
    """
    texte = contenu or ""
    texte = _RAISONNEMENT.sub("", texte)
    texte = _MARQUEUR_CKPT.sub("", texte)
    texte = _LIGNE_APPEL_RENDUE.sub("", texte)
    texte = _LIEN_LIVRABLE.sub("Livrable publié :", texte)
    texte = _LIGNES_VIDES.sub("\n\n", texte)
    return texte.strip()


# ── Memo des actions du tour ────────────────────────────────────────────────

_ECHEC = re.compile(r'"success"\s*:\s*false|"error"\s*:\s*(?!null)|^\s*(?:Erreur|Error)\b',
                    re.IGNORECASE | re.MULTILINE)
_MESSAGE_ERREUR = re.compile(r'"error"\s*:\s*"([^"]{1,80})')
# Les cles qui portent un fait utile pour la suite (identifiant de couche,
# nombre d'entites). `result` est deja tronque a 200 caracteres en base :
# on lit par motif plutot que de parser un JSON coupe.
_FAIT_CLE = re.compile(
    r'"(layer_id|layer_name|feature_count|count|total|n_features|output)"'
    r'\s*:\s*("[^"\n]{1,60}"|-?\d[\d.]*)'
)


def _resultat_cle(resultat: str) -> str:
    if _ECHEC.search(resultat or ""):
        m = _MESSAGE_ERREUR.search(resultat)
        return "échec" + (f" ({m.group(1)})" if m else "")
    faits = [f"{k} {v.strip(chr(34))}" for k, v in _FAIT_CLE.findall(resultat or "")[:2]]
    return "réussi" + (f", {', '.join(faits)}" if faits else "")


def memo_outils(tool_calls) -> str:
    """Une ligne qui dit ce qui a ete execute, sans forme d'appel.

    Pas de backtick, pas de gras, pas de parenthese apres un nom, pas de
    citation : rien que le modele puisse prendre pour un gabarit d'appel.
    `tool_calls` est la colonne de la base (JSON) ou la liste deja decodee.
    """
    if isinstance(tool_calls, str):
        try:
            tool_calls = json.loads(tool_calls)
        except ValueError:
            return ""
    if not isinstance(tool_calls, list) or not tool_calls:
        return ""
    actions = []
    for appel in tool_calls:
        if not isinstance(appel, dict) or not appel.get("tool"):
            continue
        actions.append(f"{appel['tool']} : {_resultat_cle(str(appel.get('result') or ''))}")
    if not actions:
        return ""
    return (f"{MEMO_PREFIXE} à l'utilisateur. Actions déjà exécutées à ce tour "
            f"par de vrais appels d'outils : {' ; '.join(actions)}.]")


def contenu_assistant_pour_le_modele(contenu: str | None, tool_calls=None) -> str:
    """Le message assistant tel que le modele doit le relire.

    Memo des actions d'abord (ordre chronologique : les outils precedent la
    reponse), puis le texte final. Vide si le tour n'a rien produit de
    visible ni d'action : l'appelant saute alors le message.
    """
    memo = memo_outils(tool_calls)
    texte = texte_final(contenu)
    return "\n\n".join(p for p in (memo, texte) if p)


# ── Appel d'outil ecrit en texte ────────────────────────────────────────────

# Formes vues ou attendues d'un appel « narre » :
#   > **`nom`** — …      (le rendu de l'interface, recopie : cas du 2026-09-26)
#   **`nom`**            (meme gabarit hors citation)
#   nom(…)               (pseudo-code)
#   <tool_call>…         (appel au format du modele, non reconnu par le serveur)
_APPEL_GENERIQUE = re.compile(r"<tool_call>|<function=", re.IGNORECASE)


def appel_ecrit_en_texte(texte: str | None, noms_outils) -> str | None:
    """Le nom de l'outil « appele » en texte, ou None.

    Seuls les outils exposes au modele comptent : une phrase qui cite un nom
    d'outil en passant (« avec clip_to_study_zone ») n'est PAS un appel -- on
    ne relance pas pour ca, le prompt traite ce defaut de surface (D5).
    """
    if not texte:
        return None
    if texte.lstrip().startswith(MEMO_PREFIXE):
        return "memo recopie"
    if _APPEL_GENERIQUE.search(texte):
        return "tool_call"
    noms = sorted({n for n in (noms_outils or ()) if n}, key=len, reverse=True)
    if not noms:
        return None
    alt = "|".join(re.escape(n) for n in noms)
    motifs = (
        rf"\*\*`({alt})`\*\*",
        rf"^[ \t]*>[ \t]*[*`]*({alt})\b",
        rf"(?<![\w.])({alt})\(",
    )
    for motif in motifs:
        m = re.search(motif, texte, re.MULTILINE)
        if m:
            return m.group(1)
    return None


# ── Emojis ──────────────────────────────────────────────────────────────────

# Pictogrammes (U+1F000-1FAFF), symboles divers et dingbats (U+2600-27BF :
# triangle d'alerte, sens interdit, coches et croix), quelques etoiles et
# carres (U+2B1B, 2B1C, 2B50, 2B55), selecteur de variante (U+FE0F) et liant
# invisible (U+200D). On ne touche ni aux chiffres, ni aux fleches (U+2192)
# ou aux signes (>=, x) qui portent du sens : un chiffre habille en emoji
# (chiffre + U+FE0F + U+20E3) perd son habillage mais garde son chiffre.
_EMOJI = re.compile(
    r"(?:[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B1B\u2B1C\u2B50\u2B55]"
    r"[\uFE0F\u200D]*)+ ?"
    r"|[\uFE0F\u20E3]"
)


def retirer_emojis(texte: str) -> str:
    """Retire les emojis d'un fragment de reponse, chiffres intacts.

    Travaille fragment par fragment (le texte arrive en flux) : un emoji est
    un point de code entier dans une chaine decodee, jamais coupe en deux.
    """
    if not texte:
        return texte
    return _EMOJI.sub("", texte)


# ── Noms d'outils et jargon dans le texte visible (lot 3) ───────────────────

# Libelle courant de chaque outil, en groupe nominal avec son article : il
# remplace le nom dans une phrase (« avec `smart_load` » -> « avec le
# chargement des donnees »). A distinguer de `_LIBELLES_OUTILS` de
# qgis_agent.py, qui sont des libelles d'etat (« Chargement des données… ») ;
# un test verifie que tout outil qui a un libelle d'etat a aussi celui-ci.
LIBELLES_COURANTS = {
    "clip_to_study_zone":   "le découpage selon les limites de la commune",
    "smart_load":           "le chargement des données",
    "get_project_info":     "la lecture du projet",
    "get_features":         "la lecture des entités",
    "list_datasources":     "la consultation du catalogue",
    "add_from_catalog":     "le chargement depuis le catalogue",
    "add_layer":            "l'ajout de la couche",
    "remove_layer":         "le retrait de la couche",
    "execute_python":       "un calcul dans QGIS",
    "execute_async":        "un calcul long dans QGIS",
    "poll_job":             "le suivi du calcul",
    "run_processing":       "un traitement QGIS",
    "search_algorithms":    "la recherche d'un traitement",
    "run_recipe":           "l'exécution de la recette",
    "save_recipe":          "l'enregistrement de la recette",
    "get_recipe":           "la lecture de la recette",
    "list_recipes":         "la liste des recettes",
    "list_recipes_for_study": "la liste des recettes de l'étude",
    "set_layer_style":      "la mise en forme de la couche",
    "set_layer_visibility": "l'affichage des couches",
    "zoom_to":              "le cadrage de la carte",
    "set_study_zone":       "la définition de la zone d'étude",
    "get_study_zone":       "la lecture de la zone d'étude",
    "get_screenshot":       "la capture de la carte",
    "export_layer":         "l'export de la couche",
    "export_pdf":           "l'export PDF",
    "export_web_map":       "la préparation de la carte web",
    "export_flood_map":     "la carte des zones inondables",
    "export_temporal_map":  "la carte d'évolution",
    "publish_artifact":     "la publication du livrable",
    "save_project":         "l'enregistrement du projet",
    "memory_search":        "la recherche dans la mémoire",
    "create_component":     "la création d'une brique de livrable",
    "create_assembly":      "l'assemblage du livrable",
    "publish_assembly":     "la publication du livrable",
    "describe_entity_schema": "la lecture du format attendu",
    "validate_manifest":    "la vérification du livrable",
    "upload_file":          "l'envoi du fichier",
    "download_project":     "le téléchargement du projet",
}
# Outil expose mais sans libelle : mieux vaut une formule vague qu'un nom de
# fonction (meme choix que le libelle d'etat generique).
_LIBELLE_INCONNU = "l'action correspondante"

# Decorations Markdown qui entourent un nom : code, gras, les deux.
_DECO = r"(?:\*\*`|`\*\*|\*\*|__|`)"

# « la bbox » -> « le rectangle de la zone » : l'article suit le genre du nom.
_ARTICLES_BBOX = {
    "la": "le", "une": "un", "cette": "ce", "sa": "son", "ta": "ton",
    "ma": "mon", "votre": "votre", "notre": "notre",
    "de la": "du", "à la": "au",
}

# Ce que le flux retient en fin de tampon : les deux derniers mots complets,
# le mot en cours et la ponctuation ouvrante. C'est la plus longue forme a
# reecrire d'un bloc (« de l'outil `smart_load` », « de la bbox de »).
# Mesure : ~3 jetons de retard, soit ~0,1 s au debit du modele -- invisible.
_QUEUE = re.compile(r"[(*`_\s]*(?:\S+\s+){0,2}\S*\Z")

_FENCE = re.compile(r"`{3,}")
_URL = re.compile(r"(?:https?://|www\.)\S+|\]\([^)\s]*\)")
_CODE_EN_LIGNE = re.compile(r"`[^`\n]+`")
_DEBUT_DE_PHRASE = re.compile(r"(?:(?:\A|\n)[ \t]*(?:[-*•>]|\d+[.)])?|[.!?])[ \t]*\Z")
# Contexte gardé du texte deja emis : de quoi juger un debut de phrase ou un
# « / » qui precede (URL), pas davantage.
_CONTEXTE = 40


def _backticks_simples(texte: str) -> int:
    return _FENCE.sub("", texte).count("`")


class NettoyeurSortie:
    """Remplace, dans le texte visible, les noms d'outils et « bbox ».

    Travaille en flux : `pousser(fragment)` rend ce qui peut deja s'afficher,
    `vider()` le reste en fin de reponse. Un fragment SSE coupe souvent un
    nom en deux (« clip_to_ » puis « study_zone` ») : on retient la fin du
    tampon (`_QUEUE`), et on ne coupe jamais au milieu d'une forme a
    reecrire ni d'un code en ligne ouvert.

    Jamais touches : blocs de code, code en ligne qui n'est pas exactement
    un nom d'outil (noms de couches comme `batiment_aix_en_provence`), URLs,
    chiffres, identifiants qui ne sont pas des outils connus. Un nom sans
    tiret bas (improbable) n'est remplace qu'entre backticks : nu, ce
    pourrait etre un mot courant.
    """

    def __init__(self, noms_outils=()):
        noms = {n for n in (noms_outils or ()) if isinstance(n, str)
                and re.fullmatch(r"\w+", n)}
        noms |= set(LIBELLES_COURANTS)
        alt = "|".join(re.escape(n) for n in sorted(noms, key=len, reverse=True))
        self._noms = noms
        self._motif = re.compile(
            # Glose entre parentheses : « un découpage (`clip_to_study_zone`) »,
            # « (la `bbox`) ». Elle doublait un mot deja dit : on la retire,
            # espace qui precede comprise.
            r"(?P<glose>[ \t]*\(\s*(?:(?:via|avec|par|soit|outil|l['’]outil)\s+)?"
            r"(?:(?i:la|le|les|une|un)\s+)?" + _DECO + r"?"
            r"(?:(?<![\w/.\-])(?:" + alt + r")|(?i:bbox))"
            + _DECO + r"?\s*\))"
            # Nom d'outil, avec la preposition a contracter et l'annonce a
            # absorber (« de l'outil `smart_load` » -> « du chargement… »).
            r"|(?P<outil>(?P<prep>\b(?:[Dd]e|[Àà])\s+)?"
            r"(?P<intro>(?:[Ll]['’](?:outil|action|appel)|[Ll]a\s+(?:fonction|commande))"
            r"\s+(?:(?:à|a)\s+)?)?"
            r"(?P<od>" + _DECO + r")?(?<![\w/.\-:])(?P<nom>" + alt + r")"
            r"(?P<of>" + _DECO + r")?(?![\w\-/]|\.\w))"
            # « bbox » en contexte courant, avec son article.
            r"|(?P<bbox>(?P<bart>\b(?i:de\s+la|à\s+la|la|une|cette|sa|ta|ma|votre|notre)\s+)?"
            r"(?P<bd>\*\*|`)?(?<![\w/.\-])(?i:bbox)(?P<bf>\*\*|`)?(?![\w=\-/(]|\.\w))"
        )
        self._tampon = ""
        self._contexte = ""
        self._ligne_emise = ""
        self._dans_bloc = False

    # -- API ---------------------------------------------------------------

    def pousser(self, fragment: str) -> str:
        """Ajoute un fragment ; rend le texte transforme deja affichable."""
        if not fragment:
            return ""
        self._tampon += fragment
        return self._emettre(self._coupe_sure())

    def vider(self) -> str:
        """Fin de reponse : rend tout ce qui restait retenu."""
        return self._emettre(len(self._tampon))

    # -- Interne -------------------------------------------------------------

    def _code_est_un_nom(self, code: str) -> bool:
        interieur = code.strip("`").strip("*_ ")
        return interieur in self._noms or interieur.lower() == "bbox"

    def _zones_protegees(self, texte: str, debut: int) -> list[tuple[int, int]]:
        zones: list[tuple[int, int]] = []
        dans_bloc = self._dans_bloc
        ouverture = debut
        for m in _FENCE.finditer(texte, debut):
            if dans_bloc:
                zones.append((ouverture, m.end()))
            else:
                ouverture = m.start()
            dans_bloc = not dans_bloc
        if dans_bloc:
            zones.append((ouverture, len(texte)))
        zones += [m.span() for m in _URL.finditer(texte)]
        # Code en ligne ouvert dans la partie deja emise : protege jusqu'a
        # sa fermeture.
        pos = debut
        if _backticks_simples(self._ligne_emise) % 2:
            fin = texte.find("`", debut)
            fin = len(texte) if fin < 0 else fin + 1
            zones.append((debut, fin))
            pos = fin
        zones += [m.span() for m in _CODE_EN_LIGNE.finditer(texte, pos)
                  if not self._code_est_un_nom(m.group(0))]
        return zones

    def _coupe_sure(self) -> int:
        tampon = self._tampon
        m = _QUEUE.search(tampon)
        coupe = m.start() if m else len(tampon)
        texte = self._contexte + tampon
        base = len(self._contexte)
        while coupe > 0:
            recule = coupe
            for mm in self._motif.finditer(texte, base):
                if mm.start() - base < recule < mm.end() - base:
                    recule = mm.start() - base
            # Ne pas couper un code en ligne ouvert : il serait relu comme
            # une ouverture au fragment suivant.
            debut_ligne = tampon.rfind("\n", 0, recule) + 1
            ligne = (self._ligne_emise if debut_ligne == 0 else "") + tampon[debut_ligne:recule]
            if not self._dans_bloc and _backticks_simples(ligne) % 2:
                ouvrant = max((i for i in range(debut_ligne, recule)
                               if tampon[i] == "`"
                               and tampon[max(0, i - 2):i + 3].count("`") == 1),
                              default=-1)
                if ouvrant >= 0:
                    recule = ouvrant
            if recule == coupe:
                break
            coupe = recule
        return max(coupe, 0)

    def _emettre(self, coupe: int) -> str:
        if coupe <= 0:
            return ""
        base = len(self._contexte)
        texte = self._contexte + self._tampon
        limite = base + coupe
        zones = self._zones_protegees(texte, base)
        morceaux = []
        pos = base
        for m in self._motif.finditer(texte, base):
            if m.end() > limite:
                break
            if any(a < m.end() and m.start() < b for a, b in zones):
                continue
            morceaux.append(texte[pos:m.start()])
            morceaux.append(self._remplacer(m, texte))
            pos = m.end()
        morceaux.append(texte[pos:limite])
        sortie = "".join(morceaux)

        emis = self._tampon[:coupe]
        self._tampon = self._tampon[coupe:]
        self._dans_bloc = (self._dans_bloc + len(_FENCE.findall(emis))) % 2 == 1
        self._contexte = (self._contexte + emis)[-_CONTEXTE:]
        nl = emis.rfind("\n")
        self._ligne_emise = emis[nl + 1:] if nl >= 0 else self._ligne_emise + emis
        return sortie

    @staticmethod
    def _decorer(libelle: str, ouvre: str | None, ferme: str | None) -> str:
        # Decoration symetrique (`x`, **x**, **`x`**) : retiree avec le nom.
        # Sinon on la garde, pour ne pas desequilibrer le gras alentour.
        if ouvre and ferme and ouvre == ferme[::-1]:
            return libelle
        return (ouvre or "") + libelle + (ferme or "")

    @staticmethod
    def _majuscule_si_debut(texte: str, debut: int, remplacement: str) -> str:
        if remplacement and _DEBUT_DE_PHRASE.search(texte[:debut]):
            return remplacement[0].upper() + remplacement[1:]
        return remplacement

    def _remplacer(self, m: re.Match, texte: str) -> str:
        if m.group("glose") is not None:
            return ""
        if m.group("outil") is not None:
            nom = m.group("nom")
            if not m.group("od") and "_" not in nom:
                return m.group(0)
            libelle = LIBELLES_COURANTS.get(nom, _LIBELLE_INCONNU)
            prep = (m.group("prep") or "").strip()
            if prep:
                p = prep.lower()
                if libelle.startswith("le "):
                    libelle = ("du " if p == "de" else "au ") + libelle[3:]
                elif libelle.startswith("les "):
                    libelle = ("des " if p == "de" else "aux ") + libelle[4:]
                else:
                    libelle = p + " " + libelle
                if prep[0].isupper():
                    libelle = libelle[0].upper() + libelle[1:]
            sortie = self._decorer(libelle, m.group("od"), m.group("of"))
            # Le mot qui ouvre la forme (preposition, « L'outil ») porte deja
            # la casse voulue par le modele ; sinon, regle du debut de phrase.
            intro = m.group("intro")
            if prep:
                return sortie
            if intro:
                return sortie[0].upper() + sortie[1:] if intro[0].isupper() else sortie
            return self._majuscule_si_debut(texte, m.start(), sortie)
        # bbox
        suite = texte[m.end():m.end() + 6]
        nom = "rectangle" if re.match(r"\s+(?:de|du|des|d['’])\b", suite) \
            else "rectangle de la zone"
        article = m.group("bart")
        if article:
            brut = " ".join(article.split())
            art = _ARTICLES_BBOX.get(brut.lower(), "le")
            if brut[0].isupper():
                art = art[0].upper() + art[1:]
            nom = f"{art} {nom}"
            return self._decorer(nom, m.group("bd"), m.group("bf"))
        sortie = self._decorer(nom, m.group("bd"), m.group("bf"))
        return self._majuscule_si_debut(texte, m.start(), sortie)


def humaniser(texte: str | None, noms_outils=()) -> str:
    """Version d'un bloc de `NettoyeurSortie` (texte deja complet)."""
    if not texte:
        return texte or ""
    n = NettoyeurSortie(noms_outils)
    return n.pousser(texte) + n.vider()
