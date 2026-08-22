# Independent verification of the v4 bank

These scripts check the built bank a **second time, with a second
implementation**. That is the point: `t2c_build_v4.py`'s own V1–V8 run inside
the builder, so a mistake shared by a template and the builder would validate
itself. Everything here recomputes answers along a path that shares no code
with the gold.

Every defect that actually mattered on 2026-08-22 was found this way or by an
end-to-end smoke run — **none of them tripped a build-time check**.

## The house method

1. **Replay every gold twice** against the live graph and compare with the
   stored `expected_result` (catches drift and nondeterminism).
2. **Recompute the answer in Python from the raw edge list** — BFS for
   reachability and distance, DFS with edge-uniqueness for walk depth, layered
   BFS for shortest-path counting, plain set algebra for the C5 family. No
   Cypher semantics are borrowed from the gold.
3. **Re-check each stratum's claim with independent logic** ("this pair really
   is reachable-but-not-direct", "this root really has no outgoing edge").
4. **Check the declared answer shape**, parameter uniqueness, and that empty
   answers are *attributable* to the thing the stratum is testing rather than
   to a leaf root.

## What each script covers

| script | scope | notes |
|---|---|---|
| `verify_c2.py` | C2.1–C2.3 | stratum semantics re-checked with separate Cypher |
| `verify_c3.py` | C3.1–C3.6 | full Python recomputation; found the cycle defect (root listed as its own dependency) in 118 of 750 non-empty answers |
| `verify_c4.py` | C4.3, C4.4 | also measures the `*0..6` vs `*1..6` divergence that the golds are held at |
| `verify_c5.py` | C5.1–C5.3, C5.5, C5.6 | set algebra over direct dependencies, CVE multisets, CWE sets |
| `verify_c54.py` | C5.4 | plus a replay of the sheet's original gold, which returns 69 % of the answer |
| `review_c3.py` | C3 second pass | a *third* computation path (Cypher UNION of six exact-depth patterns), C1/C2 regression replay, and the depth-6 truncation quantified |

## Running them

Neo4j must be up with the frozen import, and `.env` must carry `NEO4J_URI`,
`NEO4J_USERNAME`, `NEO4J_PASSWORD`.

```bash
docker start neo4j-securechain
python benchmark/Text2Cypher/verification/verify_c5.py
```

Each writes a `verify_*_report.json` next to the dataset.

## Two rules worth carrying into the next family

- **Pair templates: check `distinct_first_sides` and `distinct_answers`.**
  A pool that truncates the outer side of the join (`ORDER BY name LIMIT n`)
  takes the alphabetically first n versions, and a pair template's answer is
  mostly decided by its first side. C5.5 shipped 250 cases from 38 first sides
  and 10 distinct answers before this was caught.
- **A new answer *shape* needs a contract clause, and only a smoke run can
  prove the pair consistent.** Build-time validation sees the dataset alone,
  never the protocol.
