"""Inspect or explicitly release a dependency hold; never requeue failed tasks."""

import argparse
import json

from youmo_workflow.config import get_settings
from youmo_workflow.domain.time import now_utc
from youmo_workflow.features.pipeline_gates import SCOPES, Scope, active_holds, release_hold
from youmo_workflow.runtime import build_runtime


def parse_scope(value: str) -> Scope:
    for scope in SCOPES:
        if scope == value:
            return scope
    raise argparse.ArgumentTypeError("Unknown dependency scope")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", type=parse_scope)
    parser.add_argument("--reason", default="")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    runtime = build_runtime(get_settings())
    with runtime.factory.begin() as db:
        if args.resume:
            if not args.confirm or not args.reason.strip():
                parser.error("Explicit --confirm and --reason required after diagnosis")
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            release_hold(db, args.resume, args.reason, now_utc())
        print(json.dumps([hold.model_dump() for hold in active_holds(db)]))


if __name__ == "__main__":
    main()
