# Vitrine produit (GitHub Pages)

Page publique déployée sur <https://nic01asfr.github.io/Qgis-sspcloud/>.

Installation documentée là (et dans `QUICKSTART.md`) : service **Onyxia
Jupyter-python**, Kubernetes role **`admin`**, terminal, one-liner GitHub
(`install.sh`). Chart `helm-repo/` sur GitHub, images **GHCR**. Pas GitLab.

- `dist/index.html` — HTML auto-contenu (charte `--qs-*` alignée sur `hub/hub/static/produit.css`)
- `dist/captures/` — captures produit (workspace, desk, agent) + `apercu.jpg` à la racine pour Open Graph
- Workflow : `.github/workflows/pages.yml` (artifact `site/dist` → GitHub Pages)

Ne pas confondre avec `docs/` (documentation technique du dépôt).
