"""Cette suite doit tourner en CI, et l'image de l'agent doit l'attendre.

Constat du 2026-09-24 (strategie qualite, §6) : la CI ne lancait que
`hub/tests`. Les 246 tests de l'agent ne tournaient que sur un poste, et
l'image partait sans eux -- un test qu'aucune machine n'execute ne protege de
rien. Ce module verrouille les deux moities du correctif : la suite est
lancee, et la construction de l'image en depend.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_DEPOT = Path(__file__).resolve().parents[2]


def _jobs() -> dict:
    ci = (_DEPOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    return yaml.safe_load(ci)["jobs"]


def _needs(job: dict) -> list[str]:
    besoin = job.get("needs", [])
    return [besoin] if isinstance(besoin, str) else list(besoin)


def _job_qui_lance_la_suite(jobs: dict) -> str | None:
    for nom, job in jobs.items():
        for etape in job.get("steps", []):
            if etape.get("working-directory") == "agent" and "pytest" in etape.get("run", ""):
                return nom
    return None


def test_la_suite_de_l_agent_est_lancee():
    assert _job_qui_lance_la_suite(_jobs()) is not None


def test_l_image_de_l_agent_attend_sa_suite():
    jobs = _jobs()
    assert _job_qui_lance_la_suite(jobs) in _needs(jobs["build-agent"])


def test_l_image_de_l_agent_attend_toujours_celle_du_hub():
    """La feuille du produit vient de hub/ (Dockerfile.agent) : l'agent ne
    doit pas partir sur un hub rouge."""
    assert "tests" in _needs(_jobs()["build-agent"])
