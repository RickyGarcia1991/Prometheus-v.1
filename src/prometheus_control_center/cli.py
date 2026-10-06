from __future__ import annotations

import argparse
from pathlib import Path

from .archive import create_snapshot, verify_snapshot
from .config import PrometheusConfig
from .health import run_checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prometheusctl", description="Prometheus control and verification utility")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create runtime and archive directories")
    sub.add_parser("doctor", help="Run local health checks")

    snap = sub.add_parser("snapshot", help="Create a checksummed project snapshot")
    snap.add_argument("source", type=Path)
    snap.add_argument("--label", default="prometheus")

    verify = sub.add_parser("verify", help="Verify an archive against its embedded manifest")
    verify.add_argument("archive", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = PrometheusConfig.from_env()
    if args.command == "init":
        config.ensure_directories()
        print(f"Initialized home={config.home} archives={config.archive_dir}")
        return 0
    if args.command == "doctor":
        checks = run_checks(config)
        for check in checks:
            print(f"{'PASS' if check.ok else 'FAIL'} {check.name}: {check.detail}")
        return 0 if all(check.ok for check in checks) else 1
    if args.command == "snapshot":
        archive, checksum = create_snapshot(args.source, config.archive_dir, args.label)
        print(archive)
        print(checksum)
        return 0
    if args.command == "verify":
        errors = verify_snapshot(args.archive)
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        print("PASS archive verified")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
