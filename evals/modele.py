"""Structures de donnees du banc : trajectoire d'un tour, execution d'un scenario."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Outils qui modifient l'etat QGIS ou le projet. Copie de `_MUTATING_TOOLS`
# (agent/agent/qgis_agent.py) completee des exports et publications : pour le
# banc, « modifier » inclut produire un livrable. Tenue a jour a la main ; le
# test `test_outils_mutateurs_couvrent_l_agent` signale une divergence.
OUTILS_MUTATEURS: frozenset[str] = frozenset({
    "smart_load", "add_layer", "add_from_catalog", "remove_layer",
    "set_layer_style", "set_layer_visibility", "run_processing", "run_recipe",
    "execute_python", "apply_layout_template", "set_study_zone", "new_project",
    "open_project", "save_project", "upload_file", "zoom_to",
    "execute_async", "export_layer", "export_pdf", "export_web_map",
    "publish_artifact", "add_database_layer", "delete_file",
})


@dataclass
class AppelOutil:
    """Un appel d'outil tel que le flux SSE le montre.

    Le flux ne transporte qu'un apercu : chaque argument est coupe a 40
    caracteres et le resultat a 300 (qgis_agent.py, bloc « Le blockquote
    markdown »). Les champs `*_tronque` le disent.
    """
    nom: str
    arguments: dict[str, str] = field(default_factory=dict)
    arguments_tronques: bool = False
    resultat: str = ""
    resultat_tronque: bool = False
    erreur: bool = False
    images: int = 0
    lien_livrable: str = ""
    iteration: int = 0

    def arguments_texte(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in self.arguments.items())


@dataclass
class Tour:
    """Un message utilisateur et tout ce que l'agent a emis en reponse."""
    message: str
    reponse: str = ""
    appels: list[AppelOutil] = field(default_factory=list)
    iterations: int = 0
    relances: int = 0
    caracteres_raisonnement: int = 0
    battements: int = 0
    checkpoints: list[str] = field(default_factory=list)
    messages_systeme: list[str] = field(default_factory=list)
    erreur_flux: str = ""
    termine: bool = False
    arret_auto: bool = False
    arret_utilisateur: bool = False
    coupe_par_le_banc: bool = False
    duree_s: float = 0.0
    evenements: int = 0

    def outils(self) -> list[str]:
        return [a.nom for a in self.appels]

    def erreurs_outils(self) -> int:
        return sum(1 for a in self.appels if a.erreur)

    def sources(self) -> list[str]:
        """Textes qui font foi pour tracer un chiffre ou une URL : resultats d'outils."""
        textes = [a.resultat for a in self.appels if a.resultat]
        textes += [a.lien_livrable for a in self.appels if a.lien_livrable]
        return textes

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Execution:
    """Une repetition d'un scenario : ses tours et l'etat QGIS final lu par le pont."""
    scenario_id: str
    repetition: int
    session_id: str = ""
    tours: list[Tour] = field(default_factory=list)
    etat: dict | None = None
    erreur_banc: str = ""
    duree_s: float = 0.0

    def appels(self) -> list[AppelOutil]:
        return [a for t in self.tours for a in t.appels]

    def as_dict(self) -> dict:
        return asdict(self)
