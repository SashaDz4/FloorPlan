"""End-to-end analysis: one render in, rooms and an overlay out."""

import json
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np

from .detection.annotations import AnnotationRemover
from .config import Config
from .core.plan import Plan
from .detection.polygons import PolygonBuilder
from .detection.regions import RegionExtractor, open_boundary_ratio
from .rendering.overlay import Annotator
from .core.room import Room
from .detection.walls import WallDetector


class Analysis:
    """The result of analysing one plan, and how to write it out."""

    def __init__(self, plan: Plan, rooms: List[Room], labels: np.ndarray,
                 barrier: np.ndarray, wall_tops: np.ndarray,
                 report: Dict):
        self.plan = plan
        self.rooms = rooms
        self.labels = labels
        self.barrier = barrier
        self.wall_tops = wall_tops
        self.report = report

    @property
    def annotated(self) -> np.ndarray:
        return Annotator(self.plan.bgr, self.rooms).render(self.labels, self.wall_tops)

    def write(self, out_dir: Path) -> Dict[str, Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = self.plan.path.stem
        paths = {"annotated": out_dir / f"{stem}__annotated.png",
                 "json": out_dir / f"{stem}__rooms.json"}

        cv2.imwrite(str(paths["annotated"]), self.annotated)
        paths["json"].write_text(json.dumps(self.report, indent=2), encoding="utf-8")
        return paths


class FloorPlanAnalyzer:
    """Runs the stages in order and assembles the report."""

    def __init__(self, cfg: Optional[Config] = None):
        self.cfg = cfg or Config()

    def analyse(self, image_path: Path,
                total_area_sqft: Optional[float] = None) -> Analysis:
        callouts = AnnotationRemover(self.cfg)
        plan = Plan(image_path, self.cfg, callouts.detect)

        walls = WallDetector(plan)
        barrier = callouts.bridge(walls.barrier(), plan.annotation, plan.footprint)

        extractor = RegionExtractor(plan)
        regions = extractor.extract(barrier)
        labels = extractor.reclaim_fixtures(regions.labels, barrier)

        # Reclaimed pixels are room now, so they leave the wall mask - otherwise
        # wall_area_px would count them twice.
        barrier = ((barrier > 0) & (labels == 0)).astype(np.uint8)
        wall_tops = walls.tops(barrier)

        rooms = self._build_rooms(plan, labels, regions.ids)
        self._measure(plan, rooms, total_area_sqft)
        report = self._report(plan, rooms, barrier, wall_tops, regions.rejected,
                              total_area_sqft)
        return Analysis(plan, rooms, labels, barrier, wall_tops, report)

    def _build_rooms(self, plan: Plan, labels: np.ndarray,
                     ids: List[int]) -> List[Room]:
        polygons = PolygonBuilder(self.cfg, plan.scale)
        rooms = []
        for rid in sorted(ids, key=lambda r: -int((labels == r).sum())):
            mask = (labels == rid).astype(np.uint8)
            room = Room.from_mask(rid, mask, open_boundary_ratio(mask, plan.footprint))
            geometry = polygons.build(mask)
            room.polygon_px = geometry["polygon_px"]
            room.holes_px = geometry["holes_px"]
            room.polygon_area_px = geometry["polygon_area_px"]
            room.name = f"room_{len(rooms) + 1:02d}"
            rooms.append(room)
        return rooms

    @staticmethod
    def _measure(plan: Plan, rooms: List[Room],
                 total_area_sqft: Optional[float]) -> None:
        total = sum(r.area_px for r in rooms)
        for room in rooms:
            room.relative_area = round(room.area_px / total, 4) if total else 0.0
            room.relative_area_of_footprint = round(room.area_px / plan.area, 4)

        # Calibrate on enclosed rooms only: a published floor area is interior,
        # so counting a balcony in the denominator shrinks every estimate.
        enclosed = sum(r.area_px for r in rooms if r.is_enclosed)
        if total_area_sqft and enclosed:
            for room in rooms:
                room.area_sqft_estimate = round(
                    total_area_sqft * room.area_px / enclosed, 1)

    @staticmethod
    def _report(plan: Plan, rooms: List[Room], barrier, wall_tops,
                rejected, total_area_sqft) -> Dict:
        total = sum(r.area_px for r in rooms)
        enclosed = sum(r.area_px for r in rooms if r.is_enclosed)
        calibration = None
        if total_area_sqft and enclosed:
            calibration = {"total_area_sqft": total_area_sqft,
                           "basis": "enclosed rooms only",
                           "enclosed_area_px": enclosed,
                           "sqft_per_px": round(total_area_sqft / enclosed, 6)}

        return {
            "image": plan.path.name,
            "image_size": {"width": plan.width, "height": plan.height},
            "units": "pixels",
            "wall_brightness_mode": plan.brightness_mode,
            "polarity_inverted": plan.inverted,
            "annotation_removed_px": int(plan.annotation.sum()),
            "footprint_area_px": plan.area,
            "wall_area_px": int(wall_tops.sum()),
            "wall_barrier_area_px": int(barrier.sum()),
            "total_room_area_px": total,
            "room_count": len(rooms),
            "calibration": calibration,
            "rejected_regions": rejected,
            "config": plan.cfg.to_dict(),
            "rooms": [r.to_dict() for r in rooms],
        }
