#!/usr/bin/env python3
"""
FDP Crawler
-----------
Reads registry.yaml, crawls every registered FAIR Data Point, and writes
serialised outputs to ./output/ ready for deployment to gh-pages.

Outputs per run:
  output/index.jsonld               full index (DCAT catalog of FDPs)
  output/index.ttl                  same as Turtle
  output/fdps/{slug}/snapshot.jsonld  per-FDP combined graph
  output/fdps/{slug}/snapshot.ttl
  output/crawl-report.json          stats + per-FDP errors
"""

import json
import os
import re
import sys
import datetime
from pathlib import Path
from typing import Optional

import yaml
import requests
from rdflib import Graph, Namespace, URIRef, Literal, BNode
from rdflib.namespace import DCAT, DCTERMS, FOAF, RDF, RDFS, XSD

# ── Namespaces ────────────────────────────────────────────────────────────────
LDP    = Namespace("http://www.w3.org/ns/ldp#")
FDPO   = Namespace("https://w3id.org/fdp/fdp-o#")
SCHEMA = Namespace("https://schema.org/")

# ── Config ────────────────────────────────────────────────────────────────────
TIMEOUT   = 20      # seconds per HTTP request
MAX_HOPS  = 50      # max resources to follow per FDP (circuit breaker)
OUTPUT    = Path("output")

ACCEPT = "text/turtle;q=1.0, application/ld+json;q=0.8, application/rdf+xml;q=0.5"

# ── Helpers ───────────────────────────────────────────────────────────────────

def slug(url: str) -> str:
    """URL → filesystem-safe slug."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", url)[:80]


def fetch_rdf(url: str) -> Optional[Graph]:
    """Fetch URL, auto-detect RDF format, return parsed Graph or None."""
    try:
        r = requests.get(
            url,
            headers={"Accept": ACCEPT},
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "")
        g  = Graph()

        # Detect format from Content-Type then file extension then sniff
        if "turtle" in ct or url.endswith(".ttl") or url.endswith("/repository") \
                or url.endswith("/catalog") or r.text.lstrip().startswith("@prefix") \
                or r.text.lstrip().startswith("PREFIX"):
            fmt = "turtle"
        elif "json" in ct or url.endswith(".jsonld"):
            fmt = "json-ld"
        elif "rdf+xml" in ct or url.endswith(".rdf"):
            fmt = "xml"
        else:
            fmt = "turtle"   # optimistic default for extensionless FDP resources

        g.parse(data=r.text, format=fmt, publicID=url)
        return g

    except requests.exceptions.Timeout:
        print(f"    ⏱  Timeout: {url}", file=sys.stderr)
    except requests.exceptions.HTTPError as e:
        print(f"    ✗  HTTP {e.response.status_code}: {url}", file=sys.stderr)
    except Exception as e:
        print(f"    ✗  {type(e).__name__}: {url} — {e}", file=sys.stderr)
    return None


def crawl_fdp(root_url: str) -> tuple[Graph, dict]:
    """
    Crawl a single FDP starting from root_url.
    Follows ldp:contains, fdp-o:metadataCatalog, and dcat:dataset links.
    Returns (combined Graph, stats dict).
    """
    combined = Graph()
    for prefix, ns in [
        ("dcat",   DCAT),   ("dcterms", DCTERMS), ("ldp",  LDP),
        ("fdp-o",  FDPO),   ("foaf",    FOAF),     ("rdfs", RDFS),
    ]:
        combined.bind(prefix, ns)

    visited = set()
    queue   = [root_url]
    stats   = {
        "root":      root_url,
        "resources": 0,
        "catalogs":  0,
        "datasets":  0,
        "errors":    0,
        "skipped":   0,
    }

    while queue and stats["resources"] + stats["errors"] < MAX_HOPS:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        print(f"    → {url}")
        g = fetch_rdf(url)

        if g is None:
            stats["errors"] += 1
            continue

        stats["resources"] += 1
        combined += g

        # Count typed resources
        for _ in g.subjects(RDF.type, DCAT.Catalog):
            stats["catalogs"] += 1
        for _ in g.subjects(RDF.type, DCAT.Dataset):
            stats["datasets"] += 1

        # ── Discover child resources ──────────────────────────────────
        link_predicates = [
            LDP.contains,           # LDP container members
            FDPO.metadataCatalog,   # FDP root → catalog
            DCAT.dataset,           # catalog → dataset
            DCAT.distribution,      # dataset → distribution
        ]
        for pred in link_predicates:
            for _, _, obj in g.triples((None, pred, None)):
                if isinstance(obj, URIRef) and str(obj) not in visited:
                    queue.append(str(obj))

    if stats["resources"] + stats["errors"] >= MAX_HOPS:
        stats["skipped"] = len(queue)
        print(f"    ⚠  MAX_HOPS reached — {len(queue)} URLs not crawled", file=sys.stderr)

    return combined, stats


def extract_label(g: Graph, root_url: str) -> str:
    """Pull dcterms:title or rdfs:label from the root resource."""
    subject = URIRef(root_url)
    for pred in [DCTERMS.title, RDFS.label]:
        for _, _, obj in g.triples((subject, pred, None)):
            return str(obj)
    return root_url


def extract_description(g: Graph, root_url: str) -> str:
    subject = URIRef(root_url)
    for _, _, obj in g.triples((subject, DCTERMS.description, None)):
        return str(obj)
    return ""


# ── Index builder ─────────────────────────────────────────────────────────────

def build_index(crawl_results: list[dict]) -> Graph:
    """
    Build a DCAT Catalog of all successfully-crawled FDPs.
    Each FDP becomes a dcat:DataService entry in the index.
    """
    g   = Graph()
    now = Literal(datetime.datetime.utcnow().isoformat() + "Z", datatype=XSD.dateTime)

    for prefix, ns in [
        ("dcat",    DCAT),   ("dcterms", DCTERMS), ("fdp-o", FDPO),
        ("foaf",    FOAF),   ("rdfs",    RDFS),
    ]:
        g.bind(prefix, ns)

    index_uri = URIRef("https://staticfdp.github.io/fdp-index/")

    g.add((index_uri, RDF.type,           DCAT.Catalog))
    g.add((index_uri, DCTERMS.title,      Literal("StaticFDP Index", lang="en")))
    g.add((index_uri, DCTERMS.description,
           Literal("Daily-crawled index of FAIR Data Points hosted via StaticFDP / fdpCloud.", lang="en")))
    g.add((index_uri, DCTERMS.modified,   now))
    g.add((index_uri, DCTERMS.publisher,
           URIRef("https://github.com/StaticFDP")))
    g.add((index_uri, DCTERMS.license,
           URIRef("https://creativecommons.org/licenses/by/4.0/")))

    for result in crawl_results:
        if result["status"] != "ok":
            continue

        fdp_uri  = URIRef(result["root_url"])
        entry_g  = result["graph"]
        label    = extract_label(entry_g, result["root_url"])
        desc     = extract_description(entry_g, result["root_url"])

        g.add((index_uri, DCAT.dataset,     fdp_uri))
        g.add((fdp_uri,   RDF.type,          FDPO.FAIRDataPoint))
        g.add((fdp_uri,   RDF.type,          DCAT.DataService))
        g.add((fdp_uri,   DCTERMS.title,     Literal(label, lang="en")))
        g.add((fdp_uri,   DCAT.endpointURL,  fdp_uri))
        g.add((fdp_uri,   DCTERMS.modified,  now))

        if desc:
            g.add((fdp_uri, DCTERMS.description, Literal(desc, lang="en")))

        # Catalog count as schema:numberOfItems
        if result["stats"]["catalogs"]:
            g.add((fdp_uri, SCHEMA.numberOfItems,
                   Literal(result["stats"]["catalogs"], datatype=XSD.integer)))

    return g


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    registry_path = Path(__file__).parent.parent / "registry.yaml"
    if not registry_path.exists():
        print(f"Registry not found: {registry_path}", file=sys.stderr)
        sys.exit(1)

    with open(registry_path) as f:
        registry = yaml.safe_load(f)

    fdps = registry.get("fdps", [])
    print(f"Crawling {len(fdps)} registered FDP(s)…\n")

    OUTPUT.mkdir(parents=True, exist_ok=True)

    crawl_results = []
    report        = {
        "crawled_at": datetime.datetime.utcnow().isoformat() + "Z",
        "total":      len(fdps),
        "succeeded":  0,
        "failed":     0,
        "fdps":       [],
    }

    for entry in fdps:
        root_url = entry["url"]
        label    = entry.get("label", root_url)
        print(f"┌ {label}")
        print(f"│ {root_url}")

        start   = datetime.datetime.utcnow()
        g, stats = crawl_fdp(root_url)
        elapsed = (datetime.datetime.utcnow() - start).total_seconds()

        result_label = extract_label(g, root_url) or label
        ok = stats["resources"] > 0

        result = {
            "root_url":  root_url,
            "label":     result_label,
            "status":    "ok" if ok else "error",
            "graph":     g,
            "stats":     stats,
            "elapsed_s": round(elapsed, 2),
        }
        crawl_results.append(result)

        # ── Per-FDP output ────────────────────────────────────────────
        if ok:
            out_dir = OUTPUT / "fdps" / slug(root_url)
            out_dir.mkdir(parents=True, exist_ok=True)

            g.serialize(str(out_dir / "snapshot.ttl"),    format="turtle")
            g.serialize(str(out_dir / "snapshot.jsonld"), format="json-ld", indent=2)

            report["succeeded"] += 1
            print(f"│ ✓  {stats['resources']} resources, "
                  f"{stats['catalogs']} catalog(s), "
                  f"{stats['datasets']} dataset(s) [{elapsed:.1f}s]")
        else:
            report["failed"] += 1
            print(f"│ ✗  No resources retrieved [{elapsed:.1f}s]")

        report["fdps"].append({
            "url":       root_url,
            "label":     result_label,
            "status":    result["status"],
            "stats":     stats,
            "elapsed_s": result["elapsed_s"],
        })
        print("└")

    # ── Consolidated index ────────────────────────────────────────────────────
    print(f"\nBuilding consolidated index ({report['succeeded']} FDP(s))…")
    index_g = build_index(crawl_results)
    index_g.serialize(str(OUTPUT / "index.ttl"),    format="turtle")
    index_g.serialize(str(OUTPUT / "index.jsonld"), format="json-ld", indent=2)

    # ── Crawl report ──────────────────────────────────────────────────────────
    with open(OUTPUT / "crawl-report.json", "w") as f:
        # drop non-serialisable 'graph' key before writing
        safe = {k: v for k, v in report.items() if k != "graph"}
        json.dump(safe, f, indent=2, default=str)

    print(f"\n✓ Done — {report['succeeded']}/{report['total']} FDPs indexed")
    print(f"  Outputs written to {OUTPUT.resolve()}/")

    # Exit non-zero if everything failed (lets GitHub Actions flag the run)
    if report["succeeded"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
