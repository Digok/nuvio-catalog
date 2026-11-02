# Rodrigo's Catalog (Nuvio / Stremio)

Static catalog addon: metadata only (titles, posters, ratings), no streams.
Install in Nuvio: `https://digok.github.io/nuvio-catalog/manifest.json`

- `build.py` builds `docs/` from IMDb datasets (non-commercial use), Cinemeta and Wikidata.
- `refresh.sh` rebuilds and pushes; runs weekly via systemd user timer `nuvio-catalog.timer`.
- Edit curated lists (finance, medical) at the top of `build.py`.
