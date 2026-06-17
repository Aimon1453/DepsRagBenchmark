"""HTTP SPARQL client (SPARQL 1.1 JSON results)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://frink.apps.renci.org/securechainkg/sparql"


def sparql_select(
    query: str,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: int = 120,
) -> List[Dict[str, Any]]:
    """
    Run a SPARQL SELECT and return bindings as list of dicts.
    Values are Python scalars (URI and literal strings).
    """
    headers = {
        "Accept": "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    }
    body = urlencode({"query": query})
    resp = requests.post(endpoint, data=body, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return _parse_bindings(data)


def _parse_bindings(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    bindings = data.get("results", {}).get("bindings", [])
    out: List[Dict[str, Any]] = []
    for b in bindings:
        row: Dict[str, Any] = {}
        for var, cell in b.items():
            row[var] = _cell_to_python(cell)
        out.append(row)
    return out


def _cell_to_python(cell: Dict[str, Any]) -> Any:
    """Convert one SPARQL JSON binding cell to Python value."""
    t = cell.get("type")
    if t == "uri":
        return cell.get("value", "")
    if t == "literal":
        return cell.get("value", "")
    if t == "typed-literal":
        return cell.get("value", "")
    if t == "bnode":
        return f"_:{cell.get('value', '')}"
    return cell.get("value")


def batch_values_uris(uris: List[str], chunk_size: int = 80) -> List[List[str]]:
    """Split URI list into chunks for VALUES blocks."""
    chunks: List[List[str]] = []
    for i in range(0, len(uris), chunk_size):
        chunks.append(uris[i : i + chunk_size])
    return chunks


def sparql_construct_rdf(
    query: str,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: int = 300,
) -> tuple[str, str]:
    """
    Run SPARQL CONSTRUCT. Returns (body, format_hint) where format_hint is
    'nt' for N-Triples-ish lines or 'ttl' for Turtle (some endpoints only emit Turtle).

    Some SPARQL endpoints return an empty body for Accept: application/n-triples but
    work with text/turtle.
    """
    body = urlencode({"query": query})
    headers_nt = {
        "Accept": "application/n-triples",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    }
    resp = requests.post(endpoint, data=body, headers=headers_nt, timeout=timeout)
    resp.raise_for_status()
    text = (resp.text or "").strip()
    if len(text) > 2:
        return resp.text or "", "nt"

    headers_ttl = {
        "Accept": "text/turtle",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    }
    resp2 = requests.post(endpoint, data=body, headers=headers_ttl, timeout=timeout)
    resp2.raise_for_status()
    return resp2.text or "", "ttl"


def sparql_construct_ntriples(
    query: str,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: int = 300,
) -> str:
    """Backward-compatible: return body only (nt or ttl text)."""
    body, _ = sparql_construct_rdf(query, endpoint=endpoint, timeout=timeout)
    return body
