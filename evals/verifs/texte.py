"""Controles textuels d'une reponse : jargon, motifs, question de clarification."""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable


def normaliser(texte: str) -> str:
    """Minuscules sans accents, pour comparer des mots sans se soucier de la graphie."""
    decompose = unicodedata.normalize("NFKD", texte or "")
    return "".join(c for c in decompose if not unicodedata.combining(c)).lower()


def mots_presents(texte: str, mots: Iterable[str]) -> list[str]:
    """Mots ou expressions de `mots` presents dans `texte`, en mot entier.

    Insensible a la casse et aux accents : « Emprise » trouve « emprise ».
    """
    base = normaliser(texte)
    trouves = []
    for mot in mots:
        cible = normaliser(mot).strip()
        if not cible:
            continue
        motif = r"(?<![\w])" + re.escape(cible) + r"(?![\w])"
        if re.search(motif, base):
            trouves.append(mot)
    return trouves


def motifs_absents(texte: str, motifs: Iterable[str]) -> list[str]:
    """Expressions regulieres de `motifs` qui ne trouvent rien dans `texte`."""
    return [m for m in motifs if not re.search(m, texte or "", re.I | re.S)]


def motifs_presents(texte: str, motifs: Iterable[str]) -> list[str]:
    """Expressions regulieres de `motifs` qui trouvent quelque chose dans `texte`."""
    return [m for m in motifs if re.search(m, texte or "", re.I | re.S)]


def pose_une_question(texte: str) -> bool:
    """La reponse se termine-t-elle par une question a l'utilisateur ?

    On regarde le dernier paragraphe non vide, et les listes de choix qui le
    suivent souvent (« Laquelle ? - Saint-Martin (Var) - ... »). Une question
    enterree au milieu d'un long compte rendu ne compte pas : l'utilisateur
    doit voir qu'on attend sa reponse.
    """
    paragraphes = [p.strip() for p in re.split(r"\n\s*\n", texte or "") if p.strip()]
    if not paragraphes:
        return False
    fin = paragraphes[-3:]
    for p in fin:
        lignes = [ligne.strip() for ligne in p.splitlines() if ligne.strip()]
        if any(ligne.rstrip("*_ ").endswith("?") for ligne in lignes):
            return True
    return False
