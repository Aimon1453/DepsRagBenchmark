"""
BFS over sc:dependsOn from a root SoftwareVersion, then MERGE into Neo4j.
"""

from __future__ import annotations

import argparse
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv

from securechain_import import sparql_client
from securechain_import.neo4j_write import (
    ensure_versions_placeholder,
    get_driver,
    link_root,
    merge_depends_batch,
    merge_versions_batch,
    merge_vulnerabilities_batch,
    write_import_job,
)

PREFIXES = """PREFIX sc: <https://w3id.org/secure-chain/>
PREFIX schema: <http://schema.org/>
"""

logger = logging.getLogger(__name__)


def _sparql_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _values_uris(uris: List[str]) -> str:
    return " ".join(f"<{u}>" for u in uris)


def find_root_versions(
    software_name: str,
    version_name: Optional[str],
    endpoint: str,
    timeout: int,
) -> List[Dict[str, str]]:
    """Return rows soft, ver, versionName."""
    vfilter = ""
    if version_name is not None:
        vfilter = f"FILTER(?versionName = {_sparql_str(version_name)})"
    q = (
        PREFIXES
        + f"""
SELECT DISTINCT ?soft ?ver ?versionName
WHERE {{
  ?soft a sc:Software .
  ?soft schema:name ?softwareName .
  FILTER(LCASE(STR(?softwareName)) = LCASE({_sparql_str(software_name)}))
  ?soft sc:hasSoftwareVersion ?ver .
  ?ver sc:versionName ?versionName .
  {vfilter}
}}
LIMIT 500
"""
    )
    rows = sparql_client.sparql_select(q, endpoint=endpoint, timeout=timeout)
    out: List[Dict[str, str]] = []
    for r in rows:
        out.append(
            {
                "soft": str(r.get("soft", "")),
                "ver": str(r.get("ver", "")),
                "versionName": str(r.get("versionName", "")),
            }
        )
    return out


def fetch_depends_on_chunk(
    src_uris: List[str],
    endpoint: str,
    timeout: int,
) -> List[Tuple[str, str]]:
    if not src_uris:
        return []
    values = _values_uris(src_uris)
    q = (
        PREFIXES
        + f"""
SELECT DISTINCT ?src ?tgt
WHERE {{
  VALUES ?src {{ {values} }}
  ?src sc:dependsOn ?tgt .
}}
"""
    )
    rows = sparql_client.sparql_select(q, endpoint=endpoint, timeout=timeout)
    pairs: List[Tuple[str, str]] = []
    for r in rows:
        s, t = r.get("src"), r.get("tgt")
        if s and t:
            pairs.append((str(s), str(t)))
    return pairs


def fetch_version_metadata_chunk(
    ver_uris: List[str],
    endpoint: str,
    timeout: int,
) -> List[Dict[str, str]]:
    if not ver_uris:
        return []
    values = _values_uris(ver_uris)
    q = (
        PREFIXES
        + f"""
SELECT DISTINCT ?ver ?soft ?softwareName ?versionName
WHERE {{
  VALUES ?ver {{ {values} }}
  ?soft a sc:Software .
  ?soft sc:hasSoftwareVersion ?ver .
  ?soft schema:name ?softwareName .
  ?ver sc:versionName ?versionName .
}}
"""
    )
    rows = sparql_client.sparql_select(q, endpoint=endpoint, timeout=timeout)
    out: List[Dict[str, str]] = []
    for r in rows:
        out.append(
            {
                "versionUri": str(r.get("ver", "")),
                "softwareUri": str(r.get("soft", "")),
                "softwareName": str(r.get("softwareName", "")),
                "versionName": str(r.get("versionName", "")),
            }
        )
    return out


def fetch_vulnerabilities_chunk(
    ver_uris: List[str],
    endpoint: str,
    timeout: int,
) -> List[Dict[str, str]]:
    """Rows: ver, cve, cveId, optional cwe, cweId (sc:vulnerableTo / vulnerabilityType)."""
    if not ver_uris:
        return []
    values = _values_uris(ver_uris)
    q = (
        PREFIXES
        + f"""
SELECT DISTINCT ?ver ?cve ?cveId ?cwe ?cweId
WHERE {{
  VALUES ?ver {{ {values} }}
  ?ver sc:vulnerableTo ?cve .
  ?cve a sc:Vulnerability ; schema:identifier ?cveId .
  OPTIONAL {{
    ?cve sc:vulnerabilityType ?cwe .
    ?cwe schema:identifier ?cweId .
  }}
}}
"""
    )
    rows = sparql_client.sparql_select(q, endpoint=endpoint, timeout=timeout)
    out: List[Dict[str, str]] = []
    for r in rows:
        out.append(
            {
                "verUri": str(r.get("ver", "")),
                "cveUri": str(r.get("cve", "")),
                "cveId": str(r.get("cveId", "")),
                "cweUri": str(r.get("cwe", "") or ""),
                "cweId": str(r.get("cweId", "") or ""),
            }
        )
    return out


def bfs_closure(
    root_ver_uri: str,
    endpoint: str,
    timeout: int,
    max_edges: Optional[int],
    max_depth: Optional[int],
    uri_chunk: int,
) -> Tuple[Set[str], List[Tuple[str, str]], int]:
    """
    BFS over version nodes: each node is expanded at most once (outgoing sc:dependsOn).

    max_depth: number of BFS waves. max_depth=1 expands only the root (direct deps).
    Returns (version_uris, dep_edges list, sparql_select_call_count).
    """
    sparql_calls = 0
    edges: List[Tuple[str, str]] = []
    expanded_src: Set[str] = set()
    frontier: Set[str] = {root_ver_uri}
    depth = 0

    while frontier:
        if max_depth is not None and depth >= max_depth:
            break

        to_query = [u for u in sorted(frontier) if u not in expanded_src]
        if not to_query:
            break

        next_frontier: Set[str] = set()
        for chunk in sparql_client.batch_values_uris(to_query, chunk_size=uri_chunk):
            sparql_calls += 1
            pairs = fetch_depends_on_chunk(chunk, endpoint=endpoint, timeout=timeout)
            for src, tgt in pairs:
                if max_edges is not None and len(edges) >= max_edges:
                    return _collect_versions(edges, root_ver_uri), edges, sparql_calls
                edges.append((src, tgt))
                if tgt not in expanded_src:
                    next_frontier.add(tgt)

        expanded_src.update(to_query)
        frontier = next_frontier
        depth += 1

    versions = _collect_versions(edges, root_ver_uri)
    return versions, edges, sparql_calls


def _collect_versions(edges: List[Tuple[str, str]], root: str) -> Set[str]:
    s: Set[str] = {root}
    for a, b in edges:
        s.add(a)
        s.add(b)
    return s


def load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def run_import(
    software: str,
    version: Optional[str],
    endpoint: str,
    dry_run: bool,
    timeout: int,
    max_edges: Optional[int],
    max_depth: Optional[int],
    uri_chunk: int,
    neo4j_uri: Optional[str],
    neo4j_user: Optional[str],
    neo4j_password: Optional[str],
    neo4j_database: Optional[str],
) -> int:
    load_env()

    roots = find_root_versions(software, version, endpoint=endpoint, timeout=timeout)
    if not roots:
        logger.error("No SoftwareVersion found for name=%r version=%r", software, version)
        return 1

    if len(roots) > 1 and version is None:
        logger.warning(
            "Multiple versions match %r (showing first 10). "
            "Pass --version <versionName> to pick one.",
            software,
        )
        for r in roots[:10]:
            logger.warning("  candidate versionName=%s uri=%s", r["versionName"], r["ver"])

    picked = roots[0]
    root_ver_uri = picked["ver"]
    root_version_name = picked["versionName"]
    logger.info(
        "Root: software=%s versionName=%s uri=%s",
        software,
        root_version_name,
        root_ver_uri,
    )

    versions, edges, n_calls = bfs_closure(
        root_ver_uri=root_ver_uri,
        endpoint=endpoint,
        timeout=timeout,
        max_edges=max_edges,
        max_depth=max_depth,
        uri_chunk=uri_chunk,
    )

    # Metadata for all version URIs (chunked)
    meta_rows: List[Dict[str, str]] = []
    all_v = sorted(versions)
    for chunk in sparql_client.batch_values_uris(all_v, chunk_size=uri_chunk):
        n_calls += 1
        meta_rows.extend(fetch_version_metadata_chunk(chunk, endpoint=endpoint, timeout=timeout))

    seen_ver: Set[str] = set()
    neo_rows: List[Dict[str, Any]] = []
    for m in meta_rows:
        vu = m.get("versionUri", "")
        if not vu or vu in seen_ver:
            continue
        seen_ver.add(vu)
        neo_rows.append(
            {
                "softwareUri": m["softwareUri"],
                "softwareName": m["softwareName"],
                "versionUri": m["versionUri"],
                "versionName": m["versionName"],
            }
        )

    missing = [u for u in all_v if u not in seen_ver]

    vuln_rows: List[Dict[str, str]] = []
    for chunk in sparql_client.batch_values_uris(all_v, chunk_size=uri_chunk):
        n_calls += 1
        vuln_rows.extend(fetch_vulnerabilities_chunk(chunk, endpoint=endpoint, timeout=timeout))

    job_id = str(uuid.uuid4())
    logger.info(
        "Closure: versions=%d edges=%d vuln_links=%d sparql_calls=%d missing_metadata=%d",
        len(all_v),
        len(edges),
        len(vuln_rows),
        n_calls,
        len(missing),
    )

    if dry_run:
        logger.info("Dry run: not writing to Neo4j. job_id would be %s", job_id)
        return 0

    uri = neo4j_uri or os.getenv("NEO4J_URI")
    user = neo4j_user or os.getenv("NEO4J_USERNAME")
    password = neo4j_password or os.getenv("NEO4J_PASSWORD")
    database = neo4j_database or os.getenv("NEO4J_DATABASE", "neo4j")
    if not uri or not user or not password:
        logger.error("Set NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD (or pass flags).")
        return 1

    driver = get_driver(uri, user, password)
    try:
        batch = 200
        for i in range(0, len(neo_rows), batch):
            merge_versions_batch(driver, database, job_id, neo_rows[i : i + batch])
        if missing:
            for i in range(0, len(missing), batch):
                ensure_versions_placeholder(driver, database, missing[i : i + batch], job_id)

        for i in range(0, len(edges), batch):
            merge_depends_batch(driver, database, job_id, edges[i : i + batch])

        for i in range(0, len(vuln_rows), batch):
            merge_vulnerabilities_batch(driver, database, job_id, vuln_rows[i : i + batch])

        write_import_job(
            driver,
            database,
            job_id,
            software,
            root_version_name,
            endpoint,
            n_calls,
            len(all_v),
            len(edges),
        )
        link_root(driver, database, job_id, root_ver_uri)
    finally:
        driver.close()

    logger.info("Done. jobId=%s", job_id)
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(
        description="Import SecureChain dependsOn closure into Neo4j (BFS from a root version)."
    )
    p.add_argument("--software", required=True, help='schema:name, e.g. "openssl"')
    p.add_argument("--version", default=None, help="Optional sc:versionName to pin one root")
    p.add_argument(
        "--endpoint",
        default=sparql_client.DEFAULT_ENDPOINT,
        help="SPARQL endpoint URL",
    )
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument(
        "--max-edges",
        type=int,
        default=None,
        help="Stop after collecting this many dependsOn edges (safety valve).",
    )
    p.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="Optional max BFS depth from root (1 = direct deps only).",
    )
    p.add_argument(
        "--uri-chunk",
        type=int,
        default=80,
        help="How many URIs per SPARQL VALUES batch.",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--neo4j-uri", default=None)
    p.add_argument("--neo4j-user", default=None)
    p.add_argument("--neo4j-password", default=None)
    p.add_argument("--neo4j-database", default=None)

    args = p.parse_args()
    raise SystemExit(
        run_import(
            software=args.software,
            version=args.version,
            endpoint=args.endpoint,
            dry_run=args.dry_run,
            timeout=args.timeout,
            max_edges=args.max_edges,
            max_depth=args.max_depth,
            uri_chunk=args.uri_chunk,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_password=args.neo4j_password,
            neo4j_database=args.neo4j_database,
        )
    )


if __name__ == "__main__":
    main()
