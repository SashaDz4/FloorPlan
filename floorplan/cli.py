"""Command-line entry point.

With no arguments this starts the local UI, which is the normal way to use the
tool. `--batch` runs every image through the pipeline and writes the files out
instead.
"""

import argparse
import sys
from pathlib import Path

from .analyzer import FloorPlanAnalyzer
from .config import Config
from .core.imaging import list_images


def build_parser() -> argparse.ArgumentParser:
    cfg = Config()
    p = argparse.ArgumentParser(
        prog="floorplan",
        description="Approximate a 2D room layout from a 3D floor-plan render. "
                    "Starts the local UI unless --batch is given.")
    p.add_argument("--input", default="data/input_images",
                   help="image file or directory of images")

    ui = p.add_argument_group("UI (default)")
    ui.add_argument("--host", default="127.0.0.1",
                    help="interface to bind; 0.0.0.0 inside Docker")
    ui.add_argument("--port", type=int, default=8000, help="port to bind")

    batch = p.add_argument_group("batch (--batch)")
    batch.add_argument("--batch", action="store_true",
                       help="analyse every input and write files, no UI")
    batch.add_argument("--output", default="data/outputs", help="output directory")
    batch.add_argument("--total-area-sqft", type=float, default=None,
                       help="optional calibration: known total floor area, used "
                            "to convert pixel areas into square feet")
    batch.add_argument("--door-sever-frac", type=float, default=cfg.door_sever_frac,
                       help="door-cutting radius / sqrt(footprint area); "
                            "default %.3f" % cfg.door_sever_frac)
    batch.add_argument("--wall-delta", type=int, default=cfg.wall_delta,
                       help="wall threshold below the brightness mode; default %d"
                            % cfg.wall_delta)
    batch.add_argument("--min-area-frac", type=float,
                       default=cfg.region_min_area_frac,
                       help="drop regions below this share of the footprint; "
                            "default %.3f" % cfg.region_min_area_frac)
    return p


def run_batch(args) -> int:
    analyzer = FloorPlanAnalyzer(Config(
        door_sever_frac=args.door_sever_frac,
        wall_delta=args.wall_delta,
        region_min_area_frac=args.min_area_frac))

    out_dir = Path(args.output)
    for image_path in list_images(Path(args.input)):
        analysis = analyzer.analyse(image_path, args.total_area_sqft)
        paths = analysis.write(out_dir)
        report = analysis.report

        print("%s: %d regions, %d px of room area"
              % (image_path.name, report["room_count"],
                 report["total_room_area_px"]))
        for room in analysis.rooms:
            flag = "" if room.is_enclosed else "   [open to exterior]"
            print("    %s: %7d px  %5.1f%%  %d-gon%s"
                  % (room.name, room.area_px, room.relative_area * 100,
                     len(room.polygon_px), flag))
        print("    -> " + ", ".join(p.name for p in paths.values()))
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.batch:
        return run_batch(args)

    from .ui.server import serve
    return serve(args.host, args.port, Path(args.input))


if __name__ == "__main__":
    sys.exit(main())
