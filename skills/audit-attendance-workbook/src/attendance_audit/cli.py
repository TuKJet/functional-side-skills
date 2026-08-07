from __future__ import annotations

import argparse
import json
import sys

from .workbook import AttendanceAuditError, finalize_analysis, prepare_analysis


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="attendance-audit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare", help="Parse the workbook and create Agent queue files."
    )
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--work-dir", required=True)

    finalize = subparsers.add_parser("finalize", help="Apply deterministic and Agent decisions.")
    finalize.add_argument("--analysis", required=True)
    finalize.add_argument("--decisions")
    finalize.add_argument("--output-dir", required=True)

    run = subparsers.add_parser("run", help="Run end-to-end when no Agent review is needed.")
    run.add_argument("--input", required=True)
    run.add_argument("--work-dir", required=True)
    run.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_analysis(args.input, args.work_dir)
            _print(result)
            return 0
        if args.command == "finalize":
            result = finalize_analysis(args.analysis, args.output_dir, args.decisions)
            _print(result)
            return 0

        prepared = prepare_analysis(args.input, args.work_dir)
        if prepared["agent_review_items"]:
            _print(prepared)
            return 3
        result = finalize_analysis(
            prepared["analysis_file"],
            args.output_dir,
        )
        _print(result)
        return 0
    except AttendanceAuditError as error:
        _print({"status": "error", "error_code": error.code, "message": str(error)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
