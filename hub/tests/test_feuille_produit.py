"""La feuille du produit doit couvrir tout ce que les surfaces lui demandent.

Le service chargeait sa mise en forme depuis le systeme de design de l'Etat,
servi par un CDN tiers, sur cinq surfaces. Ce systeme est destine aux sites de
l'Etat ; le garder sur un service qu'on avait par ailleurs debrande n'etait
defendable ni sur le fond ni sur la forme. Il a ete remplace par une feuille
propre, sans dependance externe et sans police sous licence particuliere.

Ce module verrouille les deux facons dont ce remplacement peut se defaire en
silence.

La premiere : une classe d'icone employee dans un gabarit mais absente de la
feuille. Rien ne casse, aucune erreur n'est levee -- l'element est simplement
vide. Sur une interface qui en compte une centaine, la disparition d'une seule
passe inapercue jusqu'a ce qu'un utilisateur la signale.

La seconde : le retour du CDN. Un copier-coller depuis un ancien gabarit, et
la charte d'Etat revient sur une surface, avec sa police et son bleu.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[2]
_FEUILLE = _RACINE / "hub" / "hub" / "static" / "produit.css"

# Toutes les surfaces servies a un utilisateur, hub et agent confondus.
_SURFACES = [
    _RACINE / "hub" / "templates" / "desk.html",
    _RACINE / "hub" / "templates" / "workspace.html",
    _RACINE / "agent" / "templates" / "chat.html",
    _RACINE / "hub" / "hub" / "main.py",
    _RACINE / "hub" / "hub" / "storymap_dsfr.py",
]

# Les outils portent la table de correspondance : ils citent forcement les
# anciens noms, c'est leur role.
_OUTILS = _RACINE / "hub" / "tools"


def _feuille() -> str:
    return _FEUILLE.read_text(encoding="utf-8")


def _icones_definies() -> set[str]:
    return set(re.findall(r"^\.ic-([a-z0-9-]+)\{", _feuille(), re.M))


def _icones_employees() -> dict[str, list[str]]:
    """Nom d'icone -> surfaces qui l'emploient."""
    emploi: dict[str, list[str]] = {}
    for surface in _SURFACES:
        if not surface.exists():
            continue
        texte = surface.read_text(encoding="utf-8")
        for nom in re.findall(r'\bic-([a-z0-9-]+)', texte):
            if nom.startswith("-"):
                continue  # ic--sm, ic--md : des modificateurs de taille
            emploi.setdefault(nom, []).append(surface.name)
    return emploi


def test_la_feuille_existe_et_n_est_pas_vide() -> None:
    assert _FEUILLE.exists(), "la feuille du produit est absente"
    assert len(_feuille()) > 5000, "la feuille semble tronquee"


def test_toute_icone_employee_est_definie() -> None:
    definies = _icones_definies()
    manquantes = {
        nom: surfaces for nom, surfaces in _icones_employees().items()
        if nom not in definies
    }
    assert not manquantes, (
        "ces icones sont employees mais absentes de la feuille -- elles "
        "s'afficheront vides, sans aucune erreur : %s"
        % ", ".join("%s (%s)" % (n, ", ".join(sorted(set(s))))
                    for n, s in sorted(manquantes.items()))
    )


def test_aucune_icone_definie_sans_emploi() -> None:
    """Une icone que plus personne n'emploie alourdit la feuille pour rien.

    Le chevron fait exception : il sert a l'accordeon, qui le pose dans un
    pseudo-element et non par une classe.
    """
    employees = set(_icones_employees())
    orphelines = _icones_definies() - employees - {"chevron-bas"}
    assert not orphelines, (
        "icones definies mais employees nulle part : %s" % sorted(orphelines))


@pytest.mark.parametrize("surface", _SURFACES, ids=lambda p: p.name)
def test_aucune_surface_ne_recharge_la_charte_d_etat(surface: Path) -> None:
    if not surface.exists():
        pytest.skip("surface absente")
    texte = surface.read_text(encoding="utf-8")
    assert "gouvfr/dsfr" not in texte, (
        "%s recharge le systeme de design de l'Etat depuis un CDN" % surface.name)
    assert "data-fr-scheme" not in texte, (
        "%s pose encore l'attribut de theme de la charte" % surface.name)
    assert not re.search(r"font-family:\s*Marianne", texte, re.I), (
        "%s declare la police Marianne, qui a sa propre licence" % surface.name)


@pytest.mark.parametrize("surface", _SURFACES, ids=lambda p: p.name)
def test_aucune_classe_de_l_ancienne_charte_ne_subsiste(surface: Path) -> None:
    """Une classe `fr-*` restee en place ne style plus rien.

    Elle ne provoque pas d'erreur : l'element perd simplement sa mise en forme.
    """
    if not surface.exists():
        pytest.skip("surface absente")
    texte = surface.read_text(encoding="utf-8")
    restes = sorted(set(re.findall(r"(?<![\w-])fr-[a-zA-Z][\w-]*", texte)))
    assert not restes, (
        "%s porte encore des classes de l'ancienne charte, sans feuille pour "
        "les servir : %s" % (surface.name, ", ".join(restes)))


@pytest.mark.parametrize("surface", _SURFACES, ids=lambda p: p.name)
def test_aucun_jeton_de_l_ancienne_charte_ne_subsiste(surface: Path) -> None:
    """Le piege que l'inventaire des classes ne voit pas.

    `background: var(--background-action-high-blue-france)` ne contient ni
    `fr-`, ni `#000091` : rien qui signale une appartenance. Le CSS du CDN
    definissait ces variables ; sans lui la propriete devient invalide et
    l'element perd sa couleur, sans erreur ni avertissement. Il y en avait
    271, et c'est ainsi que le bouton d'envoi du chat s'est retrouve
    transparent apres la premiere passe de remplacement.
    """
    if not surface.exists():
        pytest.skip("surface absente")
    texte = surface.read_text(encoding="utf-8")
    jetons = re.findall(r"var\(\s*(--[a-z0-9-]+)\s*\)", texte)
    etrangers = sorted({
        j for j in jetons if not j.startswith(("--qs-", "--ic"))
    })
    assert not etrangers, (
        "%s emploie des jetons que plus aucune feuille ne definit : %s"
        % (surface.name, ", ".join(etrangers)))


def test_le_bleu_de_l_etat_a_disparu_des_surfaces() -> None:
    """#000091 est le bleu du systeme de design de l'Etat.

    Les palettes de classification cartographique ne sont pas concernees :
    elles rendent des donnees, pas une marque.
    """
    coupables = []
    for surface in _SURFACES:
        if surface.exists() and "#000091" in surface.read_text(encoding="utf-8"):
            coupables.append(surface.name)
    assert not coupables, (
        "le bleu de la charte d'Etat subsiste dans : %s" % ", ".join(coupables))


def test_la_feuille_ne_depend_d_aucune_ressource_externe() -> None:
    """Elle est inlinee dans les livrables publies, qui circulent hors ligne."""
    feuille = _feuille()
    assert "@import" not in feuille, "la feuille importe une autre feuille"
    externes = re.findall(r"url\(\s*['\"]?(https?:)?//", feuille)
    assert not externes, "la feuille charge une ressource distante"


def test_les_couleurs_sont_definies_avant_d_etre_employees() -> None:
    """Un jeton employe sans etre defini rend la propriete invalide."""
    feuille = _feuille()
    bloc_racine = feuille[feuille.index(":root {"):feuille.index("\n}", feuille.index(":root {"))]
    definis = set(re.findall(r"(--qs-[a-z0-9-]+)\s*:", bloc_racine))
    employes = set(re.findall(r"var\((--qs-[a-z0-9-]+)\)", feuille))
    manquants = employes - definis
    assert not manquants, "jetons employes sans definition : %s" % sorted(manquants)
