"""Le catalogue date ses entrees avec un epoch, pas une chaine ISO.

Constate en production le 2026-09-03, une minute apres avoir renouvele les
acces au stockage : la page `/published/nic01asfr/` est passee de « 0
publication disponible » a une erreur 500.

    main.py:9561  published_at = _fdt(item.get("published_at") ...)
    AttributeError: 'int' object has no attribute 'day'

Les trois entrees du catalogue portaient un entier. `format_datetime_fr` ne
prevoyait que `str` et `datetime` ; tout le reste tombait dans la branche
`else` et on lui demandait un attribut `.day`.

Ce qui rend le cas interessant : le defaut existait depuis toujours, mais
restait hors d'atteinte tant que les identifiants etaient perimes -- le
catalogue revenait vide, donc aucune date n'etait jamais formatee. Une panne
en cachait une autre, et reparer la premiere a revele la seconde. C'est
l'ordre inverse de celui qu'on suppose d'habitude : ici, ce n'est pas
l'echec qui prenait la forme d'une intention, c'est un echec qui en
dissimulait un autre.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from hub.models.classification import format_datetime_fr


class TestEpoch:
    def test_un_epoch_en_secondes_est_formate(self):
        """La valeur exacte lue dans le catalogue de production."""
        rendu = format_datetime_fr(1787605113)
        assert "2026" in rendu
        assert "à" in rendu

    def test_un_epoch_en_millisecondes_ne_donne_pas_une_date_absurde(self):
        """Sans le seuil, une valeur en millisecondes daterait de l'an 58000 --
        affichee sans erreur, donc jamais corrigee."""
        secondes = format_datetime_fr(1787605113)
        millisecondes = format_datetime_fr(1787605113000)
        assert secondes == millisecondes

    def test_un_flottant_passe_aussi(self):
        assert "2026" in format_datetime_fr(1787605113.5)

    def test_une_valeur_hors_bornes_ne_fait_pas_tomber_la_page(self):
        """Mieux vaut une date brute qu'une page entiere en 500."""
        assert format_datetime_fr(10 ** 18) == str(10 ** 18)


class TestFormesDejaSupportees:
    """La correction ne doit rien changer a ce qui marchait."""

    def test_une_chaine_iso(self):
        assert format_datetime_fr("2026-07-08T08:27:56.597274") == (
            "8 juillet 2026 à 08:27"
        )

    def test_un_datetime(self):
        assert format_datetime_fr(datetime(2026, 7, 8, 8, 27)) == (
            "8 juillet 2026 à 08:27"
        )

    def test_une_chaine_non_analysable_reste_brute(self):
        assert format_datetime_fr("bientot") == "bientot"

    def test_l_absence_reste_vide(self):
        assert format_datetime_fr(None) == ""

    def test_un_booleen_n_est_pas_pris_pour_un_epoch(self):
        """`bool` derive de `int` ; sans la garde, True daterait de 1970."""
        with pytest.raises(AttributeError):
            format_datetime_fr(True)
