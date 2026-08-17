"""Aggregate Text2Cypher campaign results into ranking tables.

Reads one or more results JSONL files produced by t2c_single_agent_evaluator.py,
joins each record back to the dataset by case id (for the phrasing and ecosystem
labels, which the runner does not persist), and emits the tables the thesis
reports:

  - overall ranking        model x protocol -> n / execution rate / avg score
  - by difficulty          Easy / Medium / Hard   (should be monotone decreasing)
  - by template category   C1..C5
  - by query type          SA / SR / CR
  - by phrasing            P1..P5                 (robustness to surface form)
  - by ecosystem           crates.io / pypi.org / conan.io / sources.debian.org

Each table is printed as Markdown and, with --latex-dir, also written as a
booktabs LaTeX fragment for direct \\input into chapter 4.

The model and protocol for a results file are taken from its .summary.json
sidecar when present, else parsed from the filename the runner generates
(results_<dataset>_<model>_<protocol>.jsonl).

Usage (repo root):
  python benchmark/Text2Cypher/t2c_report.py results_t2c_purdue_dataset_v3_*.jsonl
  python benchmark/Text2Cypher/t2c_report.py --dataset t2c_purdue_dataset_v3.json \
      --latex-dir tables results_*.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

T2C_DIR = Path(__file__).resolve().parent

DIFFICULTY_ORDER = ["Easy", "Medium", "Hard"]
QUERY_TYPE_ORDER = ["SA", "SR", "CR"]


def load_results(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue  # tolerate a torn final line from a hard kill
    return records


def run_label(path: Path) -> tuple[str, str]:
    """(model, protocol) for a results file, from sidecar or filename."""
    sidecar = path.with_suffix(".summary.json")
    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))["run"]
            return meta["model"], "hints" if meta.get("format_hints") else "bare"
        except (ValueError, KeyError):
            pass
    m = re.match(r"results_.+?_(?P<model>.+)_(?P<protocol>hints|bare)$", path.stem)
    if m:
        return m.group("model"), m.group("protocol")
    return path.stem, "?"


def bucket(records: list[dict], keyfn) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in records:
        k = keyfn(r)
        b = out.setdefault(k, {"n": 0, "executed": 0, "score_sum": 0.0})
        b["n"] += 1
        if r["executed_successfully"]:
            b["executed"] += 1
        b["score_sum"] += r["score"]
    for b in out.values():
        b["avg"] = b["score_sum"] / b["n"] if b["n"] else 0.0
        b["exec_rate"] = b["executed"] / b["n"] if b["n"] else 0.0
    return out


def ordered(keys, preferred: list[str] | None = None) -> list[str]:
    if preferred:
        known = [k for k in preferred if k in keys]
        return known + sorted(k for k in keys if k not in preferred)
    return sorted(keys)


def fmt(x: float) -> str:
    return f"{x:.3f}"


def markdown_table(title: str, header: list[str], rows: list[list[str]]) -> str:
    lines = [f"### {title}", "", "| " + " | ".join(header) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def latex_table(header: list[str], rows: list[list[str]]) -> str:
    """A booktabs fragment (tabular only) for \\input inside a table float."""
    esc = lambda s: s.replace("_", "\\_").replace("%", "\\%")
    cols = "l" + "r" * (len(header) - 1)
    lines = [f"\\begin{{tabular}}{{{cols}}}", "\\toprule",
             " & ".join(esc(h) for h in header) + " \\\\", "\\midrule"]
    lines += [" & ".join(esc(c) for c in r) + " \\\\" for r in rows]
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Aggregate Text2Cypher results into ranking tables.")
    p.add_argument("results", nargs="+", help="Results JSONL files (runner output)")
    p.add_argument("--dataset", default="t2c_purdue_dataset_v3.json",
                   help="Dataset JSON to join phrasing/ecosystem from")
    p.add_argument("--latex-dir", default=None,
                   help="Also write each table as a booktabs .tex fragment here")
    args = p.parse_args()

    dataset_path = T2C_DIR / args.dataset
    with dataset_path.open(encoding="utf-8") as f:
        by_id = {c["id"]: c for c in json.load(f)}

    runs: list[tuple[str, list[dict]]] = []  # (label, joined records)
    for raw in args.results:
        path = Path(raw)
        if not path.is_absolute():
            path = (T2C_DIR / raw) if (T2C_DIR / raw).exists() else Path.cwd() / raw
        records = load_results(path)
        if not records:
            print(f"WARNING: no records in {path}", file=sys.stderr)
            continue
        model, protocol = run_label(path)
        joined = 0
        for r in records:
            case = by_id.get(r["id"])
            if case:
                # v2 cases predate the phrasing/ecosystem fields; their cuts
                # simply collapse into a single "?" column.
                r["phrasing"] = case.get("phrasing", "?")
                r["ecosystem"] = case.get("ecosystem", "?")
                joined += 1
        if joined < len(records):
            print(f"WARNING: {len(records) - joined} records in {path.name} "
                  f"not found in {args.dataset}; phrasing/ecosystem cuts will drop them",
                  file=sys.stderr)
        runs.append((f"{model} / {protocol}", records))

    if not runs:
        print("no results loaded", file=sys.stderr)
        return 1

    tables: list[tuple[str, str, list[str], list[list[str]]]] = []  # (slug, title, header, rows)

    # Overall ranking, best score first.
    rows = []
    for label, records in runs:
        b = bucket(records, lambda r: "all")["all"]
        rows.append([label, str(b["n"]), fmt(b["exec_rate"]), fmt(b["avg"])])
    rows.sort(key=lambda r: -float(r[3]))
    tables.append(("overall", "Overall ranking", ["run", "n", "exec rate", "avg score"], rows))

    # Grouped cuts: one column per group value, rows in overall-ranking order.
    run_order = [r[0] for r in rows]
    cuts = [
        ("difficulty", "By difficulty", lambda r: r["difficulty"], DIFFICULTY_ORDER),
        ("category", "By template category", lambda r: r["template_id"].split(".")[0], None),
        ("query_type", "By query type", lambda r: r["query_type"], QUERY_TYPE_ORDER),
        ("phrasing", "By phrasing", lambda r: r.get("phrasing", "?"), None),
        ("ecosystem", "By ecosystem", lambda r: r.get("ecosystem", "?"), None),
    ]
    for slug, title, keyfn, preferred in cuts:
        buckets = {label: bucket(records, keyfn) for label, records in runs}
        keys = ordered({k for b in buckets.values() for k in b}, preferred)
        header = ["run"] + keys
        cut_rows = []
        for label in run_order:
            b = buckets[label]
            cut_rows.append([label] + [fmt(b[k]["avg"]) if k in b else "--" for k in keys])
        tables.append((slug, title, header, cut_rows))

    for _, title, header, rows_ in tables:
        print(markdown_table(title, header, rows_))

    if args.latex_dir:
        out_dir = Path(args.latex_dir)
        if not out_dir.is_absolute():
            out_dir = T2C_DIR / out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        for slug, _, header, rows_ in tables:
            (out_dir / f"t2c_{slug}.tex").write_text(latex_table(header, rows_), encoding="utf-8")
        print(f"LaTeX fragments written to {out_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
