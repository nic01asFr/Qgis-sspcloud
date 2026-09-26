"""Historique relu par le modele, appel ecrit en texte, emojis (D1 et D5).

Mesure live du 2026-09-26 : le contenu assistant stocke en base est le rendu
du chat (raisonnement dans `<details>`, lignes `> **`outil`** — `k=v``, blocs
de resultat). Relu tel quel au tour suivant, le modele a recopie ce format au
lieu d'emettre un appel : tour clos sans action ni message.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec = type(sys)("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

from agent import main as agent_main  # noqa: E402
from agent import texte_modele as tm  # noqa: E402

# Forme reelle d'un message assistant en base (session S2, 2026-09-26),
# reduite a l'essentiel.
_STOCKE = (
    '\n\n<details class="agent-reasoning"><summary>Raisonnement</summary>\n\n'
    "L'utilisateur veut le bati dans la commune. Je dois d'abord fixer la zone.\n\n"
    "</details>\n\n"
    "Je définis la zone d'étude sur Aix-en-Provence."
    "\n\n<!--ckpt:0a1b2c3d4e5f-->\n"
    "\n\n> **`set_study_zone`** — `target=Aix-en-Provence`, `mode=commune`\n"
    "\n```\n{\"success\": true, \"bbox\": [5.26, 43.45, 5.55, 43.59]}\n```\n"
    "\n\n> **`clip_to_study_zone`** — `layer_id=batiments_3f2a`\n"
    "\n\nLe bâti est désormais limité à la commune : **54 557 bâtiments**."
)
_APPELS = [
    {"tool": "set_study_zone", "args": {"target": "Aix-en-Provence"},
     "result": '{"success": true, "bbox": [5.26, 43.45, 5.55, 43.59]}'},
    {"tool": "clip_to_study_zone", "args": {"layer_id": "batiments_3f2a"},
     "result": '{"success": true, "layer_id": "batiments_aix", "feature_count": 54557'},
]
_OUTILS = {"get_project_info", "set_study_zone", "clip_to_study_zone", "smart_load"}


# ── Nettoyage de l'historique ───────────────────────────────────────────────


def test_le_modele_ne_relit_que_le_texte_final():
    texte = tm.texte_final(_STOCKE)
    assert "agent-reasoning" not in texte
    assert "Raisonnement" not in texte
    assert "**`" not in texte
    assert "ckpt" not in texte
    assert '"bbox"' not in texte
    assert texte.startswith("Je définis la zone d'étude sur Aix-en-Provence.")
    assert texte.endswith("**54 557 bâtiments**.")


def test_le_nettoyage_est_idempotent():
    une_fois = tm.contenu_assistant_pour_le_modele(_STOCKE, _APPELS)
    assert tm.contenu_assistant_pour_le_modele(une_fois) == une_fois


def test_le_memo_dit_ce_qui_a_ete_fait_sans_forme_d_appel():
    memo = tm.memo_outils(json.dumps(_APPELS))  # colonne de la base : JSON
    assert memo.startswith(tm.MEMO_PREFIXE)
    assert "set_study_zone : réussi" in memo
    assert "clip_to_study_zone : réussi" in memo
    assert "feature_count 54557" in memo
    assert "layer_id batiments_aix" in memo
    # Rien qui ressemble a un appel : pas de backtick, de gras, de citation,
    # ni de parenthese collee a un nom d'outil.
    assert "`" not in memo and "**" not in memo and not memo.startswith(">")
    assert tm.appel_ecrit_en_texte(memo.replace(tm.MEMO_PREFIXE, "x"), _OUTILS) is None


def test_le_memo_signale_un_echec():
    memo = tm.memo_outils([{"tool": "smart_load",
                            "result": '{"success": false, "error": "source inconnue"}'}])
    assert "smart_load : échec (source inconnue)" in memo


def test_le_memo_precede_le_texte():
    contenu = tm.contenu_assistant_pour_le_modele(_STOCKE, _APPELS)
    assert contenu.index(tm.MEMO_PREFIXE) < contenu.index("Je définis")


def test_un_tour_qui_n_a_fait_qu_ecrire_un_faux_appel_disparait():
    """Le tour fautif du 2026-09-26 ne doit pas servir de modele au suivant."""
    fautif = (
        '<details class="agent-reasoning"><summary>Raisonnement</summary>\n\n'
        "Il me faut le layer_id.\n\n</details>\n\n"
        "> **`get_project_info`** — pour récupérer le layer_id de la couche bâti\n"
    )
    assert tm.contenu_assistant_pour_le_modele(fautif) == ""


def test_le_lien_de_livrable_reste_sans_son_gabarit():
    texte = tm.texte_final("\n\n\U0001F4CE **Livrable publie** : [Voir le storymap](https://hub/x)\n")
    assert texte == "Livrable publié : [Voir le storymap](https://hub/x)"


def test_un_bloc_de_code_de_l_utilisateur_est_garde():
    """Seuls les blocs qui suivent une ligne d'outil sont retires."""
    texte = "Voici la formule :\n\n```\nsurface / 10000\n```\n"
    assert "surface / 10000" in tm.texte_final(texte)


def test_l_historique_envoye_au_modele_est_nettoye():
    messages = [
        {"role": "user", "content": "Oui, uniquement le bâti dans la commune"},
        {"role": "assistant", "content": _STOCKE, "tool_calls": json.dumps(_APPELS)},
        {"role": "user", "content": "combien à Aix ?"},
    ]
    compacte = agent_main._historique_pour_le_modele(messages)
    assistant = [m for m in compacte if m["role"] == "assistant"][0]["content"]
    assert "**`" not in assistant and "agent-reasoning" not in assistant
    assert "54 557" in assistant and tm.MEMO_PREFIXE in assistant
    # Les messages utilisateur ne sont pas touches.
    assert compacte[0]["content"] == "Oui, uniquement le bâti dans la commune"


# ── Appel ecrit en texte ────────────────────────────────────────────────────


def test_le_faux_appel_du_26_septembre_est_reconnu():
    texte = "> **`get_project_info`** — pour récupérer le layer_id de la couche bâti"
    assert tm.appel_ecrit_en_texte(texte, _OUTILS) == "get_project_info"


def test_les_autres_formes_d_appel_ecrit_sont_reconnues():
    assert tm.appel_ecrit_en_texte("Je lance **`smart_load`** tout de suite.", _OUTILS) == "smart_load"
    assert tm.appel_ecrit_en_texte('smart_load(source="bdtopo_batiments")', _OUTILS) == "smart_load"
    assert tm.appel_ecrit_en_texte("> `set_study_zone` — target=Aix", _OUTILS) == "set_study_zone"
    assert tm.appel_ecrit_en_texte('<tool_call>{"name": "x"}</tool_call>', _OUTILS) == "tool_call"
    assert tm.appel_ecrit_en_texte(tm.MEMO_PREFIXE + " ...]", _OUTILS) == "memo recopie"


def test_citer_un_outil_en_passant_n_est_pas_un_appel():
    """Defaut de surface (D5), traite par le prompt, pas par une relance."""
    for texte in (
        "J'ai découpé la couche avec clip_to_study_zone (54 557 bâtiments).",
        "Le résultat compte 54 557 bâtiments dans la commune.",
        "> Remarque : l'emprise chargée dépasse la commune.",
        "",
    ):
        assert tm.appel_ecrit_en_texte(texte, _OUTILS) is None, texte


def test_un_outil_non_expose_ne_declenche_rien():
    assert tm.appel_ecrit_en_texte("**`outil_inconnu`**", _OUTILS) is None


# ── Emojis (D5) ─────────────────────────────────────────────────────────────


def test_les_emojis_sont_retires():
    assert tm.retirer_emojis("\u26A0\uFE0F Attention : emprise large") == "Attention : emprise large"
    assert tm.retirer_emojis("Densité \U0001F7E0 moyenne") == "Densité moyenne"
    assert tm.retirer_emojis("\u2705 Fait") == "Fait"


def test_les_chiffres_et_les_signes_sont_intacts():
    for texte in ("54 557 bâtiments", "rapport 1,1 → 19 × plus", "≥ 3 étages",
                  "43.4583, 5.2694", "- item\n  - sous-item"):
        assert tm.retirer_emojis(texte) == texte
    assert tm.retirer_emojis("1\uFE0F\u20E3 Charger") == "1 Charger"
