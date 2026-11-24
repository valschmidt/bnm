"""Command line entry point: ``bnm-fetch``."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import subprocess
import sys
from pathlib import Path

from .build import build_site
from .config import Config
from .http import CachedFetcher

log = logging.getLogger("bnm")

SOURCES = ("lnm", "bnm")


def _run_source(name, fetcher, cfg):
    if name == "lnm":
        from .sources import navcen_lnm
        return navcen_lnm.fetch(fetcher, cfg.districts)
    if name == "bnm":
        from .sources import email_bnm
        return email_bnm.fetch(cfg)
    raise ValueError(name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bnm-fetch", description=__doc__)
    ap.add_argument("-c", "--config", type=Path, help="TOML config file")
    ap.add_argument("--out", type=Path, help="output (web) directory")
    ap.add_argument("--only", choices=SOURCES, action="append", help="run only this source (repeatable)")
    ap.add_argument("--districts", help="comma-separated USCG districts, e.g. 1,5")
    ap.add_argument("--rsync", help="rsync destination for the output directory, e.g. user@host:/var/www/bnm/")
    ap.add_argument("--archive", type=Path, help="SQLite file recording every notice seen")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING - 10 * min(args.verbose + 1, 2),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    cfg = Config.load(args.config)
    if args.out:
        cfg.out = args.out
    if args.only:
        cfg.sources = args.only
    if args.districts:
        cfg.districts = [int(d) for d in args.districts.split(",")]
    if args.rsync:
        cfg.rsync = args.rsync
    if args.archive:
        cfg.archive = args.archive

    # Prevent overlapping cron runs
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    lock = open(cfg.cache_dir / "bnm-fetch.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error("another bnm-fetch is running; exiting")
        return 3

    fetcher = CachedFetcher(cfg.cache_dir, cfg.user_agent, cfg.timeout, cfg.request_delay)
    results = []
    for name in cfg.sources:
        try:
            results.append(_run_source(name, fetcher, cfg))
        except Exception as exc:
            log.exception("source %s crashed", name)
            from .sources import SourceResult
            results.append(SourceResult(name, errors=[f"{type(exc).__name__}: {exc}"], ok=False))

    for r in results:
        log.info("%s: %d features, %d errors%s", r.name, len(r.features), len(r.errors),
                 "" if r.ok else " (FAILED)")

    if args.dry_run:
        print(json.dumps({r.name: {"ok": r.ok, "count": len(r.features), "errors": r.errors}
                          for r in results}, indent=1))
        return 0

    manifest = build_site(cfg.out, results, cfg.archive)
    log.info("wrote %d notices to %s", manifest["count"], cfg.out)

    if cfg.rsync:
        cmd = ["rsync", "-a", "--delete", "--exclude", ".*", f"{cfg.out}/", cfg.rsync]
        log.info("running %s", " ".join(cmd))
        subprocess.run(cmd, check=True)

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
