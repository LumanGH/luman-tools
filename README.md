# Luman Tools

Blender add-on that builds **interior trim for GTA V MLO interiors**: baseboards
(skirting and cornice), door casings and window casings — mitered, unwrapped and
already wearing a Sollumz `normal_spec.sps` material.

Select the walls, press one button, get a finished, export-ready prop.

![Luman Tools in the viewport](docs/preview.png)

---

## What it is for

An MLO shell is quick. The trim is not. Every room needs skirting that miters
correctly at inside *and* outside corners, every doorway and window needs a
casing, and all of it has to unwrap cleanly and carry a real GTA shader before
it can go into the game.

Doing that by hand — bevel, solidify, fix the corners, unwrap, fix the seams,
build the material — is an hour per room. This add-on does it per click, and
keeps it editable afterwards.

---

## The three tools

All three live in **View3D → Sidebar (N) → Luman Tools → Interior**, and share
the same profile, unwrap, shading and material settings.

### Baseboard

Select wall faces, or the wall/floor edges, in Edit Mode. The moulding is swept
along the bottom boundary of that selection and comes out as a separate mesh
object.

* **Placement: Skirting** — along the foot of the wall, growing upward.
* **Placement: Cornice** — along the top of the wall, growing downward.
* **Flip Side** — build on the other face, for walls whose normals point out.
* **End Gap** — stop the run short of each open end to clear a door casing.
  Only genuine ends are pulled back; corners and junctions are never opened up.
* **Cap Ends** — close the open ends of a run.

### Door Frame

Select the outline of the opening — the two jambs and the head — on **both**
faces of the wall: six edges for a normal doorway. The casing is swept around
them *outward*, away from the opening so the clear width is never reduced, and
proud of the wall face rather than into it. Both top corners are mitered at 45°
by the same solver the baseboard uses.

**Lining Depth** controls how far the lining runs back into the opening. At 0 it
is measured from the selection: with both wall faces selected, each casing lines
half the wall thickness and the two meet in the middle.

### Window Frame

Select the outline of the opening on **one** face of the wall — four edges for a
plain window. The casing is swept round it outward, so the daylight is never
narrowed, and all four corners are mitered.

That gives the half of the frame belonging to this side of the wall. Turn on
**Mirror** and the matching half is generated on the far face: the wall's
thickness is measured off the mesh itself, so the two halves line the reveal
between them, and the switch stays live.

---

## Why the output is usable in-game

**Miters are correct by construction, not by boolean.**
At every node of a run the profile is placed along a *miter vector* solved from
the two wall normals it joins. Because that vector is scaled by each point's own
depth, every point of the profile — not just the front face — lands at exactly
its depth from both wall planes. One formula handles a 90° room corner, a 270°
outside corner around a pillar, and any odd angle in between. Very sharp folds
are capped by **Miter Limit** so a near-180° turn produces a blunt joint instead
of a spike.

**Geometry is continuous.**
Consecutive cross-sections literally share vertices. Nothing is welded back
together afterwards, so there are no doubles, no split normals at a bend and no
shading seam where two runs meet.

**UVs are built, not unwrapped.**
U is arc length along the run, V is arc length across the cross-section, both in
metres and divided by **Texture Scale**. Consecutive segments therefore share U
by construction: the wood grain never restarts or rotates at a corner, and the
texel density is identical on a 4 m straight and on a tight bend. V is measured
on the *unmitered* profile on purpose — at a corner the mitered section is
physically wider, and using its real width there would stretch the texture
exactly where it is most visible.

**The mesh is built like a game asset.**
The bottom face (on the floor) and the back face (against the wall) are off by
default — they are invisible and only z-fight and cost triangles. **Wall Inset**
and **Floor Sink** bury the profile slightly to close hairline gaps on walls that
are not perfectly flat. **Contact Shade** darkens the vertex colour toward the
floor for free ambient contact, written into `Color 1`.

**Everything stays live.**
The generated object remembers its source object, its edge selection and every
setting. Drag Height, Depth or the profile sliders in the N-panel and it
regenerates in place. The source mesh is never modified.

---

## Profiles

Both edges of the section are shaped independently — **Inner Edge** (nearest the
opening, or nearest the floor on a skirting) and **Outer Edge** (against the
wall):

`Square` · `Chamfer` · `Bullnose` · `Cove` (scotia) · `Ogee` · `Stepped`

Each shape has its own **Size**, and `Stepped` adds a step count and step depth.
The inner edge additionally offers **Custom**: a hand-written point list in
fractions of Depth and Height, e.g.

```
0,0  1,0  1,0.6  0.5,0.8  0.5,1  0,1
```

Because the points are fractions and not metres, a custom profile keeps working
while you drag the size sliders. **Segments** controls the subdivision of curved
edges — 3 or 4 is plenty for an interior prop.

---

## Sollumz / material

The add-on builds a **`normal_spec.sps`** material wired to three maps —
diffuse, `_n` normal, `_s` specular — taken from the folder next to the add-on
or from any **Texture Folder** you point it at. `Bumpiness`,
`Specular Intensity`, `Specular Falloff` and `Specular Fresnel` are exposed
directly in the panel.

The vertex layout `normal_spec` expects is respected: `UVMap 0` and `Color 1`
are always written, so the prop exports without a fixup pass.

Sollumz is detected at runtime — legacy add-on, 4.2+ extension, or a GitHub
folder called `Sollumz-main` / `Sollumz-2.9.0` — and only its public functions
are called. **Sollumz source is never modified.**

Without Sollumz the tool still works: geometry, UVs and vertex colours are
identical and the material falls back to a Principled BSDF wired the same way,
so the asset is ready the moment Sollumz is enabled.

Material modes: **Bundled** (build one and share it), **Existing** (pick any
material in the file — your own wood, or one from an imported asset; assigned
as-is and never retextured), **Always New**, **None**.

Generated objects are gathered into `luman_baseboard`, `luman_doorframe` and
`luman_window` collections — unless the source belongs to a Sollumz Drawable, in
which case they go beside the rest of that asset.

---

## Install

1. Download this repository as a ZIP, or clone it.
2. Blender → **Edit → Preferences → Add-ons → Install from Disk**, pick the ZIP
   (or drop the `luman_tools` folder into `scripts/addons/`).
3. Enable **Luman Tools**.
4. Open the sidebar in the 3D viewport with **N** → **Luman Tools** tab.

**Requires** Blender 4.2 or newer. **Sollumz** is optional but recommended — it
is what turns the material into a real GTA shader.

---

## Quick start

1. Select your interior mesh, enter **Edit Mode**.
2. Select the wall faces of a room.
3. Sidebar → **Interior → Baseboard** → set Height / Depth → **Create Baseboard**.
4. Exit Edit Mode, select the generated object, and keep tuning it in the panel —
   it rebuilds live.

For a doorway: select the six edges of the opening on both wall faces and press
**Create Door Frame**. For a window: select the four edges on one face, tick
**Mirror**, press **Create Window Frame**.

---

## License

GPL-3.0-or-later, as required for Blender add-ons.

The bundled `.dds` wood maps are GTA V game textures, included only as a working
default for the material. They are the property of Rockstar Games; point
**Texture Folder** at your own maps for anything you intend to distribute.
