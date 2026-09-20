"""The render and everything derived from it, computed once."""

from functools import cached_property
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from ..config import Config
from .imaging import (brightness_mode, fill_holes, load_bgr,
                      normalise_polarity, to_lab)

# Given (value, footprint, scale), return a mask of pixels to leave out of the
# silhouette. Injected rather than imported so `core` stays underneath
# `detection` instead of reaching back into it.
AnnotationFinder = Callable[[np.ndarray, np.ndarray, float], np.ndarray]


class Plan:
    """A floor-plan render plus its derived views.

    Exists because the stages otherwise recompute the same things: the HSV and
    Lab conversions, the brightness mode, the wall's own b*, and the plan scale
    were each being rebuilt three to five times a run and threaded through every
    signature as extra arguments.

    Note the two footprints. ``raw_footprint`` is the silhouette as rendered,
    and is what callout detection must run against; ``footprint`` is that with
    the callouts cut out, and is what every later stage measures.
    """

    def __init__(self, path: Path, cfg: Config,
                 annotation_finder: Optional[AnnotationFinder] = None):
        self.path = path
        self.cfg = cfg
        # A dark-page export is flipped back here, once, so every stage below
        # can keep assuming a white page and bright walls.
        self.bgr, self.inverted = normalise_polarity(load_bgr(path))
        self.height, self.width = self.bgr.shape[:2]
        self._find_annotation = annotation_finder

    # -- colour views -------------------------------------------------------

    @cached_property
    def hsv(self) -> np.ndarray:
        return cv2.cvtColor(self.bgr, cv2.COLOR_BGR2HSV)

    @cached_property
    def value(self) -> np.ndarray:
        return self.hsv[:, :, 2]

    @cached_property
    def saturation(self) -> np.ndarray:
        return self.hsv[:, :, 1]

    @cached_property
    def lab(self) -> np.ndarray:
        return to_lab(self.bgr)

    @cached_property
    def page(self) -> np.ndarray:
        """Pixels the colour of the page it is printed on."""
        return ((self.value >= self.cfg.bg_value_min)
                & (self.saturation <= self.cfg.bg_sat_max))

    # -- silhouette ---------------------------------------------------------

    @cached_property
    def raw_footprint(self) -> np.ndarray:
        """The apartment silhouette, callouts included.

        Background is the white reachable from the image border; the apartment
        is the largest remaining component, which drops the dimension labels,
        the disclaimer and the entry arrow. 4-connectivity matters: with 8, a
        diagonal seam of white lets the page bleed through a corner contact.
        """
        page = self.page.astype(np.uint8)
        _, labels = cv2.connectedComponents(page, connectivity=4)
        border = (set(labels[0, :]) | set(labels[-1, :])
                  | set(labels[:, 0]) | set(labels[:, -1]))
        border.discard(0)

        foreground = (~np.isin(labels, list(border))).astype(np.uint8)
        n, comp, stats, _ = cv2.connectedComponentsWithStats(foreground, connectivity=8)
        if n <= 1:
            raise ValueError("no foreground found; is the image a plain white page?")
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        return fill_holes((comp == largest).astype(np.uint8))

    @cached_property
    def annotation(self) -> np.ndarray:
        """Pixels excluded from the silhouette - printed callouts, in practice.

        What counts as annotation is not this class's business; a finder is
        handed in. With none, the plan is taken exactly as rendered.
        """
        if self._find_annotation is None:
            return np.zeros(self.raw_footprint.shape, np.uint8)
        return self._find_annotation(self.value, self.raw_footprint,
                                     self.scale_of(self.raw_footprint))

    @cached_property
    def footprint(self) -> np.ndarray:
        """Silhouette with callouts cut out.

        Cutting drops the part of a callout that overhangs the page, while hole
        filling restores the part lying inside the building.
        """
        if not self.annotation.any():
            return self.raw_footprint
        kept = ((self.raw_footprint > 0) & (self.annotation == 0)).astype(np.uint8)
        return fill_holes(kept)

    # -- measurements -------------------------------------------------------

    @staticmethod
    def scale_of(footprint: np.ndarray) -> float:
        return float(np.sqrt(max(int(footprint.sum()), 1)))

    @cached_property
    def scale(self) -> float:
        return self.scale_of(self.footprint)

    @cached_property
    def area(self) -> int:
        return int(self.footprint.sum())

    @cached_property
    def brightness_mode(self) -> int:
        return brightness_mode(self.value, self.footprint)

    @cached_property
    def wall_b(self) -> float:
        """The wall's own b*, sampled from a band inside the footprint contour.

        That band is perimeter wall by construction, so unlike any
        brightness-derived mask it is uncontaminated by floor. The brighter half
        is used, to skip railings, planters and deeply shaded outer faces.
        """
        width = max(3, int(round(self.cfg.wall_band_frac * self.scale)))
        inner = cv2.erode(self.footprint, np.ones((3, 3), np.uint8), iterations=width)
        band = (self.footprint > 0) & (inner == 0)
        if not band.any():
            return float(np.median(self.lab[:, :, 2][self.footprint > 0]))
        values = self.lab[band]
        lit = values[values[:, 0] >= np.median(values[:, 0])]
        return float(np.median(lit[:, 2]))

    def fraction(self, frac: float, minimum: int = 1) -> int:
        """A scale-relative length, in pixels."""
        return max(minimum, int(round(frac * self.scale)))
