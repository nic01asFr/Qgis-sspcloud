"""
Indexer pour la KB qgis_tips.md.

Parse le markdown en sections `## tip: <titre>`, extrait `symptom` (embed),
`pattern` (bloc code), `note` (1 ligne), et insère chaque tip dans le
vector_store SQLite-vec avec source_type="qgis_tip".

Le symptom est l'ancre sémantique pour le matching auto sur erreur tool.
Pattern + note + title sont stockés en metadata pour injection dans la
réponse enrichie.

Usage : `python -m agent.tips.indexer` après modif de qgis_tips.md.
Idempotent : purge tous les "qgis_tip" existants puis ré-insère.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from agent import vector_store


_TIPS_FILE = Path(__file__).parent / "qgis_tips.md"


def parse_tips(markdown: str) -> list[dict]:
    """Parse un markdown en liste de tips.

    Chaque tip est délimité par `## tip: <titre>` jusqu'au prochain `## tip:`
    ou EOF. Les champs `symptom:`, `pattern:`, `note:` sont extraits.

    Le pattern peut être en bloc YAML `|` (lignes indentées) ou en bloc
    code fence ```...```. Les deux sont supportés.
    """
    tips: list[dict] = []
    # Split sur les sections "## tip: titre"
    parts = re.split(r"^## tip: (.+?)$", markdown, flags=re.MULTILINE)
    # parts[0] = prélude avant le premier ## tip:, parts[1+] = pairs (titre, body)
    if len(parts) < 3:
        return tips
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        tip = _parse_tip_body(title, body)
        if tip:
            tips.append(tip)
    return tips


def _parse_tip_body(title: str, body: str) -> dict | None:
    """Extrait symptom (1 ligne), pattern (bloc), note (1 ligne) du body."""
    symptom = ""
    note = ""
    pattern = ""

    # symptom: 1 ligne
    m = re.search(r"^symptom:\s*(.+)$", body, flags=re.MULTILINE)
    if m:
        symptom = m.group(1).strip()

    # note: 1 ligne (à la fin typiquement)
    m = re.search(r"^note:\s*(.+)$", body, flags=re.MULTILINE)
    if m:
        note = m.group(1).strip()

    # pattern: bloc YAML | OU bloc ```...```
    # Pattern YAML : "pattern: |\n  ligne1\n  ligne2\n..."
    m = re.search(
        r"^pattern:\s*\|\s*\n((?:[ \t]+.*\n?)+)",
        body,
        flags=re.MULTILINE,
    )
    if m:
        raw = m.group(1)
        # Dé-indenter (enlève l'indent commun)
        lines = raw.split("\n")
        if lines:
            indent = len(lines[0]) - len(lines[0].lstrip())
            pattern = "\n".join(
                ln[indent:] if len(ln) >= indent else ln for ln in lines
            ).rstrip()
    else:
        # Fallback : bloc code fence
        m = re.search(
            r"^pattern:.*?\n```(?:python)?\n(.*?)\n```",
            body,
            flags=re.MULTILINE | re.DOTALL,
        )
        if m:
            pattern = m.group(1).strip()

    if not symptom or not pattern:
        return None

    return {
        "title": title,
        "symptom": symptom,
        "pattern": pattern,
        "note": note,
    }


async def reindex_all() -> int:
    """Purge tous les qgis_tip existants et ré-insère depuis le markdown."""
    await vector_store.init_vector_store()

    md = _TIPS_FILE.read_text(encoding="utf-8")
    tips = parse_tips(md)
    if not tips:
        print("[indexer] Aucun tip parsé — vérifier le markdown.")
        return 0

    # Purge l'ancien
    purged = await vector_store.purge(source_type="qgis_tip")
    print(f"[indexer] {purged} anciens qgis_tip purgés.")

    # Ré-insère
    inserted = 0
    for tip in tips:
        await vector_store.add(
            text=tip["symptom"],
            source_type="qgis_tip",
            source_id=tip["title"],
            metadata={
                "title": tip["title"],
                "pattern": tip["pattern"],
                "note": tip["note"],
            },
        )
        inserted += 1
        print(f"[indexer] + {tip['title']}")

    print(f"[indexer] {inserted} tips indexés.")
    return inserted


if __name__ == "__main__":
    asyncio.run(reindex_all())
