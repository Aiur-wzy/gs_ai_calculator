"""Frozen entry point for the external release-pair updater.

This executable is deliberately built separately from the active App tree so
that it can replace that tree after the GUI exits.
"""

from genshin_app.release_pair_helper import main

if __name__ == "__main__":
    raise SystemExit(main())
