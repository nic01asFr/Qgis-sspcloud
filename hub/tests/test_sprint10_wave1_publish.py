"""
Tests Sprint 1.4 Vague 1 Equipe B (Publication & Auditabilite) - 2026-07-05.

Couvre les 3 items :
- F17 : versioning URL stable + banniere obsolescence (`hub.publications`
        + endpoints `/p/{slug}` et `/p/{slug}/v{n}`).
- F16 : bundle ZIP autonome + signature Ed25519 (`hub.publish.bundle`).
- F15 : export PDF headless via weasyprint (`hub.publish.pdf`).

Ces tests sont volontairement unitaires (ne demandent pas de workspace
pod / S3) : ils verifient les fonctions purement calculatoires + endpoints
routes + schema.
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

# Assurer que hub/ + agent/ sont sur sys.path (aligne test_audit_trail.py)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── Fixture DB isolee ─────────────────────────────────────────────────────────

@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    """Redirige studies._DB_PATH vers un tempfile pour les tests."""
    db_path = tmp_path / "studies.db"
    from hub import studies as _s
    monkeypatch.setattr(_s, "_DB_PATH", db_path)
    # Reload publications module pour picker up le nouveau _DB_PATH
    from hub import publications as _p
    monkeypatch.setattr(_p, "_DB_PATH", db_path)
    return db_path


# ============================================================================
# F17 - Versioning URL stable + banniere obsolescence
# ============================================================================


class TestF17PublicationsModule:
    """`hub.publications` : init_db + register + get_latest + get_version."""

    @pytest.mark.asyncio
    async def test_init_db_cree_table_publications(self, isolated_db):
        """init_db doit creer la table publications avec les bonnes colonnes."""
        import aiosqlite
        from hub import publications as pub_mod

        await pub_mod.init_db()
        async with aiosqlite.connect(isolated_db) as db:
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='publications'"
            )
            row = await cur.fetchone()
            assert row is not None, "Table publications non creee"

            # Verifier les colonnes attendues
            cur = await db.execute("PRAGMA table_info(publications)")
            cols = {r[1] for r in await cur.fetchall()}
            expected = {"slug", "version", "aid", "aid_version_num",
                        "sid", "owner", "kind", "published_at",
                        "superseded_by", "change_summary", "integrity_hash"}
            missing = expected - cols
            assert not missing, f"Colonnes manquantes: {missing}"

    @pytest.mark.asyncio
    async def test_register_publication_first_version_est_1(self, isolated_db):
        """Premiere publication d'un slug -> version = 1, superseded_by = None."""
        from hub import publications as pub_mod
        await pub_mod.init_db()

        info = await pub_mod.register_publication(
            slug="assembly-abc123456789", aid="abc123456789",
            aid_version_num=1, sid="s0" * 6, owner="alice",
            change_summary="Publication initiale",
            integrity_hash="sha256:00abc",
        )
        assert info["version"] == 1
        assert info["superseded_by"] is None
        assert info["slug"] == "assembly-abc123456789"

    @pytest.mark.asyncio
    async def test_register_publication_incremente_version_et_supersede(
        self, isolated_db,
    ):
        """2eme publication sur meme slug -> v2 + v1.superseded_by = 2."""
        from hub import publications as pub_mod
        await pub_mod.init_db()

        await pub_mod.register_publication(
            slug="my-story", aid="aaaaaaaaaaaa", aid_version_num=1,
            sid="s0" * 6, owner="alice",
        )
        info2 = await pub_mod.register_publication(
            slug="my-story", aid="aaaaaaaaaaaa", aid_version_num=2,
            sid="s0" * 6, owner="alice",
            change_summary="Correction typo",
        )
        assert info2["version"] == 2

        # v1 doit maintenant etre supersede
        v1 = await pub_mod.get_version("my-story", 1)
        assert v1 is not None
        assert v1["superseded_by"] == 2

        # get_latest doit retourner v2
        latest = await pub_mod.get_latest("my-story")
        assert latest is not None
        assert latest["version"] == 2

    @pytest.mark.asyncio
    async def test_list_versions_ordre_desc(self, isolated_db):
        """list_versions renvoie toutes les versions du plus recent au plus vieux."""
        from hub import publications as pub_mod
        await pub_mod.init_db()
        for _i in range(3):
            await pub_mod.register_publication(
                slug="multi-v", aid="bbbbbbbbbbbb", aid_version_num=1,
                sid="s0" * 6, owner="bob",
            )
        versions = await pub_mod.list_versions("multi-v")
        assert len(versions) == 3
        assert [v["version"] for v in versions] == [3, 2, 1]


class TestF17Endpoints:
    """Routes /p/{slug}, /p/{slug}/v{n}, /p/{slug}/versions."""

    def test_endpoint_stable_slug_route_existe(self):
        from hub.main import app
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/p/{slug}" in paths

    def test_endpoint_versioned_route_existe(self):
        from hub.main import app
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/p/{slug}/v{version}" in paths

    def test_endpoint_versions_history_route_existe(self):
        from hub.main import app
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/p/{slug}/versions" in paths


class TestF17ObsolescenceBanner:
    """Banniere obsolescence DSFR : template + injection HTML."""

    def test_template_contient_variable_publication_obsolete(self):
        """Le template Jinja doit consommer `publication_obsolete`."""
        tpl_path = (
            Path(__file__).resolve().parents[1]
            / "hub" / "maplibre_renderer" / "storymap_dsfr.html.j2"
        )
        content = tpl_path.read_text(encoding="utf-8")
        assert "publication_obsolete" in content, (
            "F17 template : variable Jinja `publication_obsolete` manquante"
        )
        assert "Version obsolete" in content, (
            "F17 template : libelle banniere DSFR manquant"
        )
        # Verifier CSS DSFR-inspire jaune
        assert "#fff4c2" in content or "#daa520" in content, (
            "F17 template : couleur banniere jaune DSFR manquante"
        )


# ============================================================================
# F16 - Bundle ZIP autonome + signature Ed25519 chain custody
# ============================================================================


class TestF16BundleZipModule:
    """`hub.publish.bundle` : build_zip_bundle + signature Ed25519."""

    def test_module_importable(self):
        from hub.publish import bundle
        assert hasattr(bundle, "build_zip_bundle")
        assert hasattr(bundle, "sign_integrity")

    def test_sign_integrity_produit_signature_valide(self):
        """Signature Ed25519 verifie avec la cle publique correspondante."""
        from hub.publish import bundle
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        priv = Ed25519PrivateKey.generate()
        priv_pem = priv.private_bytes_raw()
        # sign_integrity attend une cle brute hex ou base64
        sig, pub_hex = bundle.sign_integrity(
            b"sha256:deadbeef", priv_key_bytes=priv_pem,
        )
        assert isinstance(sig, bytes)
        assert len(sig) == 64  # Ed25519 signature = 64 bytes
        # Verification manuelle
        pub = priv.public_key()
        pub.verify(sig, b"sha256:deadbeef")  # Ne leve pas si valide

    def test_build_zip_bundle_contient_tous_les_fichiers(self, tmp_path):
        """Le ZIP doit contenir manifest.json, rendered/index.html, audit_chain.json,
        integrity.txt et README.txt."""
        from hub.publish import bundle
        manifest = {"id": "abc123456789", "sid": "s0" * 6, "title": "Test"}
        rendered_html = "<html><body>Rendu test</body></html>"
        audit_chain = {"integrity_hash": "sha256:cafebabe",
                       "components_refs": []}
        components = {"comp1": {"kind": "kpi_badge", "data": [1, 2, 3]}}
        zip_bytes = bundle.build_zip_bundle(
            manifest=manifest,
            rendered_html=rendered_html,
            audit_chain=audit_chain,
            components=components,
            assets={"logo.png": b"\x89PNG\r\n\x1a\n"},
            version_num=1,
            slug="assembly-abc123456789",
        )
        assert zip_bytes[:2] == b"PK", "Pas un ZIP valide"

        # Ouvrir et verifier contenu
        import io
        z = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
        names = z.namelist()
        assert "manifest.json" in names
        assert "rendered/index.html" in names
        assert "audit_chain.json" in names
        assert "integrity.txt" in names
        assert "README.txt" in names
        assert "components/comp1.json" in names
        assert "assets/logo.png" in names

        # Verifier signature dans integrity.txt
        integrity = z.read("integrity.txt").decode("utf-8")
        assert "sha256:cafebabe" in integrity
        assert "SIGNATURE" in integrity or "signature" in integrity.lower()

    def test_build_zip_bundle_fallback_demo_key(self, tmp_path, monkeypatch):
        """Sans CEREMA_ED25519_PRIVATE_KEY -> cle demo (tests OK, non-prod)."""
        from hub.publish import bundle
        monkeypatch.delenv("CEREMA_ED25519_PRIVATE_KEY", raising=False)
        zip_bytes = bundle.build_zip_bundle(
            manifest={"id": "a" * 12, "sid": "b" * 12, "title": "T"},
            rendered_html="<p>x</p>",
            audit_chain={"integrity_hash": "sha256:demo"},
            components={},
            assets={},
            version_num=1,
            slug="demo",
        )
        assert zip_bytes[:2] == b"PK"


class TestF16Endpoint:
    def test_export_bundle_route_existe(self):
        from hub.main import app
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/studies/{sid}/assemblies/{aid}/export-bundle" in paths


# ============================================================================
# F15 - Export PDF headless via weasyprint
# ============================================================================


class TestF15PdfModule:
    """`hub.publish.pdf` : render_pdf via weasyprint."""

    def test_module_importable(self):
        from hub.publish import pdf
        assert hasattr(pdf, "render_pdf_from_html")

    def test_render_pdf_from_html_produit_pdf_valide(self):
        """render_pdf_from_html doit produire un PDF (magic bytes %PDF)."""
        try:
            import weasyprint  # noqa: F401
        except (ImportError, OSError) as exc:
            pytest.skip(f"weasyprint indisponible (dev Windows sans Pango) : {exc}")
        from hub.publish import pdf
        html = (
            "<html><head><title>Test</title></head>"
            "<body><h1>Titre</h1><p>Contenu</p></body></html>"
        )
        pdf_bytes = pdf.render_pdf_from_html(html, integrity_hash="sha256:test")
        # Magic bytes PDF = %PDF (0x25 0x50 0x44 0x46)
        assert pdf_bytes[:4] == b"%PDF", "Pas un PDF valide"
        # PDF doit contenir plus que le header
        assert len(pdf_bytes) > 500

    def test_le_hash_est_injecte_dans_le_html_remis_au_moteur(self):
        """L'empreinte doit figurer dans le document imprime, en pied de page.

        On verifie l'injection, pas les octets du PDF : WeasyPrint encode le
        texte selon la table de glyphes de la police, donc chercher une chaine
        ASCII dans le binaire ne marche que par chance. L'ancien test le
        faisait -- il passait ou echouait selon la version de la bibliotheque,
        et etait ignore sur un poste sans Pango. Personne ne le voyait donc ni
        passer ni echouer.

        Ce qui nous appartient, c'est ce qu'on remet au moteur.
        """
        from hub.publish import pdf
        html = "<html><head></head><body><h1>Marker</h1></body></html>"
        h = "sha256:cafefeed99"

        avec_css = pdf._inject_integrity_string_set(html, h)
        assert h in avec_css, "l'empreinte n'est pas posee en variable de page"
        assert "string-set: integrity-hash" in avec_css

    def test_le_badge_visible_porte_l_empreinte(self):
        """Le pied de page CSS depend du moteur ; le badge, lui, est du HTML
        ordinaire. C'est la garantie de dernier recours qu'un lecteur puisse
        lire l'empreinte a l'ecran."""
        import inspect

        from hub.publish import pdf
        src = inspect.getsource(pdf.render_pdf_from_html)
        assert "Integrity hash :" in src
        assert "</body>" in src, (
            "le badge n'est plus insere avant la fin du corps"
        )

    def test_le_pdf_se_produit_et_reste_un_pdf(self):
        """Integration minimale : on ne verifie pas le contenu, seulement que
        la chaine aboutit. Le contenu est verifie au-dessus, la ou il est
        lisible."""
        try:
            import weasyprint  # noqa: F401
        except (ImportError, OSError) as exc:
            pytest.skip(f"weasyprint indisponible : {exc}")
        from hub.publish import pdf
        octets = pdf.render_pdf_from_html(
            "<html><head></head><body><h1>Marker</h1></body></html>",
            integrity_hash="sha256:cafefeed99",
        )
        assert octets.startswith(b"%PDF-"), "la sortie n'est pas un PDF"
        assert len(octets) > 500, "PDF suspicieusement vide"


class TestF15Endpoint:
    def test_publish_pdf_route_existe(self):
        """POST /studies/{sid}/assemblies/{aid}/publish accepte format=pdf."""
        from hub.main import app
        # Chercher la route dediee ou le params sur publish existant
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        # Nouvelle route dediee
        assert "/studies/{sid}/assemblies/{aid}/publish-pdf" in paths, (
            "Route POST /studies/{sid}/assemblies/{aid}/publish-pdf manquante "
            "(F15 Sprint 1.4 Vague 1 Equipe B)"
        )
