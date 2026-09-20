"""Printed callouts drawn on top of the plan.

These renders carry dimension labels ("13'3\" x 13'3\"", "Mech.") in a box whose
grey matches the walls, so every later stage reads them as architecture. They
are found as runs of small dark glyphs on a bright background and treated as
*occluded*: cut out of the silhouette, with the wall network bridged across so a
wall running under a callout stays continuous.
"""

from typing import List, Tuple

import cv2
import numpy as np

from ..config import Config

Box = Tuple[int, int, int, int]


class AnnotationRemover:
    """Locates printed labels and repairs the wall network across them."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    # -- detection ----------------------------------------------------------

    def detect(self, value: np.ndarray, footprint: np.ndarray,
               scale: float) -> np.ndarray:
        """Mask of printed annotation lying on the plan."""
        from ..core.imaging import brightness_mode

        mode = brightness_mode(value, footprint)
        glyphs = self._glyphs(value, footprint, mode, scale)
        lines = self._lines(glyphs)

        mask = np.zeros(footprint.shape, np.uint8)
        height, width = mask.shape
        for group in lines:
            x0 = min(g[0] for g in group)
            y0 = min(g[1] for g in group)
            x1 = max(g[0] + g[2] for g in group)
            y1 = max(g[1] + g[3] for g in group)
            pad = int(round(self.cfg.overlay_pad_frac * max(y1 - y0, 6)))
            cv2.rectangle(mask,
                          (max(0, x0 - pad), max(0, y0 - pad)),
                          (min(width - 1, x1 + pad), min(height - 1, y1 + pad)),
                          1, -1)
        return mask

    def _glyphs(self, value, footprint, mode, scale) -> List[Box]:
        """Small dark blobs sitting on a bright background."""
        bright_floor = max(150, mode - 30)
        dark = ((footprint > 0)
                & (value <= mode - self.cfg.overlay_glyph_contrast)).astype(np.uint8)
        n, _, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)

        height, width = value.shape
        out: List[Box] = []
        for j in range(1, n):
            x, y, w, h, area = stats[j]
            if not 8 <= area <= 1500 or h < 3:
                continue
            if max(h, w) > self.cfg.overlay_glyph_max_frac * scale:
                continue
            ring = value[max(0, y - 4):min(height, y + h + 4),
                         max(0, x - 4):min(width, x + w + 4)]
            if ring.size == 0 or np.median(ring) < bright_floor:
                continue
            out.append((int(x), int(y), int(w), int(h)))
        return out

    def _lines(self, glyphs: List[Box]) -> List[List[Box]]:
        """Cluster glyphs sharing a baseline, a height and a small gap.

        Deliberately conservative - at least four aligned glyphs - so ordinary
        dark furniture detail is not mistaken for text. Two of the three sample
        plans yield zero detections, correctly.
        """
        glyphs = sorted(glyphs, key=lambda g: (g[1], g[0]))
        used = [False] * len(glyphs)
        lines: List[List[Box]] = []

        for i, seed in enumerate(glyphs):
            if used[i]:
                continue
            group, used[i] = [seed], True
            growing = True
            while growing:
                growing = False
                for k, cand in enumerate(glyphs):
                    if used[k]:
                        continue
                    for member in group:
                        ref = max(member[3], 4)
                        if (abs(cand[1] - member[1]) > 0.8 * ref
                                or abs(cand[3] - member[3]) > 0.9 * ref):
                            continue
                        gap = min(abs(cand[0] - (member[0] + member[2])),
                                  abs(member[0] - (cand[0] + cand[2])),
                                  abs(cand[0] - member[0]))
                        if gap <= 2.5 * max(member[3], 6):
                            group.append(cand)
                            used[k] = growing = True
                            break
            if len(group) >= self.cfg.overlay_min_glyphs:
                lines.append(group)
        return lines

    # -- repair -------------------------------------------------------------

    def bridge(self, walls: np.ndarray, annotation: np.ndarray,
               footprint: np.ndarray) -> np.ndarray:
        """Carry the wall network across an occluded strip.

        Dropping the strip outright opens a gap and lets two rooms merge, so a
        wall entering one edge and leaving the opposite one is filled in
        between. The closing uses **line** kernels as long as the strip: a
        callout is printed *along* a wall, hiding a ~150 px run of something
        only ~15 px thick, and a disk large enough to span that would swallow
        doorways. Inpainting was tried first and rejected - it smears floor over
        the wall beneath, which merged a balcony into a bedroom.

        Clipped to the silhouette, so a callout overhanging the page is not
        painted back in as phantom wall.
        """
        if not annotation.any():
            return ((walls > 0) & (footprint > 0)).astype(np.uint8)

        outside = ((walls > 0) & (annotation == 0)).astype(np.uint8)
        repaired = outside.copy()

        n, labelled, stats, _ = cv2.connectedComponentsWithStats(annotation, connectivity=8)
        for j in range(1, n):
            x, y, w, h, _ = stats[j]
            strip = (labelled == j).astype(np.uint8)
            pad = int(round(self.cfg.overlay_bridge_pad_frac * max(w, h)))

            x0, y0 = max(0, x - pad), max(0, y - pad)
            x1 = min(annotation.shape[1], x + w + pad)
            y1 = min(annotation.shape[0], y + h + pad)
            local = outside[y0:y1, x0:x1]
            if not local.any():
                continue

            bridged = np.zeros_like(local)
            for size in ((w + 2 * pad, 1), (1, h + 2 * pad)):
                kernel = cv2.getStructuringElement(
                    cv2.MORPH_RECT, (max(1, size[0]), max(1, size[1])))
                bridged |= cv2.morphologyEx(local, cv2.MORPH_CLOSE, kernel)

            repaired[y0:y1, x0:x1] |= bridged & strip[y0:y1, x0:x1]

        return ((repaired > 0) & (footprint > 0)).astype(np.uint8)
