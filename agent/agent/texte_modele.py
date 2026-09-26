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
  public. Les chiffres ne sont jamais touches.
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
