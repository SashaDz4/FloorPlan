#!/usr/bin/env python3
"""Project entry point.

    python main.py                       # local UI on http://127.0.0.1:8000
    python main.py --batch               # analyse everything, write files
"""

import sys

from floorplan.cli import main

if __name__ == "__main__":
    sys.exit(main())
