"""
Tests contrat anti-drift Strate Source alignement.

Vérifie que `hub.models.audit_chain.Source` reste EXACTEMENT aligné avec
le schema Strate de référence `atlas/model.py:Source @ 2026-06-29`.

D-QGIS-006 (acté 2026-06-29) : recopie + test contrat anti-drift.
Validé par Strate-Architect msg wikichat 52e1bcba.
VENDORED_FROM : `hub/hub/models/VENDORED_FROM.md`.

Quand ce test casse :
1. Soit Strate a évolué -> resync hub/hub/models/audit_chain.py:Source
   + bumper date dans le commentaire `# Aligned with ... @ DATE`
2. Soit qgis-sspcloud a dérivé -> revenir alignement Strate
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hub.models.audit_chain import (
    AuditChain,
    Source,
    Statut,
)


class TestSourceSchemaContractStrate:
    """Test contrat anti-drift Strate Source @ 2026-06-29."""

    def test_source_a_exactement_7_champs(self):
        """Le schema Strate Source a 7 champs nommés et pas un de plus."""
        expected_fields = {
            "corpus", "ref_id", "millesime", "authority",
            "licence", "url", "statut",
        }
        actual_fields = set(Source.model_fields.keys())
        assert actual_fields == expected_fields, (
            f"Drift detecté ! Champs Source attendus : {expected_fields}, "
            f"trouvés : {actual_fields}. "
            f"Verifier hub/hub/models/VENDORED_FROM.md pour resync Strate."
        )

    def test_source_types_alignes(self):
        """Les annotations des 7 champs respectent le contrat Strate."""
        fields = Source.model_fields
        assert fields["corpus"].annotation is str
        assert fields["millesime"].annotation is str
        assert fields["authority"].annotation is str
        assert fields["licence"].annotation is str
        # ref_id et url sont nullable
        assert fields["ref_id"].annotation == (str | None)
        assert fields["url"].annotation == (str | None)
        # statut = enum Statut (pas Literal)
        assert fields["statut"].annotation is Statut

    def test_source_defaults_alignes(self):
        """Defaults Strate respectés (licence + statut)."""
        fields = Source.model_fields
        assert fields["licence"].default == "Licence Ouverte 2.0"
        assert fields["statut"].default == Statut.presomption_haute
        # ref_id et url ont default None
        assert fields["ref_id"].default is None
        assert fields["url"].default is None

    def test_source_config_strict(self):
        """frozen=True + extra='forbid' (Pydantic strict)."""
        config = Source.model_config
        assert config.get("frozen") is True, "Source doit être frozen"
        assert config.get("extra") == "forbid", "Source doit être strict (extra=forbid)"

    def test_source_statut_enum_values(self):
        """Statut enum a les 3 valeurs canoniques Strate."""
        assert set(Statut) == {
            Statut.verifie,
            Statut.presomption_haute,
            Statut.a_verifier,
        }
        assert Statut.verifie.value == "verifie"
        assert Statut.presomption_haute.value == "presomption_haute"
        assert Statut.a_verifier.value == "a_verifier"

    def test_source_instance_valide_avec_defaults(self):
        """On peut créer une Source avec juste les champs requis."""
        src = Source(
            corpus="BD TOPO",
            millesime="2024",
            authority="IGN",
        )
        assert src.licence == "Licence Ouverte 2.0"
        assert src.statut == Statut.presomption_haute
        assert src.ref_id is None
        assert src.url is None

    def test_source_rejette_champ_inconnu(self):
        """extra='forbid' rejette tout champ non déclaré (anti-drift)."""
        with pytest.raises(ValidationError):
            Source(
                corpus="BD TOPO",
                millesime="2024",
                authority="IGN",
                champ_inconnu="invalide",  # type: ignore[call-arg]
            )

    def test_source_est_frozen(self):
        """frozen=True empêche modification après création."""
        src = Source(
            corpus="BD TOPO",
            millesime="2024",
            authority="IGN",
        )
        with pytest.raises(ValidationError):
            src.corpus = "Modifié"  # type: ignore[misc]


class TestAuditChainIntegrityHashRename:
    """D-FORMAT-008 (2026-06-29) : rename signed_hash → integrity_hash.

    Backward-compat 1 release de grâce : signed_hash property deprecated.
    """

    def test_integrity_hash_field_present(self):
        """Le champ integrity_hash existe sur AuditChain."""
        assert "integrity_hash" in AuditChain.model_fields

    def test_compute_integrity_hash_canonique(self):
        """compute_integrity_hash retourne SHA256 canonique."""
        chain = AuditChain(
            aid="c4c9b4aa6504",
            sid="c9fef0955a53",
            owner="nicolaslaval",
        )
        h = chain.compute_integrity_hash()
        assert h.startswith("sha256:")
        assert len(h) == 7 + 64  # "sha256:" + 64 hex chars

    def test_compute_integrity_hash_deterministe(self):
        """Le hash est déterministe sur même AuditChain."""
        chain1 = AuditChain(
            aid="c4c9b4aa6504",
            sid="c9fef0955a53",
            owner="nicolaslaval",
            components_refs=["aaaa", "bbbb"],
        )
        chain2 = AuditChain(
            aid="c4c9b4aa6504",
            sid="c9fef0955a53",
            owner="nicolaslaval",
            components_refs=["aaaa", "bbbb"],
        )
        assert chain1.compute_integrity_hash() == chain2.compute_integrity_hash()

    def test_signed_hash_property_deprecated_warning(self):
        """signed_hash property émet DeprecationWarning et retourne integrity_hash."""
        chain = AuditChain(
            aid="c4c9b4aa6504",
            sid="c9fef0955a53",
            owner="nicolaslaval",
            integrity_hash="sha256:deadbeef",
        )
        with pytest.warns(DeprecationWarning, match="signed_hash is deprecated"):
            value = chain.signed_hash
        assert value == "sha256:deadbeef"

    def test_compute_signed_hash_deprecated_warning(self):
        """compute_signed_hash() émet DeprecationWarning et delegue."""
        chain = AuditChain(
            aid="c4c9b4aa6504",
            sid="c9fef0955a53",
            owner="nicolaslaval",
        )
        with pytest.warns(DeprecationWarning, match="compute_signed_hash"):
            legacy = chain.compute_signed_hash()
        assert legacy == chain.compute_integrity_hash()


class TestAuditChainSourcesPydantic:
    """D-QGIS-006 : audit_chain.sources est Pydantic strict (list[Source])."""

    def test_audit_chain_accepte_sources_pydantic(self):
        """sources accepte list[Source] typé."""
        chain = AuditChain(
            aid="c4c9b4aa6504",
            sid="c9fef0955a53",
            owner="nicolaslaval",
            sources=[
                Source(
                    corpus="BD TOPO",
                    millesime="2024",
                    authority="IGN",
                    licence="Licence Ouverte 2.0",
                    url="https://geoservices.ign.fr/bdtopo",
                    statut=Statut.verifie,
                ),
            ],
        )
        assert len(chain.sources) == 1
        assert chain.sources[0].corpus == "BD TOPO"
        assert chain.sources[0].statut == Statut.verifie

    def test_audit_chain_sources_dans_canonical(self):
        """sources est inclus dans canonical_dict pour le hash."""
        chain = AuditChain(
            aid="c4c9b4aa6504",
            sid="c9fef0955a53",
            owner="nicolaslaval",
            sources=[
                Source(corpus="BD TOPO", millesime="2024", authority="IGN"),
            ],
        )
        d = chain.canonical_dict()
        assert "sources" in d
        assert d["sources"][0]["corpus"] == "BD TOPO"
