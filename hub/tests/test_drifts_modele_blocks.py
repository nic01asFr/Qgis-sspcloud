"""Les dix ecarts au modele blocks, et ou ils en sont.

`docs/blocks-and-deliverables-model.md` recense dix ecarts D1 a D10 entre le
modele Component / Assembly / BlockNote et ce que le code fait reellement. Le
document les identifie et les priorise ; il ne dit pas lesquels ont ete traites
depuis, et rien ne signalait qu'un ecart referme se rouvre.

Ce module tient les deux bouts :

- les ecarts REFERMES sont verrouilles. Un test qui echoue ici veut dire qu'on
  vient de reintroduire un defaut deja paye une fois.
- les ecarts OUVERTS sont declares `xfail`. Ils n'echouent pas la suite -- ce
  n'est pas une dette qu'on decouvre, c'est une dette qu'on connait -- mais le
  jour ou l'un est corrige, `XPASS` le signale et on retire le marqueur.

Etat au 8 septembre 2026 : cinq refermes, cinq ouverts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[2]


def _lire(relatif: str) -> str | None:
    chemin = _RACINE / relatif
    if not chemin.exists():
        return None
    return chemin.read_text(encoding="utf-8", errors="replace")


# ── Ecarts refermes : on empeche leur retour ─────────────────────────────

def test_d1_les_kinds_atomiques_sont_offerts_a_l_agent() -> None:
    """Sans eux dans la description des outils, un modele faible retombe sur
    `narrative_text` et fabrique un pave de texte la ou le modele attend des
    briques."""
    src = _lire("agent/agent/native_tools_v2.py")
    assert src is not None
    absents = [k for k in ("kpi_grid", "heading", "quote", "separator")
               if k not in src]
    assert not absents, "kinds retires des outils de l'agent : %s" % absents


def test_d2_les_deux_ecritures_lisent_la_version_source() -> None:
    """Sans ce controle, deux auteurs sur le meme composant s'ecrasent en
    silence -- l'editeur d'un cote, l'agent de l'autre."""
    src = _lire("hub/hub/main.py")
    assert src is not None
    for endpoint in ("update_component_endpoint", "update_assembly_endpoint"):
        bloc = re.search(r"async def %s.*?(?=\n@app\.)" % endpoint, src, re.S)
        assert bloc, "endpoint %s introuvable" % endpoint
        assert "version_num_source" in bloc.group(0), (
            "%s n'exerce plus de controle de concurrence" % endpoint)


def test_d3_l_editeur_sait_mettre_a_jour_un_composant() -> None:
    """Quand il ne savait que creer, corriger cinq fois un chiffre laissait
    cinq composants orphelins et autant d'entrees dans la chaine d'audit."""
    src = _lire("blocknote-editor/src/autosave.ts")
    if src is None:
        pytest.skip("source de l'editeur absente de ce depot")
    assert re.search(r"PATCH|PUT|update_component|updateComponent", src), (
        "l'editeur ne fait plus que creer : chaque enregistrement laissera un "
        "composant de plus")


def test_d5_l_edition_directe_exerce_le_controle_de_concurrence() -> None:
    """Le modal du bureau ecrit sans passer par l'editeur : c'est le chemin le
    plus court vers un ecrasement."""
    src = _lire("hub/templates/desk.html")
    assert src is not None
    assert "version_num_source" in src, (
        "l'edition directe depuis le bureau n'envoie plus de version source")


def test_d8_la_triade_kind_runtime_rendu_reste_couverte() -> None:
    """Ajouter un kind sans son rendu passait inapercu jusqu'a l'execution."""
    couvrants = [
        t.name for t in (_RACINE / "hub" / "tests").glob("*.py")
        if (lambda s: "parametrize" in s and "ComponentKind" in s
            and ("render" in s or "runtime" in s))(
                t.read_text(encoding="utf-8", errors="replace"))
    ]
    assert couvrants, (
        "plus aucun test ne verifie qu'un kind declare a bien un rendu")


# ── Ecarts ouverts : connus, non corriges ────────────────────────────────

@pytest.mark.xfail(reason="D4 : scene_3d et media_embed tombent en placeholder. "
                          "scene_3d doit etre ABSORBE par Atlas (cf. "
                          "docs/impact-bascule-atlas.md), pas rendu a part.",
                   strict=False)
def test_d4_aucun_kind_ne_tombe_en_placeholder() -> None:
    src = _lire("hub/hub/main.py")
    assert src is not None
    en_attente = [
        k for k in ("scene_3d", "media_embed", "iframe_grist")
        if re.search(r"placeholder.{0,400}%s|%s.{0,400}placeholder" % (k, k),
                     src, re.S | re.I)
    ]
    assert not en_attente, "kinds sans rendu propre : %s" % en_attente


@pytest.mark.xfail(reason="D6 : le titre de section fait l'aller-retour par un "
                          "heading H2 natif ; deux H2 voisins fragmentent la "
                          "section sans que l'auteur l'ait demande.",
                   strict=False)
def test_d6_les_sections_ont_leur_propre_marqueur() -> None:
    for f in ("blocknote-editor/src/serializer.ts",
              "blocknote-editor/src/autosave.ts"):
        src = _lire(f)
        if src and "sectionBreak" in src:
            return
    if _lire("blocknote-editor/src/serializer.ts") is None:
        pytest.skip("source de l'editeur absente de ce depot")
    pytest.fail("aucun marqueur de section dedie : round-trip par heading H2")


@pytest.mark.xfail(reason="D7 : sans kind recipe_output, une storymap ne se "
                          "rejoue pas sur une scene plus recente sans repasser "
                          "par l'agent.",
                   strict=False)
def test_d7_une_recette_est_un_composant_comme_un_autre() -> None:
    src = (_lire("hub/hub/models/component_params.py") or "") + \
          (_lire("agent/agent/native_tools_v2.py") or "")
    assert "recipe_output" in src, "pas de kind recipe_output"


@pytest.mark.xfail(reason="D9 : un seul AssemblyKind sur cinq est rendu. Celui "
                          "qui manque et qui compte est atlas_immersive, cible "
                          "de la bascule vers Atlas.",
                   strict=False)
def test_d9_tous_les_kinds_d_assemblage_se_rendent() -> None:
    src = _lire("hub/hub/main.py")
    assert src is not None
    m = re.search(r"supported\s*=\s*\{([^}]*)\}", src)
    assert m, "liste des kinds rendus introuvable"
    rendus = set(re.findall(r'"([a-z_0-9]+)"', m.group(1)))
    attendus = {"storymap_narrative_dsfr", "dashboard", "sheet_a4",
                "modal_embed", "atlas_immersive"}
    assert not (attendus - rendus), (
        "kinds valides mais non rendus : %s" % sorted(attendus - rendus))


@pytest.mark.xfail(reason="D10 : l'agent ne peut pas lire les blocks en cours "
                          "d'edition ; on lui demande de deplacer un element "
                          "qu'il ne voit pas.",
                   strict=False)
def test_d10_l_agent_peut_lire_les_blocks_en_cours() -> None:
    src = _lire("agent/agent/native_tools_v2.py")
    assert src is not None
    assert re.search(r"read_block|get_block|list_block|blocknote_state",
                     src, re.I), "aucun outil de lecture des blocks"


# ── Le document lui-meme ─────────────────────────────────────────────────

def test_le_document_de_reference_existe_toujours() -> None:
    """Ces tests n'ont de sens qu'adosses au modele qu'ils verifient."""
    doc = _RACINE / "docs" / "blocks-and-deliverables-model.md"
    assert doc.exists(), "le modele de reference a disparu du depot"
    texte = doc.read_text(encoding="utf-8")
    assert "## 7. Invariants" in texte
    for code in ("D1", "D4", "D9", "D10"):
        assert "**%s**" % code in texte, "le drift %s n'est plus recense" % code
