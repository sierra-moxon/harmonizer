"""CLI entry point: run the mapping loop for a prepared job dir.

Backs ``just loop JOB_DIR``. The job dir must already contain the pre-pass
sidecars (run ``just prepass`` first). Requires provider credentials in the
environment (e.g. ``ANTHROPIC_API_KEY``) so the real agent can run.
"""

from __future__ import annotations

import argparse
import asyncio

from harmonizer.database.session import init_db
from harmonizer.orchestrator.loop import run_mapping_async


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the mapping loop.")
    parser.add_argument("job_dir", help="a job dir prepared by the pre-pass")
    args = parser.parse_args(argv)

    init_db()
    state = asyncio.run(run_mapping_async(args.job_dir))
    remaining = state.remaining_placeholders()
    print(f"job {state.job_id}: status={state.status.value}")
    print(
        f"  {len(state.placeholders)} placeholder(s); "
        f"{len(remaining)} still awaiting resolution; "
        f"iterations run through {state.current_iteration}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
