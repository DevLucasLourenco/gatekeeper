"""Entry point used by the plugin hooks: `python run.py hook <event>`.

The hook path skips argparse and the CLI module: it runs on every tool call.
"""

import sys

if sys.version_info < (3, 13):
    print(f"gatekeeper: needs Python 3.13+, found {sys.version.split()[0]}; gates are not enforced.", file=sys.stderr)
    raise SystemExit(0)

from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

if len(sys.argv) == 3 and sys.argv[1] == "hook":
    from gatekeeper.hooks import run_from_stdin  # noqa: E402

    raise SystemExit(run_from_stdin(sys.argv[2]))

from gatekeeper.cli import main  # noqa: E402

raise SystemExit(main())
