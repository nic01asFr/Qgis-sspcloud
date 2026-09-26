"""API du corpus documentaire d'une etude (lot L7).

    GET    /studies/{sid}/documents                   liste, resume, limites
    POST   /studies/{sid}/documents                   depot (multipart « file »), 202
    GET    /studies/{sid}/documents/recherche?q=&k=   recherche bornee a l'etude
    GET    /studies/{sid}/documents/{doc_id}          metadonnees et statut
    GET    /studies/{sid}/documents/{doc_id}/fichier  original (telechargement)
    POST   /studies/{sid}/documents/{doc_id}/reindexer
    DELETE /studies/{sid}/documents/{doc_id}          204

Chaque route verifie que l'etude appartient a l'utilisateur authentifie
(``studies.get_study(sid, username)``) : une etude d'autrui repond 404, comme
une etude inexistante. L'indexation part en tache de fond ; son statut se
lit dans la liste ou la fiche du document.

Branchement : ``app.include_router(documents_api.router)`` dans ``main.py``.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from hub import auth
from hub import documents_etude as de

log = logging.getLogger("hub.documents_api")

router = APIRouter(tags=["documents d'étude"])

# Deux extractions a la fois au plus : un PDF de 500 pages occupe un coeur.
_SEMAPHORE: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(2)
    return _SEMAPHORE


async def _etude_de(sid: str, user: dict) -> dict:
    try:
        from hub import studies
    except ImportError as exc:  # pragma: no cover - module toujours present
        raise HTTPException(503, "Module études indisponible") from exc
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    return s


def _http(exc: de.ErreurDocument) -> HTTPException:
    return HTTPException(exc.code, exc.message)


async def indexer_en_tache_de_fond(sid: str, doc_id: str) -> None:
    """Indexation hors de la boucle d'evenements (thread), bornee en parallele."""
    de.EN_COURS.add((sid, doc_id))
    try:
        async with _semaphore():
            await asyncio.to_thread(de.indexer, sid, doc_id)
    finally:
        de.EN_COURS.discard((sid, doc_id))


@router.get("/studies/{sid}/documents")
async def lister_documents(sid: str, user: dict = Depends(auth.get_current_user)):
    await _etude_de(sid, user)
    try:
        docs = de.lister(sid)
    except de.ErreurDocument as exc:
        raise _http(exc) from exc
    return {"sid": sid, "documents": docs, "resume": de.resume(sid),
            "limites": {**de.limites(), "formats": sorted(de.FORMATS)}}


@router.post("/studies/{sid}/documents", status_code=202)
async def ajouter_document(
    sid: str,
    taches: BackgroundTasks,
    file: UploadFile = File(...),
    user: dict = Depends(auth.get_current_user),
):
    await _etude_de(sid, user)
    # Lecture bornee : on ne charge pas en memoire au-dela de la limite.
    maxi = de.limites()["taille_max_mo"] * 1024 * 1024
    contenu = await file.read(maxi + 1)
    try:
        doc = de.ajouter(sid, file.filename, contenu, auteur=user.get("username"))
    except de.ErreurDocument as exc:
        raise _http(exc) from exc
    de.EN_COURS.add((sid, doc["id"]))
    taches.add_task(indexer_en_tache_de_fond, sid, doc["id"])
    return {"document": doc}


@router.get("/studies/{sid}/documents/recherche")
async def rechercher_documents(
    sid: str,
    q: str = Query(..., min_length=1, max_length=1000),
    k: int = Query(5, ge=1, le=20),
    longueur: int = Query(450, ge=120, le=1200),
    user: dict = Depends(auth.get_current_user),
):
    await _etude_de(sid, user)
    try:
        return await asyncio.to_thread(de.rechercher, sid, q, k, longueur)
    except de.ErreurDocument as exc:
        raise _http(exc) from exc


@router.get("/studies/{sid}/documents/{doc_id}")
async def obtenir_document(sid: str, doc_id: str,
                           user: dict = Depends(auth.get_current_user)):
    await _etude_de(sid, user)
    try:
        return {"document": de.obtenir(sid, doc_id)}
    except de.ErreurDocument as exc:
        raise _http(exc) from exc


@router.get("/studies/{sid}/documents/{doc_id}/fichier")
async def telecharger_document(sid: str, doc_id: str,
                               user: dict = Depends(auth.get_current_user)):
    await _etude_de(sid, user)
    try:
        chemin, doc = de.chemin_fichier(sid, doc_id)
    except de.ErreurDocument as exc:
        raise _http(exc) from exc
    # Toujours en piece jointe et en octets bruts : un document depose ne
    # s'execute jamais dans l'origine du hub.
    return FileResponse(chemin, media_type="application/octet-stream",
                        filename=doc.get("nom_fichier") or chemin.name,
                        headers={"X-Content-Type-Options": "nosniff"})


@router.post("/studies/{sid}/documents/{doc_id}/reindexer", status_code=202)
async def reindexer_document(sid: str, doc_id: str, taches: BackgroundTasks,
                             user: dict = Depends(auth.get_current_user)):
    await _etude_de(sid, user)
    if (sid, doc_id) in de.EN_COURS:
        return JSONResponse({"detail": "Indexation déjà en cours."}, status_code=409)
    try:
        doc = de.preparer_reindexation(sid, doc_id)
    except de.ErreurDocument as exc:
        raise _http(exc) from exc
    de.EN_COURS.add((sid, doc_id))
    taches.add_task(indexer_en_tache_de_fond, sid, doc_id)
    return {"document": doc}


@router.delete("/studies/{sid}/documents/{doc_id}", status_code=204)
async def retirer_document(sid: str, doc_id: str,
                           user: dict = Depends(auth.get_current_user)):
    await _etude_de(sid, user)
    try:
        de.retirer(sid, doc_id)
    except de.ErreurDocument as exc:
        raise _http(exc) from exc
    return Response(status_code=204)
