"""Approximating a region mask with a simplified polygon."""

from typing import Dict, List

import cv2
import numpy as np

from ..config import Config
from ..core.imaging import disk


def interior_angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Angle at b, in degrees, between ba and bc."""
    v1, v2 = a - b, c - b
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 == 0 or n2 == 0:
        return 180.0
    return float(np.degrees(np.arccos(
        np.clip(float(np.dot(v1, v2)) / (n1 * n2), -1.0, 1.0))))


class PolygonBuilder:
    """Traces and cleans up the outline of a region."""

    def __init__(self, cfg: Config, scale: float):
        self.cfg = cfg
        self.scale = scale

    def build(self, mask: np.ndarray) -> Dict:
        """Outer polygon plus any significant interior holes.

        A closing pass first removes the bite marks white furniture standing
        against a wall leaves, so the outline follows the room, not the sofa.
        """
        smooth = cv2.morphologyEx(
            mask.astype(np.uint8), cv2.MORPH_CLOSE,
            disk(int(round(self.cfg.poly_smooth_frac * self.scale))))

        contours, hierarchy = cv2.findContours(
            smooth, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return {"polygon_px": [], "holes_px": [], "polygon_area_px": 0}

        hierarchy = hierarchy[0]
        outer_idx = max((i for i in range(len(contours)) if hierarchy[i][3] == -1),
                        key=lambda i: cv2.contourArea(contours[i]))
        outer = self._simplify(contours[outer_idx])
        outer_area = abs(cv2.contourArea(contours[outer_idx]))

        holes = []
        for i in range(len(contours)):
            if hierarchy[i][3] != outer_idx:
                continue
            hole_area = abs(cv2.contourArea(contours[i]))
            if hole_area < self.cfg.hole_min_area_frac * max(outer_area, 1.0):
                continue
            poly = self._simplify(contours[i])
            if len(poly) >= 3:
                holes.append(poly)
                outer_area -= hole_area

        return {"polygon_px": outer, "holes_px": holes,
                "polygon_area_px": int(round(outer_area))}

    def _simplify(self, contour: np.ndarray) -> List[List[int]]:
        eps = self.cfg.poly_eps_frac * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, max(eps, 1.0), True)
        pts = np.array([[p[0][0], p[0][1]] for p in approx], np.float64)
        if len(pts) > 3:
            pts = self._regularise(pts)
        return [[int(p[0]), int(p[1])] for p in pts]

    def _regularise(self, points: np.ndarray) -> np.ndarray:
        """Clean up a traced outline.

        approxPolyDP follows whatever the mask does, and the mask stays noisy
        where white joinery met a wall - railing slats leave a comb, a bath
        panel a notch - which shows up as needle spikes and runs of collinear
        micro-edges. Three local rules run until the outline stops changing.

        No orthogonality is imposed: these plans are in perspective and their
        perimeter walls genuinely splay, so snapping to axes would move real
        corners rather than tidy them.
        """
        pts = [np.asarray(p, np.float64) for p in points]
        min_edge = self.cfg.poly_min_edge_frac * self.scale

        changed = True
        while changed and len(pts) > 3:
            changed = False
            n = len(pts)
            angles = [interior_angle(pts[(i - 1) % n], pts[i], pts[(i + 1) % n])
                      for i in range(n)]

            tightest = int(np.argmin(angles))
            if angles[tightest] < self.cfg.poly_spike_deg:
                del pts[tightest]
                changed = True
                continue

            straightest = int(np.argmax(angles))
            if angles[straightest] > 180.0 - self.cfg.poly_collinear_deg:
                del pts[straightest]
                changed = True
                continue

            lengths = [np.linalg.norm(pts[(i + 1) % n] - pts[i]) for i in range(n)]
            shortest = int(np.argmin(lengths))
            if lengths[shortest] < min_edge:
                j = (shortest + 1) % n
                pts[shortest] = (pts[shortest] + pts[j]) / 2.0
                del pts[j]
                changed = True

        return np.array([[int(round(p[0])), int(round(p[1]))] for p in pts], np.int32)
