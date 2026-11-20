"""Entrypoint for ATRO (Desktop GUI default, CLI when arguments are passed)."""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    multiprocessing.freeze_support()

    if len(sys.argv) > 1:
        from atro.cli import main as cli_main

        return cli_main(sys.argv[1:])

    from atro.ui.main_window import launch_gui

    return launch_gui()


if __name__ == "__main__":
    raise SystemExit(main())
