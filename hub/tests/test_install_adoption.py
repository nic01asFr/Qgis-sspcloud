"""`install.sh` rattache des ressources : elles doivent être celles du chart.

Contexte. Sur une instance installée autrement — à la main, ou par une
version antérieure du chart — des ressources existent sans appartenir à la
release Helm. L'upgrade refuse alors de les adopter et échoue en entier :

    Error: ... "qgis-workspace-nic01asfr" ... cannot be imported into the
    current release: invalid ownership metadata

Constaté le 2026-09-04 sur user-nic01asfr, où le workspace tournait hors
release. `install.sh` les rattache donc avant d'appeler helm.

Le danger de cette opération. Rattacher une ressource que le chart **ne rend
pas** la place sous la garde de Helm sans qu'il la produise — et Helm
supprime ce qu'il possède mais ne rend plus. C'est exactement ainsi que
l'agent a été effacé plus tôt cette semaine : adopté, puis exclu du rendu.

Ce test verrouille l'invariant : la liste de rattachement est exactement
l'ensemble des ressources rendues par le chart. Ni plus — sinon on arme une
suppression. Ni moins — sinon l'installation échoue là où elle devrait
passer. Il échouera au premier changement du chart qui rompt l'accord, ce qui
est le seul moment où quelqu'un peut encore y penser.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[2]
_INSTALL = _RACINE / "install.sh"
_CHART = _RACINE / "charts" / "qgis-hub"

_UTILISATEUR = "unutilisateur"

# kubectl accepte des alias ; le chart, lui, nomme les especes en toutes
# lettres. On compare des especes canoniques, pas des orthographes.
_ALIAS = {
    "pvc": "persistentvolumeclaim",
    "sts": "statefulset",
    "svc": "service",
    "sa": "serviceaccount",
    "ing": "ingress",
}


def _liste_du_script() -> set[tuple[str, str]]:
    """Les couples (espèce, nom) que `install.sh` rattache."""
    texte = _INSTALL.read_text(encoding="utf-8")
    bloc = re.search(r'for _r in \\\n(.*?);\s*do', texte, re.S)
    assert bloc, "boucle de rattachement introuvable dans install.sh"
    couples = set()
    for espece, nom in re.findall(r'"([a-z]+) ([^"]+)"', bloc.group(1)):
        nom = nom.replace("$USERNAME", _UTILISATEUR)
        couples.add((_ALIAS.get(espece, espece), nom))
    return couples


def _liste_du_chart() -> set[tuple[str, str]]:
    """Les couples (espèce, nom) que le chart produit réellement."""
    yaml = pytest.importorskip("yaml")
    rendu = subprocess.run(
        ["helm", "template", "qgis-hub", str(_CHART),
         "--set", f"oidc.username={_UTILISATEUR}",
         "--set", "workspace.enabled=true",
         "--set", "agent.enabled=true",
         "--set", "ingress.hostname=exemple",
         "--set", "security.apiKey=x",
         "--set", "llm.apiKey=y"],
        capture_output=True, text=True, timeout=120,
    )
    assert rendu.returncode == 0, f"helm template a echoue : {rendu.stderr[:300]}"
    return {
        (d["kind"].lower(), d["metadata"]["name"])
        for d in yaml.safe_load_all(rendu.stdout) if d
    }


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")
class TestAccordAvecLeChart:
    def test_rien_n_est_rattache_qui_ne_soit_rendu(self):
        """Le sens dangereux : adopter sans rendre arme une suppression."""
        en_trop = _liste_du_script() - _liste_du_chart()
        assert not en_trop, (
            f"ressources rattachees mais non rendues par le chart : {sorted(en_trop)}. "
            "Helm supprime ce qu'il possede et ne rend plus."
        )

    def test_tout_ce_qui_est_rendu_est_rattachable(self):
        """Le sens gênant : oublier une ressource fait echouer l'installation
        exactement dans le cas que ce rattachement devait couvrir."""
        oubliees = _liste_du_chart() - _liste_du_script()
        assert not oubliees, (
            f"rendues par le chart mais jamais rattachees : {sorted(oubliees)}. "
            "Une instance qui les porte deja fera echouer l'upgrade."
        )


class TestPrudenceDuRattachement:
    def test_une_ressource_d_une_autre_release_n_est_pas_volee(self):
        texte = _INSTALL.read_text(encoding="utf-8")
        assert 'appartient a la release' in texte, (
            "rattacher une ressource possedee par une autre release la lui "
            "arracherait ; le script doit s'arreter et le dire"
        )

    def test_le_secret_des_acces_s3_n_est_pas_rattache(self):
        """`passerelle-s3-creds` n'est pas rendu par le chart et porte des cles
        que le chart ignore (HF_TOKEN, S3_ENCRYPT_KEY). L'adopter reviendrait
        a le condamner."""
        assert ("passerelle-s3-creds", ) not in [
            (n,) for _, n in _liste_du_script()
        ]


class TestDiagnostic:
    """Le message d'echec ne connaissait que les Secrets."""

    @pytest.mark.parametrize("espece,nom", [
        ("StatefulSet", "qgis-workspace-nic01asfr"),
        ("Secret", "passerelle-s3-creds"),
        ("Service", "qgis-agent-svc"),
    ])
    def test_l_espece_et_le_nom_sont_extraits(self, espece, nom):
        ligne = (f'Error: {espece} "{nom}" in namespace "user-x" exists and '
                 'cannot be imported into the current release')
        motif = re.search(r'[A-Za-z]+ "[^"]+" in namespace', ligne)
        assert motif
        assert motif.group(0).split()[0].lower() == espece.lower()
        assert re.sub(r'.*"(.*)".*', r"\1", motif.group(0)) == nom

    def test_le_cas_des_champs_immuables_est_couvert(self):
        """Un StatefulSet adopte dont le gabarit a bouge echoue autrement, et
        le remede n'est pas le meme : supprimer l'objet en gardant ses pods."""
        texte = _INSTALL.read_text(encoding="utf-8")
        assert "field is immutable" in texte
        assert "--cascade=orphan" in texte
