#!/usr/bin/env python3
"""Project entry point.

    python main.py --input data/input_images --output data/outputs
    python main.py --serve
"""

import sys

from floorplan.cli import main

if __name__ == "__main__":
    sys.exit(main())
