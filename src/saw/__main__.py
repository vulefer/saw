"""Allows running the CLI as ``python -m saw``."""

import sys

from saw.cli import main

if __name__ == "__main__":
    sys.exit(main())
