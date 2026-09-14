"""Kildespor command-line interface.

    kildespor bootstrap [--out data/bulk/enheter.csv] [--n 1200]
    kildespor run      [--n 1000] [--seed kildespor-v1] [--since-run]
    kildespor validate [--run-dir data/profiles/run_...]
    kildespor report   [--run-dir data/profiles/run_...]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import CONFIG
from .differ import diff_profile, summarise_changes
from .explain import explain_profile, validate_profile
from .http_client import PoliteClient
from .models import CompanyProfile
from .pipeline import Pipeline
from .sampling import download_bulk_csv, sample_orgnrs
from .store import latest_run_dir, load_profiles, save_profiles

log = logging.getLogger("kildespor.cli")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _new_run_dir() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return str(Path(CONFIG.data_dir) / "profiles" / f"run_{stamp}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_bootstrap(args: argparse.Namespace) -> int:
    csv_path = args.out
    if os.path.exists(csv_path) and not args.force:
        log.info("bulk CSV already present: %s (use --force to re-download)", csv_path)
    else:
        log.info("downloading Brreg bulk CSV (one request, ~200-400 MB) ...")
        download_bulk_csv(csv_path)
    orgnrs, manifest = sample_orgnrs(csv_path, args.n, seed=args.seed)
    manifest_path = Path(CONFIG.data_dir) / "sample_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    sample_path = Path(CONFIG.data_dir) / "sample_orgnrs.json"
    sample_path.write_text(json.dumps(orgnrs, indent=1), encoding="utf-8")
    log.info(
        "sampled %d orgnrs of %d (seed=%s) -> %s",
        manifest["sample_size"], manifest["universe_size"], args.seed, sample_path,
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    sample_path = Path(CONFIG.data_dir) / "sample_orgnrs.json"
    if args.n and not args.use_sample_file:
        csv_path = Path(CONFIG.data_dir) / "bulk" / "enheter.csv"
        if not csv_path.exists():
            log.info("no bulk CSV yet; running bootstrap first ...")
            cmd_bootstrap(argparse.Namespace(out=str(csv_path), n=args.n, seed=args.seed, force=False))
        orgnrs, _ = sample_orgnrs(str(csv_path), args.n, seed=args.seed)
    else:
        if not sample_path.exists():
            log.error("no sample file at %s; run `kildespor bootstrap` first", sample_path)
            return 2
        orgnrs = json.loads(sample_path.read_text(encoding="utf-8"))

    run_dir = args.run_dir or _new_run_dir()
    prev_dir = args.since_run or latest_run_dir(str(Path(CONFIG.data_dir) / "profiles"), exclude=run_dir)

    started = time.monotonic()
    pipeline = Pipeline(client=PoliteClient(snapshot_dir=str(Path(CONFIG.data_dir) / "snapshots")))
    profiles = pipeline.run(orgnrs)
    elapsed = time.monotonic() - started

    # --- Diff against previous run (typed updates)
    previous = load_profiles(prev_dir) if prev_dir else {}
    total_changes: list[dict] = []
    for p in profiles:
        p.changes = diff_profile(previous.get(p.organisasjonsnummer), p)
        total_changes.extend(p.changes)

    out = save_profiles(profiles, run_dir)
    summary = {
        "run_dir": out,
        "previous_run_dir": prev_dir,
        "profiles": len(profiles),
        "facts_published": pipeline.stats["facts_published"],
        "facts_unavailable": pipeline.stats["facts_unavailable"],
        "website_gate_pass": pipeline.stats["website_gate_pass"],
        "website_gate_ambiguous": pipeline.stats["website_gate_ambiguous"],
        "requests_used": pipeline.stats["requests_used"],
        "request_budget": CONFIG.max_requests,
        "elapsed_seconds": round(elapsed, 1),
        "changes_total": len(total_changes),
        "changes_by_type": summarise_changes(total_changes),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (Path(out) / "_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    run_dir = args.run_dir or latest_run_dir(str(Path(CONFIG.data_dir) / "profiles"))
    if not run_dir or not Path(run_dir).is_dir():
        log.error("no run directory found")
        return 2
    profiles = load_profiles(run_dir)
    problems: list[str] = []
    for orgnr, p in sorted(profiles.items()):
        for violation in validate_profile(p):
            problems.append(f"{orgnr}: {violation}")
    if problems:
        for line in problems:
            print("VIOLATION:", line)
        print(f"\n{len(problems)} violation(s) in {len(profiles)} profiles")
        return 1
    print(f"OK: {len(profiles)} profiles pass all hard-fail checks")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = args.run_dir or latest_run_dir(str(Path(CONFIG.data_dir) / "profiles"))
    if not run_dir or not Path(run_dir).is_dir():
        log.error("no run directory found")
        return 2
    profiles = load_profiles(run_dir)
    sample_n = min(args.show, len(profiles))
    shown = 0
    for orgnr in sorted(profiles):
        if shown >= sample_n:
            break
        p = profiles[orgnr]
        print("=" * 72)
        print(f"ORG {orgnr}")
        for line in explain_profile(p):
            print(f"  - {line}")
        shown += 1
    summary_path = Path(run_dir) / "_summary.json"
    if summary_path.exists():
        print("=" * 72)
        print(summary_path.read_text(encoding="utf-8"))
    return 0


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kildespor",
        description="Kildespor — evidence-linked Norwegian company profiles from open data",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_boot = sub.add_parser("bootstrap", help="download universe + draw the deterministic sample")
    p_boot.add_argument("--out", default=str(Path(CONFIG.data_dir) / "bulk" / "enheter.csv"))
    p_boot.add_argument("--n", type=int, default=1200)
    p_boot.add_argument("--seed", default="kildespor-v1")
    p_boot.add_argument("--force", action="store_true")
    p_boot.set_defaults(func=cmd_bootstrap)

    p_run = sub.add_parser("run", help="build profiles for the sample")
    p_run.add_argument("--n", type=int, default=0, help="sample size (0 = use saved sample file)")
    p_run.add_argument("--seed", default="kildespor-v1")
    p_run.add_argument("--use-sample-file", action="store_true", default=True)
    p_run.add_argument("--run-dir", default=None)
    p_run.add_argument("--since-run", default=None, help="previous run dir to diff against")
    p_run.set_defaults(func=cmd_run)

    p_val = sub.add_parser("validate", help="check hard-fail rules on a run")
    p_val.add_argument("--run-dir", default=None)
    p_val.set_defaults(func=cmd_validate)

    p_rep = sub.add_parser("report", help="print human-readable profile explanations")
    p_rep.add_argument("--run-dir", default=None)
    p_rep.add_argument("--show", type=int, default=5)
    p_rep.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
