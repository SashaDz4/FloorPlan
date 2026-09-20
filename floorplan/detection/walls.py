"""Wall detection.

Two masks come out of this, and the difference matters:

* the **barrier**, deliberately generous because it must not develop holes or
  rooms leak into each other;
* the **tops**, what gets reported and drawn - walls seen from above are a flat
  plateau at the brightness mode, while the side faces the 3D view exposes fall
  away from it and have no place in a 2D plan.
"""

import cv2
import numpy as np

from ..config import Config
from ..core.imaging import disk, reconstruct


class WallDetector:
    """Builds the wall barrier and the wall-top mask for one plan."""

    def __init__(self, plan):
        self.plan = plan
        self.cfg: Config = plan.cfg

    # -- barrier ------------------------------------------------------------

    def candidates(self) -> np.ndarray:
        """Bright, near-neutral pixels of the wall's own colour.

        Brightness alone carries the plans whose walls are neutral white, where
        several floors share the wall's hue and only lightness separates them.
        It fails where a bathroom floor is near-white tile at wall lightness, so
        a b* veto is applied on top - walls there hold b*=+6 lit or shaded,
        against +2 on the tile. Only b*: a* is near-identical on walls and
        floors, so including it in a colour distance adds pure noise.

        The veto is applied per *region*, not per pixel. Pixel-wise it also
        deletes the scattered wall pixels that shading and bounce light push
        off-colour, which punches holes through walls and merges rooms.
        """
        plan = self.plan
        thr = plan.brightness_mode - self.cfg.wall_delta
        mask = ((plan.footprint > 0) & (plan.value >= thr)
                & (plan.saturation <= self.cfg.wall_sat_max)).astype(np.uint8)

        # See-through openings are not wall: a balcony railing shows the page
        # between its balusters, and hole filling closed those gaps into the
        # silhouette at page white, above the wall's own grey.
        if thr < self.cfg.bg_value_min - 4:
            mask = ((mask > 0) & ~plan.page).astype(np.uint8)

        k = np.ones((self.cfg.wall_close_px,) * 2, np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)

        off_colour = ((mask > 0)
                      & (np.abs(plan.lab[:, :, 2] - plan.wall_b)
                         > self.cfg.wall_b_tolerance)).astype(np.uint8)
        seed = cv2.morphologyEx(
            off_colour, cv2.MORPH_OPEN,
            disk(plan.fraction(self.cfg.wall_offcolour_min_frac, 2)))
        if seed.any():
            mask = ((mask > 0) & (reconstruct(seed, off_colour) == 0)).astype(np.uint8)
        return mask

    @staticmethod
    def network(candidates: np.ndarray) -> np.ndarray:
        """Largest connected component of the candidates.

        Every partition meets the perimeter wall, so the true structure is one
        component - 73-91% of candidate pixels on the samples. Bright objects
        floating inside a room (a white rug, a duvet) form their own components
        and would otherwise cut that room into pieces.
        """
        n, comp, stats, _ = cv2.connectedComponentsWithStats(candidates, connectivity=8)
        if n <= 1:
            return np.zeros_like(candidates)
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        return (comp == largest).astype(np.uint8)

    def barrier(self) -> np.ndarray:
        return self.network(self.candidates())

    # -- tops ---------------------------------------------------------------

    def top_band(self, tolerance: int = None) -> np.ndarray:
        """Pixels on a wall's flat top, as opposed to its side face.

        A one-sided threshold cannot separate the two; a two-sided band around
        the brightness mode can.
        """
        if tolerance is None:
            tolerance = self.cfg.wall_top_tolerance
        plan = self.plan
        band = ((plan.footprint > 0)
                & (np.abs(plan.value.astype(np.int16) - plan.brightness_mode) <= tolerance)
                & (plan.saturation <= self.cfg.wall_sat_max)
                & (np.abs(plan.lab[:, :, 2] - plan.wall_b) <= self.cfg.wall_b_tolerance)
                & (~plan.page)).astype(np.uint8)
        return cv2.morphologyEx(band, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    def tops(self, barrier: np.ndarray) -> np.ndarray:
        return ((barrier > 0) & (self.top_band() > 0)).astype(np.uint8)
