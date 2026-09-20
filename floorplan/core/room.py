"""A detected room region."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import cv2
import numpy as np


@dataclass
class Room:
    """One room-like region: its mask, its measurements and its outline.

    A class rather than a dict because the label is internal bookkeeping while
    everything else is output. As a dict that distinction was carried by an
    underscore-prefixed key that had to be stripped on the way out.
    """

    label: int
    mask: np.ndarray
    area_px: int
    centroid_px: List[int]
    bbox_px: List[int]
    open_boundary_ratio: float
    polygon_px: List[List[int]] = field(default_factory=list)
    holes_px: List[List[List[int]]] = field(default_factory=list)
    polygon_area_px: int = 0
    name: str = ""
    relative_area: float = 0.0
    relative_area_of_footprint: float = 0.0
    area_sqft_estimate: Optional[float] = None

    @classmethod
    def from_mask(cls, label: int, mask: np.ndarray, open_ratio: float) -> "Room":
        ys, xs = np.nonzero(mask)
        x, y, w, h = cv2.boundingRect(mask.astype(np.uint8))
        return cls(
            label=label,
            mask=mask,
            area_px=int(mask.sum()),
            centroid_px=[int(round(xs.mean())), int(round(ys.mean()))],
            bbox_px=[int(x), int(y), int(w), int(h)],
            open_boundary_ratio=round(open_ratio, 3),
        )

    @property
    def is_enclosed(self) -> bool:
        """False for a balcony, a patio, or anything not fully walled in."""
        return self.open_boundary_ratio < 0.10

    def to_dict(self) -> Dict:
        out = {
            "id": self.name,
            "area_px": self.area_px,
            "relative_area": self.relative_area,
            "relative_area_of_footprint": self.relative_area_of_footprint,
            "centroid_px": self.centroid_px,
            "bbox_px": self.bbox_px,
            "open_boundary_ratio": self.open_boundary_ratio,
            "polygon_px": self.polygon_px,
            "holes_px": self.holes_px,
            "polygon_area_px": self.polygon_area_px,
            "is_enclosed": self.is_enclosed,
        }
        if self.area_sqft_estimate is not None:
            out["area_sqft_estimate"] = self.area_sqft_estimate
        return out
