"""Verificateur de chiffres tracables.

Principe (strategie qualite, par. 4.2 et 5.2) : chaque nombre qu'une reponse
presente a l'utilisateur doit figurer dans au moins un resultat d'outil du
tour. Un nombre qu'on ne retrouve nulle part est soit invente, soit calcule
sans que le calcul soit visible : dans les deux cas, on le signale.

Module pur, sans dependance hors bibliotheque standard, pour pouvoir tourner
aussi en production avant l'affichage d'une reponse.

Regles, dans cet ordre, pour chaque nombre de la reponse :

1. On cherche d'abord a le TRACER. S'il est trace, il est accepte, meme s'il
   ressemble a une annee ou a un petit entier.
2. Trace direct : un nombre des sources vaut le meme, a l'arrondi affiche pres
   (« 5,27 » trace 5.2734 ; « 51,4 millions » trace 51 450 444). Les unites de
   surface et de longueur sont converties (ha, km2 contre m2 ; km contre m).
   Un nombre rond (au moins 1 000, terminant par des zeros) est compare a
   l'arrondi correspondant : « 300 000 » trace 300 551.
3. Approximation annoncee (« environ », « pres de », « plus de », « ~ »...) :
   tolerance relative `tolerance_approx` (5 % par defaut).
4. Pourcentage : trace s'il figure tel quel (12,5 ou 0,125), ou s'il vaut
   100 * a / b pour deux nombres a et b des sources (statut `derive`).
5. Sinon, il peut etre IGNORE s'il n'est pas une donnee : petit entier de
   discours (<= `seuil_petits_entiers`, hors pourcentage), annee (1900-2100
   ecrite sans separateur), ordinal et numero d'arrondissement (« 4e »,
   « 1er »), code postal (5 chiffres suivis d'un nom propre, ou annonces par
   « code postal », « INSEE »), identifiant colle a des lettres (« S1 »,
   « qwen3 »), numero d'element de liste, heure, date, et les identifiants
   de systemes de coordonnees (EPSG:2154, Lambert 93, WGS 84).
6. Sinon il est NON TRACE.

Limites connues : les valeurs obtenues par somme, difference ou produit ne
sont pas reconstituees ; un compte legitime ecrit « 2024 » sans separateur et
absent des sources est pris pour une annee ; « 1,234 » est lu a la francaise
(1.234), jamais comme 1 234.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable

# Espaces admis comme separateurs de milliers : espace, insecable, fine
# insecable, fine.
_ESP = " \N{NO-BREAK SPACE}\N{NARROW NO-BREAK SPACE}\N{THIN SPACE}"

TRACE = "trace"
DERIVE = "derive"
IGNORE = "ignore"
NON_TRACE = "non_trace"


@dataclass
class ReglesChiffres:
    seuil_petits_entiers: int = 10
    tolerance_approx: float = 0.05
    arrondi_zeros: bool = True
    ratios_pourcentage: bool = True
    ignorer_annees: bool = True
    max_nombres_sources: int = 5000
    max_paires_ratio: int = 300


@dataclass
class NombreTrouve:
    texte: str
    valeur: float
    debut: int
    fin: int
    corps: str = ""
    decimales: int = 0
    echelle: float = 1.0
    unite: str = ""
    approx: bool = False
    statut: str = ""
    raison: str = ""
    source: int | None = None

    def as_dict(self) -> dict:
        return {
            "texte": self.texte, "valeur": self.valeur, "statut": self.statut,
            "raison": self.raison, "source": self.source,
        }


@dataclass
class VerificationChiffres:
    nombres: list[NombreTrouve] = field(default_factory=list)

    @property
    def non_traces(self) -> list[NombreTrouve]:
        return [n for n in self.nombres if n.statut == NON_TRACE]

    @property
    def ok(self) -> bool:
        return not self.non_traces

    def resume(self) -> str:
        if self.ok:
            verifies = sum(1 for n in self.nombres if n.statut in (TRACE, DERIVE))
            return f"{verifies} nombre(s) trace(s), aucun non trace"
        liste = ", ".join(f"« {n.texte} »" for n in self.non_traces)
        return f"{len(self.non_traces)} nombre(s) non trace(s) : {liste}"


# ── Pretraitement ──────────────────────────────────────────────────────────

_MOTIFS_A_MASQUER = [
    re.compile(r"```.*?```", re.S),                       # blocs de code
    re.compile(r"`[^`\n]*`"),                             # code en ligne
    re.compile(r"<!--.*?-->", re.S),                      # commentaires HTML
    re.compile(r"!?\[[^\]]*\]\([^)]*\)"),                 # liens et images
    re.compile(r"\b(?:https?|ftp)://\S+", re.I),          # URL nues
    re.compile(r"\bEPSG\s*[: ]\s*\d+", re.I),
    re.compile(r"\bLambert[\s-]*93\b", re.I),
    re.compile(r"\bLambert[\s-]*(?:I{1,3}|IV|étendu)\b", re.I),
    re.compile(r"\bRGF[\s-]*93\b", re.I),
    re.compile(r"\bWGS[\s-]*84\b", re.I),
    re.compile(r"\bCC\s?(?:4[2-9]|50)\b", re.I),
    re.compile(r"\bIGN[\s-]*69\b", re.I),
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b"),  # dates ISO
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),           # dates jj/mm/aaaa
    re.compile(r"\b\d{1,2}\s?[hH]\s?\d{2}\b"),           # heures 15h30
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),          # heures 10:45
    re.compile(r"(?m)^[ \t]*\d{1,3}[.)](?=\s)"),          # puces numerotees
]


def _masquer(texte: str) -> str:
    """Remplace les zones non pertinentes par des espaces (positions gardees)."""
    for motif in _MOTIFS_A_MASQUER:
        texte = motif.sub(lambda m: " " * len(m.group(0)), texte)
    return texte


# ── Extraction ─────────────────────────────────────────────────────────────

_NOMBRE = re.compile(
    r"(?<![\w.,])"
    r"(?P<signe>[-\N{MINUS SIGN}])?"
    r"(?P<corps>"
    rf"\d{{1,3}}(?:[{_ESP}]\d{{3}})+(?:,\d+)?"      # 51 450 444 / 1 234,5
    r"|\d{1,3}(?:\.\d{3}){2,}(?:,\d+)?"            # 1.234.567
    r"|\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?"            # 51,450,444 (anglais)
    r"|\d{1,3}(?:_\d{3})+"                         # 51_450_444
    r"|\d+(?:[.,]\d+)?"                            # 51450444 / 5,27 / 5.27
    r")"
    r"(?![\d])"
)

_ECHELLES = {
    "millier": 1e3, "milliers": 1e3, "k": 1e3,
    "million": 1e6, "millions": 1e6, "m": 1e6, "mio": 1e6,
    "milliard": 1e9, "milliards": 1e9, "md": 1e9, "mds": 1e9,
}
# Facteurs pour ramener l'unite affichee a l'unite de base des sources.
_UNITES = {
    "%": "%", "pour cent": "%", "pourcent": "%",
    "km²": "km2", "km2": "km2", "kilomètres carrés": "km2",
    "m²": "m2", "m2": "m2", "mètres carrés": "m2",
    "ha": "ha", "hectare": "ha", "hectares": "ha",
    "km": "km", "kilomètre": "km", "kilomètres": "km",
    "m": "m", "mètre": "m", "mètres": "m",
}
_FACTEURS = {
    "km2": (1.0, 1e6, 100.0),
    "ha": (1.0, 1e4, 0.01),
    "m2": (1.0, 1e-4, 1e-6),
    "km": (1.0, 1000.0),
    "m": (1.0, 0.001),
    "%": (1.0, 0.01),
    "": (1.0,),
}
_SUFFIXE = re.compile(
    r"^[" + _ESP + r"]?(?P<suffixe>"
    r"milliards?|millions?|milliers?|Mds?|Md|Mio|k|M"
    r")(?![A-Za-zÀ-ÿ0-9])",
)
_UNITE = re.compile(
    r"^[" + _ESP + r"]?(?:d[e'’]\s*)?(?P<unite>"
    r"%|pour ?cent|km²|km2|m²|m2|kilomètres carrés|mètres carrés|hectares?|ha"
    r"|kilomètres?|km|mètres?|m)(?![a-zà-ÿ0-9²])",
    re.I,
)
_ORDINAL = re.compile(r"^(?:e|è|ème|eme|er|ère|ere|re|nd|nde|°)(?![a-zà-ÿ])", re.I)
_COLLE_LETTRE = re.compile(r"^[A-Za-zÀ-ÿ_]")
_APPROX = re.compile(
    r"(?:environ|approximativement|à peu près|a peu pres|près de|pres de|"
    r"presque|quelque|autour de|de l'ordre de|plus de|moins de|au moins|"
    r"au plus|jusqu'à|jusqu'a|~|≈|±|quasi|un peu plus de|un peu moins de)"
    r"[\s\N{NO-BREAK SPACE}]*$",
    re.I,
)
_INDICES_CODE = re.compile(
    r"(?:code postal|\bcp|insee|code commune|code officiel|n°|\bno\.|numéro|"
    r"numero|article|ligne|étape|etape|option|scénario|scenario|version|v)"
    r"[\s\N{NO-BREAK SPACE}:]*$",
    re.I,
)


def _valeur(corps: str) -> tuple[float, int]:
    """Convertit le corps d'un nombre en (valeur, decimales affichees)."""
    brut = corps
    for esp in _ESP + "_":
        brut = brut.replace(esp, "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,}(?:,\d+)?", brut):
        brut = brut.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?", brut):
        brut = brut.replace(",", "")
    else:
        brut = brut.replace(",", ".")
    decimales = len(brut.split(".", 1)[1]) if "." in brut else 0
    return float(brut), decimales


def extraire_nombres(texte: str) -> list[NombreTrouve]:
    """Extrait les nombres d'un texte en francais, avec leur contexte."""
    masque = _masquer(texte)
    trouves: list[NombreTrouve] = []
    for m in _NOMBRE.finditer(masque):
        corps = m.group("corps")
        valeur, decimales = _valeur(corps)
        if m.group("signe"):
            valeur = -valeur
        fin = m.end()
        apres = masque[fin:fin + 40]
        echelle = 1.0
        unite = ""
        ms = _SUFFIXE.match(apres)
        if ms:
            suffixe = ms.group("suffixe")
            echelle = 1e6 if suffixe == "M" else _ECHELLES.get(suffixe.lower(), 1.0)
            fin += ms.end()
            apres = masque[fin:fin + 40]
        mu = _UNITE.match(apres)
        if mu:
            unite = _UNITES.get(mu.group("unite").lower(), "")
            fin += mu.end()
        nombre = NombreTrouve(
            texte=texte[m.start():fin].strip(),
            valeur=valeur * echelle,
            debut=m.start(),
            fin=fin,
            corps=corps,
            decimales=decimales,
            echelle=echelle,
            unite=unite,
            approx=bool(_APPROX.search(masque[max(0, m.start() - 30):m.start()])),
        )
        trouves.append(nombre)
    return trouves


def nombres_des_sources(sources: Iterable[str], limite: int = 5000) -> list[tuple[float, int]]:
    """Tous les nombres des sources, format JSON ou francais, dedoublonnes.

    Retourne des couples (valeur, indice de la source).
    """
    vus: dict[float, int] = {}
    json_nombre = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
    for indice, source in enumerate(sources):
        if not source:
            continue
        for m in json_nombre.finditer(source):
            try:
                v = float(m.group(0))
            except ValueError:
                continue
            if math.isfinite(v):
                vus.setdefault(v, indice)
        for n in extraire_nombres(source):
            vus.setdefault(n.valeur, indice)
            if len(vus) >= limite:
                break
        if len(vus) >= limite:
            break
    return list(vus.items())


# ── Tracage ────────────────────────────────────────────────────────────────

def _zeros_finaux(n: NombreTrouve) -> int:
    if n.decimales or n.echelle != 1.0:
        return 0
    entier = int(abs(n.valeur))
    if entier < 1000:
        return 0
    k = 0
    while entier and entier % 10 == 0:
        entier //= 10
        k += 1
    return k


def _correspond(n: NombreTrouve, s: float, regles: ReglesChiffres) -> bool:
    mantisse_pas = 10.0 ** (-n.decimales) * n.echelle
    tol_base = 0.5 * mantisse_pas
    if regles.arrondi_zeros:
        k = _zeros_finaux(n)
        if k:
            tol_base = max(tol_base, 0.5 * 10.0 ** k)
    for facteur in _FACTEURS.get(n.unite, (1.0,)):
        cible = n.valeur * facteur
        tol = tol_base * facteur + 1e-9 * max(1.0, abs(cible))
        if n.approx:
            tol = max(tol, regles.tolerance_approx * abs(cible))
        if abs(s - cible) <= tol:
            return True
        # Une valeur signee dans les sources et affichee sans signe (« une
        # baisse de 12 % » pour -12) reste la meme donnee.
        if abs(abs(s) - abs(cible)) <= tol and s < 0 <= n.valeur:
            return True
    return False


def _raison_ignore(texte: str, masque: str, n: NombreTrouve,
                   regles: ReglesChiffres) -> str | None:
    avant = masque[max(0, n.debut - 30):n.debut]
    apres_brut = texte[n.fin:n.fin + 30]
    # Colle a une lettre avant : identifiant (S1, qwen3, L2).
    if n.debut > 0 and re.match(r"[A-Za-zÀ-ÿ_]", texte[n.debut - 1]):
        return "identifiant"
    if _ORDINAL.match(apres_brut):
        return "ordinal"
    if (_COLLE_LETTRE.match(apres_brut) and not n.unite
            and n.echelle == 1.0):
        return "identifiant"
    if re.search(r"\barrondissement", texte[n.fin:n.fin + 25], re.I):
        return "arrondissement"
    entier_simple = (n.decimales == 0 and n.echelle == 1.0
                     and re.fullmatch(r"\d+", n.corps) is not None)
    if entier_simple and _INDICES_CODE.search(avant):
        return "numero"
    if (entier_simple and len(n.corps) == 5
            and re.match(r"^[\s\N{NO-BREAK SPACE}]+[A-ZÀ-Ý]", apres_brut)):
        return "code postal"
    if (regles.ignorer_annees and entier_simple and n.unite != "%"
            and 1900 <= n.valeur <= 2100):
        return "annee"
    if (n.unite != "%" and n.decimales == 0 and n.echelle == 1.0
            and abs(n.valeur) <= regles.seuil_petits_entiers):
        return "petit entier"
    return None


def verifier_chiffres(
    reponse: str,
    sources: Iterable[str],
    regles: ReglesChiffres | None = None,
) -> VerificationChiffres:
    """Verifie que chaque nombre de `reponse` se retrouve dans `sources`.

    `sources` : les resultats d'outils du tour (texte brut ou JSON).
    """
    regles = regles or ReglesChiffres()
    sources = list(sources)
    valeurs = nombres_des_sources(sources, regles.max_nombres_sources)
    masque = _masquer(reponse)
    resultat = VerificationChiffres()
    for n in extraire_nombres(reponse):
        for valeur, indice in valeurs:
            if _correspond(n, valeur, regles):
                n.statut, n.raison, n.source = TRACE, "present dans un resultat d'outil", indice
                break
        if not n.statut and n.unite == "%" and regles.ratios_pourcentage:
            paires = [v for v, _ in valeurs[:regles.max_paires_ratio]]
            for a in paires:
                for b in paires:
                    if b and a != b and _correspond(n, 100.0 * a / b, regles):
                        n.statut = DERIVE
                        n.raison = f"rapport de deux resultats ({a:g} / {b:g})"
                        break
                if n.statut:
                    break
        if not n.statut:
            raison = _raison_ignore(reponse, masque, n, regles)
            if raison:
                n.statut, n.raison = IGNORE, raison
            else:
                n.statut, n.raison = NON_TRACE, "absent des resultats d'outils"
        resultat.nombres.append(n)
    return resultat

