"""Batch MERGE into Neo4j."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from neo4j import Driver

logger = logging.getLogger(__name__)


def get_driver(uri: str, user: str, password: str) -> Driver:
    from neo4j import GraphDatabase

    return GraphDatabase.driver(uri, auth=(user, password))


def write_import_job(
    driver: Driver,
    database: str,
    job_id: str,
    root_software_name: str,
    root_version_name: Optional[str],
    endpoint: str,
    sparql_batches: int,
    versions_count: int,
    edges_count: int,
) -> None:
    q = """
    MERGE (j:SecureChainImport {jobId: $jobId})
    SET j.rootSoftware = $rootSoftware,
        j.rootVersion = $rootVersion,
        j.endpoint = $endpoint,
        j.sparqlBatches = $sparqlBatches,
        j.versionsCount = $versionsCount,
        j.edgesCount = $edgesCount,
        j.importedAt = $importedAt
    """
    with driver.session(database=database) as session:
        session.run(
            q,
            {
                "jobId": job_id,
                "rootSoftware": root_software_name,
                "rootVersion": root_version_name,
                "endpoint": endpoint,
                "sparqlBatches": sparql_batches,
                "versionsCount": versions_count,
                "edgesCount": edges_count,
                "importedAt": datetime.now(timezone.utc).isoformat(),
            },
        )


def link_root(
    driver: Driver,
    database: str,
    job_id: str,
    root_version_uri: str,
) -> None:
    q = """
    MATCH (j:SecureChainImport {jobId: $jobId})
    MATCH (v:SoftwareVersion {uri: $rootUri})
    MERGE (j)-[:ROOT_VERSION]->(v)
    """
    with driver.session(database=database) as session:
        session.run(q, {"jobId": job_id, "rootUri": root_version_uri})


def merge_versions_batch(
    driver: Driver,
    database: str,
    job_id: str,
    rows: List[Dict[str, Any]],
) -> None:
    """
    rows: list of dicts with keys softwareUri, softwareName, versionUri, versionName
    """
    q = """
    UNWIND $rows AS row
    MERGE (s:Software {uri: row.softwareUri})
    ON CREATE SET s.name = row.softwareName, s.source = 'securechain'
    ON MATCH SET s.name = coalesce(s.name, row.softwareName)
    MERGE (v:SoftwareVersion {uri: row.versionUri})
    ON CREATE SET v.versionName = row.versionName, v.source = 'securechain', v.jobId = $jobId
    ON MATCH SET v.versionName = coalesce(v.versionName, row.versionName),
                 v.jobId = coalesce(v.jobId, $jobId)
    MERGE (s)-[:HAS_VERSION]->(v)
    """
    with driver.session(database=database) as session:
        session.run(q, {"rows": rows, "jobId": job_id})


def merge_depends_batch(
    driver: Driver,
    database: str,
    job_id: str,
    pairs: List[Tuple[str, str]],
) -> None:
    """pairs: (fromVersionUri, toVersionUri)"""
    rows = [{"fromUri": a, "toUri": b} for a, b in pairs]
    q = """
    UNWIND $rows AS row
    MERGE (a:SoftwareVersion {uri: row.fromUri})
    MERGE (b:SoftwareVersion {uri: row.toUri})
    MERGE (a)-[r:DEPENDS_ON]->(b)
    ON CREATE SET r.source = 'securechain', r.jobId = $jobId
    """
    with driver.session(database=database) as session:
        session.run(q, {"rows": rows, "jobId": job_id})


def merge_vulnerabilities_batch(
    driver: Driver,
    database: str,
    job_id: str,
    rows: List[Dict[str, Any]],
) -> None:
    """
    rows: verUri, cveUri, cveId, optional cweUri, cweId
    Maps sc:vulnerableTo -> VULNERABLE_TO, sc:vulnerabilityType -> VULNERABILITY_TYPE.
    """
    if not rows:
        return
    q = """
    UNWIND $rows AS row
    MATCH (v:SoftwareVersion {uri: row.verUri})
    MERGE (c:Vulnerability {uri: row.cveUri})
    ON CREATE SET c.cveId = row.cveId, c.source = 'securechain', c.jobId = $jobId
    ON MATCH SET c.cveId = coalesce(c.cveId, row.cveId),
                 c.jobId = coalesce(c.jobId, $jobId)
    MERGE (v)-[r:VULNERABLE_TO]->(c)
    ON CREATE SET r.source = 'securechain', r.jobId = $jobId
    WITH v, c, row
    WHERE row.cweUri IS NOT NULL AND row.cweUri <> ''
      AND row.cweId IS NOT NULL AND row.cweId <> ''
    MERGE (w:VulnerabilityType {uri: row.cweUri})
    ON CREATE SET w.cweId = row.cweId, w.source = 'securechain', w.jobId = $jobId
    ON MATCH SET w.cweId = coalesce(w.cweId, row.cweId),
                 w.jobId = coalesce(w.jobId, $jobId)
    MERGE (c)-[t:VULNERABILITY_TYPE]->(w)
    ON CREATE SET t.source = 'securechain', t.jobId = $jobId
    """
    with driver.session(database=database) as session:
        session.run(q, {"rows": rows, "jobId": job_id})


def ensure_versions_placeholder(
    driver: Driver,
    database: str,
    version_uris: Iterable[str],
    job_id: str,
) -> None:
    """MERGE version nodes with only uri (when metadata is missing for some URIs)."""
    rows = [{"uri": u} for u in version_uris]
    if not rows:
        return
    q = """
    UNWIND $rows AS row
    MERGE (v:SoftwareVersion {uri: row.uri})
    ON CREATE SET v.source = 'securechain', v.jobId = $jobId
    """
    with driver.session(database=database) as session:
        session.run(q, {"rows": rows, "jobId": job_id})
