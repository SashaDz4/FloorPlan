"""Analysis service behind the UI: samples, uploads, caching, encoding.

Kept separate from the HTTP plumbing so the server only deals with requests and
this only deals with floor plans.
"""

import hashlib
import tempfile
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..analyzer import Analysis, FloorPlanAnalyzer
from ..config import Config
from ..core.imaging import SUPPORTED_SUFFIXES, list_images

# Parameters the UI may change: type, range and slider step. Anything not listed
# here cannot be set from a request.
#
# The step is not cosmetic. An HTML range input snaps to min + n*step, so a
# default off that grid is silently rounded and the UI then shows a result the
# pipeline would never produce - which is exactly what happened with
# region_min_area_frac at step 0.001 (0.004 snapped to 0.0045 and dropped a
# room). validate_grid() below makes that failure loud instead.
ADJUSTABLE: Dict[str, tuple] = {
    "wall_delta": (int, 0, 40, 1),
    "region_min_area_frac": (float, 0.0005, 0.0500, 0.0005),
    "wall_top_tolerance": (int, 2, 40, 1),
}

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# What one analysis hands back: the report and the room overlay.
Cached = Tuple[Dict, bytes]


def validate_grid() -> None:
    """Every default must sit exactly on its slider's step grid."""
    cfg = Config()
    for name, (_, low, _high, step) in ADJUSTABLE.items():
        offset = (getattr(cfg, name) - low) / step
        if abs(offset - round(offset)) > 1e-9:
            raise ValueError(
                f"{name}: default {getattr(cfg, name)} is not on the slider grid "
                f"(min={low}, step={step}); the UI would silently round it")


class AnalysisService:
    """Runs the pipeline for the UI and remembers recent results."""

    def __init__(self, samples_dir: Path, cache_size: int = 12):
        self.samples_dir = samples_dir
        self._cache: "OrderedDict[tuple, Cached]" = OrderedDict()
        self._cache_size = cache_size

    # -- samples ------------------------------------------------------------

    def samples(self) -> List[str]:
        if not self.samples_dir.is_dir():
            return []
        return [p.name for p in list_images(self.samples_dir)]

    def resolve_sample(self, name: str) -> Path:
        """Look a sample up by name.

        Matched against the known list rather than joined onto a path, so a
        crafted name cannot escape the samples directory.
        """
        if name not in self.samples():
            raise ValueError(f"unknown sample: {name}")
        return self.samples_dir / name

    # -- configuration ------------------------------------------------------

    @staticmethod
    def build_config(overrides: Dict[str, str]) -> Config:
        """Apply clamped overrides to the defaults."""
        cfg = Config()
        changes = {}
        for key, raw in overrides.items():
            if key not in ADJUSTABLE or raw in (None, ""):
                continue
            kind, low, high, _ = ADJUSTABLE[key]
            try:
                value = kind(float(raw))
            except (TypeError, ValueError):
                continue
            changes[key] = kind(min(max(value, low), high))
        return replace(cfg, **changes) if changes else cfg

    @staticmethod
    def parameter_spec() -> List[Dict]:
        validate_grid()
        cfg = Config()
        return [{"name": name, "min": low, "max": high, "step": step,
                 "default": getattr(cfg, name), "integer": kind is int}
                for name, (kind, low, high, step) in ADJUSTABLE.items()]

    # -- analysis -----------------------------------------------------------

    def analyse_sample(self, name: str, overrides: Dict[str, str]):
        path = self.resolve_sample(name)
        key = ("sample", name, tuple(sorted(overrides.items())))
        return self._cached(key, path, overrides)

    def analyse_upload(self, data: bytes, filename: str,
                       overrides: Dict[str, str]):
        if not data:
            raise ValueError("empty upload")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError(f"upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

        suffix = Path(filename or "").suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ValueError(f"unsupported image type: {suffix or 'unknown'}")

        key = ("upload", hashlib.sha256(data).hexdigest(),
               tuple(sorted(overrides.items())))
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        # The client filename is used only for its extension; the path itself is
        # generated here, so it cannot be steered.
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(data)
            tmp = Path(fh.name)
        try:
            return self._cached(key, tmp, overrides, display_name=filename)
        except ValueError:
            # Never echo the temporary path back to the client.
            raise ValueError(f"could not read the uploaded {suffix} file")
        finally:
            tmp.unlink(missing_ok=True)

    def _cached(self, key, path: Path, overrides, display_name=None):
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        cfg = self.build_config(overrides)
        analysis = FloorPlanAnalyzer(cfg).analyse(path)
        payload = self._encode(analysis, display_name)

        self._cache[key] = payload
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return payload

    @staticmethod
    def _encode(analysis: Analysis, display_name: Optional[str]) -> Cached:
        report = dict(analysis.report)
        if display_name:
            report["image"] = display_name
        return report, _png(analysis.annotated)


def _png(image: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("failed to encode PNG")
    return buf.tobytes()
