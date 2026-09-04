"""Le compteur de livrables doit compter ce que la page montre.

Constate le 2026-09-04 sur les deux instances en production. La section « Mes
livrables publiés » titrait « 3 » (nic01asfr) et « 13 » (nicolaslaval)
au-dessus d'une liste vide portant « Aucune publication pour l'instant ».

    catalog_total = len(all_items)                       -> le badge
    catalog_items = [... study_id == etude active][:30]  -> la liste

Deux populations sous un meme titre. Le cas etait frequent, pas marginal :
2 publications sur 3 chez nic01asfr et 5 sur 13 chez nicolaslaval n'ont
aucune etude rattachee, donc n'apparaissaient sous aucune etude active.

Et la formulation aggravait tout : « Aucune publication pour l'instant.
Demande a l'agent de publier une storymap » -- on invitait a produire ce qui
existait deja. Un utilisateur qui suit ce conseil republie, et se retrouve
avec des doublons qu'il ne voit toujours pas.

C'est le meme motif que le reste de la semaine, sous une forme de plus :
ici le chiffre etait juste et la liste etait juste, chacun de son cote. Ce
qui etait faux, c'est de les presenter comme deux vues d'une meme chose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_GABARIT = (Path(__file__).resolve().parents[1] / "templates" / "workspace.html")
_MAIN = (Path(__file__).resolve().parents[1] / "hub" / "main.py")


def _sans_commentaires(source: str) -> str:
    return "\n".join(l for l in source.splitlines()
                     if not l.strip().startswith("#"))


class TestLeBadgeSuitLaListe:
    def test_le_badge_ne_lit_plus_le_total(self):
        """`catalog_total or catalog_count` affichait la totalite au-dessus
        d'une liste filtree."""
        html = _GABARIT.read_text(encoding="utf-8")
        assert "catalog_total or catalog_count" not in html, (
            "le badge compte de nouveau une population que la liste ne montre pas"
        )
        assert '<span class="count">{{ catalog_count }}</span>' in html

    def test_le_comptage_porte_sur_ce_qui_est_montre(self):
        source = _sans_commentaires(_MAIN.read_text(encoding="utf-8"))
        assert "catalog_count=len(montres)" in source, (
            "le compteur doit denombrer la liste affichee, pas une autre"
        )


class TestCeQuiManqueEstDit:
    def test_l_ecart_est_calcule(self):
        """Ce qui existe sans etre montre doit etre nomme, pas tu."""
        source = _sans_commentaires(_MAIN.read_text(encoding="utf-8"))
        assert "catalog_ailleurs=" in source
        assert "catalog_tronques=" in source

    def test_la_troncature_a_30_est_signalee(self):
        """`[:30]` coupait sans le dire : la liste pouvait etre incomplete
        pour une etude qui depasse ce seuil."""
        source = _sans_commentaires(_MAIN.read_text(encoding="utf-8"))
        assert "len(retenus) - len(montres)" in source

    def test_le_gabarit_affiche_l_ecart(self):
        html = _GABARIT.read_text(encoding="utf-8")
        assert "catalog_ailleurs" in html and "catalog_tronques" in html


class TestLEtatVideNeMentPlus:
    def test_il_distingue_aucune_de_aucune_ici(self):
        html = _GABARIT.read_text(encoding="utf-8")
        i = html.find("Aucune publication")
        bloc = html[max(0, i - 400):i + 600]
        assert "{% if catalog_total %}" in bloc, (
            "l'absence doit distinguer « rien nulle part » de « rien ici »"
        )
        assert "rattachée à cette étude" in bloc

    def test_il_propose_le_chemin_vers_les_autres(self):
        """Dire qu'elles existent sans dire ou les voir ne repare qu'a moitie."""
        html = _GABARIT.read_text(encoding="utf-8")
        assert html.count("/published/{{ username }}/") >= 2

    def test_le_lien_utilise_une_variable_fournie(self):
        """`owner` n'existe pas dans le contexte du workspace : le lien serait
        tombe sur /published// -- une adresse morte au bout d'une phrase qui
        promet de montrer les publications."""
        contexte = _MAIN.read_text(encoding="utf-8")
        i = contexte.find("async def _desk_context")
        assert '"username": _ONYXIA_USER' in contexte[i:i + 1200]
        assert "{{ owner }}" not in _GABARIT.read_text(encoding="utf-8")


class TestSansEtudeActive:
    def test_on_liste_tout_plutot_que_rien(self):
        """Sans etude active, le filtre ne retenait rien et la page montrait
        une liste vide sous le total."""
        source = _sans_commentaires(_MAIN.read_text(encoding="utf-8"))
        i = source.find("retenus = list(all_items)")
        assert i != -1, "sans etude active, la liste doit montrer tout"
        assert "if actif:" in source[max(0, i - 400):i]
