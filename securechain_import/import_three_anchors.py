from __future__ import annotations

import argparse
import logging
import os
import sys

from securechain_import.import_graph import find_root_versions, run_import

LODASH_PRIMARY = ("lodash", "4.17.21")
LODASH_FALLBACK = (
    "js-lodash",
    "4.17.4",
) 

logger = logging.getLogger(__name__)


def _build_three_anchors(endpoint: str, timeout: int) -> tuple[tuple[str, str], tuple[str, str], tuple[str, str]]:
    """Return (requests, lodash-side, django) software+version tuples for KG lookup."""
    req = ("requests", "2.31.0")
    djg = ("django", "4.2.11")
    lodash_sw, lodash_ver = LODASH_PRIMARY
    if not find_root_versions(lodash_sw, lodash_ver, endpoint=endpoint, timeout=timeout):
        fs, fv = LODASH_FALLBACK
        logger.warning(
            "SecureChain KG has no SoftwareVersion for %s @ %s (Spanish compares to pkg:npm/lodash@%s); "
            "using fallback %s @ %s for a lodash-adjacent closure.",
            lodash_sw,
            lodash_ver,
            LODASH_PRIMARY[1],
            fs,
            fv,
        )
        lodash_sw, lodash_ver = fs, fv
    if not find_root_versions(lodash_sw, lodash_ver, endpoint=endpoint, timeout=timeout):
        raise SystemExit(
            f"No SoftwareVersion after lodash fallback {lodash_sw!r} @ {lodash_ver!r}. "
            "Try another --endpoint or check KG availability."
        )
    return req, (lodash_sw, lodash_ver), djg


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(
        description="Import three SecureChain dependency closures into Neo4j (Purdue KG)."
    )
    p.add_argument(
        "--neo4j-uri",
        default=os.getenv("NEO4J_URI_SECURECHAIN", "bolt://127.0.0.1:7689"),
        help="Target Neo4j Bolt URI (Spanish mini uses 7688; SecureChain dock uses 7689).",
    )
    p.add_argument("--neo4j-user", default=os.getenv("NEO4J_USERNAME", "neo4j"))
    p.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD", "password"))
    p.add_argument("--neo4j-database", default=os.getenv("NEO4J_DATABASE", "neo4j"))
    p.add_argument(
        "--max-depth",
        type=int,
        default=6,
        help="BFS depth over sc:dependsOn (Spanish copy script uses MAX_REQUIRE_DEPTH=6).",
    )
    p.add_argument(
        "--max-edges",
        type=int,
        default=None,
        help="Optional ceiling on edges collected per root (total safety valve).",
    )
    p.add_argument(
        "--endpoint",
        default=None,
        help="SPARQL endpoint URL (default: securechain_import.sparql_client.DEFAULT_ENDPOINT)",
    )
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--uri-chunk", type=int, default=80)
    p.add_argument("--dry-run", action="store_true")

    args = p.parse_args()

    from securechain_import import sparql_client

    endpoint = args.endpoint or sparql_client.DEFAULT_ENDPOINT

    anchors = list(_build_three_anchors(endpoint, args.timeout))

    failures = []
    for software, version in anchors:
        logger.info("=== %s @ %s ===", software, version)
        code = run_import(
            software=software,
            version=version,
            endpoint=endpoint,
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
        if code != 0:
            failures.append((software, version, code))

    if failures:
        for soft, ver, code in failures:
            logger.error("FAILED %s @ %s (exit %s)", soft, ver, code)
        return 1

    logger.info(
        "All three anchors imported. Open Neo4j Browser on the SecureChain "
        "instance and query e.g. MATCH (v:SoftwareVersion)-[r]->(w) RETURN v,r,w LIMIT 200"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
