"""Une etude active sans projet actif : l'etat qui se referme sur lui-meme.

Constate en production le 2026-08-23. `active_study` portait une ligne,
`active_project` aucune. Consequence en chaine :

    get_active_project_id() -> None
      -> pid_for_save = None
        -> save_active_project_pod_code(sid, None) n'ecrit que le legacy
          -> projects/{pid}/project.qgz n'existe jamais
            -> le hub ne trouve pas le projet que sa propre base annonce
              -> l'interface affiche « aucun projet actif »

Et rien ne le repare : `/studies/{sid}/activate` enregistre bien le projet
actif, mais une etude deja active n'est jamais reactivee. L'utilisateur reste
bloque dans cet etat sans savoir qu'il doit rebasculer son etude a la main.
"""

from __future__ import annotations

import pytest

import hub.studies as studies


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Une base neuve, dans l'etat exact ou la production a ete trouvee."""
    import sqlite3
    chemin = tmp_path / "studies.db"
    c = sqlite3.connect(chemin)
    c.executescript(
        """
        CREATE TABLE studies (sid TEXT PRIMARY KEY, owner TEXT, name TEXT,
                              status TEXT DEFAULT 'active');
        CREATE TABLE study_projects (
            pid TEXT PRIMARY KEY, sid TEXT, owner TEXT, label TEXT,
            qgz_path TEXT, is_default INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active', created_at INTEGER DEFAULT 0);
        CREATE TABLE active_study (owner TEXT PRIMARY KEY, sid TEXT);
        CREATE TABLE active_project (owner TEXT PRIMARY KEY, pid TEXT);
        """
    )
    c.execute("INSERT INTO studies VALUES ('8627f5ac3ac5','nic01asfr','test','active')")
    c.execute(
        "INSERT INTO study_projects VALUES "
        "('ee1ccfdf9881','8627f5ac3ac5','nic01asfr','Projet principal','',1,'active',100)"
    )
    c.execute("INSERT INTO active_study VALUES ('nic01asfr','8627f5ac3ac5')")
    # active_project reste VIDE : c'est tout le sujet.
    c.commit()
    c.close()
    monkeypatch.setattr(studies, "_DB_PATH", str(chemin))
    return chemin


@pytest.mark.asyncio
async def test_le_projet_principal_est_retabli(base):
    """Une etude active qui a un projet principal a forcement un projet actif."""
    assert await studies.get_active_project_id("nic01asfr") == "ee1ccfdf9881"


@pytest.mark.asyncio
async def test_le_rattrapage_est_persiste(base):
    """Sinon on le referait a chaque lecture, et le dual-write resterait
    tributaire du hasard d'un appel."""
    await studies.get_active_project_id("nic01asfr")
    import sqlite3
    c = sqlite3.connect(base)
    lignes = c.execute("SELECT pid FROM active_project WHERE owner='nic01asfr'").fetchall()
    c.close()
    assert lignes == [("ee1ccfdf9881",)]


@pytest.mark.asyncio
async def test_un_projet_actif_existant_n_est_jamais_ecrase(base):
    """Le rattrapage repare un vide, il ne decide pas a la place de l'utilisateur."""
    import sqlite3
    c = sqlite3.connect(base)
    c.execute("INSERT INTO study_projects VALUES "
              "('aaaa11112222','8627f5ac3ac5','nic01asfr','Variante','',0,'active',200)")
    c.execute("INSERT INTO active_project VALUES ('nic01asfr','aaaa11112222')")
    c.commit(); c.close()
    assert await studies.get_active_project_id("nic01asfr") == "aaaa11112222"


@pytest.mark.asyncio
async def test_le_projet_principal_prime_sur_les_variantes(base):
    import sqlite3
    c = sqlite3.connect(base)
    c.execute("INSERT INTO study_projects VALUES "
              "('bbbb11112222','8627f5ac3ac5','nic01asfr','Variante','',0,'active',50)")
    c.commit(); c.close()
    # la variante est plus ancienne, mais is_default departage avant created_at
    assert await studies.get_active_project_id("nic01asfr") == "ee1ccfdf9881"


@pytest.mark.asyncio
async def test_un_projet_archive_n_est_pas_ressuscite(base):
    import sqlite3
    c = sqlite3.connect(base)
    c.execute("UPDATE study_projects SET status='archived' WHERE pid='ee1ccfdf9881'")
    c.commit(); c.close()
    assert await studies.get_active_project_id("nic01asfr") is None


@pytest.mark.asyncio
async def test_sans_etude_active_on_n_invente_rien(base):
    import sqlite3
    c = sqlite3.connect(base)
    c.execute("DELETE FROM active_study")
    c.commit(); c.close()
    assert await studies.get_active_project_id("nic01asfr") is None


@pytest.mark.asyncio
async def test_le_projet_d_un_autre_utilisateur_n_est_pas_adopte(base):
    assert await studies.get_active_project_id("quelqu_un_dautre") is None
