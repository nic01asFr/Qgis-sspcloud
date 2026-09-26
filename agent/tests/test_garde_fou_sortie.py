"""Garde-fou de sortie : aucun nom d'outil ni « bbox » dans le texte visible.

Mesure live du 2026-09-26, apres le lot qualite 2 (scenario S1, bati d'Aix) :
malgre la regle de prompt « aucun nom d'outil », le modele a ecrit a
l'utilisateur « il faut effectuer un découpage (`clip_to_study_zone`) » et
« l'emprise rectangulaire (la `bbox`) ». La regle de prompt ne suffit pas :
une reecriture deterministe du texte affiche prend le relais.

Deux proprietes tiennent l'ensemble :
- le resultat en flux est identique au resultat en bloc, quel que soit le
  decoupage en fragments SSE ;
- rien d'autre ne bouge : code, URLs, chiffres, noms de couches.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_ICI = Path(__file__).resolve().parent
if str(_ICI) not in sys.path:
    sys.path.insert(0, str(_ICI))

if "sqlite_vec" not in sys.modules:
    _vec = type(sys)("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

from agent import texte_modele as tm  # noqa: E402
from agent import qgis_agent as qa  # noqa: E402

# Le harnais qui deroule la vraie `chat_stream` contre un modele simule.
from test_appel_ecrit_relance import (  # noqa: E402,F401
    _ClientModele, _appel_outil, _texte, _tour, agent,
)

_NOMS = {"smart_load", "clip_to_study_zone", "get_project_info", "set_study_zone",
         "outil_sans_libelle", "geocode"}


def h(texte: str) -> str:
    return tm.humaniser(texte, _NOMS)


def _en_flux(texte: str, tailles) -> str:
    n = tm.NettoyeurSortie(_NOMS)
    sortie, i = "", 0
    for k in tailles:
        if i >= len(texte):
            break
        sortie += n.pousser(texte[i:i + k])
        i += k
    sortie += n.pousser(texte[i:])
    return sortie + n.vider()


# ── Les cas reels ───────────────────────────────────────────────────────────

def test_la_glose_du_decoupage_disparait():
    assert h("il faut effectuer un découpage (`clip_to_study_zone`).") == \
        "il faut effectuer un découpage."


def test_la_glose_bbox_disparait():
    assert h("dans l'emprise rectangulaire (la `bbox`) de la zone d'étude") == \
        "dans l'emprise rectangulaire de la zone d'étude"


def test_reponse_s1_complete():
    avant = ("Pour ne garder que le bâti de la commune, il faut effectuer un "
             "découpage (`clip_to_study_zone`). Pour l'instant, 54 557 bâtiments "
             "sont chargés dans l'emprise rectangulaire (la `bbox`) de la zone.")
    apres = h(avant)
    assert "clip_to_study_zone" not in apres and "bbox" not in apres
    assert "54 557 bâtiments" in apres
    assert apres.startswith("Pour ne garder que le bâti de la commune, il faut "
                            "effectuer un découpage. Pour l'instant")


# ── Formes d'un nom d'outil ─────────────────────────────────────────────────

@pytest.mark.parametrize("avant, apres", [
    ("J'ai utilisé `smart_load` ici.", "J'ai utilisé le chargement des données ici."),
    ("J'ai utilisé **smart_load** ici.", "J'ai utilisé le chargement des données ici."),
    ("J'ai utilisé **`smart_load`** ici.", "J'ai utilisé le chargement des données ici."),
    ("J'ai utilisé smart_load ici.", "J'ai utilisé le chargement des données ici."),
    ("Je lance l'outil `smart_load`.", "Je lance le chargement des données."),
    ("Je passe par la fonction smart_load.", "Je passe par le chargement des données."),
])
def test_code_gras_ou_nu(avant, apres):
    assert h(avant) == apres


@pytest.mark.parametrize("avant, apres", [
    ("le résultat de `smart_load`", "le résultat du chargement des données"),
    ("le résultat de l'outil smart_load", "le résultat du chargement des données"),
    ("grâce à `smart_load`", "grâce au chargement des données"),
    ("avant de `set_study_zone`", "avant de la définition de la zone d'étude"),
])
def test_prepositions_contractees(avant, apres):
    assert h(avant) == apres


@pytest.mark.parametrize("avant, apres", [
    ("`smart_load` a chargé 12 couches.", "Le chargement des données a chargé 12 couches."),
    ("Fait. `smart_load` a réussi.", "Fait. Le chargement des données a réussi."),
    ("Étapes :\n- `smart_load` : ok", "Étapes :\n- Le chargement des données : ok"),
    ("> **`get_project_info`** — pour lire", "> La lecture du projet — pour lire"),
    ("Étape : `smart_load`", "Étape : le chargement des données"),
    ("L'outil `smart_load` a réussi.", "Le chargement des données a réussi."),
    ("De `smart_load` on retient 12 couches.", "Du chargement des données on retient 12 couches."),
    ("Fait. La bbox est large.", "Fait. Le rectangle de la zone est large."),
    ("Fait. bbox large.", "Fait. Rectangle de la zone large."),
])
def test_majuscule_en_debut_de_phrase_seulement(avant, apres):
    assert h(avant) == apres


@pytest.mark.parametrize("avant, apres", [
    ("un découpage (**clip_to_study_zone**) suffit", "un découpage suffit"),
    ("un découpage (via `clip_to_study_zone`) suffit", "un découpage suffit"),
    ("un découpage (l'outil clip_to_study_zone) suffit", "un découpage suffit"),
    ("le rectangle (bbox) de la zone", "le rectangle de la zone"),
])
def test_gloses_entre_parentheses(avant, apres):
    assert h(avant) == apres


def test_outil_expose_sans_libelle():
    assert h("avec `outil_sans_libelle`") == "avec l'action correspondante"


def test_tout_libelle_d_etat_a_son_libelle_courant():
    """Un outil qui a un libelle d'etat (« Chargement des données… ») doit
    aussi avoir son groupe nominal : sinon il tomberait sur la formule
    vague, alors qu'on sait ce qu'il fait."""
    manquants = set(qa._LIBELLES_OUTILS) - set(tm.LIBELLES_COURANTS)
    assert not manquants


# ── bbox ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("avant, apres", [
    ("La bbox est large.", "Le rectangle de la zone est large."),
    ("dans la bbox de la zone d'étude", "dans le rectangle de la zone d'étude"),
    ("hors de la bbox.", "hors du rectangle de la zone."),
    ("une bbox trop large", "un rectangle de la zone trop large"),
    ("cette `bbox`", "ce rectangle de la zone"),
    ("en bbox", "en rectangle de la zone"),
    ("la BBOX", "le rectangle de la zone"),
])
def test_bbox_en_contexte_courant(avant, apres):
    assert h(avant) == apres


# ── Faux positifs : rien ne bouge ───────────────────────────────────────────

@pytest.mark.parametrize("texte", [
    "La couche `batiment_aix_en_provence` compte 54 557 entités.",
    "Couche `smart_load_resultat` et fichier smart_load.py intacts.",
    "Variable study_id et set_study_zone_v2 inconnus.",
    "Voir https://exemple.fr/api/smart_load pour le détail.",
    "Le [lien](https://x.fr/clip_to_study_zone) reste.",
    "Le paramètre `bbox=5.3,43.2` et bbox_filtre restent.",
    "Le chargement des données est fini, zoom compris.",
    "Code `processing.run('native:clip', smart)` intact.",
    "Coordonnées 5.38, 43.29 : 1 234 bâtiments.",
    "Le géocodage geocode l'adresse.",
    "Un appel (`foo_bar`) inconnu reste tel quel.",
])
def test_ce_qui_ne_doit_pas_bouger(texte):
    assert h(texte) == texte


def test_nom_sans_tiret_bas_remplace_seulement_en_code():
    assert h("J'utilise `geocode`.") == "J'utilise l'action correspondante."


def test_bloc_de_code_intact():
    texte = "Exemple :\n```python\nsmart_load(bbox=x)\n```\nPuis `smart_load`."
    assert h(texte) == "Exemple :\n```python\nsmart_load(bbox=x)\n```\nPuis le chargement des données."


def test_bloc_de_code_non_ferme_intact():
    texte = "Voici :\n```\nclip_to_study_zone(layer)\nla bbox"
    assert h(texte) == texte


def test_idempotent():
    for texte in ("un découpage (`clip_to_study_zone`).", "la bbox de `smart_load`"):
        assert h(h(texte)) == h(texte)


# ── Flux ────────────────────────────────────────────────────────────────────

_CAS_FLUX = [
    "Pour ne garder que le bâti, il faut effectuer un découpage (`clip_to_study_zone`).",
    "Les bâtiments sont dans l'emprise rectangulaire (la `bbox`) de la zone d'étude.",
    "J'ai utilisé `smart_load` puis **`clip_to_study_zone`** sur `batiment_aix_en_provence`.",
    "le résultat de l'outil smart_load ; hors de la bbox de la zone.",
    "Code `processing.run('native:clip', a)` puis `smart_load` et https://x.fr/smart_load ok",
    "Exemple :\n```python\nsmart_load(bbox=x)\n```\nPuis `smart_load`, la bbox.",
    "- `smart_load` : 54 557 bâtiments\n- (via `clip_to_study_zone`) 12 000",
]


@pytest.mark.parametrize("texte", _CAS_FLUX)
def test_flux_identique_au_bloc_pour_toute_coupure_en_deux(texte):
    attendu = h(texte)
    for i in range(len(texte) + 1):
        assert _en_flux(texte, [i]) == attendu, i


@pytest.mark.parametrize("texte", _CAS_FLUX)
def test_flux_identique_au_bloc_en_fragments_aleatoires(texte):
    attendu = h(texte)
    for graine in range(50):
        r = random.Random(graine)
        tailles = [r.randint(1, 8) for _ in range(len(texte))]
        assert _en_flux(texte, tailles) == attendu, graine


def test_flux_caractere_par_caractere():
    for texte in _CAS_FLUX:
        assert _en_flux(texte, [1] * len(texte)) == h(texte)


def test_le_flux_ne_retient_que_quelques_mots():
    """Sans nom d'outil, tout s'affiche sauf la fin : pas de retard visible."""
    n = tm.NettoyeurSortie(_NOMS)
    phrase = "Voici le nombre de bâtiments trouvés dans la commune ce matin "
    emis = n.pousser(phrase)
    assert phrase.startswith(emis)
    assert len(phrase.split()) - len(emis.split()) <= 3
    assert emis + n.vider() == phrase


def test_un_nom_coupe_entre_deux_fragments_n_apparait_jamais():
    n = tm.NettoyeurSortie(_NOMS)
    emis = n.pousser("J'ai lancé `clip_to_")
    emis += n.pousser("study_zone` puis ")
    emis += n.pousser("fini.")
    emis += n.vider()
    assert "clip_to" not in emis
    assert emis == "J'ai lancé le découpage selon les limites de la commune puis fini."


def test_vider_sans_rien_recu():
    n = tm.NettoyeurSortie(_NOMS)
    assert n.pousser("") == ""
    assert n.vider() == ""


# ── De bout en bout : la vraie chat_stream ──────────────────────────────────

def _texte_en_fragments(morceaux: list[str]) -> list[str]:
    lignes = ["data: " + json.dumps({"choices": [{"delta": {"content": m},
                                                  "finish_reason": None}]})
              for m in morceaux]
    lignes += [
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    return lignes


def test_chat_stream_affiche_et_persiste_le_libelle(agent):
    """Le live du 2026-09-26, decoupe comme le serveur le fait : noms coupes
    entre fragments, glose entre parentheses."""
    evenements = _tour(agent, [_texte_en_fragments([
        "Il faut effectuer un découpage (`clip_to", "_study_zone`). Les 54 557 ",
        "bâtiments sont dans l'emprise rectangulaire (la `bb", "ox`) de la zone.",
    ])])
    affiche = _texte(evenements)
    assert "Il faut effectuer un découpage. Les 54 557 bâtiments sont dans " \
           "l'emprise rectangulaire de la zone." in affiche
    assert "clip_to" not in affiche and "bbox" not in affiche
    assert "clip_to" not in agent.persistes[-1]
    assert "bbox" not in agent.persistes[-1]


def test_le_modele_relit_son_texte_brut_pendant_le_tour(agent):
    """Le texte renvoye au modele entre deux appels ne change pas : seule
    la sortie affichee est reecrite."""
    lignes = _texte_en_fragments(["Je lance `smart_load` maintenant."])[:-2]
    lignes += _appel_outil("get_project_info")
    _tour(agent, [lignes, _texte_en_fragments(["C'est fait."])])
    suivant = _ClientModele.envois[1]["messages"]
    assistant = [m for m in suivant if m.get("role") == "assistant"]
    assert assistant[-1]["content"] == "Je lance `smart_load` maintenant."
    assert "le chargement des données" in agent.persistes[-1]


def test_un_appel_ecrit_reste_detecte_sur_le_texte_brut(agent):
    """D1 lit le texte avant reecriture : le garde-fou ne masque pas un
    appel ecrit en texte au detecteur."""
    faux = "> **`get_project_info`** — pour récupérer le layer_id"
    evenements = _tour(agent, [
        _texte_en_fragments(["> **`get_pro", "ject_info`** — pour récupérer le layer_id"]),
        _texte_en_fragments(["La couche est prête."]),
    ])
    assert len(_ClientModele.envois) == 2
    assert _ClientModele.envois[1]["messages"][-2] == {"role": "assistant", "content": faux}
    retraits = [e["retirer_texte"] for e in evenements
                if isinstance(e, dict) and "retirer_texte" in e]
    assert retraits == [h(faux)]
    assert "La couche est prête." in _texte(evenements)
