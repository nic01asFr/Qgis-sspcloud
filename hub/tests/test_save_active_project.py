"""Tests hub.studies.save_active_project_pod_code — Sprint isolation Fix #1.

Verifie que la nouvelle fonction dual-write :
- Genere du code Python valide (parseable en AST)
- Backward compat : save_active_pod_code(sid) = wrapper qui ne write que legacy
- Nouveau : save_active_project_pod_code(sid, pid) inclut le write pid-scope
- Le code injecte bien le pid dans le pattern conditionnel `if pid and n_layers > 0`
- Le pid-scope write utilise Paths=Absolute=True (evite bug relatif sous-dossier)
- Restore state legacy apres write pid-scope (setFileName + Paths=False)
"""
from __future__ import annotations

import ast

from hub.studies import save_active_pod_code, save_active_project_pod_code


SID = "c6cbb74ceeb7"
PID = "a1b2c3d4e5f6"


def test_backward_compat_wrapper():
    """save_active_pod_code(sid) doit produire du code parseable (backward compat)."""
    code = save_active_pod_code(SID)
    assert isinstance(code, str)
    assert len(code) > 100
    # Doit contenir le sid injecte
    assert SID in code
    # Le code doit etre parseable
    ast.parse(code)


def test_no_pid_write_legacy_only():
    """save_active_project_pod_code(sid, None) : bloc pid ne s'execute pas.

    Le code contient bien la logique pid-scope, mais la condition
    `if pid and n_layers > 0` est fausse avec pid=None -> comportement legacy strict.
    """
    code = save_active_project_pod_code(SID, None)
    assert SID in code
    # pid=None doit apparaitre dans le code injecte
    assert "pid = None" in code
    # La branche legacy write doit toujours etre presente
    assert "STUDY_SAVE_OK" in code
    # La branche pid-scope aussi (pour execution conditionnelle a runtime)
    assert "STUDY_SAVE_PID_OK" in code
    assert 'projects/{sid}/projects/' not in code  # pas d'erreur de path


def test_with_pid_dual_write():
    """save_active_project_pod_code(sid, pid) : contient le bloc pid-scope actif."""
    code = save_active_project_pod_code(SID, PID)
    assert SID in code
    assert PID in code
    # Les 2 marqueurs OK doivent etre presents
    assert "STUDY_SAVE_OK" in code       # legacy
    assert "STUDY_SAVE_PID_OK" in code   # pid-scope
    # Le pid-scope utilise Paths=Absolute=True (evite bug ../data/ depuis sous-dossier)
    assert 'writeEntry("Paths", "Absolute", True)' in code
    # Restore apres pid-scope : setFileName + Paths=False
    assert 'writeEntry("Paths", "Absolute", False)' in code


def test_pid_target_path_correct():
    """Le path pid-scope doit etre /data/studies/{sid}/projects/{pid}/project.qgz."""
    code = save_active_project_pod_code(SID, PID)
    # Pattern attendu apres injection : Path(f"/data/studies/{sid}/projects/{pid}/project.qgz")
    # Le sid + pid sont injectes en runtime via variables Python
    assert '/data/studies/{sid}/projects/{pid}/project.qgz' in code


def test_ast_parseable_both_modes():
    """Les 2 modes (pid=None et pid=str) generent du code Python valide."""
    for pid in (None, PID):
        code = save_active_project_pod_code(SID, pid)
        try:
            ast.parse(code)
        except SyntaxError as exc:
            raise AssertionError(f"AST parse failed for pid={pid!r}: {exc}\n{code[:500]}")


def test_no_double_write_legacy():
    """La branche legacy write doit apparaitre UNE seule fois (pas de doublon)."""
    code = save_active_project_pod_code(SID, PID)
    # Le pattern "proj.write(str(target))" (write legacy) doit apparaitre 1x
    # Le pattern "proj.write(str(pid_target))" (write pid) doit apparaitre 1x
    assert code.count("proj.write(str(target))") == 1
    assert code.count("proj.write(str(pid_target))") == 1


def test_restore_state_after_pid_write():
    """Apres le write pid-scope, on restore fileName + Paths=False (legacy)."""
    code = save_active_project_pod_code(SID, PID)
    # Le restore doit venir apres le write pid_target
    idx_pid_write = code.find("proj.write(str(pid_target))")
    idx_restore_filename = code.find("proj.setFileName(str(target))", idx_pid_write)
    assert idx_restore_filename > idx_pid_write, (
        "Le restore setFileName(target) doit venir APRES le write pid_target"
    )
