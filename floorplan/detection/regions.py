"""Turning the free space between walls into labelled rooms."""

from dataclasses import dataclass, field
from typing import List

import cv2
import numpy as np

from ..config import Config
from ..core.imaging import disk


@dataclass
class RegionSet:
    """Labelled rooms, and what was filtered out on the way."""

    labels: np.ndarray                          # int32, 0 = not a room
    ids: List[int]
    rejected: List[dict] = field(default_factory=list)


class RegionExtractor:
    """Free space -> seeds -> geodesic growth -> shape filtering."""

    def __init__(self, plan):
        self.plan = plan
        self.cfg: Config = plan.cfg

    def extract(self, barrier: np.ndarray) -> RegionSet:
        free = self._free_space(barrier)
        seeds = self._cores(free)
        labels = self.grow(free, seeds)
        kept, rejected = self._filter(labels)
        return RegionSet(labels=labels, ids=kept, rejected=rejected)

    def _free_space(self, barrier: np.ndarray) -> np.ndarray:
        """Everything inside the silhouette that is not wall.

        Furniture stays in: it sits *inside* a room, so keeping it keeps the
        room whole. Only structure removes area.
        """
        free = ((self.plan.footprint > 0) & (barrier == 0)).astype(np.uint8)
        return cv2.morphologyEx(free, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    def _cores(self, free: np.ndarray) -> np.ndarray:
        """One seed per room.

        Rooms connect through doorways, so plain connected components merge
        them. Opening with a disk wider than a door erases those necks. Rooms
        narrower than the disk vanish too, so any free component left without a
        core is re-seeded with itself.
        """
        area = self.plan.area
        cores = cv2.morphologyEx(
            free, cv2.MORPH_OPEN, disk(self.plan.fraction(self.cfg.door_sever_frac, 3)))

        n, comp, stats, _ = cv2.connectedComponentsWithStats(cores, connectivity=4)
        labels = np.zeros(comp.shape, np.int32)
        next_id = 0
        for j in range(1, n):
            if stats[j, cv2.CC_STAT_AREA] < self.cfg.core_min_area_frac * area:
                continue
            next_id += 1
            labels[comp == j] = next_id

        nf, fcomp, fstats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
        for j in range(1, nf):
            if fstats[j, cv2.CC_STAT_AREA] < self.cfg.region_min_area_frac * area:
                continue
            piece = fcomp == j
            if labels[piece].max() == 0:
                next_id += 1
                labels[piece] = next_id
        return labels

    @staticmethod
    def grow(region: np.ndarray, seeds: np.ndarray) -> np.ndarray:
        """Expand seeds geodesically until ``region`` is covered.

        Iterated 3x3 dilation clipped to the region: growth follows the free
        space rather than straight-line distance, so a label cannot jump a wall
        and two labels meet on the neck separating them.
        """
        labels = seeds.astype(np.uint16)
        kernel = np.ones((3, 3), np.uint8)
        inside = region > 0
        while True:
            unlabelled = inside & (labels == 0)
            if not unlabelled.any():
                break
            grown = cv2.dilate(labels, kernel)
            nxt = np.where(unlabelled & (grown > 0), grown, labels).astype(np.uint16)
            if np.array_equal(nxt, labels):
                break                      # the rest is unreachable from any seed
            labels = nxt
        labels[~inside] = 0
        return labels.astype(np.int32)

    def _filter(self, labels: np.ndarray):
        area = self.plan.area
        min_area = self.cfg.region_min_area_frac * area
        min_radius = self.cfg.min_inscribed_frac * self.plan.scale

        kept: List[int] = []
        rejected: List[dict] = []
        for rid in np.unique(labels):
            if rid == 0:
                continue
            mask = labels == rid
            px = int(mask.sum())
            if px < min_area:
                rejected.append({"id": int(rid), "area_px": px,
                                 "reason": "below_min_area"})
                labels[mask] = 0
                continue
            radius = float(cv2.distanceTransform(
                mask.astype(np.uint8), cv2.DIST_L2, 5).max())
            if radius < min_radius:
                # Thin slivers are the outer faces of perimeter walls, which the
                # 3D projection exposes inside the silhouette. Not rooms.
                rejected.append({"id": int(rid), "area_px": px,
                                 "inscribed_radius_px": round(radius, 1),
                                 "reason": "too_thin"})
                labels[mask] = 0
                continue
            kept.append(int(rid))
        return kept, rejected

    # -- built-ins and door leaves ------------------------------------------

    def reclaim_fixtures(self, labels: np.ndarray, barrier: np.ndarray) -> np.ndarray:
        """Give non-structural white shapes back to the room they stand in.

        Built-in joinery and **open door leaves** are rendered in the wall's own
        white and touch one, so they join the barrier and bite notches out of
        rooms - wrong for area (floor under a tub is still bathroom floor) and
        the main source of ragged outlines.

        Thickness cannot separate them: a door leaf and an interior partition
        measure 2.0 px and 3.0 px half-width, so any blur or erosion deleting
        the door destroys the wall.

        What does separate them is what a wall is *for*: a wall pixel is
        structural if it lies on the surface separating two territories. Labels
        are grown over the whole image - with the exterior seeded as its own
        territory, so perimeter walls have a boundary through them too - and the
        test is anchored on that surface: a pixel is structural if it lies no
        further from it than the wall's radius *there*, plus a margin. A wall's
        cross-section passes; a door leaf's far end, tens of pixels from the
        doorway it hinges on, does not.
        """
        plan, cfg = self.plan, self.cfg
        exterior = int(labels.max()) + 1
        seeds = labels.copy()
        seeds[plan.footprint == 0] = exterior
        territory = self.grow(np.ones_like(plan.footprint), seeds)

        lab = territory.astype(np.uint16)
        kernel = np.ones((3, 3), np.uint8)
        highest = cv2.dilate(lab, kernel)
        lowest = cv2.erode(np.where(lab == 0, np.uint16(65535), lab), kernel)
        partition = ((highest != lowest) & (territory > 0) & (barrier > 0)).astype(np.uint8)
        if not partition.any():
            return labels

        radius = cv2.distanceTransform(barrier, cv2.DIST_L2, 5)
        source = np.where(partition > 0, 0, 255).astype(np.uint8)
        away, nearest = cv2.distanceTransformWithLabels(
            source, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)

        ys, xs = np.nonzero(partition)
        # Capped: distance is measured on a barrier that still contains the
        # fixture, so a panel welded to a wall makes that wall measure thick and
        # the inflated radius would shelter the fixture that caused it.
        cap = float(np.percentile(radius[ys, xs], cfg.fixture_radius_percentile))
        radius_at = np.zeros(int(nearest.max()) + 1, np.float32)
        radius_at[nearest[ys, xs]] = np.minimum(radius[ys, xs], cap)
        reach = np.minimum(radius_at[nearest] + cfg.fixture_margin_frac * plan.scale,
                           cfg.fixture_max_reach_frac * plan.scale)

        # Never reclaim into the exterior: the outer face of a perimeter wall
        # must not be handed to the room behind it.
        candidate = ((barrier > 0) & (away > reach)
                     & (territory > 0) & (territory != exterior)).astype(np.uint8)

        out = labels.copy()
        n, comp, stats, _ = cv2.connectedComponentsWithStats(candidate, connectivity=8)
        for j in range(1, n):
            if stats[j, cv2.CC_STAT_AREA] < cfg.fixture_min_area_frac * plan.area:
                continue
            piece = comp == j
            out[piece] = territory[piece]
        return out


def open_boundary_ratio(mask: np.ndarray, footprint: np.ndarray) -> float:
    """Share of a region's outline facing the page instead of a wall.

    ~0 for an enclosed room; high for a balcony or patio.
    """
    outline = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_GRADIENT,
                               np.ones((3, 3), np.uint8)) > 0
    if not outline.any():
        return 0.0
    outside = cv2.dilate((footprint == 0).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    return float((outline & outside).sum()) / float(outline.sum())
