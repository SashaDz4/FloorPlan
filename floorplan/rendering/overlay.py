"""Annotated overlays."""

from typing import List

import cv2
import numpy as np

from ..core.room import Room

# Distinct, reasonably colour-blind-friendly hues, in BGR. Every entry is
# saturated: rooms are colour, structure is neutral, so the two never trade
# places. The two greys the original palette carried were dropped for that
# reason - a grey room next to a grey wall is exactly the ambiguity to avoid.
PALETTE = [
    (180, 119, 31), (14, 127, 255), (44, 160, 44), (40, 39, 214),
    (189, 103, 148), (75, 86, 140), (194, 119, 227), (0, 165, 255),
    (34, 189, 188), (207, 190, 23), (138, 223, 152), (120, 187, 255),
    (150, 152, 255), (139, 61, 72), (210, 182, 247), (137, 218, 219),
]

# Walls: dark and neutral against the render's light grey, and drawn at a
# stronger alpha than the rooms. The previous slate at 0.45 moved the wall
# pixels by only ~90 units of BGR distance and was easy to miss; this moves
# them by ~155.
WALL_TINT = (58, 52, 48)
WALL_ALPHA = 0.55


def _legend_box(img, entries) -> None:
    """White panel listing colour/label pairs, top-left."""
    if not entries:
        return
    font, sc, th = cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1
    pad, sw, line = 10, 22, 22
    width = max(cv2.getTextSize(t, font, sc, th)[0][0]
                for _, t in entries) + sw + 3 * pad
    height = line * len(entries) + 2 * pad
    x0, y0 = 12, 12

    cv2.rectangle(img, (x0, y0), (x0 + width, y0 + height), (255, 255, 255), -1)
    cv2.rectangle(img, (x0, y0), (x0 + width, y0 + height), (170, 170, 170), 1)
    for i, (col, text) in enumerate(entries):
        cy = y0 + pad + i * line + line // 2
        cv2.rectangle(img, (x0 + pad, cy - 7), (x0 + pad + sw, cy + 7), col, -1)
        cv2.rectangle(img, (x0 + pad, cy - 7), (x0 + pad + sw, cy + 7),
                      (120, 120, 120), 1)
        cv2.putText(img, text, (x0 + pad + sw + 8, cy + 5), font, sc,
                    (40, 40, 40), th, cv2.LINE_AA)


class Annotator:
    """Draws the room result over the render.

    Holds the canvas and palette so the drawing helpers do not have to pass an
    image, a colour and a room around between themselves.
    """

    def __init__(self, bgr: np.ndarray, rooms: List[Room], alpha: float = 0.45):
        self.bgr = bgr
        self.rooms = rooms
        self.alpha = alpha

    @staticmethod
    def colour(index: int):
        return PALETTE[index % len(PALETTE)]

    def render(self, labels: np.ndarray, walls: np.ndarray = None) -> np.ndarray:
        """Translucent fills, outlines and captions.

        Walls are tinted too and named in the legend: they belong to no room, so
        leaving them bare made the plan look full of unexplained gaps.
        """
        overlay = np.zeros_like(self.bgr)
        room_px = labels > 0
        for i, room in enumerate(self.rooms):
            overlay[labels == room.label] = self.colour(i)

        out = np.where(room_px[:, :, None],
                       cv2.addWeighted(self.bgr, 1 - self.alpha, overlay, self.alpha, 0),
                       self.bgr).copy()

        # Walls blend separately, at their own alpha.
        if walls is not None:
            wall_px = (walls > 0) & (labels == 0)
            wall_layer = np.zeros_like(self.bgr)
            wall_layer[wall_px] = WALL_TINT
            blended = cv2.addWeighted(self.bgr, 1 - WALL_ALPHA, wall_layer, WALL_ALPHA, 0)
            out[wall_px] = blended[wall_px]

        for i, room in enumerate(self.rooms):
            col = self.colour(i)
            poly = np.array(room.polygon_px, np.int32).reshape(-1, 1, 2)
            if len(poly) >= 3:
                dashed = not room.is_enclosed
                self._outline(out, poly, (255, 255, 255), 4, dashed)
                self._outline(out, poly, col, 2, dashed)
            for hole in room.holes_px:
                hp = np.array(hole, np.int32).reshape(-1, 1, 2)
                cv2.polylines(out, [hp], True, col, 1, cv2.LINE_AA)

            cx, cy = room.centroid_px
            self._caption(out, f"{room.name}  {room.relative_area * 100:.1f}%",
                          (int(cx), int(cy)), col)

        entries = []
        if walls is not None:
            entries.append((WALL_TINT, "wall / structure - not a room"))
        if any(not r.is_enclosed for r in self.rooms):
            entries.append(((255, 255, 255), "dashed = open to exterior"))
        _legend_box(out, entries)
        return out

    @staticmethod
    def _outline(img, poly, col, thickness, dashed):
        if not dashed:
            cv2.polylines(img, [poly], True, col, thickness, cv2.LINE_AA)
            return
        pts = poly.reshape(-1, 2).astype(np.float64)
        dash, gap = 11.0, 7.0
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            span = float(np.linalg.norm(b - a))
            if span < 1e-6:
                continue
            direction = (b - a) / span
            travelled = 0.0
            while travelled < span:
                end = min(travelled + dash, span)
                p = (a + direction * travelled).astype(np.int32)
                q = (a + direction * end).astype(np.int32)
                cv2.line(img, tuple(p), tuple(q), col, thickness, cv2.LINE_AA)
                travelled = end + gap

    @staticmethod
    def _caption(img, text, centre, col):
        font, sc, th = cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
        (tw, tht), base = cv2.getTextSize(text, font, sc, th)
        pad, (h, w) = 5, img.shape[:2]

        x = max(pad + 1, min(int(centre[0] - tw / 2), w - tw - pad - 1))
        y = max(tht + pad + 1, min(int(centre[1] + tht / 2), h - base - pad - 1))
        tl, br = (x - pad, y - tht - pad), (x + tw + pad, y + base + 2)
        cv2.rectangle(img, tl, br, (255, 255, 255), -1)
        cv2.rectangle(img, tl, br, col, 1)
        cv2.putText(img, text, (x, y), font, sc, (30, 30, 30), th, cv2.LINE_AA)
