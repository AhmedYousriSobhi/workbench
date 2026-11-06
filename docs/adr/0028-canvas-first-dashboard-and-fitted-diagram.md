# ADR 0028: A canvas-first dashboard and a diagram drawn to the shape of its panel

Status: accepted (issues 319 and 312; builds on ADR 0024, "the architecture as the main view")

## Context
ADR 0024 made the Infrastructure architecture the main view, but the page left it as a 760 px card with empty space beside and
under it, the node list behind a toggle and Connections on a separate page. The diagram itself had a fixed 1180 x 470
geometry, so in any wider or taller panel it was scaled down and letterboxed. A first attempt at a flat, full-width arrangement
(services at the far right, service lines in a long channel above everything) filled the width but read badly.

## Decision
- **Page.** Under the strip, one grid: the diagram is the left column (about 64 % of the width, the full height); the node list
  is top right; Connections is bottom right. Connections shows how to reach each part until something is selected, then the
  selected part (a pop-up beside the part on narrow screens), with a Timeline tab. Selecting a node grows the Connections area.
  Under 1100 px everything stacks in one column.
- **Diagram.** `buildFlow` builds its geometry from the measured shape of the panel (`flowGeom`): top to bottom (Clients row,
  Access row, Cluster nodes) with the Services column at the right and its lines running down a gutter and into each box from the
  left. Extra width goes to the main column (more tile columns), extra height to the gaps and the service pitch, and node tiles
  grow up to 2x and then show temperature and GPU load. A `ResizeObserver` rebuilds it when the panel changes shape. Narrow
  screens keep the original 1180-wide drawing.
- **Legend.** A **Key** button opens a popover, so the diagram keeps the whole panel; it also shows a sample of each line style.
- **Data.** The drawing reads the declared graph from `/api/cluster` (`webui/graph.py`), so a later renderer (levels of detail,
  2.5D) can use the same model.

## Consequences
- `scripts/ui_shots.py` checks that the drawing fits its panel (aspect ratio within 15 %), that the page does not scroll at
  1280x900 with six nodes or 1920x1080 with 50, and that the Key opens and closes with the keyboard.
- Very large clusters make the diagram taller and smaller (it scales down; no page scroll). The global view (issue 321) condenses
  clusters into cards instead.
- The service lines no longer carry a text label in the column arrangement; the method is in each box's tip and the Key shows
  the line styles.
