"""Image loading and small stateless transforms shared across the pipeline.

These stay plain functions on purpose: they hold no state and depend on nothing
but their arguments, so a class would add ceremony without hiding anything.
"""

from pathlib import Path
from typing import List

import cv2
import numpy as np

SUPPORTED_SUFFIXES = {".webp", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def list_images(path: Path) -> List[Path]:
    """Images to process: a single file, or a directory's contents."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"input path does not exist: {path}")
    files = sorted(p for p in path.iterdir() if p.suffix.lower() in SUPPORTED_SUFFIXES)
    if not files:
        raise FileNotFoundError(f"no supported images found in {path}")
    return files


def load_bgr(path: Path) -> np.ndarray:
    """Load as 3-channel BGR, compositing any alpha over white.

    The renders sit on a white page, so alpha must flatten the same way or
    background detection sees something else.
    """
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"could not decode image: {path}")
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        rgb = img[:, :, :3].astype(np.float32)
        return (rgb * alpha + 255.0 * (1.0 - alpha)).round().astype(np.uint8)
    return img[:, :, :3]


def is_dark_page(bgr: np.ndarray, corner_frac: float = 0.02) -> bool:
    """True when the render sits on a dark page rather than a white one.

    Every later stage assumes the page is white and the walls are the brightest
    thing on it. That holds for these renders, but it is an assumption about
    presentation, not about architecture - a dark-themed export inverts both and
    the whole pipeline collapses: background detection finds nothing, the
    silhouette swallows the entire image, and one region comes back covering it.

    The four corners are sampled rather than the full border, because the plan
    can run to the edge of the frame but rarely into a corner. Measured on the
    samples the signal is not close: median corner brightness is 255 on the
    originals and 0 on inverted copies.
    """
    height, width = bgr.shape[:2]
    grey = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    k = max(4, int(corner_frac * min(height, width)))
    corners = np.concatenate([grey[:k, :k].ravel(), grey[:k, -k:].ravel(),
                              grey[-k:, :k].ravel(), grey[-k:, -k:].ravel()])
    return float(np.median(corners)) < 128.0


def normalise_polarity(bgr: np.ndarray):
    """Return the render with a white page, and whether it had to be inverted."""
    return (255 - bgr, True) if is_dark_page(bgr) else (bgr, False)


def disk(radius: int) -> np.ndarray:
    radius = max(1, int(radius))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1,) * 2)


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill every enclosed hole in a binary mask.

    The complement is flooded from outside a 1 px padding ring, so "outside" is
    reachable even when the object touches the image border.
    """
    inverse = (1 - mask).astype(np.uint8)
    padded = cv2.copyMakeBorder(inverse, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=1)
    ff = np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), np.uint8)
    cv2.floodFill(padded, ff, (0, 0), 0)
    return ((mask > 0) | (padded[1:-1, 1:-1] > 0)).astype(np.uint8)


def reconstruct(seed: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Morphological reconstruction: grow ``seed`` geodesically inside ``mask``."""
    current = seed.copy()
    kernel = np.ones((3, 3), np.uint8)
    while True:
        nxt = cv2.dilate(current, kernel) & mask
        if np.array_equal(nxt, current):
            return current
        current = nxt


def to_lab(bgr: np.ndarray) -> np.ndarray:
    """CIE Lab in natural units: L* 0..100, a*/b* centred on zero."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[:, :, 0] *= 100.0 / 255.0
    lab[:, :, 1] -= 128.0
    lab[:, :, 2] -= 128.0
    return lab


def brightness_mode(value: np.ndarray, footprint: np.ndarray,
                    prominence: float = 0.25) -> int:
    """Brightness of the wall tops, from the footprint's V histogram.

    Takes the *highest* peak carrying at least ``prominence`` of the tallest
    peak's mass - not the tallest outright, which a large light floor can win
    even when it is clearly darker than the walls. Measuring this per image is
    what makes the wall threshold transfer: the samples peak at 216, 234, 238.
    """
    inside = value[footprint > 0]
    hist = np.bincount(inside, minlength=256).astype(np.float64)
    smoothed = cv2.GaussianBlur(hist.reshape(-1, 1), (1, 11), 0).ravel()

    upper = smoothed[128:]
    tallest = upper.max()
    if tallest <= 0:
        return 255

    # Endpoints excluded: the V=255 bin collects specular highlights and white
    # label boxes, which are not wall surface.
    peaks = [i for i in range(1, len(upper) - 1)
             if upper[i] >= upper[i - 1] and upper[i] >= upper[i + 1]
             and upper[i] >= prominence * tallest]
    return 128 + (peaks[-1] if peaks else int(np.argmax(upper)))
