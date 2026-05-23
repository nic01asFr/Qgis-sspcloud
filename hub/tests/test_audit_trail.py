"""Tests d'observabilité : audit_trail.read_treatments + summarize_for_methodology.

Vérifie que la lecture du log d'audit respecte :
  - le filtrage par `since` (timestamp)
  - le filtrage par `kinds` (liste blanche)
  - la limite de retour (`limit`)
  - la robustesse face aux lignes corrompues (silently skipped)
  - le tri implicite (ordre chronologique d'écriture conservé)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub.audit_trail import read_treatments, summarize_for_methodology  # noqa: E402


def _write_log(events):
    """Écrit une liste d'évènements dans un fichier temporaire, renvoie le path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8",
    )
    for evt in events:
        tmp.write(json.dumps(evt, ensure_ascii=False) + "\n")
    tmp.close()
    return Path(tmp.name)


def test_read_treatments_empty_missing_file():
    """Fichier inexistant → liste vide, pas d'exception."""
    assert read_treatments(log_path="/tmp/does_not_exist_xyz.jsonl") == []


def test_read_treatments_returns_all_in_order():
    p = _write_log([
        {"ts": 1, "kind": "processing", "tool": "a", "ok": True},
        {"ts": 2, "kind": "export", "tool": "b", "ok": True},
        {"ts": 3, "kind": "processing", "tool": "c", "ok": True},
    ])
    try:
        events = read_treatments(log_path=p)
        assert [e["tool"] for e in events] == ["a", "b", "c"]
    finally:
        p.unlink()


def test_read_treatments_filters_since():
    p = _write_log([
        {"ts": 10, "kind": "processing", "tool": "old", "ok": True},
        {"ts": 20, "kind": "processing", "tool": "new", "ok": True},
    ])
    try:
        events = read_treatments(log_path=p, since=15)
        assert len(events) == 1
        assert events[0]["tool"] == "new"
    finally:
        p.unlink()


def test_read_treatments_filters_kinds():
    p = _write_log([
        {"ts": 1, "kind": "processing", "tool": "a", "ok": True},
        {"ts": 2, "kind": "layer_added", "tool": "b", "ok": True},
        {"ts": 3, "kind": "export", "tool": "c", "ok": True},
    ])
    try:
        events = read_treatments(log_path=p, kinds=["processing", "export"])
        kinds_returned = {e["kind"] for e in events}
        assert kinds_returned == {"processing", "export"}
        assert len(events) == 2
    finally:
        p.unlink()


def test_read_treatments_limit():
    p = _write_log([
        {"ts": i, "kind": "processing", "tool": f"t{i}", "ok": True}
        for i in range(20)
    ])
    try:
        events = read_treatments(log_path=p, limit=5)
        assert len(events) == 5
        # Limit garde les N derniers (suffix de la liste)
        assert events[0]["tool"] == "t15"
        assert events[-1]["tool"] == "t19"
    finally:
        p.unlink()


def test_read_treatments_skips_corrupted_lines():
    """Une ligne JSON cassée ne doit pas planter la lecture."""
    p = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8",
    )
    p.write('{"ts": 1, "kind": "processing", "tool": "ok1", "ok": true}\n')
    p.write("garbage{not json\n")
    p.write("\n")  # ligne vide
    p.write('{"ts": 2, "kind": "processing", "tool": "ok2", "ok": true}\n')
    p.close()
    path = Path(p.name)
    try:
        events = read_treatments(log_path=path)
        tools = [e["tool"] for e in events]
        assert tools == ["ok1", "ok2"]
    finally:
        path.unlink()


def test_summarize_for_methodology_keeps_only_significant_ok():
    """summarize ne retient que processing/export avec ok=True (champ algo)."""
    events = [
        {"ts": 1, "kind": "processing", "tool": "a", "ok": True, "outputs": ["o1"]},
        {"ts": 2, "kind": "layer_added", "tool": "x", "ok": True, "outputs": []},
        {"ts": 3, "kind": "processing", "tool": "b", "ok": False, "outputs": ["o2"]},
        {"ts": 4, "kind": "export", "tool": "c", "ok": True, "outputs": ["o3"],
         "params": {"ext": "geojson"}},
        {"ts": 5, "kind": "python", "tool": "d", "ok": True, "outputs": []},
    ]
    out = summarize_for_methodology(events)
    # Le step processing porte algo=tool, le step export porte algo=ext.
    algos = [e["algo"] for e in out]
    assert "a" in algos  # processing ok=True conservé
    assert "geojson" in algos  # export ok=True conservé (algo = extension)
    assert "x" not in algos  # layer_added exclu
    assert "b" not in algos  # ok=False exclu
    assert "d" not in algos  # python exclu


if __name__ == "__main__":
    test_read_treatments_empty_missing_file()
    test_read_treatments_returns_all_in_order()
    test_read_treatments_filters_since()
    test_read_treatments_filters_kinds()
    test_read_treatments_limit()
    test_read_treatments_skips_corrupted_lines()
    test_summarize_for_methodology_keeps_only_significant_ok()
    print("OK — 7 tests passent.")
