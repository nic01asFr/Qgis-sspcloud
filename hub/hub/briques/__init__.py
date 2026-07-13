"""hub.briques — Bibliotheque de briques reutilisables.

Cette bibliotheque regroupe des artefacts modulaires servant a :

  1. Cadrer l'agent LLM (regles GLOBAL_RULES et FORBIDDEN injectees dans le
     prompt systeme, chantier G7) pour eviter l'hallucination de sources ou
     de dispositifs reglementaires.
  2. Composer les livrables (titre + KPI + carte + narrative + disclaimer +
     conclusion) via des snippets narrative et des compositions partielles
     (chantier G4).
  3. Etre importees telles quelles par les recipes deterministes.

Chaque brique a un identifiant stable, une version, et un dossier categorie :

  - rules/global/     : regles universelles injectees en systeme
  - rules/forbidden/  : interdictions absolues
  - narrative/        : snippets Markdown / Jinja2
  - compositions/     : compositions partielles JSON (fragment scene_manifest)
  - palettes/         : palettes couleur / classifications reutilisables
  - use_cases/        : squelettes de scenarios (diagnostic_temporel, etc.)

Le loader in-memory (`hub.briques_loader`) expose l'introspection et le
contenu brut via des endpoints REST `/briques*`.
"""
