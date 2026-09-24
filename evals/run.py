"""Point d'entree du banc : `python -m evals.run ...`.

Exemple (voir README) :

    python -m evals.run --scenarios evals/scenarios \
        --agent-url https://<hub>/agent --hub-url https://<hub> \
        --bridge http://localhost:8080 --study <sid> --repeat 3

Le jeton est lu dans `--jeton` ou, de preference, dans la variable
d'environnement EVALS_JETON (il n'apparait alors ni dans l'historique du
shell ni dans le rapport).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from evals.clients import ClientAgent, ClientHub, ErreurHttp
from evals.evaluation import ResultatExecution, evaluer
from evals.garde_fou import RefusGardeFou, verifier_active, verifier_etude
from evals.modele import Execution
from evals.pont import Pont, PontCommande, PontHttp
from evals.rapport import agreger, ecrire
from evals.scenario import Scenario, ScenarioInvalide, charger
from evals.sonde import lire_etat, preparer
from evals.verifs.urls import CATALOGUE_PAR_DEFAUT

REPETITIONS_PAR_DEFAUT = 3
CODE_REFUS = 2


def construire_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m evals.run",
        description="Banc d'evaluation comportementale de l'agent QGIS (niveau C).",
    )
    p.add_argument("--scenarios", nargs="+", default=[str(Path(__file__).parent / "scenarios")],
                   help="fichiers ou dossiers YAML (defaut : evals/scenarios)")
    p.add_argument("--filtre", default="", help="expression reguliere sur l'id des scenarios")
    p.add_argument("--agent-url", default="", help="URL de l'agent : https://<hub>/agent (proxy) ou l'agent direct")
    p.add_argument("--hub-url", default="", help="URL du hub (etude, garde-fou, points de retour)")
    p.add_argument("--jeton", default="", help="cle qgis_... (defaut : variable EVALS_JETON)")
    p.add_argument("--utilisateur", default="", help="X-Hub-Proxy-User, seulement en appel direct de l'agent")
    pont = p.add_mutually_exclusive_group()
    pont.add_argument("--bridge", default="", help="URL HTTP du pont QGIS (ex. http://localhost:8080)")
    pont.add_argument("--bridge-cmd", default="",
                      help="commande qui lit la requete JSON sur stdin et rend la reponse (ex. kubectl exec ... curl)")
    p.add_argument("--study", default="", help="identifiant de l'etude bac a sable (OBLIGATOIRE)")
    p.add_argument("--activer-etude", action="store_true",
                   help="activer l'etude bac a sable si elle ne l'est pas (sinon refus)")
    p.add_argument("--repeat", type=int, default=0,
                   help=f"repetitions par scenario (defaut : celles du scenario, sinon {REPETITIONS_PAR_DEFAUT})")
    p.add_argument("--duree-max-tour", type=float, default=900.0,
                   help="au-dela, le banc coupe le tour et demande l'arret (secondes)")
    p.add_argument("--url-autorisee", action="append", default=[],
                   help="entree supplementaire de liste blanche d'URL (repetable)")
    p.add_argument("--profil", default="", help="profile_id envoye au chat (defaut : celui de l'etude)")
    p.add_argument("--sortie", default="", help="dossier du rapport (defaut : evals/resultats/<horodatage>)")
    p.add_argument("--dry-run", action="store_true",
                   help="valider et lister les scenarios, sans aucun appel reseau")
    return p


class Banc:
    def __init__(self, scenarios: list[Scenario], agent: ClientAgent, hub: ClientHub, pont: Pont,
                 sid: str, sortie: Path, repetitions: int = 0, liste_blanche: list[str] | None = None,
                 duree_max_tour: float = 900.0, activer: bool = False, profil: str = "",
                 journal: Callable[[str], None] = print, horloge: Callable[[], float] = time.monotonic):
        self.scenarios = scenarios
        self.agent = agent
        self.hub = hub
        self.pont = pont
        self.sid = sid
        self.sortie = sortie
        self.repetitions = repetitions
        self.liste_blanche = liste_blanche or []
        self.duree_max_tour = duree_max_tour
        self.activer = activer
        self.profil = profil
        self.log = journal
        self.horloge = horloge
        self.etude: dict = {}
        self._base: tuple[str, str] | None = None

    # -- garde-fou -------------------------------------------------------
    def verifier_garde_fou(self) -> None:
        """Leve RefusGardeFou si l'etude n'est pas un bac a sable actif."""
        self.etude = self.hub.etude(self.sid) or {}
        avertissement = verifier_etude(self.sid, self.etude)
        if avertissement:
            self.log(f"attention : {avertissement}")
        active = self.hub.etude_active()
        if (active or {}).get("id") != self.sid and self.activer:
            self.log(f"activation de l'etude bac a sable {self.sid}")
            self.hub.activer(self.sid)
            active = self.hub.etude_active()
        verifier_active(self.sid, active)

    def _reverifier_active(self) -> None:
        verifier_active(self.sid, self.hub.etude_active())

    # -- reinitialisation ------------------------------------------------
    def _poser_base(self) -> None:
        session = f"banc-{uuid.uuid4().hex[:12]}"
        ckpt = f"banc-base-{uuid.uuid4().hex[:12]}"
        try:
            self.hub.point_de_retour(session, ckpt)
            self._base = (session, ckpt)
            self.log(f"point de retour initial pose ({ckpt})")
        except ErreurHttp as exc:
            self._base = None
            self.log(f"attention : point de retour impossible ({exc}) ; les repetitions "
                     "s'enchaineront sur l'etat laisse par la precedente, sauf vider_projet.")

    def _restaurer_base(self) -> None:
        if self._base:
            session, ckpt = self._base
            self.hub.restaurer(session, ckpt, self.sid)

    # -- jeu -------------------------------------------------------------
    def _jouer_une(self, s: Scenario, rep: int) -> Execution:
        ex = Execution(scenario_id=s.id, repetition=rep, session_id=str(uuid.uuid4()))
        debut = self.horloge()
        try:
            self._reverifier_active()
            self._restaurer_base()
            for ligne in preparer(self.pont, s):
                self.log(f"  preparation : {ligne}")
            brut = self.sortie / "brut" / s.id
            brut.mkdir(parents=True, exist_ok=True)
            for i, message in enumerate(s.messages(), start=1):
                evenements: list[dict] = []
                tour = self.agent.envoyer(message, ex.session_id, self.duree_max_tour,
                                          profil=self.profil, journal=evenements)
                (brut / f"r{rep}-t{i}.jsonl").write_text(
                    "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evenements),
                    encoding="utf-8", newline="\n")
                ex.tours.append(tour)
                if i == 1:
                    self.agent.etiqueter(ex.session_id, "origine", "banc_evaluation")
                self.log(f"  tour {i} : {tour.iterations} iteration(s), outils {tour.outils()}, "
                         f"{tour.duree_s:.0f} s")
                if tour.erreur_flux or tour.coupe_par_le_banc:
                    break
            try:
                ex.etat = lire_etat(self.pont, s)
            except Exception as exc:
                self.log(f"  sonde d'etat en echec : {exc}")
                ex.etat = None
        except RefusGardeFou:
            raise
        except Exception as exc:
            ex.erreur_banc = f"{type(exc).__name__}: {exc}"
            self.log(f"  execution invalide : {ex.erreur_banc}")
        ex.duree_s = self.horloge() - debut
        return ex

    def jouer(self) -> list[ResultatExecution]:
        self.verifier_garde_fou()
        self._poser_base()
        resultats: list[ResultatExecution] = []
        try:
            for s in self.scenarios:
                n = self.repetitions or s.repetitions or REPETITIONS_PAR_DEFAUT
                for rep in range(1, n + 1):
                    self.log(f"{s.id} : repetition {rep}/{n}")
                    ex = self._jouer_une(s, rep)
                    r = evaluer(s, ex, self.liste_blanche)
                    self.log(f"  -> {'reussie' if r.reussi else 'echouee'}"
                             + ("" if r.valide else " (invalide)"))
                    resultats.append(r)
        finally:
            try:
                self._reverifier_active()
                self._restaurer_base()
            except Exception as exc:
                self.log(f"attention : restauration finale impossible ({exc})")
        return resultats


def _dry_run(scenarios: list[Scenario]) -> int:
    for s in scenarios:
        att = s.attentes
        print(f"{s.id:32} {s.famille:22} {len(s.tours)} tour(s)  "
              f"attentes : {', '.join(sorted(att)) or 'defauts'}  seuil {s.seuil_reussite:.0%}")
    print(f"{len(scenarios)} scenario(s) valides.")
    return 0


def main(argv: list[str] | None = None,
         fabrique_hub: Callable[..., ClientHub] = ClientHub,
         fabrique_agent: Callable[..., ClientAgent] = ClientAgent,
         fabrique_pont: Callable[[argparse.Namespace], Pont] | None = None) -> int:
    args = construire_parser().parse_args(argv)
    try:
        scenarios = charger(args.scenarios)
    except (ScenarioInvalide, OSError) as exc:
        print(f"scenarios invalides : {exc}", file=sys.stderr)
        return CODE_REFUS
    if args.filtre:
        scenarios = [s for s in scenarios if re.search(args.filtre, s.id)]
    if args.dry_run:
        return _dry_run(scenarios)

    # Garde-fou d'abord : rien ne part sur le reseau sans etude designee.
    if not args.study.strip():
        print("refus : --study est obligatoire (etude dont le nom commence par "
              "« bac-a-sable »).", file=sys.stderr)
        return CODE_REFUS
    manquants = [o for o, v in (("--agent-url", args.agent_url), ("--hub-url", args.hub_url)) if not v]
    if not (args.bridge or args.bridge_cmd):
        manquants.append("--bridge ou --bridge-cmd")
    jeton = args.jeton or os.environ.get("EVALS_JETON", "")
    if not jeton:
        manquants.append("--jeton ou EVALS_JETON")
    if manquants:
        print(f"refus : options manquantes : {', '.join(manquants)}", file=sys.stderr)
        return CODE_REFUS

    hub = fabrique_hub(args.hub_url, jeton)
    agent = fabrique_agent(args.agent_url, jeton, args.utilisateur)
    if fabrique_pont is not None:
        pont = fabrique_pont(args)
    else:
        pont = PontHttp(args.bridge) if args.bridge else PontCommande(args.bridge_cmd)
    horodatage = datetime.now().strftime("%Y%m%d-%H%M%S")
    sortie = Path(args.sortie) if args.sortie else Path(__file__).parent / "resultats" / horodatage
    liste_blanche = [args.hub_url, *CATALOGUE_PAR_DEFAUT, *args.url_autorisee]
    banc = Banc(scenarios, agent, hub, pont, args.study.strip(), sortie, args.repeat,
                liste_blanche, args.duree_max_tour, args.activer_etude, args.profil)
    try:
        resultats = banc.jouer()
    except RefusGardeFou as exc:
        print(f"refus du garde-fou : {exc}", file=sys.stderr)
        return CODE_REFUS
    except ErreurHttp as exc:
        print(f"hub injoignable : {exc}", file=sys.stderr)
        return CODE_REFUS
    meta = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "etude": args.study, "etude_nom": banc.etude.get("name", ""),
        "agent_url": args.agent_url, "hub_url": args.hub_url,
        "repetitions": args.repeat or "selon scenario",
        "reinitialisation": "point de retour hub" if banc._base else "aucune",
    }
    rapport = agreger(scenarios, resultats, meta)
    chemin_json, chemin_md = ecrire(rapport, sortie)
    print(f"rapport : {chemin_md}")
    print(f"donnees : {chemin_json}")
    return 0 if all(f["seuil_atteint"] for f in rapport["scenarios"]) else 1


if __name__ == "__main__":
    sys.exit(main())
