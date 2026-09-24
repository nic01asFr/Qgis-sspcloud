"""Verificateur d'URL d'une reponse de l'agent.

Regle (strategie qualite, par. 4.2 et 5.2) : seules les URL du hub et du
catalogue passent. Une URL MinIO ou S3 signee, un lien `undefined`, un lien
relatif ou un hote inconnu sont refuses.

Option `exiger_trace` : une URL autorisee doit en plus figurer dans un
resultat d'outil du tour. Elle attrape l'URL de hub plausible mais inventee
(bon hote, mauvais identifiant de livrable).

Module pur, bibliotheque standard seulement, reutilisable en production.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlsplit

AUTORISEE = "autorisee"
INVENTEE = "inventee"
INTERDITE = "interdite"

# Hotes du catalogue de donnees (sources que l'agent peut legitimement citer).
CATALOGUE_PAR_DEFAUT: tuple[str, ...] = (
    "data.geopf.fr",
    "geoservices.ign.fr",
    "www.geoportail.gouv.fr",
    "geo.api.gouv.fr",
    "api-adresse.data.gouv.fr",
    "cadastre.data.gouv.fr",
    "www.data.gouv.fr",
    "www.insee.fr",
    "www.openstreetmap.org",
)

_INDICES_STOCKAGE = re.compile(
    r"minio|x-amz-|amazonaws\.com|(?:^|\.)s3[.-]|awsaccesskeyid", re.I,
)

_LIEN_MD = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]*)>?(?:\s+\"[^\"]*\")?\s*\)")
_URL_NUE = re.compile(r"\b(?:https?|ftp)://[^\s<>\"'`)\]]+", re.I)
_PONCTUATION_FINALE = ".,;:!?»"


@dataclass
class UrlTrouvee:
    url: str
    statut: str
    raison: str

    def as_dict(self) -> dict:
        return {"url": self.url, "statut": self.statut, "raison": self.raison}


@dataclass
class VerificationUrls:
    urls: list[UrlTrouvee] = field(default_factory=list)

    @property
    def refusees(self) -> list[UrlTrouvee]:
        return [u for u in self.urls if u.statut != AUTORISEE]

    @property
    def ok(self) -> bool:
        return not self.refusees

    def resume(self) -> str:
        if self.ok:
            return f"{len(self.urls)} URL autorisee(s)"
        return "; ".join(f"{u.url} ({u.raison})" for u in self.refusees)


def extraire_urls(texte: str) -> list[str]:
    """URL des liens Markdown et URL nues, dans l'ordre, sans doublon.

    Les images en data: (captures de carte) sont ignorees.
    """
    trouvees: list[str] = []
    positions: list[tuple[int, int]] = []
    for m in _LIEN_MD.finditer(texte):
        cible = m.group(1).strip()
        positions.append(m.span())
        if cible.lower().startswith("data:"):
            continue
        trouvees.append(cible)
    for m in _URL_NUE.finditer(texte):
        if any(a <= m.start() < b for a, b in positions):
            continue
        trouvees.append(m.group(0).rstrip(_PONCTUATION_FINALE))
    vues: list[str] = []
    for u in trouvees:
        if u not in vues:
            vues.append(u)
    return vues


def _autorisee_par(url: str, entree: str) -> bool:
    """Une entree de liste blanche : hote nu, ou URL avec prefixe de chemin."""
    entree = entree.strip()
    if not entree:
        return False
    cible = urlsplit(url)
    hote = (cible.hostname or "").lower()
    if "://" not in entree:
        entree_hote = entree.lower().strip("/")
        return hote == entree_hote or hote.endswith("." + entree_hote)
    ref = urlsplit(entree)
    if (ref.hostname or "").lower() != hote:
        return False
    if ref.scheme and ref.scheme != cible.scheme:
        return False
    if ref.port != cible.port:
        return False
    prefixe = ref.path.rstrip("/")
    return not prefixe or cible.path == prefixe or cible.path.startswith(prefixe + "/")


def verifier_urls(
    reponse: str,
    liste_blanche: Iterable[str],
    sources: Iterable[str] = (),
    exiger_trace: bool = False,
    relatifs_autorises: bool = False,
) -> VerificationUrls:
    """Classe chaque URL de `reponse` : autorisee, inventee ou interdite."""
    blanche = [e for e in liste_blanche if e]
    sources = [s for s in sources if s]
    resultat = VerificationUrls()
    for url in extraire_urls(reponse):
        bas = url.lower()
        if not url or bas in ("undefined", "null", "none", "#"):
            resultat.urls.append(UrlTrouvee(url, INTERDITE, "lien vide ou undefined"))
            continue
        if _INDICES_STOCKAGE.search(url):
            resultat.urls.append(UrlTrouvee(url, INTERDITE, "stockage MinIO/S3 expose"))
            continue
        if "://" not in url:
            if relatifs_autorises and url.startswith("/"):
                resultat.urls.append(UrlTrouvee(url, AUTORISEE, "lien relatif admis"))
            else:
                resultat.urls.append(UrlTrouvee(url, INTERDITE, "lien relatif ou sans schema"))
            continue
        if not bas.startswith(("http://", "https://")):
            resultat.urls.append(UrlTrouvee(url, INTERDITE, "schema non web"))
            continue
        if not any(_autorisee_par(url, e) for e in blanche):
            hote = urlsplit(url).hostname or "?"
            resultat.urls.append(UrlTrouvee(url, INTERDITE, f"hote hors liste blanche ({hote})"))
            continue
        if exiger_trace and not any(url in s or url.rstrip("/") in s for s in sources):
            resultat.urls.append(UrlTrouvee(url, INVENTEE, "absente des resultats d'outils"))
            continue
        resultat.urls.append(UrlTrouvee(url, AUTORISEE, "liste blanche"))
    return resultat
