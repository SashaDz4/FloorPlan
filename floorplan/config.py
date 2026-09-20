"""Tunable parameters.

Every length is a fraction of the plan *scale* (``sqrt(footprint_area)``), not a
pixel count: the sample renders are 659 px and 1140 px wide, and scale-relative
thresholds transfer between them while absolute ones do not.
"""

from dataclasses import asdict, dataclass


@dataclass
class Config:
    # Page background -------------------------------------------------------
    bg_value_min: int = 248
    bg_sat_max: int = 6

    # Printed callouts ------------------------------------------------------
    overlay_min_glyphs: int = 4
    """Aligned glyphs needed before a run counts as printed text."""
    overlay_glyph_contrast: int = 90
    overlay_glyph_max_frac: float = 0.05
    overlay_pad_frac: float = 0.8
    """Padding around a text line, times its height, to cover the label box."""
    overlay_bridge_pad_frac: float = 0.35
    """Margin searched either side of a callout for wall to bridge across."""

    # Walls -----------------------------------------------------------------
    wall_delta: int = 8
    """Barrier threshold, below the brightness mode. Deliberately generous."""
    wall_sat_max: int = 40
    wall_close_px: int = 3
    wall_band_frac: float = 0.012
    """Width of the footprint-contour band sampled for the wall's own colour."""
    wall_b_tolerance: float = 3.0
    """Max |b* - b*_wall|. Only b* is used; a* carries no signal here."""
    wall_offcolour_min_frac: float = 0.015
    """Opening radius deciding what counts as an off-colour *area*."""
    wall_top_tolerance: int = 8
    """Half-width of the band isolating wall tops from their side faces."""

    # Regions ---------------------------------------------------------------
    door_sever_frac: float = 0.040
    """Opening radius that cuts door openings. Above half a door, below half
    the narrowest room."""
    core_min_area_frac: float = 0.002
    region_min_area_frac: float = 0.004
    min_inscribed_frac: float = 0.016
    """Minimum inscribed radius, rejecting slivers left by perimeter faces."""

    # Built-ins and door leaves ---------------------------------------------
    fixture_radius_percentile: float = 80.0
    """Caps the wall radius used by the structural test, so a panel welded to a
    wall cannot inflate that wall and shelter itself."""
    fixture_max_reach_frac: float = 0.016
    """Ceiling on how far a wall pixel may lie from its partition. Calibrated:
    perimeter walls measure 18-20 px, interior partitions 9-10 px."""
    fixture_margin_frac: float = 0.008
    fixture_min_area_frac: float = 0.0001

    # Polygons --------------------------------------------------------------
    poly_smooth_frac: float = 0.008
    poly_eps_frac: float = 0.005
    poly_min_edge_frac: float = 0.013
    poly_collinear_deg: float = 9.0
    poly_spike_deg: float = 32.0
    hole_min_area_frac: float = 0.004

    def to_dict(self) -> dict:
        return asdict(self)
