#!/usr/bin/env python3
"""Inspect a custom Agent Arena brief set before using it as a shadow benchmark.

This script does NOT score the student harness. It answers a different question:
"How close is this author-authored benchmark to the instructor's mechanical
private-brief authoring contract?"

For each brief it prints:
- schema validity;
- strict acceptance problems (uniqueness/depth/enumerability/verdict rules);
- the question's top retrieval hits;
- whether nominated supporting docs are shallow hits;
- trap classes reachable from the question.

At the end it runs the set-level dispersion check.

A behavioral shadow set may intentionally fail strict acceptance checks. That is
fine as long as we label it honestly. Use --strict only when you want a non-zero
exit code for any private-style authoring violation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parent.parent
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from arena.briefs import (  # noqa: E402
    acceptance_problems,
    dispersion_problems,
    load_brief_file,
    schema_problems,
    trap_classes,
)
from arena.corpus import Corpus  # noqa: E402
from arena.scorer import MAX_SCORED_CLAIMS  # noqa: E402

DEFAULT_BENCHMARK = LAB_ROOT / "benchmarks" / "shadow_hidden.json"


def _support_ids(brief: dict) -> list[str]:
    ids: list[str] = []
    for fact in brief.get("required_facts", []):
        if not isinstance(fact, dict):
            continue
        for doc_id in fact.get("supporting_doc_ids") or []:
            if isinstance(doc_id, str) and doc_id not in ids:
                ids.append(doc_id)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--briefs", default=str(DEFAULT_BENCHMARK))
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if any private-style acceptance/dispersion check fails",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable summary")
    args = parser.parse_args()

    path = Path(args.briefs)
    payload = load_brief_file(path)
    briefs = list(payload["briefs"])
    corpus_seed = int(payload.get("corpus_seed", 42))
    corpus = Corpus.generate(seed=corpus_seed)

    rows = []
    any_problem = False
    for brief in briefs:
        brief_id = brief.get("brief_id", "<missing>")
        schema = schema_problems(brief)
        acceptance = acceptance_problems(brief, corpus)
        question = brief.get("question_vi") or ""
        hits = corpus.search(question, k=MAX_SCORED_CLAIMS)
        hit_ids = [doc.doc_id for doc in hits]
        supports = _support_ids(brief)
        shallow_supports = [doc_id for doc_id in supports if doc_id in hit_ids]
        traps = sorted(trap_classes(brief, corpus, k=5))

        row = {
            "brief_id": brief_id,
            "schema_problems": schema,
            "acceptance_problems": [list(problem) for problem in acceptance],
            "supporting_doc_ids": supports,
            "shallow_supporting_doc_ids": shallow_supports,
            "top_hits": hit_ids,
            "trap_classes": traps,
            "strict_private_like": not schema and not acceptance,
        }
        rows.append(row)
        if schema or acceptance:
            any_problem = True

    dispersion = dispersion_problems(briefs, corpus)
    if dispersion:
        any_problem = True

    summary = {
        "brief_file": str(path),
        "set": payload.get("set"),
        "corpus_seed": corpus_seed,
        "n_briefs": len(briefs),
        "strict_private_like_count": sum(1 for row in rows if row["strict_private_like"]),
        "dispersion_problems": [list(problem) for problem in dispersion],
        "briefs": rows,
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("=" * 78)
        print("AGENT ARENA — SHADOW BENCHMARK AUTHORING CHECK")
        print(f"  file        : {path}")
        print(f"  set         : {payload.get('set')}")
        print(f"  corpus seed : {corpus_seed}")
        print(f"  briefs      : {len(briefs)}")
        print("=" * 78)
        for row in rows:
            label = "STRICT-LIKE" if row["strict_private_like"] else "BEHAVIORAL"
            print(f"\n{row['brief_id']}  [{label}]")
            print(f"  support     : {', '.join(row['supporting_doc_ids']) or '(none)'}")
            print(f"  shallow     : {', '.join(row['shallow_supporting_doc_ids']) or '(none)'}")
            print(f"  traps top-5 : {', '.join(row['trap_classes']) or '(none)'}")
            print(f"  top-{MAX_SCORED_CLAIMS:<2}      : {', '.join(row['top_hits'])}")
            if row["schema_problems"]:
                for problem in row["schema_problems"]:
                    print(f"  SCHEMA      : {problem}")
            if row["acceptance_problems"]:
                for problem in row["acceptance_problems"]:
                    print(f"  ACCEPTANCE  : {problem}")
            if not row["schema_problems"] and not row["acceptance_problems"]:
                print("  ACCEPTANCE  : PASS")

        print("\n" + "-" * 78)
        if dispersion:
            print("SET-LEVEL DISPERSION PROBLEMS:")
            for problem in dispersion:
                print(f"  {problem}")
        else:
            print("SET-LEVEL DISPERSION: PASS")
        print(
            f"Strict-private-like briefs: {summary['strict_private_like_count']}/{len(briefs)}"
        )
        print(
            "Note: failing a strict authoring check does not make a behavioral stress case "
            "useless; it only means we must not pretend it is equivalent to the coach's "
            "private benchmark."
        )

    return 2 if args.strict and any_problem else 0


if __name__ == "__main__":
    raise SystemExit(main())
