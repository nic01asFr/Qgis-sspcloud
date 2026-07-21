from setuptools import setup, find_packages

setup(
    name="qgis-mcp-hub",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "fastapi[standard]",
        "uvicorn[standard]",
        "httpx",
        "PyJWT[crypto]",
        "aiosqlite",
        "pyyaml",
        "python-multipart",
        "jinja2",
        "boto3",
        # Sprint Composants-1 (2026-06-24) : Scene Manifest V0.2 vendored
        # dans hub/vendor/scene_manifest.py utilise Pydantic v2. FastAPI le
        # tire deja en transitive, on le pin ici pour visibilite explicite.
        "pydantic>=2.0",
        # Sprint 1.5 Wave 1 (S9) : compaction rolling delta assemblies_index.
        # Rolling delta jsondiff aggregate versions > 30j -> 1 snapshot compact.
        "jsondiff>=1.10",
        # Sprint 1.4 Vague 1 Equipe B (2026-07-05) - F16/F15 publication.
        "cryptography>=41.0",  # F16 bundle ZIP + signature Ed25519
        "weasyprint>=60.0",    # F15 export PDF headless
        # Chantier G3 (2026-07-13) : auto-reprojection GeoJSON Lambert 93 ->
        # WGS84 dans _build_interactive_map_ctx (hub/geo_utils.py). Import
        # tardif + fail-soft si absent.
        "pyproj>=3.5",
        # Sprint sec-vague0 dette OOM piste PMTiles V0.4 (2026-07-21) :
        # externalisation features vectorielles en tuiles PMTiles cote
        # publish, pour contourner le hard limit MinIO SSPCloud sur uploads
        # >5MB (token stsonly interdit s3:CreateMultipartUpload, put_object
        # coupe la connexion sur payloads 19-38MB). PMTiles = format
        # monofichier vector tiles (spec Protomaps v3), sert via HTTP
        # Range Requests. 19MB geojson brut -> ~2MB pmtiles (compression
        # MVT + zstd interne). MapLibre natif via plugin pmtiles-protocol
        # (chargement inline dans _interactive_map_partial.j2).
        # - pmtiles : reader + writer + spec v3
        # - mapbox-vector-tile : encode features -> MVT (protobuf)
        # - shapely : geometrie helpers (deja tire par pyproj transitive
        #   dans certains contextes, on l'explicite ici)
        "pmtiles>=3.4.0",
        "mapbox-vector-tile>=2.0.0",
        "shapely>=2.0",
    ],
)
