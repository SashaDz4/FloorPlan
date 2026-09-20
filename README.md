# 3D Floor Plan → 2D Room Layout

A transparent, classical-CV prototype that takes a single 3D apartment floor-plan
render and produces an approximate 2D room layout: room-like regions, their
boundaries as polygons, and their relative pixel areas.

No training data, no model weights, no network access - just OpenCV and NumPy,
and no manual correction step: every result below is what the pipeline produces
on its own. A small local UI comes with it for driving the pipeline by hand.

![rooms](data/outputs/heritage%20towers_a1_1%20Bed%201%20Bath%20593%20Sq.%20Ft.__annotated.png)

Every region filled, outlined and measured, from one 3D render with no input
beyond the image itself.

The UI is the default way in; `--batch` writes the files instead. One render
in, two files out:

| file | holds |
| --- | --- |
| `<name>__annotated.png` | rooms filled, outlined and labelled with their share |
| `<name>__rooms.json` | polygons and areas, as data |

---

## Quick start

### Docker Compose (recommended)

```bash
docker compose up --build          # local UI on http://localhost:8000
```

That is the normal way to use it. For a one-off batch run that writes files into
`data/outputs` instead:

```bash
docker compose run --rm batch
```

### Docker

```bash
docker build -t floorplan-2d .
docker run --rm -p 127.0.0.1:8000:8000 -v "$PWD/data:/app/data" \
  floorplan-2d --host 0.0.0.0
```

### Local Python (3.9+)

```bash
pip install -r requirements.txt
python main.py                     # UI on http://127.0.0.1:8000
python main.py --batch             # analyse everything, write files
```

`main.py` in the project root is the entry point; `python -m floorplan` works
too, since the package sits at the root.

### The UI

Running with no arguments starts it. A single page served by the standard
library - no framework, no extra dependencies, no internet. Pick a bundled sample or drop in your own image, then
switch between the two views: **rooms** and **wall graph**.

Three parameters are exposed as sliders and re-run the pipeline live -
`wall_delta`, `region_min_area_frac` and `wall_top_tolerance`. Anything else in
the query is ignored: the slider list is an allowlist, not a hint. Both views come back with every analysis, so switching
between them never re-runs anything. Whatever is on screen downloads as PNG, and
the full report as JSON.

Slider steps are checked against the defaults at startup. A default that is not
on its step grid gets silently rounded by the browser, and the page then shows a
result the pipeline would never produce - which is exactly what happened once,
costing a room on one sample.

The current view lives in the URL, so a configuration can be bookmarked or
linked to: `?sample=<name>&view=walls&wall_delta=12`.

It binds loopback by default. `--host 0.0.0.0` is only for Docker, where the
container must accept the forwarded port; the compose file publishes it as
`127.0.0.1:8000` so it stays on your machine. This is a development server -
single user, one browser tab, not intended to face a network.

### Options

```
--input PATH              image file or directory        [data/input_images]

UI (default)
--host HOST               interface to bind                      [127.0.0.1]
--port PORT               port to bind                                [8000]

batch (--batch)
--batch                   analyse every input and write files, no UI
--output DIR              output directory                    [data/outputs]
--total-area-sqft FLOAT   optional calibration; pixel areas to sq ft
--door-sever-frac FLOAT   door-cutting radius / sqrt(footprint area)  [0.040]
--wall-delta INT          wall threshold below the brightness mode        [8]
--min-area-frac FLOAT     drop regions below this share of the plan   [0.004]
```

---

## Outputs

**`<name>__annotated.png`** — the source render with each detected region filled,
its simplified polygon outlined, and its id plus share of total room area. Walls
are tinted dark and named in the legend: they belong to no room, and leaving
them bare made the plan look full of unexplained gaps. Regions open to the
exterior (balcony, patio) are outlined with a dashed stroke.

Rooms are always coloured and walls always neutral, so the two can never be
confused; the two greys the palette used to carry were dropped for that reason.

![annotated rooms](data/outputs/limestone%20ranch_santa%20fe_625sq__annotated.png)

**`<name>__rooms.json`**

```jsonc
{
  "image": "heritage towers_a1_1 Bed 1 Bath 593 Sq. Ft..webp",
  "image_size": { "width": 1140, "height": 855 },
  "units": "pixels",
  "wall_brightness_mode": 216,      // adaptive threshold actually used
  "polarity_inverted": false,       // true if the render came on a dark page
  "annotation_removed_px": 14395,   // printed callouts cut out before measuring
  "footprint_area_px": 419789,      // whole apartment silhouette
  "wall_area_px": 64702,            // wall tops - what is drawn and measured
  "wall_barrier_area_px": 67906,    // the generous mask used for segmentation
  "total_room_area_px": 350944,     // sum of the detected regions
  "room_count": 7,
  "calibration": null,              // populated by --total-area-sqft;
                                    // calibrated on enclosed rooms only
  "rejected_regions": [],           // what was filtered out, and why
  "config": {},                     // every parameter used for this run
  "rooms": [
    {
      "id": "room_01",
      "area_px": 184530,
      "relative_area": 0.5258,               // share of total_room_area_px
      "relative_area_of_footprint": 0.4396,  // share of the whole silhouette
      "centroid_px": [425, 399],
      "bbox_px": [229, 61, 366, 642],
      "polygon_px": [[579, 61]],             // simplified outer boundary
      "holes_px": [],                        // significant interior holes
      "polygon_area_px": 183572,
      "open_boundary_ratio": 0.075,          // share of outline facing the page
      "is_enclosed": true                    // false => balcony / patio / open
    }
  ]
}
```

`relative_area` is the headline number the brief asks for. `polygon_area_px`
differs slightly from `area_px` because the polygon is a simplification of the
mask — the mask is the measurement, the polygon is the drawing.

---

## Approach

Nine stages. Each is a separate class, so any one of them can be run on its own
against a `Plan` and its output looked at directly.

### 1. Polarity and footprint — separate the apartment from the page

Everything downstream assumes a white page with the walls as the brightest thing
on it. That is an assumption about *presentation*, not architecture, and a
dark-themed export breaks it completely - background detection finds nothing,
the silhouette swallows the whole image, and a single region comes back covering
it. Measured on inverted copies of the samples: page-coloured pixels drop from
55.9% of the image to 0.2%, and the silhouette goes from 43.6% to 100%.

So polarity is decided once, up front, from the median brightness of the four
corners - the plan can run to the edge of the frame but rarely into a corner. On
a dark page the render is inverted and every later stage proceeds unchanged. The
signal is not marginal: 255 against 0 on the samples. `polarity_inverted` in the
report says whether it fired.

Verified: inverted copies of all three plans produce output **identical field for
field** to the originals.


The renders sit on a white page. The background is the set of white pixels
**reachable from the image border**, found by 4-connected labelling; anything
else is foreground. The apartment is then the single largest foreground
component, which discards the dimension labels, the disclaimer text and the
entry arrow automatically. Holes are filled.

4-connectivity matters: with 8-connectivity a diagonal seam of white pixels lets
the page bleed through a corner contact into an interior white area.

### 2. Printed annotation — cut the callouts out

These are marketing renders, and they carry dimension callouts drawn *on top* of
the plan (`13'3" x 13'3"`, `11'9" x 10'9"`, `Mech.`). The callout box is grey at
V≈210 — the same grey as the walls at V=216 — so every later stage reads it as
architecture. It joined the wall network, acted as a barrier, and sliced the
balcony it covered into a comb. **This was the single largest source of ragged
outlines**, not the polygon simplifier.

Callouts are found as runs of at least four similarly-sized, horizontally
aligned dark glyphs sitting on a bright background. The region is then treated
as *occluded*, not repainted:

* it is cut out of the silhouette, so the part overhanging the page disappears
  while `fill_holes` restores the part lying inside the building;
* the wall network is **bridged across** it, so a wall running under a callout
  stays continuous — clipped to the silhouette, so a callout that overhangs the
  page is not painted back in as a slab of phantom wall (that bug put 4.9% of
  `heritage`'s wall mask outside the building).

The bridge uses **line** kernels as long as the strip, not a disk. A callout is
printed *along* a wall, so it hides a ~150 px run of a wall only ~15 px thick; a
disk big enough to span that would swallow doorways. Getting this wrong was
visible: inpainting the callout smeared balcony floor over the wall beneath it
and merged the balcony into the bedroom, and a disk-kernel bridge did the same.

Two of the three plans yield zero detections — correct, their text lies outside
the silhouette and the footprint stage had already dropped it.

### 3. Walls — adaptive brightness threshold

Walls are the brightest large flat surface in these renders, so they dominate
the upper half of the footprint's V histogram. The pipeline finds that peak per
image and thresholds a few levels below it.

**Measuring the peak per image is what makes this transfer.** The three sample
renders peak at **V = 216, 234 and 238**. A threshold tuned on one of them badly
over- or under-segments the others — this was the first thing that broke during
exploration.

**See-through openings are not wall.** A balcony railing or a stair tread shows
the page between its balusters. Those gaps sit *inside* the silhouette — hole
filling closed them — at page white, comfortably above the wall's own grey, so
they passed the brightness threshold and were tinted as structure. They are
excluded by matching the page colour exactly, and only when the walls are
clearly darker than the page, so a genuinely white-walled render is not eaten.
Measured: 2.6% of the raw mask on two plans, landing exactly on the railing
gaps; ~0 on the third.

### 4. Wall colour — a veto that rescues white-tiled rooms

Brightness alone fails on a render whose bathroom and kitchen floors are
near-white tile: the threshold swallows those floors and the rooms disappear.
Lightness cannot fix it, because a *shaded* wall there measures L\*=83 while the
*lit* tile floor measures L\*=91 — the wall is darker than the floor.

What does separate them is the wall's own colour. Sampling a band just inside
the footprint contour — guaranteed to be perimeter wall, and uncontaminated by
floor, unlike any brightness-derived mask — gives the wall's b\* directly:

| plan | wall b\* | |
| --- | ---: | --- |
| heritage towers | +1 | neutral white |
| limestone ranch | +1 | neutral white |
| highlandlux | +6 | cream |

On the cream plan every wall sits at b\* = 6–8 whether lit or deeply shaded,
against b\* = 2 on the tile floors. **Only b\* is used.** a\* is near-identical on
walls and floors in these renders, so a full Lab colour distance mixes in pure
noise and destroys the separation — measured, the wall and floor ΔE
distributions overlap completely while their b\* distributions do not.

The veto is applied **per region, not per pixel**. A pixel-wise veto also
deletes the scattered wall pixels that shading and bounce light push off-colour,
which punches a hole through a wall and merges two rooms — observed directly:
it merged the bedroom into the living room. Requiring an off-colour *area*
(an opening plus morphological reconstruction) confines the veto to genuine
mis-classified floor. On the two neutral-walled plans it removes **exactly 0.0%**
of the mask, so it cannot regress them.

### 5. Wall network — keep only the connected structure

The raw bright mask also contains white furniture: rugs, duvets, glass tables,
counters. Left in, a white rug cuts a living room into three pieces.

In a floor plan every partition meets the perimeter wall, so the true structure
is **one connected component** — 73-91% of the candidate pixels on the sample
images. Keeping only the largest component drops the floating bright objects and
fixed most of the spurious fragmentation in one line.

Furniture that is *not* bright stays inside the free space, which is correct: a
sofa sits in a room, so including it keeps the room whole. Only structure
removes area.

### 6. Regions — sever doors, then grow

`free = footprint - wall network`. A plain connected-component pass on that
merges rooms, because doors are gaps in the wall.

So: **open the free space with a disk wider than a door**. The necks vanish and
one core per room is left. Rooms narrower than the disk (a linen cupboard, a
utility closet) vanish too, so any free component that lost all of its cores is
re-seeded with itself.

The cores are then grown back by iterated 3x3 dilation clipped to the free mask.
This is geodesic growth — it follows the free space rather than straight-line
distance, so a label cannot jump a wall, and two labels meet on the neck that
separates them.

Finally regions are filtered on minimum area and on minimum inscribed-circle
radius; everything dropped is listed in `rejected_regions` with the reason.

### 7. Door leaves and built-ins — give them back to the room

Three things are rendered in the same white as the walls and touch one, so they
join the wall network: built-in joinery (a bath panel, a vanity, a run of
kitchen units), and **open door leaves**, which swing out into a room as a thin
diagonal blade. Both bite notches out of the room — wrong for area (the floor
under a tub is still bathroom floor) and a major source of waviness.

**Blurring or eroding them away does not work, and the measurement says why.**
On `highlandlux` a door leaf and an interior partition have almost the same
half-width — 2.0 px against 3.0 px. Any blur, opening or erosion strong enough
to delete the door also destroys the walls, which on that plan are only ~6 px
thick.

The usable distinction is what a wall is *for*: **a wall pixel is structural if
it lies on the surface separating two territories.** Room labels are grown over
the whole image — with the exterior seeded as a territory of its own, so
perimeter walls have a boundary running through them too — and the surface where
two territories meet is the real partition.

The test is anchored on that surface rather than measured per pixel. For each
wall pixel the nearest point of the partition is found, and the pixel is
structural if it lies no further away than the wall's own radius *there*, plus a
margin. A wall's cross-section passes: its edge is half a thickness from its own
centre-line. A door leaf fails: its far end is tens of pixels from the doorway it
hinges on, while the wall radius there is a few.

Two corrections were needed to make that behave:

* An earlier per-pixel version compared the distance to the radius **at the
  pixel itself**, which condemned every wall *edge* — the radius goes to zero
  there — and shaved 18% off the mask.
* The radius is capped, and the reach has a ceiling. Distance is measured on a
  wall mask that still contains the fixture, so a bath panel welded to a wall
  makes that wall measure very thick, and the inflated radius would shelter the
  very fixture that caused it. The ceiling is calibrated against the renders
  rather than guessed: measured cross-sections give perimeter walls 18-20 px and
  interior partitions 9-10 px, so a half-width of ~0.016 x scale covers a real
  wall and anything past it is something stood against one.

Reclaimed pixels are removed from the wall mask as well as added to the room,
so `wall_area_px` does not count them twice.

After this stage the wall mask that survives but that the structural test still
calls non-structural is 0.6% on `highlandlux` and 0.7% on `limestone`. On
`heritage` the 3.0% left is almost entirely the outer face of perimeter walls,
whose nearest territory is the exterior — it cannot be handed to a room, and it
really is wall, just seen from outside.

Pixels whose territory is the exterior are never reclaimed, so the outer face of
a perimeter wall is not handed to the room behind it.

### 8. Wall tops - what gets reported

The barrier used for segmentation is deliberately generous: it must not develop
holes, or rooms leak into each other. That same generosity makes it collect the
brightest part of each wall's **side** face, which the 3D view exposes but a 2D
plan has no room for.

So the mask that is reported and drawn is not the barrier. Walls are seen from
above, so their top is a flat plateau at the brightness mode while the side
faces fall away from it as a gradient; a **two-sided band** around the mode
keeps the top and drops the face. Segmentation still uses the full barrier, so
nothing about the rooms changes - only `wall_area_px` and the overlay. The
barrier's own size stays available as `wall_barrier_area_px`.

Verified not to open gaps: the wall network stays a single connected component
on all three plans after the restriction.

### 9. Polygons and areas

A closing pass removes the bite marks that white furniture standing against a
wall leaves in the outline, then `findContours` + `approxPolyDP` produce the
simplified boundary and any significant interior holes.

`approxPolyDP` traces whatever the mask does, and the mask stays noisy wherever
white joinery met a wall — railing slats leave a comb, a bath panel leaves a
notch. Three local rules are then applied until the outline stops changing:
needle-thin **spikes** (interior angle under 32 deg) are dropped, **near-collinear**
neighbours are merged, and **short edges** are collapsed to their midpoint. On
the balcony this took the outline from 17 vertices to 10.

No orthogonality is imposed. These plans are rendered in perspective and their
perimeter walls genuinely splay — the left wall of `heritage` leans about 5 deg —
so snapping edges to axes would move real corners rather than tidy them. Area is the pixel count
of the mask; relative area is its share of total detected room area.

Every length threshold is expressed as a fraction of **`sqrt(footprint_area)`**,
not in pixels. The samples are 659 px and 1140 px wide; scale-relative
thresholds transfer between them and absolute ones do not.

---

## Assumptions

1. **Top-down view.** Near-orthographic, camera roughly overhead. Mild
   perspective is tolerated — the samples have it, and the outer walls visibly
   splay — but an oblique or isometric view would break the wall mask.
2. **The plan sits on a plain page**, reachable from the image border. A
   dark-page export is detected and flipped back before anything else runs, so
   either polarity works; what it cannot handle is a busy or textured
   background.
3. **Walls are the brightest large flat surface** in the render, and are close to
   neutral in hue (saturation <= 40). After polarity normalisation, so a dark
   theme satisfies this too.
4. **Walls form one connected network.** True for any single-storey plan.
5. **Floors are distinguishable from walls by lightness *or* by b\*.** Either is
   enough. A floor that is both the same lightness *and* the same hue as the
   walls would still be lost.
6. **Walls are one colour per plan.** A feature wall in a contrasting colour
   would be vetoed as if it were floor.
7. **Limited occlusion.** Furniture may cover floor, but no furniture spans a
   whole room in near-white.
8. **Doors are narrower than rooms.** The door-severing radius sits between half
   a door width and half the narrowest room.
9. Areas are **interior clear floor area** — wall thickness is excluded, as in a
   normal net-area take-off.

---

## Results on the three samples

All three run fully automatically - there is no manual-correction step, and no
hand-authored data ships with the repo.

| render | regions | assessment |
| --- | ---: | --- |
| `heritage towers_a1` | 7 | Clean. Living/kitchen/entry correctly read as one open-plan space; bedroom, both bath zones, mech. closet, W/D closet and balcony all separated. The bedroom comes out as a clean 4-gon once its dimension callout is removed. |
| `limestone ranch_santa fe` | 10 | Clean. Bedroom, bathroom, walk-in closet, four small closets, balcony and balcony store all separated. |
| `highlandlux-citadel` | 11 | Bedroom, bathroom, patio, five closets and niches all separated. Kitchen stays merged with the dining area, which is correct here — they share a wide cased opening. |

All three:

| `heritage towers_a1` | `limestone ranch_santa fe` | `highlandlux-citadel` |
| --- | --- | --- |
| ![heritage rooms](data/outputs/heritage%20towers_a1_1%20Bed%201%20Bath%20593%20Sq.%20Ft.__annotated.png) | ![limestone rooms](data/outputs/limestone%20ranch_santa%20fe_625sq__annotated.png) | ![highlandlux rooms](data/outputs/highlandlux-citadel-1%20Bed%201%20Bath%20623%20Sq.%20Ft.__annotated.png) |

### Area accuracy check

`heritage towers_a1` prints its own ground truth on the render. Calibrating on
the 593 sq ft total from the filename:

```bash
python main.py \
  --input "data/input_images/heritage towers_a1_1 Bed 1 Bath 593 Sq. Ft..webp" \
  --batch --output data/outputs --total-area-sqft 593
```

| room | drawn on the render | estimated | error |
| --- | ---: | ---: | ---: |
| bedroom (`room_02`) | 11 ft 9 in x 10 ft 9 in = 126.3 sq ft | 118.4 sq ft | **-6.3%** |

Calibration deliberately uses **enclosed rooms only** — counting the balcony in
the denominator, as an earlier version did, shrinks every estimate. The residual
bias is still negative by construction: wall thickness is excluded from each
room but not from the published total. It is a sanity check on one room of one
render, not a benchmark.

---

## Limitations

1. **White floor on white walls is handled, but narrowly.** The b\* veto
   separates `highlandlux-citadel`'s near-white tile floors from its cream walls
   on a margin of about 4 b\* units. A render with genuinely neutral walls *and*
   neutral white tile floors at the same lightness has no cue left and would
   still lose those rooms — the veto would find nothing off-colour to remove.
2. **Open-plan spaces stay merged.** With no wall between kitchen and living
   room, the pipeline reports one region — which is what the wall geometry says.
   Splitting them needs a floor-material cue (see Next steps).
3. **Thickness-based wall/blob separation does not work.** Wall half-widths run
   4-8 px typically but reach 20-30 px at junctions and on splayed perimeter
   walls, which overlaps the size of a small tiled floor. Tried and rejected.
4. **Areas are projected pixel areas.** Perspective makes the far side of the
   plan slightly smaller than the near side; no homography is estimated, so
   there is a systematic few-percent tilt across the image.
5. **Walls are charged to no room.** Sum of room areas < footprint area. This is
   deliberate but means `relative_area` is a share of *clear floor*, not of the
   gross footprint. Both numbers are in the JSON.
6. **Balconies and patios are reported as regions**, flagged with
   `is_enclosed: false` rather than dropped — the brief asks for room-*like*
   regions and it is the caller's decision whether an outdoor deck counts.
7. **Wall thickness follows the render, not the plan.** The perimeter band is
   wide because the 3D projection exposes the *outer face* of the external walls
   inside the silhouette. Those pixels really are wall, so they are kept, but a
   measured wall thickness would overstate the true one.
8. **Polygons are simplified, not rectified.** Spikes, collinear runs and short
   edges are cleaned up, but no axis snapping is applied — deliberately, since
   perspective makes the perimeter walls genuinely non-parallel. Rooms bounded
   by large white built-ins still carry some waviness where a fixture sits close
   enough to a partition to pass the structural test.
9. **Polarity is read from the image corners.** A plan that fills the frame
   corner to corner would defeat it. Not a concern on anything plan-shaped - even
   cropped tight to the silhouette, the corners of the bounding box are still
   page - but it is the assumption the flip rests on.
10. **No test suite.** There is a startup check that the UI slider defaults sit
   on their step grid - it catches a real bug class, where the browser silently
   rounds a default and the page shows a result the pipeline cannot produce -
   but nothing else is covered automatically.
11. **Single image, single storey.** No multi-floor handling, no stitching.

---

## Next steps

Roughly in order of value per hour:

1. **Split open-plan regions by floor material.** k-means in Lab on the floor
   pixels of a region, split when two clusters both hold a large, spatially
   coherent share. `limestone ranch` has wood in the kitchen and carpet in the
   living room; this would separate them without inventing a wall.
2. **Rectify the polygons.** Estimate the dominant edge orientations, snap
   near-parallel edges, and regularise corners. Large visual gain, and makes the
   output usable as CAD input.
3. **Recover perspective.** Fit a homography from the near-rectangular outer
   wall and rectify before measuring, removing the tilt bias in the areas.
4. **Texture cue as a second opinion on white-on-white.** Tile grout is a
   regular grid; wall tops are flat. A local gradient-energy or FFT-peak feature
   would cover the case where walls and floor share both lightness and hue,
   which the b\* veto cannot.
5. **SAM / promptable segmentation as an alternate backend** behind the same
   interface, with the classical pipeline producing the prompts. Keeps the
   result explainable while raising the ceiling on hard renders.
6. **A wall graph feeding the polygons.** Thinning the wall mask to
   centrelines and deriving each room from the resulting cycles would give clean
   straight-edged polygons instead of outlines traced from a noisy mask, and is
   the representation CAD and BIM tools actually want. This was built and then
   removed: it produced a good-looking graph but never fed the rooms, so it was
   decoration rather than structure.
7. **An evaluation set and a test suite.** Nothing here is scored against
   labelled data, so every judgement about wall and room quality is visual. A
   handful of hand-traced masks with an IoU score would let parameter changes be
   measured rather than eyeballed, and the graph invariants that were checked by
   hand during development - no dangling node references, zero gap between an
   edge end and its node - belong in tests rather than in one-off scripts.

---

## Repo layout

```
main.py                  entry point
floorplan/               the package
  config.py              Config - every tunable, scale-relative
  analyzer.py            FloorPlanAnalyzer / Analysis - orchestration
  cli.py                 command-line interface

  core/                  input representation and shared primitives
    imaging.py           loading, disk, fill_holes, Lab, brightness mode
    plan.py              Plan - the render and everything derived from it
    room.py              Room - one region, its measurements and outline

  detection/             the computer-vision stages, in pipeline order
    annotations.py       stage 2 - printed callouts, occlusion repair
    walls.py             stages 3-5, 8 - barrier, colour veto, wall tops
    regions.py           stages 6-7 - rooms, fixture reclamation
    polygons.py          stage 9 - simplification and regularisation

  rendering/
    overlay.py           room overlay and legend

  ui/
    service.py           AnalysisService - samples, uploads, caching
    server.py            the local page and its stdlib HTTP server

data/
  input_images/          the three sample renders
  outputs/               annotated PNG, wall graph PNG, room JSON
```

The packages are layered, and the layering is enforced by the imports:
`core` depends on nothing but `config`, `detection` builds on `core`,
`analyzer` composes the stages, and `ui` and `cli` sit on top. `Plan` takes its
callout finder as an argument rather than importing one, which is what keeps
`core` from reaching back into `detection`.
