"""Agregation des resultats et rapports JSON + Markdown."""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

from evals.evaluation import ResultatExecution
from evals.scenario import Scenario


def _taux(n: int, total: int) -> float | None:
    return round(n / total, 3) if total else None


def _pct(t: float | None) -> str:
    return "-" if t is None else f"{100 * t:.0f} %"


def agreger(scenarios: list[Scenario], resultats: list[ResultatExecution], meta: dict) -> dict:
    par_id: dict[str, list[ResultatExecution]] = defaultdict(list)
    for r in resultats:
        par_id[r.scenario_id].append(r)
    fiches = []
    par_categorie: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    par_famille: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for s in scenarios:
        runs = par_id.get(s.id, [])
        valides = [r for r in runs if r.valide]
        reussis = [r for r in valides if r.reussi]
        criteres: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in valides:
            for c in r.criteres:
                criteres[c.nom][1] += 1
                criteres[c.nom][0] += int(c.ok)
                par_categorie[c.categorie][1] += 1
                par_categorie[c.categorie][0] += int(c.ok)
        iterations = [t.iterations for r in valides for t in r.execution.tours]
        durees = [r.execution.duree_s for r in valides]
        taux = _taux(len(reussis), len(valides))
        par_famille[s.famille][0] += len(reussis)
        par_famille[s.famille][1] += len(valides)
        fiches.append({
            "id": s.id,
            "famille": s.famille,
            "titre": s.titre,
            "repetitions": len(runs),
            "valides": len(valides),
            "invalides": [r.criteres[0].detail for r in runs if not r.valide],
            "reussites": len(reussis),
            "taux_reussite": taux,
            "seuil": s.seuil_reussite,
            "seuil_atteint": (taux is not None and taux >= s.seuil_reussite),
            "criteres": {
                nom: {"reussites": ok, "total": tot, "taux": _taux(ok, tot)}
                for nom, (ok, tot) in sorted(criteres.items())
            },
            "cout": {
                "iterations_mediane": statistics.median(iterations) if iterations else None,
                "iterations_max": max(iterations) if iterations else None,
                "duree_mediane_s": round(statistics.median(durees), 1) if durees else None,
                "duree_max_s": round(max(durees), 1) if durees else None,
                "erreurs_outils": sum(t.erreurs_outils() for r in valides for t in r.execution.tours),
                "arrets_auto": sum(1 for r in valides if any(t.arret_auto for t in r.execution.tours)),
                "execute_python": sum(t.outils().count("execute_python")
                                      for r in valides for t in r.execution.tours),
            },
            "executions": [r.resume() for r in runs],
        })
    toutes_iterations = [t.iterations for r in resultats if r.valide for t in r.execution.tours]
    return {
        "meta": meta,
        "synthese": {
            "scenarios": len(scenarios),
            "executions": len(resultats),
            "executions_valides": sum(1 for r in resultats if r.valide),
            "taux_reussite_global": _taux(sum(1 for r in resultats if r.valide and r.reussi),
                                          sum(1 for r in resultats if r.valide)),
            "seuils_atteints": sum(1 for f in fiches if f["seuil_atteint"]),
            "par_categorie": {k: {"reussites": v[0], "total": v[1], "taux": _taux(*v)}
                              for k, v in sorted(par_categorie.items())},
            "par_famille": {k: {"reussites": v[0], "total": v[1], "taux": _taux(*v)}
                            for k, v in sorted(par_famille.items())},
            "iterations_mediane": statistics.median(toutes_iterations) if toutes_iterations else None,
        },
        "scenarios": fiches,
    }


def markdown(rapport: dict) -> str:
    s = rapport["synthese"]
    m = rapport["meta"]
    lignes = [
        "# Banc d'evaluation comportementale — rapport",
        "",
        f"- Date : {m.get('date', '?')}",
        f"- Etude bac a sable : {m.get('etude', '?')} ({m.get('etude_nom', '?')})",
        f"- Agent : {m.get('agent_url', '?')}",
        f"- Repetitions demandees : {m.get('repetitions', '?')}",
        f"- Executions valides : {s['executions_valides']} / {s['executions']}",
        f"- Taux de reussite global : {_pct(s['taux_reussite_global'])}",
        f"- Scenarios au seuil : {s['seuils_atteints']} / {s['scenarios']}",
        f"- Mediane des iterations par tour : {s['iterations_mediane'] if s['iterations_mediane'] is not None else '-'}",
        "",
        "## Par categorie de critere",
        "",
        "| Categorie | Reussites | Total | Taux |",
        "|---|---|---|---|",
    ]
    for cat, v in s["par_categorie"].items():
        lignes.append(f"| {cat} | {v['reussites']} | {v['total']} | {_pct(v['taux'])} |")
    lignes += ["", "## Par scenario", "",
               "| Scenario | Famille | Reussites | Taux | Seuil | Iter. med. | Duree med. | Erreurs outils | Arrets auto |",
               "|---|---|---|---|---|---|---|---|---|"]
    for f in rapport["scenarios"]:
        c = f["cout"]
        seuil = f"{_pct(f['seuil'])} {'atteint' if f['seuil_atteint'] else 'NON atteint'}"
        lignes.append(
            f"| {f['id']} | {f['famille']} | {f['reussites']}/{f['valides']} | {_pct(f['taux_reussite'])} "
            f"| {seuil} | {c['iterations_mediane'] if c['iterations_mediane'] is not None else '-'} "
            f"| {c['duree_mediane_s'] if c['duree_mediane_s'] is not None else '-'} s "
            f"| {c['erreurs_outils']} | {c['arrets_auto']} |"
        )
    for f in rapport["scenarios"]:
        lignes += ["", f"### {f['id']} — {f['titre'] or f['famille']}", ""]
        if f["invalides"]:
            lignes.append(f"Executions invalides (banc) : {len(f['invalides'])} — {f['invalides'][0]}")
            lignes.append("")
        echoues = {n: v for n, v in f["criteres"].items() if v["reussites"] < v["total"]}
        if not echoues:
            lignes.append("Tous les criteres passent sur les executions valides.")
        else:
            lignes += ["| Critere en echec | Reussites | Taux |", "|---|---|---|"]
            for nom, v in echoues.items():
                lignes.append(f"| `{nom}` | {v['reussites']}/{v['total']} | {_pct(v['taux'])} |")
            lignes += ["", "Detail du premier echec par critere :", ""]
            vus = set()
            for ex in f["executions"]:
                for e in ex["echecs"]:
                    if e["nom"] not in vus:
                        vus.add(e["nom"])
                        lignes.append(f"- `{e['nom']}` (repetition {ex['repetition']}) : {e['detail']}")
        lignes.append("")
        for ex in f["executions"]:
            outils = " / ".join(", ".join(t) or "aucun" for t in ex["outils"])
            lignes.append(f"- repetition {ex['repetition']} : {'reussie' if ex['reussi'] else 'echouee'}"
                          f", outils : {outils}")
    return "\n".join(lignes) + "\n"


def ecrire(rapport: dict, dossier: Path) -> tuple[Path, Path]:
    dossier.mkdir(parents=True, exist_ok=True)
    chemin_json = dossier / "rapport.json"
    chemin_md = dossier / "rapport.md"
    chemin_json.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    chemin_md.write_text(markdown(rapport), encoding="utf-8", newline="\n")
    return chemin_json, chemin_md
