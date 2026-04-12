# FDP Index

A serverless FAIR Data Point index backed entirely by GitHub.

- **Registry** — `registry.yaml` in this repo lists every FDP root to crawl
- **Crawl** — a GitHub Actions workflow runs every day at 03:00 UTC, crawls all registered FDPs, and commits results to the `gh-pages` branch
- **Index** — served as static files from GitHub Pages at `https://staticfdp.github.io/fdp-index/`
- **Ping** — FDP repos trigger a re-crawl by sending a GitHub Repository Dispatch event (no server needed)

## Adding your FDP

**Option A — Pull request (recommended):**
Add a line to `registry.yaml` and open a PR. Once merged, your FDP will be crawled in the next daily run.

**Option B — Automated ping from your FDP repo:**
In your FDP repo's `publish.yml`, add:
```yaml
- name: Ping FDP index
  run: |
    curl -sf -X POST \
      -H "Authorization: Bearer ${{ secrets.FDPINDEX_DISPATCH_TOKEN }}" \
      -H "Accept: application/vnd.github.v3+json" \
      -H "Content-Type: application/json" \
      -d '{"event_type":"fdp-ping","client_payload":{"url":"${{ vars.FDP_ROOT_URL }}"}}' \
      https://api.github.com/repos/StaticFDP/fdp-index/dispatches
```
`FDPINDEX_DISPATCH_TOKEN` must be a fine-grained PAT with `contents:write` on this repo.
On first ping your FDP is added to `registry.yaml` automatically.

## Index outputs (gh-pages)

| File | Format | Description |
|------|--------|-------------|
| `index.jsonld` | JSON-LD | Full index — all FDP metadata |
| `index.ttl` | Turtle | Same, as Turtle |
| `index.html` | HTML | Human-browsable index |
| `fdps/{slug}/snapshot.jsonld` | JSON-LD | Per-FDP metadata snapshot |
| `fdps/{slug}/snapshot.ttl` | Turtle | Per-FDP metadata snapshot |
| `crawl-report.json` | JSON | Last crawl stats + errors |

## Schemas

Validation uses **ShEx as primary**, with SHACL generated automatically:
- `profiles/FairDataPoint.shex` → `profiles/FairDataPoint-SHACL.ttl` (generated)
- `profiles/Catalog.shex`       → `profiles/Catalog-SHACL.ttl` (generated)
- `profiles/Dataset.shex`       → `profiles/Dataset-SHACL.ttl` (generated)
