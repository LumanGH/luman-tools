"""Luman Baseboard - interior skirting / cornice generator for Sollumz assets.

WHAT IT DOES
------------
You select the walls (faces) or the wall/floor edges of an interior in Edit
Mode, press one button, and get a separate mesh object that runs a moulding
profile along that line with:

  * correct mitered corners - inside AND outside - by construction,
  * continuous geometry through a run (consecutive cross-sections literally
    share vertices, nothing is welded back together afterwards),
  * a straight, non-distorted UV unwrap in real world units, built from the
    geometry rather than solved by an unwrapper, so it is final immediately
    and tiles cleanly through every bend,
  * a Sollumz `normal_spec.sps` material wired to the three textures that ship
    next to this file (diffuse / _n normal / _s specular).

Everything stays live: the generated object keeps the source object, the edge
selection and every setting, so dragging Height/Depth/Profile in the N-panel
regenerates it in place. The source mesh is never modified.

HOW THE CORNERS WORK
--------------------
The profile is a 2D polyline in (out, up):

    out   distance away from the wall, into the room
    up    height above the floor line

At every node of the run the profile is placed as

    P = node + out * m + up * up_axis

where `m` is the MITER VECTOR - the solution of

    m . n0 = 1      (n0 = outward normal of the wall before the corner)
    m . n1 = 1      (n1 = outward normal of the wall after the corner)
    m . up = 0      (stays horizontal)

Because `m` is scaled by `out`, EVERY point of the profile - not just the
front face - lands at exactly its own depth from BOTH wall planes. That is the
definition of a correct miter, and the same single formula handles a 90-degree
room corner, a 270-degree outside corner around a pillar, and any odd angle in
between. Straight runs make the system singular, which is detected and falls
through to "carry on straight"; very sharp folds are capped by Miter Limit so
a near-180-degree turn produces a blunt joint instead of a spike.

WHY THE UVs ARE BUILT, NOT UNWRAPPED
------------------------------------
U is the arc length travelled along the run, V is the arc length across the
cross-section - both in metres, then divided by Texture Scale. Consecutive
segments therefore share U by construction, so the wood grain never restarts
or rotates at a corner, and the density is identical on a 4 m straight and on
a tight bend. V is measured on the UNMITERED profile on purpose: at a corner
the mitered cross-section is physically wider, and using its real width there
would stretch the texture exactly where it is most visible.

SOLLUMZ
-------
Sollumz is detected at runtime (legacy add-on, extension, or a GitHub folder
called Sollumz-main / Sollumz-2.9.0 - the module name differs in each case) and
only its own public functions are called; Sollumz source is never modified.
`post_create_shader_add_default_images` is deliberately never called - it drops
a blank generated image into every empty sampler and that blank exports as a
real, blank texture.

Without Sollumz the tool still works: the geometry, the UVs and the vertex
colours are identical, and the material falls back to a plain Principled BSDF
wired the same way, so the asset is ready the moment Sollumz is enabled.
"""

import importlib
import math
import os
import re

import bmesh
import bpy
from mathutils import Matrix, Vector

_EPS = 1e-8

# The GTA shader this tool builds, and the samplers it exposes. Verified
# against szio's Shaders.xml: normal_spec has DiffuseSampler, BumpSampler and
# SpecSampler, and a vertex layout of Position/Normal/Colour0/TexCoord0/Tangent
# - which is why UVMap 0 and Color 1 are always written.
SHADER_FILENAME = "normal_spec.sps"
DIFFUSE_SAMPLER = "DiffuseSampler"
BUMP_SAMPLER = "BumpSampler"
SPEC_SAMPLER = "SpecSampler"

MATERIAL_NAME_PREFIX = "luman_baseboard_"

# Collection every generated baseboard is gathered into, unless the source
# belongs to a Sollumz Drawable - then it goes beside the rest of that asset.
COLLECTION_NAME = "luman_baseboard"
DOORFRAME_COLLECTION_NAME = "luman_doorframe"
WINDOW_COLLECTION_NAME = "luman_window"

# Vertex ids for the mirrored half of a window frame start here, far above
# anything a real mesh reaches, so a derived point can never collide with a
# source one. Derived rather than selected, so they are never serialised.
_MIRROR_IDS = 1 << 30

_NAME_PATTERN = r"^%s_(\d{3,})$"
_SUFFIX_PATTERN = re.compile(r"\.\d{3}$")

# Suffixes that decide which sampler an image file belongs in.
_NORMAL_SUFFIXES = ("_n", "_normal", "_nrm", "_norm")
_SPEC_SUFFIXES = ("_s", "_spec", "_specular")
_IMAGE_EXTENSIONS = (".dds", ".png", ".tga", ".tif", ".tiff", ".jpg", ".jpeg")


# ===============================================================
# Sollumz detection and integration
# ===============================================================
#
# The module name depends entirely on how Sollumz was installed:
#   extension (4.2+)   bl_ext.<repo>.sollumz
#   legacy add-on      Sollumz
#   GitHub download    Sollumz-main / Sollumz-master / Sollumz-2.9.0
# so it is resolved by importing a module only Sollumz has, never by guessing
# at the name. The name only decides the search ORDER.

_MARKER_MODULE = "sollumz_properties"
_resolved = {"key": None, "base": None}


class SollumzUnavailable(Exception):
    """Sollumz is not enabled, or its szio dependency is not mounted."""


class ShaderError(Exception):
    """Sollumz is here, but the shader material could not be built."""


def _addon_leaf_name(module_name):
    """The add-on's own name, with any extension-repository prefix removed.

    Splitting on the last dot would be wrong: an extension is
    "bl_ext.<repo>.<id>" and the id never contains a dot, but a legacy folder
    freely does - "Sollumz-2.9.0" would come back as "0".
    """
    if module_name.startswith("bl_ext."):
        parts = module_name.split(".", 2)
        return parts[2] if len(parts) > 2 else ""
    return module_name


def _verify_sollumz(module_name):
    try:
        importlib.import_module(f"{module_name}.{_MARKER_MODULE}")
        return True
    except Exception:
        return False


def _sollumz_base():
    """Sollumz's registered module name, or None. Cached until the set of
    enabled add-ons changes - a miss costs one ImportError per add-on and this
    is asked on every panel redraw."""
    key = tuple(bpy.context.preferences.addons.keys())
    if _resolved["key"] == key:
        return _resolved["base"]

    ordered = sorted(key, key=lambda name: (
        _addon_leaf_name(name).lower() != "sollumz",
        not _addon_leaf_name(name).lower().startswith("sollumz"),
        name.lower(),
    ))
    base = next((name for name in ordered if _verify_sollumz(name)), None)
    _resolved.update(key=key, base=base)
    return base


def _sz(submodule):
    base = _sollumz_base()
    if base is None:
        raise SollumzUnavailable("Sollumz add-on is not enabled.")
    return importlib.import_module(f"{base}.{submodule}")


_status_cache = {"key": None, "value": None}


def sollumz_status():
    """(available, message). Says WHY when unavailable - "not detected" alone
    sends people off to reinstall something they already have.

    Cached on the set of enabled add-ons, because the panel asks on every
    redraw and has_required_dependencies() is not free.
    """
    key = tuple(bpy.context.preferences.addons.keys())
    if _status_cache["key"] == key:
        return _status_cache["value"]
    value = _sollumz_status_uncached()
    _status_cache.update(key=key, value=value)
    return value


def _sollumz_status_uncached():
    base = _sollumz_base()
    if base is None:
        return False, ("Sollumz not found. Install it and tick its checkbox in "
                       "Preferences > Add-ons. The baseboard still builds - it "
                       "just gets a preview material instead of normal_spec.sps.")
    try:
        importlib.import_module(f"{base}.ydr.shader_materials")
    except Exception as error:
        return False, f"Sollumz ('{base}') is enabled but its shader module will not import: {error}"
    try:
        deps = importlib.import_module(f"{base}.dependencies")
        if not deps.has_required_dependencies():
            return False, ("Sollumz is enabled but its dependencies (szio) are "
                           "not installed. Install them from Sollumz's Preferences.")
    except Exception:
        # A fork without that module is not evidence of anything.
        pass
    return True, f"Sollumz OK ('{base}')."


def sollumz_available():
    return sollumz_status()[0]


def uv_map_name(index=0):
    """Sollumz's name for a UV map ("UVMap 0"). Falls back to the known name so
    an already-generated baseboard keeps rebuilding without Sollumz."""
    try:
        return _sz("tools.meshhelper").get_uv_map_name(index)
    except Exception:
        return f"UVMap {index}"


def color_attr_name(index=0):
    """Sollumz's name for a colour attribute ("Color 1")."""
    try:
        return _sz("tools.meshhelper").get_color_attr_name(index)
    except Exception:
        return f"Color {index + 1}"


def find_drawable_parent(obj):
    """The owning Sollumz Drawable, or None."""
    try:
        helper = _sz("sollumz_helper")
        props = _sz("sollumz_properties")
        return helper.find_sollumz_parent(obj, props.SollumType.DRAWABLE)
    except Exception:
        return None


def convert_to_drawable_model(obj):
    _sz("tools.drawablehelper").convert_obj_to_model(obj)


def assign_material(obj, material):
    """Append `material` and bring the mesh's UV/Color attributes in line with
    what the shader expects, the same way Sollumz's own Create Shader Material
    operator does. Falls back to a plain append without Sollumz."""
    obj.data.materials.append(material)
    try:
        _sz("ydr.operators.materials").post_create_shader_update_object(obj, material)
    except Exception:
        pass


def set_object_material(obj, material):
    """Put `material` in the object's slot, replacing whatever is there.

    Uses the full Sollumz-aware path when the object has no slot yet, so a
    freshly built mesh still gets its UV/Color attributes lined up; after that
    it is a straight swap.
    """
    if not obj.data.materials:
        assign_material(obj, material)
    else:
        obj.data.materials[0] = material


def _is_shader_material(mat):
    try:
        props = _sz("sollumz_properties")
        return getattr(mat, "sollum_type", None) == props.MaterialType.SHADER
    except Exception:
        return False


# ===============================================================
# Textures bundled next to this file
# ===============================================================

def addon_directory():
    return os.path.dirname(os.path.abspath(__file__))


def _classify(stem):
    low = stem.lower().replace("-", "_")
    for suffix in _NORMAL_SUFFIXES:
        if low.endswith(suffix):
            return "normal"
    for suffix in _SPEC_SUFFIXES:
        if low.endswith(suffix):
            return "specular"
    return "diffuse"


# Scans keyed by folder, and stamped with the folder's modification time. The
# panel names the textures it found, so it asks on EVERY redraw - without this
# that is a directory listing per mouse move. The mtime stamp is what makes a
# file dropped into the folder show up on the next redraw with nothing to press.
_texture_cache = {}


def find_texture_set(directory=""):
    """Sort the images in `directory` into {diffuse, normal, specular} paths.

    An empty directory string means "next to this file", which is where the
    three bundled wood maps live. .dds wins over other formats for the same
    stem: that is what a GTA texture actually is, so if someone keeps a working
    .png beside it the .dds is the one that ships.
    """
    directory = bpy.path.abspath(directory) if directory else addon_directory()
    try:
        stamp = os.stat(directory).st_mtime_ns
    except OSError:
        stamp = None
    cached = _texture_cache.get(directory)
    if cached is not None and cached[0] == stamp:
        return dict(cached[1])

    result = _scan_texture_set(directory)
    _texture_cache[directory] = (stamp, result)
    return dict(result)


def _scan_texture_set(directory):
    result = {"diffuse": "", "normal": "", "specular": ""}
    if not os.path.isdir(directory):
        return result

    best = {}
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return result

    for name in names:
        stem, extension = os.path.splitext(name)
        extension = extension.lower()
        if extension not in _IMAGE_EXTENSIONS:
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        kind = _classify(stem)
        rank = _IMAGE_EXTENSIONS.index(extension)
        if kind not in best or rank < best[kind][0]:
            best[kind] = (rank, path)

    for kind, (_, path) in best.items():
        result[kind] = path
    return result


def _load_image(path, colorspace):
    """Load a texture, reusing it if Blender already has it.

    The filepath is left exactly as loaded so Sollumz's sollumz_texture_name
    (basename without extension) produces the right GTA texture name on export.
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        image = bpy.data.images.load(path, check_existing=True)
    except (RuntimeError, OSError):
        return None
    try:
        image.colorspace_settings.name = colorspace
    except (TypeError, AttributeError):
        # Some .dds variants reject one of these; not worth failing over.
        pass
    return image


# ===============================================================
# Material
# ===============================================================

_PARAMETER_NODE = "SOLLUMZ_NT_SHADER_Parameter"


def _set_parameter(material, name, value):
    """Write one shader value parameter. Sollumz stores each as a node named
    after the parameter, so this lookup is exact. Returns True if it landed."""
    node = material.node_tree.nodes.get(name)
    if node is None or node.bl_idname != _PARAMETER_NODE:
        return False
    if isinstance(value, (tuple, list)):
        for key, component in zip("XYZW", value):
            try:
                node.set(key, float(component))
            except Exception:
                return False
    else:
        try:
            node.set(0, float(value))
        except Exception:
            return False
    return True


def shader_parameters(settings):
    """The normal_spec values that make a painted/varnished wood plinth read
    correctly under interior lighting.

    specularIntensityMult is the one that matters most: normal_spec's whole
    character comes from the highlight rolling along the moulding's curves, and
    at 0 a profile with a bullnose or an ogee looks completely flat however
    strong its normal map is. specMapIntMask picks the RED channel of the
    _s map as the intensity source, which is how GTA's own wood specular maps
    are authored.
    """
    return {
        "bumpiness": settings.bumpiness,
        "specularIntensityMult": settings.spec_intensity,
        "specularFalloffMult": settings.spec_falloff,
        "specularFresnel": settings.spec_fresnel,
        "specMapIntMask": (1.0, 0.0, 0.0),
        "useTessellation": 0.0,
        "wetnessMultiplier": 1.0,
    }


def _material_name(textures):
    stem = os.path.splitext(os.path.basename(textures.get("diffuse") or ""))[0]
    return f"{MATERIAL_NAME_PREFIX}{stem or 'notex'}"


def _find_existing_material(name):
    """One we made earlier, or None.

    Name-scoped rather than shader-scoped on purpose: matching on
    normal_spec.sps alone would happily adopt - and then retexture - an
    unrelated material the user set up by hand, and normal_spec is one of the
    most common shaders in the game.
    """
    for mat in bpy.data.materials:
        if mat.name == name or mat.name.startswith(name + "."):
            return mat
    return None


def build_material(settings, reuse=True):
    """Find, pick or create the baseboard material.

    Returns (material, warnings). Never raises: geometry is the point of this
    tool, and a shader problem must not cost the user their mesh. A reused or
    hand-picked material is left exactly as it is, so tweaks survive.
    """
    warnings = []

    # An explicitly chosen material wins and is never touched - not its
    # textures, not its shader values. Picking your own wood and having the
    # tool immediately retexture it would defeat the point of picking it.
    if settings.material_mode == 'PICK':
        # Via the resolver, because the create operator holds a name where the
        # panels hold a pointer.
        chosen = resolve_material_choice(settings)
        if chosen is not None:
            return chosen, warnings
        warnings.append("Material is set to Existing but none is chosen - "
                        "falling back to the bundled one.")

    textures = find_texture_set(settings.texture_dir)
    if not textures["diffuse"]:
        warnings.append(
            "No diffuse texture found - drop the wood maps next to luman_tools.py, "
            "or point Texture Folder at them.")

    name = _material_name(textures)
    if reuse:
        existing = _find_existing_material(name)
        if existing is not None:
            return existing, warnings

    if sollumz_available():
        try:
            material = _create_sollumz_material(name, textures, settings)
            return material, warnings
        except Exception as error:
            warnings.append(f"normal_spec.sps could not be built ({error}); using a preview material.")

    return _create_preview_material(name, textures), warnings


def _create_sollumz_material(name, textures, settings):
    """A real Sollumz normal_spec.sps shader material.

    The shader is confirmed to exist in the currently mounted ShaderManager
    before anything is created, so a missing/renamed shader fails cleanly
    instead of leaving a half-built material behind.
    """
    shader_module = importlib.import_module("szio.gta5.shader")
    if shader_module.ShaderManager.find_shader(SHADER_FILENAME) is None:
        raise ShaderError(f"'{SHADER_FILENAME}' was not found by Sollumz's ShaderManager.")

    shader_materials = _sz("ydr.shader_materials")
    material = shader_materials.create_shader(SHADER_FILENAME)
    material.name = name

    apply_material_settings(material, settings, textures)
    return material


def apply_material_settings(material, settings, textures=None):
    """Push the texture set and the value parameters onto an existing material.

    Split out from creation so the panel can offer an explicit "Update
    Material" - reuse deliberately never rewrites a material behind the user's
    back, but they still need a way to ask for it.
    """
    if textures is None:
        textures = find_texture_set(settings.texture_dir)

    node_tree = getattr(material, "node_tree", None)
    if node_tree is None:
        return []

    slots = (
        (DIFFUSE_SAMPLER, textures["diffuse"], 'sRGB'),
        # Non-Color for both maps: reading a normal or a specular mask through
        # sRGB bends the lighting response in a way no parameter can undo.
        (BUMP_SAMPLER, textures["normal"], 'Non-Color'),
        (SPEC_SAMPLER, textures["specular"], 'Non-Color'),
    )
    for node_name, path, colorspace in slots:
        node = node_tree.nodes.get(node_name)
        if node is None or not isinstance(node, bpy.types.ShaderNodeTexImage):
            continue
        image = _load_image(path, colorspace)
        if image is None:
            continue
        node.image = image
        # Left non-embedded: the texture belongs in the asset's own TXD,
        # referenced by name, not baked into the .ydr.
        if hasattr(node, "texture_properties"):
            node.texture_properties.embedded = False

    missing = [name for name, value in shader_parameters(settings).items()
               if not _set_parameter(material, name, value)]
    return missing


def _create_preview_material(name, textures):
    """Principled BSDF fallback for when Sollumz is not available.

    Wired the same way normal_spec is - diffuse to Base Color, _n through a
    Normal Map node, _s to Specular - so the viewport shows the same thing the
    game will, and the material can simply be swapped for a real shader later.
    """
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    node_tree = material.node_tree
    nodes = node_tree.nodes
    links = node_tree.links

    bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        output = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None) \
            or nodes.new("ShaderNodeOutputMaterial")
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    def image_node(path, colorspace, y, node_name):
        image = _load_image(path, colorspace)
        if image is None:
            return None
        node = nodes.new("ShaderNodeTexImage")
        node.image = image
        node.name = node_name
        node.label = node_name
        node.location = (-620, y)
        return node

    diffuse = image_node(textures["diffuse"], 'sRGB', 300, DIFFUSE_SAMPLER)
    if diffuse is not None:
        links.new(diffuse.outputs["Color"], bsdf.inputs["Base Color"])

    spec = image_node(textures["specular"], 'Non-Color', 0, SPEC_SAMPLER)
    if spec is not None:
        # Blender 4.x renamed the socket; try both rather than guess.
        for socket in ("Specular IOR Level", "Specular Tint", "Specular"):
            if socket in bsdf.inputs:
                links.new(spec.outputs["Color"], bsdf.inputs[socket])
                break

    normal = image_node(textures["normal"], 'Non-Color', -300, BUMP_SAMPLER)
    if normal is not None:
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.location = (-300, -300)
        links.new(normal.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

    return material


# ===============================================================
# Profiles
# ===============================================================
#
# A profile is a list of (out, up) points in metres, ordered
#
#     back-bottom -> front-bottom -> ... -> front-top -> back-top
#
# and that order is load-bearing: the outward normal of the segment from
# point i to i+1 is (dU * out_axis - dO * up_axis), which only points away
# from the solid if the profile is wound this way. It is also what lets the
# Bottom Face / Back Face toggles work by index - the first segment is always
# the bottom, the implicit closing segment is always the back.

PROFILE_ITEMS = [
    ('SQUARE', "Square", "Left square - this edge is not shaped at all"),
    ('CHAMFER', "Chamfer", "Cut off at 45 degrees"),
    ('ROUND', "Bullnose", "Rounded over"),
    ('COVE', "Cove", "Scooped out - the classic scotia"),
    ('OGEE', "Ogee", "S-curve running back to the wall"),
    ('STEP', "Stepped", "Cut into steps"),
]

# Only the inner list offers this: a hand-written section replaces the whole
# thing, so there is nothing left for the other edge to shape.
INNER_PROFILE_ITEMS = PROFILE_ITEMS + [
    ('CUSTOM', "Custom", "Your own point list, typed into Custom Profile"),
]


def _arc(cx, cy, radius, start_deg, end_deg, segments):
    points = []
    for i in range(segments + 1):
        angle = math.radians(start_deg + (end_deg - start_deg) * (i / segments))
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def parse_custom_profile(text, depth, height):
    """Parse "out,up out,up ..." into profile points.

    Values are fractions of Depth/Height (0..1) rather than metres, so a custom
    profile keeps working when the size sliders are dragged - which is the
    whole point of the live rebuild. Returns [] when nothing parses.
    """
    points = []
    for token in text.replace(";", " ").replace("|", " ").split():
        out_text, _, up_text = token.partition(",")
        try:
            points.append((float(out_text) * depth, float(up_text) * height))
        except ValueError:
            continue
    return points if len(points) >= 2 else []


def end_shape(kind, depth, budget, amount, steps, step_depth, segments):
    """One worked END of a section, and how much of the height it uses.

    Authored from the flat plate outward: up = 0 is where the shaping meets the
    full-thickness part, and up grows toward the tip. Both ends of a section
    are built from this same library and simply placed at opposite ends, which
    is what lets an architrave be stepped against the opening and chamfered
    against the wall at the same time - the two ends of real trim are almost
    never the same, and they used to have to be.

    `budget` is how much height this end may use; `amount` is the fraction of
    the smaller of Depth and that budget the shape actually takes.

    Returns (points, span). An unshaped end is a single corner point and zero
    span, which composes with everything else without a special case.
    """
    size = min(depth, budget) * min(max(amount, 0.0), 0.98)
    segments = max(1, segments)

    if kind == 'CHAMFER' and size > _EPS:
        return [(depth, 0.0), (depth - size, size)], size

    if kind == 'ROUND' and size > _EPS:
        # Convex quarter: leaves the flat tangentially and turns back to the wall.
        return _arc(depth - size, 0.0, size, 0, 90, segments), size

    if kind == 'COVE' and size > _EPS:
        # Concave quarter - the centre sits outside the solid, so it scoops in.
        return _arc(depth, size, size, -90, -180, segments), size

    if kind == 'OGEE':
        span = min(budget, depth * 4.0)
        if span <= _EPS:
            return [(depth, 0.0)], 0.0
        points = []
        for i in range(segments + 1):
            t = i / segments
            # Smoothstep against a linear rise gives the classic S: convex
            # where it leaves the flat, concave where it meets the wall.
            s = t * t * (3.0 - 2.0 * t)
            points.append((depth * (1.0 - s), span * t))
        return points, span

    if kind == 'STEP':
        # Size is how far the steps run ALONG the face - the width of the
        # stepped band - which the count then divides up. It used to be pinned
        # at nine tenths of the budget and ignore Size entirely, so the slider
        # did nothing.
        #
        # Along the face rather than into the thickness, because the steps
        # always work the full depth: that is what makes a stepped architrave
        # read as steps rather than as scratches.
        count = max(1, steps)
        span = budget * min(max(amount, 0.0), 1.0)
        if span <= _EPS:
            return [(depth, 0.0)], 0.0
        # Each stage drops the thickness and then runs flat to the next
        # riser, so every step is a (riser, tread) pair.
        # Size is how far the steps run along the face; Depth is how much
        # thickness they take off in total. The two are independent on purpose -
        # wide shallow steps and narrow deep ones are different mouldings, and
        # tying them together would make half of them unreachable.
        cut = depth * min(max(step_depth, 0.0), 1.0)
        points = [(depth, 0.0)]
        for k in range(1, count + 1):
            thickness = depth - cut * k / count
            points.append((thickness, span * (k - 1) / count))
            points.append((thickness, span * k / count))
        return points, span

    return [(depth, 0.0)], 0.0


def build_profile(settings):
    """The cross-section for the current settings, in metres.

    Composed from TWO ends: the inner one at up = 0, nearest the opening or the
    floor, and the outer one at up = Height. Between them the section runs at
    full thickness. Real trim is worked differently at its two edges - a casing
    stepped against the opening and eased against the wall - and one profile
    for the whole section could only ever do one of them.

    Insets are applied last, and only to the points that sit ON the wall or ON
    the floor: pushing the whole section back would quietly shrink the Depth
    that was asked for, whereas extending just the hidden back and bottom
    closes the seam without changing the visible shape at all.
    """
    depth = max(settings.depth, 1e-4)
    height = max(settings.height, 1e-4)
    segments = max(1, settings.segments)

    if settings.profile_type == 'CUSTOM':
        points = parse_custom_profile(settings.custom_profile, depth, height)
        if not points:
            points = [(0.0, 0.0), (depth, 0.0), (depth, height), (0.0, height)]
    else:
        # Half the height each at most, so neither end can eat the other.
        budget = height * 0.5
        inner, inner_span = end_shape(
            settings.profile_type, depth, budget,
            settings.amount, settings.steps, settings.step_depth, segments)
        outer, outer_span = end_shape(
            settings.outer_profile_type, depth, budget,
            settings.outer_amount, settings.outer_steps, settings.outer_step_depth,
            segments)

        points = [(0.0, 0.0)]
        # The inner end is authored tip-last, so it is reversed and folded down
        # to sit at up = 0 with its tip against the opening.
        points += [(out, inner_span - up) for out, up in reversed(inner)]
        points += [(out, height - outer_span + up) for out, up in outer]
        points.append((0.0, height))

    points = _dedupe(points)

    inset = settings.wall_inset
    sink = settings.floor_sink
    offset = settings.vertical_offset
    result = []
    for out, up in points:
        if out <= _EPS:
            out = -inset
        if up <= _EPS:
            up = -sink
        result.append((out, up + offset))
    return result



def _dedupe(points, tolerance=1e-7):
    """Drop points that repeat the previous one - the arcs above intentionally
    start where the preceding straight segment ended."""
    out = []
    for point in points:
        if out and abs(point[0] - out[-1][0]) < tolerance and abs(point[1] - out[-1][1]) < tolerance:
            continue
        out.append(point)
    return out


# ===============================================================
# Reading the selection
# ===============================================================

class RunEdge:
    """One edge of the line the baseboard runs along.

    Stored by source-mesh vertex index so edges can be chained and so the whole
    selection can be re-read later for the live rebuild. `out` is the outward
    horizontal direction for this edge - away from the wall, into the room.
    """

    __slots__ = ("va", "vb", "out", "candidates", "support", "pinned", "reach")

    def __init__(self, va, vb, out, candidates=None, support=None, pinned=False,
                 reach=None):
        self.va = va
        self.vb = vb
        self.out = out
        # Every direction this edge's faces allow. Kept because a single edge
        # often cannot tell which is right - see resolve_outs().
        self.candidates = candidates if candidates is not None else [(0.0, out)]
        # Which way the solid lies: the average of the adjacent faces' in-plane
        # directions, pointing into them. Used to give the run's plane normal a
        # sign, since a plane normal has no inherent one.
        self.support = support if support is not None else out.copy()
        # Set on a rebuild, where the direction was decided once already and
        # must not be re-decided behind the user's back.
        self.pinned = pinned
        # Where the surrounding faces reach, relative to this edge - see
        # _edge_reach(). Only filled in for the window tool, which is the only
        # one that needs to know how thick the wall behind the run is.
        self.reach = reach if reach is not None else []


def local_up(matrix_world):
    """World +Z expressed in the object's local space.

    The baseboard is built in the source object's local space (so copying its
    world matrix drops it exactly onto the geometry), but "up" and "horizontal"
    are world concepts - a rotated wall object would otherwise generate a
    plinth standing sideways.
    """
    try:
        up = matrix_world.to_3x3().inverted() @ Vector((0.0, 0.0, 1.0))
    except ValueError:
        up = Vector((0.0, 0.0, 1.0))
    return up.normalized() if up.length > _EPS else Vector((0.0, 0.0, 1.0))


def _face_out(face, edge_dir, midpoint, up):
    """The outward direction a given face implies for an edge, or None.

    Two cases, because an edge at the wall/floor junction borders one of each:

      * a WALL (its normal is roughly horizontal) - the outward direction is
        the wall's own normal, which by definition points into the room;
      * a FLOOR or ceiling (normal roughly vertical) - the normal is useless
        here, so the direction toward the face's centre is used instead, which
        also points into the room.

    Either way the result is flattened into the horizontal plane and made
    perpendicular to the edge, because that is the plane the miter is solved in.
    """
    normal = face.normal
    if normal.length < _EPS:
        return None

    normal = normal.normalized()
    if abs(normal.dot(up)) < 0.7:
        candidate = normal - up * normal.dot(up)
    else:
        candidate = face.calc_center_median() - midpoint

    # Perpendicular to the run and horizontal - anything else would make the
    # miter solve for a plane that is not the wall's.
    candidate = candidate - edge_dir * candidate.dot(edge_dir)
    candidate = candidate - up * candidate.dot(up)
    if candidate.length < _EPS:
        return None
    return candidate.normalized()


def _edge_candidates(edge, up):
    """Every outward direction this edge's adjacent faces imply, best first.

    Each entry is (verticalness, direction), where verticalness is how far the
    face is from being a wall - 0 for a perfectly vertical face, 1 for a floor.
    Sorted, so entry 0 is the best guess from this edge alone.

    Which one is actually right often cannot be decided from a single edge. On a
    staircase, the edge where a riser meets the wall borders TWO vertical faces
    - the wall and the riser - and they are equally wall-like. resolve_outs()
    settles those using the run they belong to.
    """
    v0, v1 = edge.verts
    direction = v1.co - v0.co
    if direction.length < _EPS:
        return []
    direction = direction.normalized()
    midpoint = (v0.co + v1.co) * 0.5

    candidates = []
    support = Vector((0.0, 0.0, 0.0))
    for face in edge.link_faces:
        out = _face_out(face, direction, midpoint, up)
        if out is not None:
            vertical = abs(face.normal.normalized().dot(up)) if face.normal.length > _EPS else 1.0
            candidates.append((vertical, out))

        # Which way this face's material lies, along the face and away from the
        # edge. Its sum over the adjacent faces points into the solid, which is
        # the only thing that can give the run's plane normal a sign.
        tangent = _face_tangent(face, direction, midpoint)
        if tangent is not None:
            support += tangent

    candidates.sort(key=lambda item: item[0])
    return candidates, (support.normalized() if support.length > _EPS else None)


def _edge_reach(edge):
    """Where the faces around this edge reach, as offsets from its midpoint.

    Kept as raw offsets rather than as one measured depth because the direction
    that depth has to be measured along - the wall's own normal - is not settled
    until resolve_outs() has run over the whole run, and by then the source mesh
    is long gone. A window's reveal shows up in here as the offsets that run
    BACKWARD from the wall's face; how far back they go is the wall's thickness,
    which is what the far half of the frame has to be placed at.
    """
    v0, v1 = edge.verts
    midpoint = (v0.co + v1.co) * 0.5
    offsets = []
    for face in edge.link_faces:
        for vert in face.verts:
            offset = vert.co - midpoint
            if offset.length > _EPS:
                offsets.append(offset)
    return offsets


def wall_thickness(edges):
    """How thick the wall behind the run is, measured from the mesh itself.

    Per edge it is how far the faces touching it reach behind the surface the
    run lies on; over the run it is the MEDIAN of those, so one odd edge - a
    sill that also carries the geometry of a shelf, say - cannot decide the
    answer for the whole window.

    Returns 0.0 when nothing reaches back at all, which is what a wall modelled
    as a single plane looks like: there is no far side to put anything on.
    """
    depths = []
    for edge in edges:
        if not edge.reach:
            continue
        back = -edge.out
        depth = max(offset.dot(back) for offset in edge.reach)
        if depth > 1e-5:
            depths.append(depth)
    if not depths:
        return 0.0
    depths.sort()
    return depths[len(depths) // 2]


def mirrored_edges(edges, coords, thickness):
    """The same run again, on the far face of the wall.

    Half a window frame is what one loop of edges can describe; the other half
    lives on a loop that is the same shape, `thickness` further back, and faces
    the other way. Rather than making the user find and select that second loop,
    it is derived here - which also means the Mirror switch stays live on a
    finished frame instead of being fixed at the moment it was created.

    The copies are given vertex ids of their own, well clear of the source
    mesh's, so the two halves chain and node separately; `coords` is extended
    in place with their positions. Their outward directions are the reverse of
    the originals' and are PINNED, since they were derived rather than read off
    a face and there is nothing left to work out about them.

    Each node moves along the average of the outward directions meeting at it,
    so a run that turns a corner mid-way still comes out as one connected loop
    instead of splitting at the turn.
    """
    shift = {}
    for edge in edges:
        for index in (edge.va, edge.vb):
            shift.setdefault(index, Vector((0.0, 0.0, 0.0)))
            shift[index] += edge.out

    for index, direction in shift.items():
        offset = -direction.normalized() * thickness if direction.length > _EPS else None
        if offset is None:
            offset = Vector((0.0, 0.0, 0.0))
        coords[index + _MIRROR_IDS] = coords[index] + offset

    extra = []
    for edge in edges:
        support = -edge.support if edge.support is not None else None
        out = -edge.out
        extra.append(RunEdge(edge.va + _MIRROR_IDS, edge.vb + _MIRROR_IDS, out,
                             [(0.0, out.copy())], support, pinned=True))
    return extra


def _face_tangent(face, edge_dir, midpoint):
    """The in-plane direction from the edge into `face`, or None.

    Not flattened to horizontal, unlike _face_out: on a staircase this is the
    direction across a tread or up a riser, and both matter.
    """
    normal = face.normal
    if normal.length < _EPS:
        return None
    normal = normal.normalized()

    tangent = face.calc_center_median() - midpoint
    tangent = tangent - edge_dir * tangent.dot(edge_dir)
    tangent = tangent - normal * tangent.dot(normal)
    return tangent.normalized() if tangent.length > _EPS else None


def run_plane_normal(points, up_reference):
    """The normal of the plane a run lies in, or None when that is not the
    answer to "which way is out".

    THIS is what a staircase needs. Its zigzag - treads and risers alike - lies
    entirely in the wall plane, and the moulding's thickness grows along that
    plane's normal while its height grows within the plane. Read off the
    adjacent faces instead, a riser's normal points along the flight, and the
    board built from it lies across the steps rather than against the wall.

    Returns None in the three cases where the run's own plane is not the
    surface the moulding lies on:

      * a straight run - a line lies in infinitely many planes;
      * a run that is not planar at all;
      * a HORIZONTAL plane, which is what a flat run round a room is. There the
        moulding stands up OUT of the run's plane rather than within it, and the
        wall faces already say which way is out.
    """
    if len(points) < 3:
        return None

    directions = []
    for k in range(len(points) - 1):
        span = points[k + 1] - points[k]
        if span.length > _EPS:
            directions.append(span.normalized())
    if len(directions) < 2:
        return None

    normal = None
    for other in directions[1:]:
        candidate = directions[0].cross(other)
        if candidate.length > 1e-4:
            normal = candidate.normalized()
            break
    if normal is None:
        return None                      # every segment parallel - a straight run

    # Every segment has to lie in that plane, or the run is not planar and no
    # single normal describes it.
    if any(abs(direction.dot(normal)) > 1e-3 for direction in directions):
        return None
    extent = max((point - points[0]).length for point in points)
    limit = max(1e-5, extent * 1e-4)
    if any(abs((point - points[0]).dot(normal)) > limit for point in points):
        return None

    if abs(normal.dot(up_reference)) > 0.7:
        return None                      # a flat run - see above
    return normal


def resolve_outs(edges, chains, coords, up_reference, kind='BASEBOARD'):
    """Decide, for every edge, which of its faces the moulding lies against.

    The rule is PERPENDICULARITY TO THE RUN, judged over the edge and its two
    neighbours in the chain. The surface a moulding lies on is the one the run
    travels across, so its normal is perpendicular to the run everywhere along
    it; a face that merely happens to touch the run is not.

    A staircase is the case that needs this. Where a riser meets the wall, the
    edge borders the wall and the riser, both vertical, both equally wall-like -
    so the older "most vertical face wins" rule picked whichever came first and
    the run flipped between them. But the riser's normal points along the
    TREAD's direction, while the wall's normal is perpendicular to the tread and
    to the riser alike. That is what separates them.

    Verticalness stays as the tie-break, which is what settles a tread edge: the
    stair's side face and the tread face are both perpendicular to the run
    there, and only one of them is the surface the moulding sits on.
    """
    for vseq, eseq in chains:
        points = [coords[v] for v in vseq]
        directions = []
        for k in range(len(eseq)):
            span = points[k + 1] - points[k]
            directions.append(span.normalized() if span.length > _EPS else None)

        # A run that lies in its own non-horizontal plane is settled in one go,
        # and correctly for every segment at once. Skipped when the run was
        # pinned by a rebuild, which already has an answer.
        #
        # Both a staircase and an opening's outline are that kind of run - a
        # stair skirting lies ON the plane, a casing stands proud of it - and
        # the sign below is where they part company. It also settles the one
        # case the per-edge rule below cannot: a jamb split into several edges,
        # where the wall's face and the reveal's are equally perpendicular to
        # the run and equally vertical, so nothing local can choose between
        # them. The run as a whole can.
        plane = None
        if not any(edges[i].pinned for i in eseq):
            plane = run_plane_normal(points, up_reference)
        if plane is not None:
            # A plane normal has no inherent sign; take it from which way the
            # solid lies. A skirting sits ON the solid, a frame stands clear of
            # it, so the same measurement is read with opposite signs - which is
            # the whole difference between the two tools at this point.
            vote = 0.0
            for i in eseq:
                support = edges[i].support
                if support is not None:
                    vote += support.dot(plane)
            if abs(vote) <= 1e-3 * len(eseq):
                # Nothing around the run leans either way - a wall modelled as
                # a single plane, with no reveal and no thickness. The plane
                # cannot be signed, so the faces are asked instead, below.
                plane = None
            else:
                if vote * (1.0 if kind == 'BASEBOARD' else -1.0) < 0.0:
                    plane = -plane
                for i in eseq:
                    edges[i].out = plane.copy()
                continue

        for k, edge_index in enumerate(eseq):
            edge = edges[edge_index]
            if edge.pinned or len(edge.candidates) < 2:
                continue

            nearby = [directions[i] for i in (k - 1, k, k + 1)
                      if 0 <= i < len(directions) and directions[i] is not None]
            if not nearby:
                continue

            def score(entry):
                vertical, out = entry
                # Rounded so "equally perpendicular" really does fall through to
                # the tie-break instead of being decided by float noise.
                parallelism = round(sum(abs(out.dot(d)) for d in nearby), 4)
                return (parallelism, vertical)

            edge.out = min(edge.candidates, key=score)[1]

        # Then make the SIGNS agree along the run. Each direction came from a
        # face normal, and neighbouring faces can be wound oppositely - a stair
        # welded together from separate step solids is the usual way that
        # happens. One flipped edge mid-run turns the board inside out there,
        # which reads as a twist or a gap rather than as an obvious mistake.
        #
        # Only near-parallel neighbours are corrected. A genuine corner turns
        # the direction by 90 degrees, where the dot product carries no sign
        # information at all and "agreement" is meaningless.
        for k in range(1, len(eseq)):
            previous = edges[eseq[k - 1]].out
            current = edges[eseq[k]].out
            alignment = current.dot(previous)
            if alignment < -0.7:
                edges[eseq[k]].out = -current


def gather_from_faces(bm, up, placement, tolerance):
    """The bottom (or top) boundary edges of every selected face.

    Selecting a whole wall is the fastest way to work, and this is what makes
    it mean "run a plinth along the foot of this wall": for each selected face
    the extreme height is found and only the edges lying at it are taken. A
    face whose normal is not roughly horizontal is skipped - a floor is not a
    wall, and taking its lowest edge would put a plinth across the middle of
    the room.
    """
    edges = {}
    coords = {}
    skipped = 0

    for face in bm.faces:
        if not face.select:
            continue
        normal = face.normal
        if normal.length < _EPS or abs(normal.normalized().dot(up)) > 0.7:
            skipped += 1
            continue

        heights = [v.co.dot(up) for v in face.verts]
        extreme = min(heights) if placement == 'BOTTOM' else max(heights)

        horizontal = normal.normalized()
        horizontal = horizontal - up * horizontal.dot(up)
        if horizontal.length < _EPS:
            skipped += 1
            continue
        horizontal.normalize()

        for edge in face.edges:
            v0, v1 = edge.verts
            if abs(v0.co.dot(up) - extreme) > tolerance or abs(v1.co.dot(up) - extreme) > tolerance:
                continue
            if (v1.co - v0.co).length < _EPS:
                continue
            key = (min(v0.index, v1.index), max(v0.index, v1.index))
            coords[v0.index] = v0.co.copy()
            coords[v1.index] = v1.co.copy()
            if key in edges:
                # Two selected faces share this edge (an inside corner column,
                # or a wall selected from both sides). Averaging their outward
                # directions would aim the profile into the wall, so the first
                # one wins and stays consistent along the run.
                continue
            edges[key] = RunEdge(v0.index, v1.index, horizontal.copy(),
                                 support=horizontal.copy())

    return list(edges.values()), coords, skipped


def gather_from_edges(bm, up, measure=False):
    """The selected edges themselves, with an outward direction derived from
    whichever adjacent face is most wall-like."""
    edges = []
    coords = {}
    skipped = 0

    for edge in bm.edges:
        if not edge.select:
            continue
        candidates, support = _edge_candidates(edge, up)
        if not candidates:
            skipped += 1
            continue
        v0, v1 = edge.verts
        coords[v0.index] = v0.co.copy()
        coords[v1.index] = v1.co.copy()
        # A provisional pick, refined by resolve_outs() once the chains are known.
        edges.append(RunEdge(v0.index, v1.index, candidates[0][1], candidates, support,
                             reach=_edge_reach(edge) if measure else None))

    return edges, coords, skipped


def gather_selection(bm, up, settings):
    """Read the run out of an edit-mode BMesh, in whichever way fits what the
    user actually selected."""
    bm.verts.index_update()
    bm.faces.index_update()
    bm.edges.index_update()
    bm.normal_update()

    mode = settings.source_mode
    if mode == 'AUTO':
        has_faces = any(f.select for f in bm.faces)
        mode = 'FACES' if has_faces else 'EDGES'

    if mode == 'FACES':
        edges, coords, skipped = gather_from_faces(
            bm, up, settings.placement, settings.bottom_tolerance)
        if edges:
            return edges, coords, skipped
        # Face select mode with nothing usable - fall through to the edges,
        # which are selected too and are probably what was meant.
    # Only a window needs to know what is behind the run, and measuring it is
    # not free on a long skirting - so it is measured only when it is wanted.
    return gather_from_edges(bm, up, measure=settings.kind == 'WINDOW')


def canonical_nodes(edges, coords, tolerance):
    """Group the run's vertices by POSITION, so a corner is one node.

    Chains used to be walked through shared vertex INDICES, which quietly
    assumed the run belongs to one welded surface. A staircase almost never
    does: it arrives as a box per step, or as steps and wall that merely touch,
    and then the tread edge and the riser edge meet at the same POINT while
    being two different VERTICES.

    The run then read as a pile of one-edge fragments - each with its own end
    caps and no miter anywhere - which is exactly the broken output: separate
    slabs floating at the step corners instead of a mitered board.

    Returns (node_of, node_co): vertex index -> node id, and node id -> its
    position. Neighbouring cells are searched as well as the point's own, so a
    pair straddling a cell boundary still meets.
    """
    cell = max(tolerance, 1e-6)
    buckets = {}
    node_of = {}
    node_co = {}

    def cell_key(co):
        return (int(math.floor(co.x / cell)),
                int(math.floor(co.y / cell)),
                int(math.floor(co.z / cell)))

    for edge in edges:
        for index in (edge.va, edge.vb):
            if index in node_of:
                continue
            position = coords[index]
            key = cell_key(position)

            found = None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for node in buckets.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                            if (node_co[node] - position).length <= tolerance:
                                found = node
                                break
                        if found is not None:
                            break
                    if found is not None:
                        break
                if found is not None:
                    break

            if found is None:
                found = len(node_co)
                node_co[found] = position.copy()
                buckets.setdefault(key, []).append(found)
            node_of[index] = found

    return node_of, node_co


def build_chains(edges, node_of):
    """Group the run edges into ordered, continuous chains.

    A chain is walked NODE to node - a node being a position, not a vertex, so
    a run assembled from separate boxes chains together like a welded one. It
    ends at a loose end, at a junction (3+ edges meet - there is no single
    correct way to continue), or back at its own start node for a closed loop
    such as a complete room. Chains starting from loose ends are emitted first
    so an open run is walked end to end rather than from some arbitrary middle.

    Returns [(node_sequence, edge_index_sequence)], where a closed loop repeats
    its start node at the end.
    """
    adjacency = {}
    for i, edge in enumerate(edges):
        adjacency.setdefault(node_of[edge.va], []).append(i)
        adjacency.setdefault(node_of[edge.vb], []).append(i)

    used = set()

    def walk(start):
        vseq = [start]
        eseq = []
        current = start
        while True:
            if eseq and (len(adjacency[current]) > 2 or current == start):
                break
            next_edge = next((i for i in adjacency[current] if i not in used), None)
            if next_edge is None:
                break
            used.add(next_edge)
            edge = edges[next_edge]
            node_a, node_b = node_of[edge.va], node_of[edge.vb]
            other = node_b if node_a == current else node_a
            eseq.append(next_edge)
            vseq.append(other)
            current = other
        return vseq, eseq

    chains = []
    ordered = sorted(adjacency)
    for v in ordered:
        if len(adjacency[v]) == 1 and adjacency[v][0] not in used:
            chains.append(walk(v))
    for v in ordered:
        while any(i not in used for i in adjacency[v]):
            chains.append(walk(v))

    return [chain for chain in chains if chain[1]], adjacency


# ===============================================================
# Sweeping the profile
# ===============================================================

class MeshData:
    """Everything needed to build the mesh, with UVs carried per face corner.

    Per-corner rather than per-vertex on purpose: the end caps share their
    vertices with the ring faces but need a completely different mapping, and
    a per-vertex UV would force the two to agree.
    """

    def __init__(self):
        self.verts = []
        self.faces = []          # list[tuple[int, ...]]
        self.face_uvs = []       # list[tuple[tuple[float, float], ...]], aligned with faces
        self.face_colors = []    # list[tuple[tuple[float, float, float, float], ...]]
        # Runs that End Gap shortened out of existence, so the operator can say
        # so rather than leaving the user to notice a missing stretch of wall.
        self.dropped_runs = 0


def _segment_frames(points, chain_outs):
    """Per-segment (direction, out, up) for one chain.

    `up` is the axis the profile's HEIGHT runs along, and it is derived per
    segment as `direction x out` rather than being world Z. That single change
    is what makes a staircase work.

    On a flat run the two are the same thing: direction and out are both
    horizontal, so their cross product is world Z - exactly what this used to
    hardcode. On a stair they are not. The run zigzags inside the wall plane,
    and on a riser the direction IS vertical, so extruding the profile along
    world Z extrudes it along the run and collapses the geometry. That is why
    the risers came out as disconnected slabs with nothing joining them.

    `out` is re-orthogonalised against the direction first, so a slightly
    off-perpendicular wall normal cannot skew the frame.
    """
    directions, outs, ups = [], [], []
    for k in range(len(chain_outs)):
        span = points[k + 1] - points[k]
        direction = span.normalized() if span.length > _EPS else None

        out = None
        up = None
        if direction is not None:
            out = chain_outs[k] - direction * chain_outs[k].dot(direction)
            if out.length > _EPS:
                out.normalize()
                # (out, up, direction) is right-handed: out x up = direction.
                up = direction.cross(out)
                up = up.normalized() if up.length > _EPS else None
            else:
                out = None

        if out is None or up is None:
            direction = None
        directions.append(direction)
        outs.append(out)
        ups.append(up)
    return directions, outs, ups


def _joint_vector(dir_a, dir_b, axis_a, axis_b, limit):
    """Where one profile axis lands at a joint between two segments.

    A profile point (o, u) sits at `node + o*out + u*up` on each segment, and
    the mitered position is where the two segments' extrusions of it meet:

        o*out_a + u*up_a + t*dir_a  =  o*out_b + u*up_b + s*dir_b

    That is linear in o and u, so solving it once per AXIS - once with
    (out_a, out_b) and once with (up_a, up_b) - gives a basis, and every
    profile point is then a linear combination of the two. Two small solves per
    node instead of one per point.

    Solved in the least-squares sense, because three equations carry only two
    unknowns; they are consistent whenever the joint is geometrically sensible,
    and least squares degrades gracefully when it is not.

    This is the same maths the old code did for the wall normals alone. Doing it
    for BOTH axes is what handles a joint where the wall stays put and the run
    changes direction inside it - which is every step of a staircase.
    """
    cosine = dir_a.dot(dir_b)
    denominator = 1.0 - cosine * cosine
    if denominator < 1e-9:
        # Collinear - the run carries straight on, and there is nothing to miter.
        return axis_a.copy()

    rhs = axis_b - axis_a
    t = (dir_a.dot(rhs) - cosine * dir_b.dot(rhs)) / denominator
    result = axis_a + dir_a * t
    if result.length > limit:
        # A near-180-degree fold sends the true miter off to infinity. Blunt
        # the joint instead of growing a spike across the whole model.
        result = result.normalized() * limit
    return result


def _chain_is_upside_down(points, chain_outs, up_reference):
    """Would this chain, walked as it stands, build the profile downwards?

    The profile's up axis is `direction x out`, so it reverses with the walking
    direction - and connectivity decides that, not the geometry. Left alone,
    one run in a room comes out standing and the next hanging.

    Judged over the whole chain and weighted by segment length, so a long
    horizontal stretch outvotes noise. On a staircase the vertical risers score
    zero either way, which leaves the treads to decide - exactly the segments
    whose orientation is unambiguous.
    """
    score = 0.0
    for k in range(len(chain_outs)):
        span = points[k + 1] - points[k]
        length = span.length
        if length < _EPS:
            continue
        candidate = span.normalized().cross(chain_outs[k])
        score += candidate.dot(up_reference) * length
    return score < 0.0


def _loop_grows_inward(points, chain_outs):
    """Would this CLOSED run, walked as it stands, build the profile into the
    hole it goes round?

    A window is the closed case of a casing: the loop lies on the wall's face
    and the frame has to grow away from the opening, never across it. Which way
    it grows depends only on which way round the loop happened to be walked -
    `up` is `direction x out`, and reversing the walk reverses it.

    The usual world-up test cannot say here. On a rectangle the sill votes up
    and the head votes down by exactly as much, and they cancel; a square window
    would then be decided by floating-point noise. So it is asked geometrically
    instead: does the profile's up axis lead away from the middle of the loop?
    Length-weighted, so on a wide window the sill and head decide it.
    """
    count = len(points) - 1                      # the last point repeats the first
    if count < 1:
        return False
    centre = Vector((0.0, 0.0, 0.0))
    for point in points[:count]:
        centre += point
    centre /= count

    score = 0.0
    for k in range(len(chain_outs)):
        span = points[k + 1] - points[k]
        length = span.length
        if length < _EPS:
            continue
        up_axis = span.normalized().cross(chain_outs[k])
        midpoint = (points[k] + points[k + 1]) * 0.5
        score += up_axis.dot(midpoint - centre) * length
    return score < 0.0


def _trim_chain(points, outs, start_amount, end_amount):
    """Pull an open run back from its ends by the given arc lengths.

    This is the door-frame case: the wall's bottom edge runs all the way into
    the opening, but the moulding has to stop short of the architrave. Trimming
    by ARC LENGTH rather than by moving the end vertex means a gap larger than
    the last segment keeps eating back through the run, dropping whole segments
    on the way, instead of overshooting past the vertex and folding the run
    back on itself.

    `points` and `outs` are modified copies; outs has one entry per segment, so
    a dropped segment drops its wall direction with it.

    Returns (points, outs), or None when the two gaps meet - a stub shorter
    than the clearance asked for is not a baseboard, and emitting a sliver is
    worse than emitting nothing.
    """
    points = list(points)
    outs = list(outs)

    def eat(from_start, amount):
        remaining = amount
        while remaining > _EPS:
            if len(points) < 2:
                return False
            a, b = (0, 1) if from_start else (-1, -2)
            span = points[b] - points[a]
            length = span.length
            if length < _EPS:
                # Degenerate segment - drop it and carry on.
                points.pop(a)
                outs.pop(0 if from_start else -1)
                continue
            if length > remaining + _EPS:
                points[a] = points[a] + span.normalized() * remaining
                return True
            remaining -= length
            points.pop(a)
            outs.pop(0 if from_start else -1)
        return True

    if not eat(True, start_amount) or not eat(False, end_amount):
        return None
    if len(points) < 2 or not outs:
        return None
    return points, outs


def _miter_basis(points, directions, outs, ups, closed, limit):
    """Per-node (out axis, up axis) for placing the profile.

    A profile point (o, u) at node j goes to `points[j] + o*A + u*B`. Scaling
    the basis by the profile's own coordinates - rather than mitering the front
    face alone - is what puts EVERY point of the section at its own correct
    distance from both surfaces meeting at the joint.
    """
    count = len(points)
    basis = []
    for j in range(count):
        incident = []
        if j > 0:
            incident.append(j - 1)
        elif closed:
            incident.append(len(directions) - 1)
        if j < count - 1:
            incident.append(j)
        elif closed:
            incident.append(0)

        incident = [k for k in incident if 0 <= k < len(directions) and directions[k] is not None]

        if not incident:
            basis.append((Vector((0.0, 0.0, 0.0)), Vector((0.0, 0.0, 0.0))))
        elif len(incident) == 1:
            k = incident[0]
            basis.append((outs[k].copy(), ups[k].copy()))
        else:
            a, b = incident
            basis.append((
                _joint_vector(directions[a], directions[b], outs[a], outs[b], limit),
                _joint_vector(directions[a], directions[b], ups[a], ups[b], limit),
            ))
    return basis


def _oriented(indices, uvs, positions, desired):
    """Return the face's corners in whichever winding makes its normal point
    toward `desired`.

    Computed against the real positions rather than assumed algebraically, so
    it stays correct no matter which direction the chain happened to be walked
    or how the outward vectors were sign-corrected upstream.
    """
    normal = (positions[1] - positions[0]).cross(positions[2] - positions[1])
    if normal.dot(desired) < 0.0:
        return tuple(reversed(indices)), tuple(reversed(uvs))
    return tuple(indices), tuple(uvs)


def opening_thickness(edges, chains, node_co):
    """The wall's thickness, measured from the selection rather than guessed.

    Two chains that face each other - their outward directions opposed - are
    the two faces of the same opening, and the distance between them along
    either normal is the thickness the reveal has to be lined over.

    Returns None when there is only one face selected, where there is nothing
    to measure against and the depth has to be given explicitly.
    """
    entries = []
    for vseq, eseq in chains:
        if eseq:
            entries.append((edges[eseq[0]].out, node_co[vseq[0]]))

    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            out_a, point_a = entries[i]
            out_b, point_b = entries[j]
            if out_a.dot(out_b) > -0.9:
                continue                      # not facing each other
            separation = abs((point_b - point_a).dot(out_a))
            if separation > 1e-6:
                return separation
    return None


def build_mesh_data(edges, chains, adjacency, coords, profile, up, settings, lining=0.0):
    """Sweep the profile along every chain.

    Geometry inside a chain is continuous by construction - consecutive
    cross-sections reference the same vertices - so only the joints BETWEEN
    chains (junctions, loop seams) depend on the merge pass afterwards.
    """
    data = MeshData()

    # Which way is "out of the wall" comes from the wall's own normal, i.e. the
    # side it is visible from. That is right for an interior modelled from the
    # inside, and exactly backwards for one whose faces point the other way -
    # in which case the whole run is built behind the wall. There is no way to
    # tell those apart from a single wall, so this is a switch rather than a
    # guess.
    flip = -1.0 if settings.flip_side else 1.0

    # A cornice hangs down from the ceiling line. Flipping the up axis mirrors
    # the whole profile without needing a second set of profiles; it is applied
    # per segment below, once the frames exist, because there is no single up
    # axis any more - on a staircase it changes at every step.
    up_sign = -1.0 if settings.placement == 'TOP' else 1.0

    scale = max(settings.uv_scale, 1e-4)
    offset_u = settings.uv_offset_u
    offset_v = settings.uv_offset_v

    # V is measured on the UNMITERED profile: at a corner the real section is
    # wider, and using that width would shear the texture along the run instead
    # of carrying the grain cleanly through the joint - which is exactly what a
    # real mitered trim does. Same reason U comes from the chain centreline.
    if lining > 0.0:
        # Prepended, so the lining comes before the profile's own bottom. Done
        # before the V arc length is measured, so the lining gets its own
        # stretch of texture rather than sharing the casing's.
        #
        # Flat, and only flat. Any stepping belongs on the architrave, where
        # the profile does it - a step cut into the reveal instead is a rebate
        # for a leaf, which is a different thing and was not what was wanted.
        profile = [(profile[0][0] - lining, profile[0][1])] + list(profile)

    profile_v = [0.0]
    for i in range(1, len(profile)):
        step = math.dist(profile[i], profile[i - 1])
        profile_v.append(profile_v[-1] + step)
    total_v = profile_v[-1] + math.dist(profile[0], profile[-1])

    # Which profile segments produce faces. The bottom sits on the floor and
    # the back against the wall, so both are off by default: they are invisible
    # in game and cost real triangles and z-fighting.
    #
    # Lining the opening is the exception. The extra point runs BACK from the
    # profile's own bottom, into the reveal, and then both that segment and the
    # bottom itself have to be drawn - together they make one continuous
    # surface from the depth of the opening out to the face of the casing.
    draw_bottom = settings.make_bottom or lining > 0.0
    segment_indices = list(range(len(profile) - 1))
    if not draw_bottom and segment_indices:
        segment_indices = segment_indices[1:]
    closing_segment = settings.make_back and len(profile) > 2

    profile_pairs = [(i, i + 1) for i in segment_indices]
    if closing_segment:
        profile_pairs.append((len(profile) - 1, 0))

    def segment_v(i0, i1):
        # Index 0 as the END of a segment means the back face wrapping around,
        # so it gets the full arc length rather than restarting at zero.
        return profile_v[i0], (profile_v[i1] if i1 != 0 else total_v)

    # V is zeroed on the part that is actually generated, not on the profile's
    # first point: with the bottom face off, the visible band would otherwise
    # start partway up the texture, and UV Offset V would not mean what it says.
    band = [value for pair in profile_pairs for value in segment_v(*pair)]
    v_min = min(band) if band else 0.0
    v_span = max((max(band) - v_min) if band else 1.0, 1e-6)

    def _to_uv(u, v):
        if settings.uv_mode == 'FIT_V':
            result = (u / scale + offset_u, v / v_span + offset_v)
        else:
            result = (u / scale + offset_u, v / scale + offset_v)
        return (result[1], result[0]) if settings.uv_rotate else result

    def map_uv(u, v):
        return _to_uv(u, v - v_min)

    def map_cap_uv(u, height):
        """Caps get a planar mapping across the section instead of the arc
        length one - they face along the run, not across it - carried by the
        run distance so two caps never land on the same texels."""
        return _to_uv(u, height)

    color = tuple(settings.color_rgb)
    alpha = settings.alpha
    ao = min(max(settings.ao_strength, 0.0), 1.0)
    ups = [point[1] for point in profile]
    lowest_up = min(ups)
    up_span = max(max(ups) - lowest_up, 1e-6)

    def color_at(index):
        """Fake contact shading: darkest where the moulding meets the floor,
        clean by the top.

        Baked into Colour0 rather than the texture, so the same tiling wood map
        works at any height and it costs nothing at runtime - normal_spec
        multiplies the vertex colour into the diffuse anyway. Driven by the
        profile's HEIGHT, not its arc length, so a deep profile does not shade
        differently from a shallow one of the same height.
        """
        if ao <= 0.0:
            return (color[0], color[1], color[2], alpha)
        t = min(max((profile[index][1] - lowest_up) / up_span, 0.0), 1.0)
        factor = 1.0 - ao * (1.0 - t)
        return (color[0] * factor, color[1] * factor, color[2] * factor, alpha)

    profile_colors = [color_at(i) for i in range(len(profile))]

    for vseq, eseq in chains:
        points = [coords[v] for v in vseq]
        chain_outs = [edges[i].out * flip for i in eseq]
        closed = len(vseq) > 2 and vseq[0] == vseq[-1]

        # Which ends are genuine ends of a run, decided BEFORE any trimming
        # moves the points: a vertex where only one selected edge meets. A
        # chain that was cut at a junction continues into another chain, and
        # pulling that back would tear a gap open in the middle of a corner.
        open_start = not closed and len(adjacency.get(vseq[0], ())) == 1
        open_end = not closed and len(adjacency.get(vseq[-1], ())) == 1

        # A chain is walked in whichever direction connectivity happened to
        # take it, and `direction x out` flips with it - so half the runs would
        # come out upside down. Orienting the chain once, by whether that
        # product agrees with world up over the run as a whole, fixes every
        # segment at once. Length-weighted, and the vertical segments of a
        # staircase contribute nothing either way, so the treads decide - which
        # is exactly the right thing to be decided by.
        # A closed frame - a window - has no up and down to agree with; what it
        # has is an opening to stay clear of, so that is what decides its walk.
        if closed and settings.kind != 'BASEBOARD':
            reversed_walk = _loop_grows_inward(points, chain_outs)
        else:
            reversed_walk = _chain_is_upside_down(points, chain_outs, up)
        if reversed_walk:
            points.reverse()
            chain_outs.reverse()
            open_start, open_end = open_end, open_start

        if settings.end_gap > 0.0 and (open_start or open_end):
            trimmed = _trim_chain(points, chain_outs,
                                  settings.end_gap if open_start else 0.0,
                                  settings.end_gap if open_end else 0.0)
            if trimmed is None:
                # The gaps ate the whole run. A stub shorter than the clearance
                # asked for is not a baseboard; dropping it is reported rather
                # than silently emitting a sliver.
                data.dropped_runs += 1
                continue
            points, chain_outs = trimmed

        directions, seg_outs, seg_ups = _segment_frames(points, chain_outs)
        if up_sign < 0.0:
            # Cornice: the profile hangs down instead of standing up. Flipping
            # the axis here, after the frames are built, keeps every face
            # normal below derived from the axis actually used.
            seg_ups = [u * up_sign if u is not None else None for u in seg_ups]
        basis = _miter_basis(points, directions, seg_outs, seg_ups, closed, settings.miter_limit)

        distance = [0.0]
        for j in range(1, len(points)):
            distance.append(distance[-1] + (points[j] - points[j - 1]).length)

        # One ring of vertices per node; a segment's quad simply references the
        # two rings it lies between, so nothing needs welding along the run.
        rings = []
        for j, point in enumerate(points):
            out_axis, up_axis = basis[j]
            ring = []
            for out_value, height in profile:
                position = point + out_axis * out_value + up_axis * height
                ring.append(len(data.verts))
                data.verts.append(position)
            rings.append(ring)

        def segment_u(k, j, i):
            """U for one corner, projected onto ITS OWN segment's direction.

            A mitered band cannot be flattened without distortion - the outer
            edge round an inside corner really is shorter than the centreline,
            so the surface has a cone point at every joint and no single
            continuous chart can be an isometry. Trying to make one was what
            stretched the texture: charging every profile point the centreline's
            length cost 29% on a room's top face and 36% on the front face of a
            staircase, where the joints fold in the run/up plane instead of the
            horizontal one.

            So the moulding is unwrapped the way it is actually built - as a
            run of flat boards meeting at diagonal cuts. Projecting each corner
            onto its own segment's direction makes the mapping an exact isometry
            on every face, and the two boards at a joint simply meet along that
            diagonal: their U agrees exactly on the centreline and separates
            outward from it, which is the miter cut itself.
            """
            offset = data.verts[rings[j][i]] - points[k]
            return distance[k] + offset.dot(directions[k])

        # len(chain_outs), not len(eseq): trimming can drop whole segments.
        for k in range(len(chain_outs)):
            if directions[k] is None:
                continue
            out_axis, up_axis = seg_outs[k], seg_ups[k]
            j0, j1 = k, k + 1

            for i0, i1 in profile_pairs:
                v0, v1 = segment_v(i0, i1)

                delta_out = profile[i1][0] - profile[i0][0]
                delta_up = profile[i1][1] - profile[i0][1]
                # Outward normal of this strip, from THIS segment's own frame -
                # never from the traversal direction, which flips arbitrarily
                # between chains. Per-segment, because on a staircase the up
                # axis is horizontal on a riser and vertical on a tread.
                desired = out_axis * delta_up - up_axis * delta_out
                if desired.length < _EPS:
                    continue

                indices = (rings[j0][i0], rings[j0][i1], rings[j1][i1], rings[j1][i0])
                uvs = (map_uv(segment_u(k, j0, i0), v0), map_uv(segment_u(k, j0, i1), v1),
                       map_uv(segment_u(k, j1, i1), v1), map_uv(segment_u(k, j1, i0), v0))
                colors = (profile_colors[i0], profile_colors[i1],
                          profile_colors[i1], profile_colors[i0])
                positions = [data.verts[i] for i in indices]

                oriented, oriented_uvs = _oriented(indices, uvs, positions, desired)
                if oriented != indices:
                    colors = tuple(reversed(colors))
                data.faces.append(oriented)
                data.face_uvs.append(oriented_uvs)
                data.face_colors.append(colors)

        if settings.cap_ends and len(profile) >= 3 and not closed:
            # Only a genuine loose end gets a cap - a node where several chains
            # meet is an interior joint, and capping it would bury a solid face
            # inside the corner. Decided above, before trimming moved anything.
            for j, is_open in ((0, open_start), (len(points) - 1, open_end)):
                if not is_open:
                    continue
                neighbour = points[1] if j == 0 else points[-2]
                axis = points[j] - neighbour
                if axis.length < _EPS:
                    continue
                # Away from the run at both ends, which falls out of the
                # subtraction itself - no per-end sign to get wrong.
                desired = axis.normalized()

                indices = tuple(rings[j])
                uvs = tuple(map_cap_uv(distance[j] + out, height) for out, height in profile)
                colors = tuple(profile_colors)
                positions = [data.verts[i] for i in indices]
                oriented, oriented_uvs = _oriented(indices, uvs, positions, desired)
                if oriented != indices:
                    colors = tuple(reversed(colors))
                data.faces.append(oriented)
                data.face_uvs.append(oriented_uvs)
                data.face_colors.append(colors)

    return data


# ===============================================================
# Turning MeshData into a real mesh
# ===============================================================

def create_mesh(name, data):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in data.verts], [], [list(f) for f in data.faces])
    mesh.update()
    return mesh


def write_uv_and_color(mesh, data):
    """Write UVMap 0 / Color 1 straight onto the loops.

    from_pydata preserves both face order and corner order, so polygon i is
    data.faces[i] and its loops run in the same order - which is what lets the
    per-face UVs computed during the sweep be laid down without any matching
    pass.

    Sollumz's naming is asked for rather than hardcoded, but the fallback is
    the known name so an object still rebuilds correctly with Sollumz disabled.
    normal_spec's vertex layout requires both TexCoord0 and Colour0, so both
    are always written even when every value is the default.
    """
    if not mesh.polygons:
        return

    loop_uv = [(0.0, 0.0)] * len(mesh.loops)
    loop_rgba = [(1.0, 1.0, 1.0, 1.0)] * len(mesh.loops)

    for polygon in mesh.polygons:
        index = polygon.index
        if index >= len(data.face_uvs):
            continue
        uvs = data.face_uvs[index]
        colors = data.face_colors[index]
        for corner, loop_index in enumerate(polygon.loop_indices):
            if corner < len(uvs):
                loop_uv[loop_index] = uvs[corner]
            if corner < len(colors):
                loop_rgba[loop_index] = colors[corner]

    uv_attribute = mesh.attributes.get(uv_map_name(0))
    if uv_attribute is None:
        uv_attribute = mesh.attributes.new(name=uv_map_name(0), type='FLOAT2', domain='CORNER')
    flat = []
    for uv in loop_uv:
        flat.extend(uv)
    uv_attribute.data.foreach_set("vector", flat)

    color_attribute = mesh.attributes.get(color_attr_name(0))
    if color_attribute is None:
        color_attribute = mesh.attributes.new(name=color_attr_name(0), type='BYTE_COLOR', domain='CORNER')
    flat = []
    for rgba in loop_rgba:
        flat.extend(rgba)
    # color_srgb, not color: BYTE_COLOR stores sRGB bytes, and writing linear
    # values here would quietly darken every vertex colour.
    color_attribute.data.foreach_set("color_srgb", flat)
    mesh.update()


def cleanup_mesh(mesh, distance):
    """Weld the joints between chains, then remove what nothing references.

    Two jobs, and the second one always runs:

      * WELD - runs once on the finished mesh so sections generated from
        different chains can weld to each other; geometry inside a chain is
        already continuous and has nothing to merge. Skipped when the distance
        is zero.

      * LOOSE VERTICES - the sweep lays a full ring of profile points down at
        every node, but the Bottom Face and Back Face toggles are off by
        default, and with both off the back-bottom point of the profile is not
        referenced by a single polygon. That left one stray vertex per node,
        sitting `Wall Inset` behind the wall and `Floor Sink` below the floor -
        invisible in the viewport until you go into Edit Mode, and quietly
        stretching the object's bounding box out through the wall.

        Deleting them here rather than not creating them keeps the ring
        indexing simple and stays correct for every combination of the face
        toggles and the end caps, including a cap that uses points no ring face
        does.

    Safe after the UV/Color pass - bmesh carries loop layers through, and the
    corners that get welded already agree.
    """
    bm = bmesh.new()
    bm.from_mesh(mesh)

    if distance > 0.0:
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=distance)
        bmesh.ops.dissolve_degenerate(bm, dist=distance, edges=bm.edges)

    loose = [vertex for vertex in bm.verts if not vertex.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context='VERTS')

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def apply_shading(mesh, mode, angle_degrees):
    """Flat, fully smooth, or smooth-with-sharp-edges.

    AUTO is the one that matters for mouldings: a bullnose or an ogee needs its
    curve smooth, while the front face meeting the top must stay a crisp line.
    Done with the sharp_edge attribute rather than a Smooth by Angle modifier,
    so the result is real mesh data that exports and that the live rebuild can
    reproduce without touching an operator.
    """
    if not mesh.polygons:
        return

    smooth = mode != 'FLAT'
    try:
        mesh.polygons.foreach_set("use_smooth", [smooth] * len(mesh.polygons))
    except Exception:
        attribute = mesh.attributes.get("sharp_face")
        if attribute is None:
            attribute = mesh.attributes.new(name="sharp_face", type='BOOLEAN', domain='FACE')
        attribute.data.foreach_set("value", [not smooth] * len(mesh.polygons))

    if mode != 'AUTO':
        existing = mesh.attributes.get("sharp_edge")
        if existing is not None:
            mesh.attributes.remove(existing)
        mesh.update()
        return

    threshold = math.radians(angle_degrees)
    sharp = [False] * len(mesh.edges)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.edges.index_update()
    for edge in bm.edges:
        if len(edge.link_faces) == 2 and edge.index < len(sharp):
            try:
                sharp[edge.index] = edge.calc_face_angle(0.0) > threshold
            except ValueError:
                sharp[edge.index] = False
    bm.free()

    attribute = mesh.attributes.get("sharp_edge")
    if attribute is None:
        attribute = mesh.attributes.new(name="sharp_edge", type='BOOLEAN', domain='EDGE')
    attribute.data.foreach_set("value", sharp)
    mesh.update()


def centre_origin(obj, mesh):
    """Origin to Geometry (median) through the data API.

    bpy.ops.object.origin_set is off limits in the live rebuild - Blender warns
    against calling operators from property update callbacks - so the median is
    subtracted from the vertices and folded back into the object matrix, which
    is exactly what the operator does.
    """
    if not mesh.vertices:
        return
    median = Vector((0.0, 0.0, 0.0))
    for vertex in mesh.vertices:
        median += vertex.co
    median /= len(mesh.vertices)
    for vertex in mesh.vertices:
        vertex.co -= median
    obj.matrix_world = obj.matrix_world @ Matrix.Translation(median)


def generate(source_obj, edges, coords, settings, mesh_name):
    """Full pipeline from a read selection to a finished mesh, or None.

    Shared by the create operator and the live rebuild so the two can never
    drift apart - which they would the moment one of them grew a step the
    other did not.

    Returns (mesh, runs, dropped runs, note), where the note is something worth
    telling the user that only this level can see.
    """
    # Nodes by position, not by vertex index: a staircase is usually a box per
    # step, and its corners are coincident vertices rather than shared ones.
    # The floor keeps exactly-coincident points together even with the cleanup
    # merge turned off.
    tolerance = max(settings.merge_distance, 1e-5)
    node_of, node_co = canonical_nodes(edges, coords, tolerance)

    chains, adjacency = build_chains(edges, node_of)
    if not chains:
        return None

    # Which surface each edge lies against can only be settled once the run it
    # belongs to is known - see resolve_outs(). Mutates `edges` in place, so the
    # caller serialises the resolved directions rather than the provisional ones.
    resolve_outs(edges, chains, node_co, local_up(source_obj.matrix_world), settings.kind)

    # How thick the wall is, which only the window tool asks for and only it
    # measures for (see _edge_reach). Wanted whether or not the far half is
    # being built: it is also how deep the reveal this half has to line is.
    reveal = wall_thickness(edges) if settings.kind == 'WINDOW' else 0.0
    note = None
    if settings.kind == 'WINDOW' and getattr(settings, "mirror", False) and reveal <= 1e-5:
        # Worth saying out loud: the switch is on, the viewport did not change,
        # and the reason is in the wall rather than in the tool.
        note = "Mirror found no far face - this wall has no thickness to measure."
    if settings.kind == 'WINDOW' and getattr(settings, "mirror", False) and reveal > 1e-5:
        coords = dict(coords)
        edges = list(edges) + mirrored_edges(edges, coords, reveal)
        # The far half is new geometry, so the run has to be nodded and chained
        # again. Not resolved again: both halves already know which way is out,
        # and the copies are pinned so nothing can re-decide it for them.
        node_of, node_co = canonical_nodes(edges, coords, tolerance)
        chains, adjacency = build_chains(edges, node_of)

    up = local_up(source_obj.matrix_world)
    profile = build_profile(settings)
    if len(profile) < 2:
        return None

    # A casing always closes the reveal. Leaving it open means looking straight
    # through the sides of the opening, which is never what anybody wants, so
    # it is not a switch - only how deep it runs is.
    lining = 0.0
    if settings.kind != 'BASEBOARD':
        lining = settings.lining_depth
        if lining <= 0.0:
            measured = opening_thickness(edges, chains, node_co)
            if measured is None:
                # Only one face of the opening was selected. A window still
                # knows how deep the reveal is - it measured it off the mesh -
                # so half a frame still lines its half of the reveal, and the
                # two halves meet in the middle whether they were made together
                # or one at a time.
                measured = reveal
            # Half each, so the two casings meet in the middle of the reveal
            # instead of laying two coincident surfaces over each other.
            lining = measured * 0.5 if measured else 0.0

    data = build_mesh_data(edges, chains, adjacency, node_co, profile, up, settings, lining)
    if not data.faces:
        return None

    mesh = create_mesh(mesh_name, data)
    write_uv_and_color(mesh, data)
    cleanup_mesh(mesh, settings.merge_distance)
    apply_shading(mesh, settings.shading, settings.sharp_angle)
    return mesh, len(chains) - data.dropped_runs, data.dropped_runs, note


# ===============================================================
# Settings, shared by the Scene panel, the operator and the object
# ===============================================================

SETTING_NAMES = (
    "kind", "source_mode", "placement", "flip_side", "bottom_tolerance", "mirror",
    "height", "depth",
    "profile_type", "amount", "steps", "step_depth",
    "outer_profile_type", "outer_amount", "outer_steps", "outer_step_depth",
    "custom_profile", "segments",
    "wall_inset", "floor_sink", "vertical_offset", "end_gap",
    "make_bottom", "make_back", "cap_ends",
    "lining_depth",
    "uv_mode", "uv_scale", "uv_offset_u", "uv_offset_v", "uv_rotate",
    "color_rgb", "alpha", "ao_strength",
    "shading", "sharp_angle",
    "merge_distance", "miter_limit",
    "material_mode", "material", "texture_dir",
    "bumpiness", "spec_intensity", "spec_falloff", "spec_fresnel",
)


# Settings that describe the MATERIAL rather than the mesh. On a generated
# object these write straight to the material instead of regenerating geometry -
# dragging Bumpiness and seeing nothing happen until you find a button is not a
# slider, it is a puzzle.
MATERIAL_SETTING_NAMES = frozenset((
    "material_mode", "material", "texture_dir",
    "bumpiness", "spec_intensity", "spec_falloff", "spec_fresnel",
))


DOORFRAME_DEFAULTS = {
    # A casing is worked against the opening and eased against the wall;
    # a skirting is the other way round. Same properties, different
    # starting point, so neither tool opens on a shape nobody wants.
    # A whole casing, dialled in - what the tool opens on for someone who has
    # just installed it and pressed the button. 150 mm across the wall, 40 mm
    # proud of it, one clean step against the opening and an eased outer edge.
    'height': 0.15,
    'depth': 0.04,
    'profile_type': 'STEP',
    'amount': 0.62,
    'steps': 1,
    'step_depth': 0.74,
    'outer_profile_type': 'CHAMFER',
    'outer_amount': 0.5,
}


# A window is the same casing, closed round the opening. Same shape to start
# from, and a half at a time: one loop of edges is one face of the wall, and
# Mirror is what asks for the other.
WINDOW_DEFAULTS = dict(DOORFRAME_DEFAULTS)


def settings_annotations(update=None, material_update=None, pointers=True,
                         overrides=None):
    """Every setting, defined once.

    The Scene panel, the operator and the generated object all use these same
    definitions, so a tooltip or a limit can only ever be changed in one place
    and the three can never disagree about what a value means.
    """
    # Each entry is recorded as (factory, kwargs) and only turned into a real
    # property at the end, once the loop below knows its NAME - which is what
    # decides whether it gets the geometry callback or the material one.
    def prop(factory, **kwargs):
        return factory, kwargs

    definitions = {
        "kind": prop(
            bpy.props.EnumProperty,
            name="Kind",
            description="Which trim this is. It decides which side of the run's surface the "
                        "geometry is built on, which the geometry alone cannot say",
            items=[
                ('BASEBOARD', "Baseboard", "Skirting or cornice lying on a wall"),
                ('DOORFRAME', "Door Frame", "Casing standing proud of a wall around an opening"),
                ('WINDOW', "Window Frame", "Casing around a closed opening, half a wall at a time"),
            ],
            default='BASEBOARD',
        ),
        "mirror": prop(
            bpy.props.BoolProperty,
            name="Mirror",
            description=("Build the matching half on the far face of the wall as well. "
                         "The wall's thickness is measured from the mesh, so the two halves "
                         "meet in the middle of the reveal"),
            default=False,
        ),
        "source_mode": prop(
            bpy.props.EnumProperty,
            name="Source",
            description="What the run is read from",
            items=[
                ('AUTO', "Auto", "Use selected faces if there are any, otherwise selected edges"),
                ('FACES', "Wall Faces", "Take the bottom (or top) boundary edges of the selected faces"),
                ('EDGES', "Edges", "Run along exactly the selected edges"),
            ],
            default='AUTO',
        ),
        "placement": prop(
            bpy.props.EnumProperty,
            name="Placement",
            description="Which end of the wall the moulding sits at",
            items=[
                ('BOTTOM', "Skirting", "Along the foot of the wall, growing upward"),
                ('TOP', "Cornice", "Along the top of the wall, growing downward"),
            ],
            default='BOTTOM',
        ),
        "flip_side": prop(
            bpy.props.BoolProperty,
            name="Flip Side",
            description=("Build the moulding on the other side of the wall. Turn on when the "
                         "run comes out behind the wall instead of in the room - that happens "
                         "when the wall's faces point outwards"),
            default=False,
        ),
        "bottom_tolerance": prop(
            bpy.props.FloatProperty,
            name="Edge Tolerance",
            description="How far from a face's lowest/highest point an edge may sit and still count as its boundary",
            default=0.002, min=0.0, max=1.0, precision=4, unit='LENGTH',
        ),

        "height": prop(
            bpy.props.FloatProperty,
            name="Height", description="How far up the wall the moulding reaches",
            default=0.12, min=0.001, max=2.0, precision=4, unit='LENGTH',
        ),
        "depth": prop(
            bpy.props.FloatProperty,
            name="Depth", description="How far the moulding stands out from the wall",
            default=0.02, min=0.001, max=1.0, precision=4, unit='LENGTH',
        ),
        "profile_type": prop(
            bpy.props.EnumProperty,
            name="Inner Edge",
            description="Shape of the edge nearest the opening, or nearest the floor on a skirting",
            items=INNER_PROFILE_ITEMS, default='SQUARE',
        ),
        "amount": prop(
            bpy.props.FloatProperty,
            name="Inner Size",
            description="How big the inner edge's shape is. For a chamfer, bullnose or cove "
                        "that is the size of the cut; for Stepped it is how far the steps run "
                        "along the face, which the step count then divides up",
            default=0.5, min=0.0, max=0.98,
        ),
        "steps": prop(
            bpy.props.IntProperty,
            name="Inner Steps",
            description="How many steps the inner edge is cut into, when it is Stepped",
            default=2, min=1, max=8,
        ),
        "step_depth": prop(
            bpy.props.FloatProperty,
            name="Inner Step Depth",
            description="How much thickness the inner edge's steps take off in total, as a "
                        "fraction of Depth. Size sets how far they run; this sets how deep they cut",
            default=0.7, min=0.0, max=1.0,
        ),
        "outer_profile_type": prop(
            bpy.props.EnumProperty,
            name="Outer Edge",
            description="Shape of the edge furthest from the opening, against the wall",
            items=PROFILE_ITEMS, default='CHAMFER',
        ),
        "outer_amount": prop(
            bpy.props.FloatProperty,
            name="Outer Size",
            description="How big the outer edge's shape is - the size of the cut, or the "
                        "width of the stepped band",
            default=0.5, min=0.0, max=0.98,
        ),
        "outer_steps": prop(
            bpy.props.IntProperty,
            name="Outer Steps",
            description="How many steps the outer edge is cut into, when it is Stepped",
            default=2, min=1, max=8,
        ),
        "outer_step_depth": prop(
            bpy.props.FloatProperty,
            name="Outer Step Depth",
            description="How much thickness the outer edge's steps take off in total",
            default=0.7, min=0.0, max=1.0,
        ),
        "custom_profile": prop(
            bpy.props.StringProperty,
            name="Custom Profile",
            description=("Points as \"out,up out,up ...\" in fractions of Depth and Height, from "
                         "the wall at the inner edge, out to the front, and back to the wall at "
                         "the outer one. Replaces both edges. "
                         "Example: 0,0 1,0 1,0.6 0.5,0.8 0.5,1 0,1"),
            default="0,0 1,0 1,0.6 0.5,0.8 0.5,1 0,1",
        ),
        "segments": prop(
            bpy.props.IntProperty,
            name="Segments",
            description="Subdivisions in a curved edge. Each one costs a quad per run segment - "
                        "3 or 4 is plenty for an interior prop",
            default=3, min=1, max=24,
        ),
        "wall_inset": prop(
            bpy.props.FloatProperty,
            name="Wall Inset",
            description=("Bury the back of the profile this far into the wall, closing any hairline "
                         "gap on a wall that is not perfectly flat. Leave at 0 for single-plane "
                         "walls - there the buried part comes out the other side"),
            default=0.0, min=0.0, max=0.2, precision=4, unit='LENGTH',
        ),
        "floor_sink": prop(
            bpy.props.FloatProperty,
            name="Floor Sink",
            description=("Bury the bottom of the profile this far below the floor line, for the "
                         "same reason as Wall Inset, and with the same caveat on a single-plane floor"),
            default=0.0, min=0.0, max=0.2, precision=4, unit='LENGTH',
        ),
        "vertical_offset": prop(
            bpy.props.FloatProperty,
            name="Vertical Offset", description="Raise or lower the whole moulding along the wall",
            default=0.0, min=-2.0, max=2.0, precision=4, unit='LENGTH',
        ),

        "make_bottom": prop(
            bpy.props.BoolProperty,
            name="Bottom Face",
            description="Generate the face lying on the floor. Off by default - it is invisible and z-fights with the floor",
            default=False,
        ),
        "make_back": prop(
            bpy.props.BoolProperty,
            name="Back Face",
            description="Generate the face against the wall. Off by default - it is invisible in game",
            default=False,
        ),
        "end_gap": prop(
            bpy.props.FloatProperty,
            name="End Gap",
            description=("Stop the moulding this far short of each open end of a run, to clear a "
                         "door frame or an architrave. Only the genuine ends are pulled back - a "
                         "corner, or a junction where two runs meet, is never opened up"),
            default=0.0, min=0.0, max=2.0, precision=4, unit='LENGTH',
        ),
        "lining_depth": prop(
            bpy.props.FloatProperty,
            name="Lining Depth",
            description=("How far the lining runs back into the opening. At 0 it is measured "
                         "from the selection: with both faces of the wall selected, each casing "
                         "lines half the thickness and the two meet in the middle"),
            default=0.0, min=0.0, max=2.0, precision=4, unit='LENGTH',
        ),
        "cap_ends": prop(
            bpy.props.BoolProperty,
            name="Cap Ends",
            description="Close the open ends of a run, e.g. where it stops at a doorway. Junctions between runs are never capped",
            default=True,
        ),

        "uv_mode": prop(
            bpy.props.EnumProperty,
            name="UV Mode", description="How the unwrap is scaled",
            items=[
                ('WORLD', "World Units", "Both axes in metres per tile - square texels, tiles seamlessly along any length"),
                ('FIT_V', "Fit Profile", "The cross-section fills 0..1 across, for a texture painted as one plinth section"),
            ],
            default='WORLD',
        ),
        "uv_scale": prop(
            bpy.props.FloatProperty,
            name="Texture Scale", description="Metres of surface per texture tile",
            default=1.0, min=0.001, max=100.0, precision=3, unit='LENGTH',
        ),
        "uv_offset_u": prop(
            bpy.props.FloatProperty, name="UV Offset U",
            description="Slide the texture along the run", default=0.0, min=-100.0, max=100.0,
        ),
        "uv_offset_v": prop(
            bpy.props.FloatProperty, name="UV Offset V",
            description="Slide the texture across the profile", default=0.0, min=-100.0, max=100.0,
        ),
        "uv_rotate": prop(
            bpy.props.BoolProperty,
            name="Rotate UVs 90°",
            description=("Swap the two axes. On by default: the bundled wood runs its grain "
                         "across the tile, so unrotated it would cross the board instead of "
                         "following it"),
            default=True,
        ),

        "color_rgb": prop(
            bpy.props.FloatVectorProperty,
            name="Vertex Color", description="Colour written into Color 1, which the shader multiplies into the diffuse",
            subtype='COLOR', size=3, default=(1.0, 1.0, 1.0), min=0.0, max=1.0,
        ),
        "alpha": prop(
            bpy.props.FloatProperty,
            name="Alpha", description="Alpha written into Color 1",
            default=1.0, min=0.0, max=1.0,
        ),
        "ao_strength": prop(
            bpy.props.FloatProperty,
            name="Contact Shade",
            description="Darken the vertex colour toward the floor, for free contact shading",
            default=0.0, min=0.0, max=1.0,
        ),

        "shading": prop(
            bpy.props.EnumProperty,
            name="Shading", description="How the generated mesh is shaded",
            items=[
                ('AUTO', "Auto Smooth", "Smooth, with edges sharper than the angle below kept crisp"),
                ('FLAT', "Flat", "Every face flat"),
                ('SMOOTH', "Smooth", "Every face smooth"),
            ],
            default='AUTO',
        ),
        "sharp_angle": prop(
            bpy.props.FloatProperty,
            name="Sharp Angle", description="Edges bending more than this stay sharp",
            default=40.0, min=0.0, max=180.0,
        ),

        "merge_distance": prop(
            bpy.props.FloatProperty,
            name="Merge Distance",
            description="Weld vertices this close together, joining runs that meet at a junction",
            default=0.0002, min=0.0, max=0.1, precision=5, unit='LENGTH',
        ),
        "miter_limit": prop(
            bpy.props.FloatProperty,
            name="Miter Limit",
            description="Longest a corner miter may grow, in multiples of Depth. Stops a very sharp fold spiking off",
            default=4.0, min=1.0, max=20.0,
        ),

        "material_mode": prop(
            bpy.props.EnumProperty,
            name="Material", description="Which material the baseboard gets",
            items=[
                ('AUTO', "Bundled",
                 "Build a normal_spec.sps material from the textures in the folder below, "
                 "reusing it between baseboards. This is the out-of-the-box default"),
                ('PICK', "Existing",
                 "Use a material already in this file - your own wood, or one from an asset "
                 "you have imported"),
                ('NEW', "Always New",
                 "Build a separate bundled material every time instead of sharing one"),
                ('NONE', "None", "Leave the object without a material"),
            ],
            default='AUTO',
        ),
        # "material" itself is added after this dict - see below. It has to be a
        # PointerProperty on the panels so the user gets Blender's own material
        # browser, and operators cannot hold an ID pointer at all, so the
        # operator is given the material's NAME instead.
        "texture_dir": prop(
            bpy.props.StringProperty,
            name="Texture Folder",
            description="Where to look for the diffuse / _n / _s maps. Empty means next to luman_tools.py",
            default="", subtype='DIR_PATH',
        ),

        "bumpiness": prop(
            bpy.props.FloatProperty,
            name="Bumpiness", description="normal_spec bumpiness - strength of the normal map",
            default=1.0, min=0.0, max=10.0,
        ),
        "spec_intensity": prop(
            bpy.props.FloatProperty,
            name="Specular Intensity", description="normal_spec specularIntensityMult",
            default=0.35, min=0.0, max=10.0,
        ),
        "spec_falloff": prop(
            bpy.props.FloatProperty,
            name="Specular Falloff", description="normal_spec specularFalloffMult - higher is a tighter highlight",
            default=120.0, min=0.0, max=1000.0,
        ),
        "spec_fresnel": prop(
            bpy.props.FloatProperty,
            name="Specular Fresnel", description="normal_spec specularFresnel",
            default=0.97, min=0.0, max=1.0,
        ),
    }

    annotations = {}
    for name, (factory, kwargs) in definitions.items():
        if overrides and name in overrides:
            kwargs = dict(kwargs, default=overrides[name])
        if material_update is not None and name in MATERIAL_SETTING_NAMES:
            callback = material_update
        else:
            callback = update
        if callback is not None:
            kwargs = dict(kwargs, update=callback)
        annotations[name] = factory(**kwargs)

    # The chosen material, in whichever form the owner can hold.
    extra = {"update": material_update or update} if (material_update or update) else {}
    if pointers:
        annotations["material"] = bpy.props.PointerProperty(
            name="Use Material",
            description=("The material to assign. Any material in this file - your own wood, or "
                         "one from an asset you imported. It is assigned as-is and never "
                         "retextured"),
            type=bpy.types.Material, **extra)
    else:
        annotations["material_name"] = bpy.props.StringProperty(
            name="Use Material",
            description="Name of the material to assign",
            default="", **extra)
    return annotations


def resolve_material_choice(settings):
    """The material the user picked, whichever way it is stored.

    Panels hold a real PointerProperty; the create operator holds a name,
    because Blender does not allow an ID pointer on an operator property.
    """
    material = getattr(settings, "material", None)
    if material is not None:
        return material
    name = getattr(settings, "material_name", "")
    return bpy.data.materials.get(name) if name else None


def copy_settings(source, target):
    for name in SETTING_NAMES:
        if name == "material":
            # Translates between the two forms - a pointer on the panels, a
            # name on the operator - so the same call works either direction.
            chosen = resolve_material_choice(source)
            if hasattr(target, "material"):
                target.material = chosen
            elif hasattr(target, "material_name"):
                target.material_name = chosen.name if chosen is not None else ""
            continue
        value = getattr(source, name)
        if hasattr(value, "__len__") and not isinstance(value, str):
            value = tuple(value)
        setattr(target, name, value)


class LUMAN_PG_baseboard_settings(bpy.types.PropertyGroup):
    __annotations__ = settings_annotations()


class LUMAN_PG_doorframe_settings(bpy.types.PropertyGroup):
    __annotations__ = settings_annotations(overrides=DOORFRAME_DEFAULTS)


class LUMAN_PG_window_settings(bpy.types.PropertyGroup):
    __annotations__ = settings_annotations(overrides=WINDOW_DEFAULTS)


# ===============================================================
# Per-object data and the live rebuild
# ===============================================================

_rebuilding = False


def serialise_edges(edges):
    """Store the run as "va,vb,ox,oy,oz ...".

    Vertex indices rather than positions, so moving, rotating or scaling the
    source still rebuilds correctly. The outward vector is stored alongside
    because the generic "most vertical face wins" rule cannot know which face
    the user actually selected - on rebuild it is used to pick the matching
    candidate rather than being trusted blindly, so a rotated wall still works.
    """
    return " ".join(
        "%d,%d,%.6g,%.6g,%.6g" % (e.va, e.vb, e.out.x, e.out.y, e.out.z)
        for e in edges
    )


def parse_edges(text):
    keys = []
    for token in text.split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            va, vb = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        out = Vector((0.0, 0.0, 0.0))
        if len(parts) >= 5:
            try:
                out = Vector((float(parts[2]), float(parts[3]), float(parts[4])))
            except ValueError:
                out = Vector((0.0, 0.0, 0.0))
        keys.append((va, vb, out))
    return keys


def edges_from_keys(source_obj, keys, measure=False):
    """Re-read the stored run out of the source mesh.

    Returns (edges, coords, missing) - `missing` counts edges that are no
    longer there, which is how a source that has been re-topologised gets
    reported instead of silently producing nonsense.
    """
    up = local_up(source_obj.matrix_world)

    bm = bmesh.new()
    bm.from_mesh(source_obj.data)
    bm.verts.ensure_lookup_table()
    bm.faces.index_update()
    bm.normal_update()

    by_key = {}
    for edge in bm.edges:
        a, b = edge.verts[0].index, edge.verts[1].index
        by_key[(a, b)] = edge
        by_key[(b, a)] = edge

    edges = []
    coords = {}
    missing = 0
    for va, vb, stored_out in keys:
        edge = by_key.get((va, vb))
        if edge is None:
            missing += 1
            continue

        # Re-derive the candidates from the source - so a rotated or reshaped
        # wall is followed - but let the STORED direction choose among them, so
        # a rebuild stays faithful to the surface the run was originally
        # resolved onto. Pinning it to one candidate also makes resolve_outs()
        # a no-op for this edge, which is what stops a rebuild from quietly
        # re-deciding something the user has already seen and accepted.
        candidates, support = _edge_candidates(edge, up)
        if candidates and stored_out.length > _EPS:
            # Matched on the AXIS, by absolute dot product, then given the
            # stored sign back. A signed comparison loses to any perpendicular
            # candidate as soon as the face is wound the other way from the
            # direction that was stored - which is exactly the case on a stair,
            # where the run's plane normal is signed by where the solid is and
            # the side face's normal points the opposite way.
            best = max(candidates, key=lambda item: abs(item[1].dot(stored_out)))
            direction = best[1] if best[1].dot(stored_out) >= 0.0 else -best[1]
            candidates = [(best[0], direction)]
        elif not candidates:
            if stored_out.length <= _EPS:
                missing += 1
                continue
            candidates = [(0.0, stored_out.normalized())]

        v0, v1 = edge.verts
        coords[v0.index] = v0.co.copy()
        coords[v1.index] = v1.co.copy()
        edges.append(RunEdge(v0.index, v1.index, candidates[0][1], candidates,
                             support, pinned=True,
                             reach=_edge_reach(edge) if measure else None))

    bm.free()
    return edges, coords, missing


def rebuild(obj):
    """Regenerate `obj` from its stored source, run and settings.

    Returns a short status string for the panel, or None when all is well.
    Never raises: this runs from a UI callback, where an exception would print
    a traceback on every mouse move.
    """
    global _rebuilding
    if _rebuilding:
        return None

    data = obj.luman_baseboard_data
    source = data.source_object
    if source is None:
        return "Source object is gone - this baseboard can no longer rebuild."
    if source.type != 'MESH':
        return "Source object is not a mesh."
    if source.mode == 'EDIT':
        return "Source is in Edit Mode - leave it to rebuild."

    keys = parse_edges(data.edge_keys)
    if not keys:
        return "No stored edge selection."

    _rebuilding = True
    try:
        edges, coords, missing = edges_from_keys(source, keys,
                                                 measure=data.kind == 'WINDOW')
        if not edges:
            return "Stored edges are gone from the source mesh (was it edited?)."

        old_mesh = obj.data
        materials = list(old_mesh.materials)

        result = generate(source, edges, coords, data, old_mesh.name)
        if result is None:
            return "Current settings produce no geometry."
        new_mesh, _, dropped, note = result

        for material in materials:
            new_mesh.materials.append(material)

        # Put the object back onto the source before re-centring, so repeated
        # rebuilds cannot accumulate origin drift.
        obj.matrix_world = source.matrix_world.copy()
        obj.data = new_mesh
        centre_origin(obj, new_mesh)

        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)

        if dropped:
            # Shown in the panel's status line: dragging End Gap past the
            # length of a short run makes it vanish, and that needs saying.
            return f"End Gap is longer than {dropped} run(s) - they were left out."
        if note:
            return note
        if missing:
            return f"{missing} stored edge(s) no longer exist in the source mesh."
        return None
    except Exception as error:
        return f"Rebuild failed: {error}"
    finally:
        _rebuilding = False


def scene_settings_for(context, kind):
    """The panel settings belonging to a tool."""
    scene = context.scene
    if kind == 'DOORFRAME':
        return scene.luman_doorframe
    if kind == 'WINDOW':
        return scene.luman_window
    return scene.luman_baseboard


def remember_settings(context, data):
    """Push a generated object's settings back onto its tool's panel.

    Tuning a frame and then deleting it used to throw the tuning away: the panel
    still held whatever the CREATE operator wrote at the time, not what was
    dialled in afterwards, so the next one came out of the old shape. Now the
    panel follows whatever was last actually looked at, and building the same
    thing somewhere else is just pressing the button again.

    Safe to call from an update callback: the panel's own properties carry no
    update of their own, so writing to them cannot come back round.
    """
    if context is None:
        return
    try:
        copy_settings(data, scene_settings_for(context, data.kind))
    except Exception:
        # A UI callback must never raise - it runs on every mouse move.
        pass


def _on_setting_changed(self, context):
    obj = getattr(self, "id_data", None)
    if obj is None or not isinstance(obj, bpy.types.Object):
        return
    if _rebuilding or not self.is_baseboard:
        return
    # Remembered even with live update paused: the value is what the next one
    # should start from whether or not this one has been rebuilt yet.
    remember_settings(context, self)
    if not self.live_update:
        return
    self.status = rebuild(obj) or ""


def _on_material_setting_changed(self, context):
    """A shader value or the texture folder changed - rewrite the material
    rather than regenerating the mesh, which these settings do not affect.

    The material may well be shared with other baseboards, and they change with
    it. That is what a shared material is, and it is what makes dialling in one
    plinth dial in the whole interior.
    """
    obj = getattr(self, "id_data", None)
    if obj is None or not isinstance(obj, bpy.types.Object):
        return
    if _rebuilding or not self.is_baseboard:
        return
    remember_settings(context, self)

    try:
        # Picking a different material swaps it straight away - that is the
        # whole point of a picker, and it is not a geometry change.
        if self.material_mode == 'PICK':
            chosen = self.material
            if chosen is not None and obj.active_material is not chosen:
                set_object_material(obj, chosen)
            return
        if self.material_mode == 'NONE':
            return

        material = obj.active_material
        # Only ever rewrite a material this tool built. A material the user
        # picked, or made by hand, is theirs.
        if material is None or not material.name.startswith(MATERIAL_NAME_PREFIX):
            return
        apply_material_settings(material, self)
    except Exception:
        # A UI callback must never raise - it would print a traceback on every
        # mouse move.
        pass


def _object_annotations():
    annotations = {
        "is_baseboard": bpy.props.BoolProperty(
            name="Is Baseboard",
            description="Marks an object as generated by this tool",
            default=False,
        ),
        "source_object": bpy.props.PointerProperty(
            name="Source",
            description="The mesh this baseboard was generated from. Rebuilding reads its edges again",
            type=bpy.types.Object,
        ),
        "edge_keys": bpy.props.StringProperty(
            name="Edge Keys",
            description="Source-mesh vertex index pairs, and outward directions, of the run this was built along",
            default="",
        ),
        "live_update": bpy.props.BoolProperty(
            name="Live Update",
            description="Rebuild immediately whenever a setting changes. Turn off on very long runs and use Rebuild Now",
            default=True,
        ),
        "status": bpy.props.StringProperty(name="Status", default=""),
    }
    annotations.update(settings_annotations(update=_on_setting_changed,
                                            material_update=_on_material_setting_changed))
    return annotations


class LUMAN_PG_baseboard_object(bpy.types.PropertyGroup):
    __annotations__ = _object_annotations()


# ===============================================================
# Operators
# ===============================================================

def _base_name(name):
    return _SUFFIX_PATTERN.sub("", name)


def _get_or_create_collection(context, name):
    """Find or create our collection, reusing one Blender had to rename, and
    never adopting an unrelated collection the user already has elsewhere."""
    parent = context.scene.collection
    for child in parent.children:
        if _base_name(child.name) == name:
            return child
    existing = bpy.data.collections.get(name)
    if existing is not None and existing not in parent.children_recursive:
        parent.children.link(existing)
        return existing
    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    return collection


def _next_name(prefix):
    """Explicit sequential naming (baseboard_001, doorframe_001, ...) rather
    than relying on Blender's automatic .001 suffixing."""
    pattern = re.compile(_NAME_PATTERN % re.escape(prefix))
    highest = 0
    for name in bpy.data.objects.keys():
        match = pattern.match(name)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}_{highest + 1:03d}"


def _select_only(context, obj):
    for other in context.selected_objects:
        if other is not obj:
            other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


# What both casings settle for themselves rather than offering. A casing has
# nothing to decide in Fit or Advanced: its feet are left open, since they sit
# on the floor and a cap there is a face nobody can ever see and one more thing
# for the exporter to carry; it never stops short of anything; the reveal is
# always closed to the thickness measured from the wall itself; and the run can
# only ever come from edges - an opening's outline is not a set of faces to take
# the boundary of.
_FRAME_FORCED = {"cap_ends": False, "end_gap": 0.0, "lining_depth": 0.0,
                 "wall_inset": 0.0, "floor_sink": 0.0, "vertical_offset": 0.0,
                 "source_mode": 'EDGES', "make_bottom": False, "make_back": False}
_FRAME_DEFAULTS = ("merge_distance", "miter_limit", "bottom_tolerance")


class _CreateTrim:
    """Shared body of both create operators.

    A PLAIN mixin, not an Operator subclass: Blender maps an RNA struct back to
    one Python class, and subclassing a registered operator breaks that mapping
    for the parent - it stops being callable at all. The two tools are the same
    machinery aimed at different surfaces (see resolve_outs), so they share the
    implementation and differ only in these four attributes.
    """
    # No 'REGISTER': the redo panel vanishes as soon as you click anything
    # else, and every value it would offer is on the finished object instead,
    # where it rebuilds live and stays reachable.
    bl_options = {'UNDO'}

    panel_prop = "luman_baseboard"
    forced_kind = 'BASEBOARD'
    name_prefix = "baseboard"
    target_collection = COLLECTION_NAME
    # Values the tool decides for itself rather than offering. Applied after the
    # panel is read, so they hold even in a file where the settings were dragged
    # about before these controls were taken away.
    forced_values = {"mirror": False}
    forced_defaults = ()

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and context.mode == 'EDIT_MESH'

    def _seed_from_panel(self, context):
        """Fill in anything the caller did not set from the N-panel.

        Done here rather than in invoke() because Blender skips invoke()
        entirely in background mode, and the tool must behave the same driven
        from a script as from the button.
        """
        panel = getattr(context.scene, self.panel_prop)
        for name in SETTING_NAMES:
            if name == "material":
                if not self.properties.is_property_set("material_name"):
                    self.material_name = panel.material.name if panel.material else ""
                continue
            if not self.properties.is_property_set(name):
                value = getattr(panel, name)
                if hasattr(value, "__len__") and not isinstance(value, str):
                    value = tuple(value)
                setattr(self, name, value)

    def execute(self, context):
        self._seed_from_panel(context)
        # Never taken from the panel: which tool was pressed IS the answer.
        self.kind = self.forced_kind
        for name, value in self.forced_values.items():
            setattr(self, name, value)
        for name in self.forced_defaults:
            # On self.properties, not on self: an operator's RNA values live
            # there, and the operator itself has no such method.
            self.properties.property_unset(name)

        source_obj = context.active_object
        source_mesh = source_obj.data

        # Read the selection while still in Edit Mode; everything is copied out
        # as plain Vectors in the source object's local space, so it stays
        # valid after leaving edit mode below.
        bm = bmesh.from_edit_mesh(source_mesh)
        up = local_up(source_obj.matrix_world)
        edges, coords, skipped = gather_selection(bm, up, self)

        if not edges:
            self.report({'WARNING'},
                        "Nothing usable selected. Select the wall faces, or the edges where "
                        "wall meets floor, and try again.")
            return {'CANCELLED'}

        name = _next_name(self.name_prefix)
        result = generate(source_obj, edges, coords, self, name)
        if result is None:
            self.report({'WARNING'}, "These settings produce no geometry from that selection.")
            return {'CANCELLED'}
        mesh, chain_count, dropped, note = result

        # Done reading the source BMesh - leave Edit Mode so a new object can
        # safely be created, selected and parented. The source is never touched.
        bpy.ops.object.mode_set(mode='OBJECT')

        new_obj = bpy.data.objects.new(name, mesh)
        # Built entirely in source-local space, so copying the world matrix
        # places it exactly on the wall regardless of the source's transform.
        new_obj.matrix_world = source_obj.matrix_world.copy()

        # Part of a Drawable? Then it belongs beside the rest of that asset,
        # not in a tool-named collection off to the side - an object parented
        # to a Drawable but linked elsewhere shows up greyed out under it and
        # gets left behind by anything working on the Drawable's collection.
        drawable = find_drawable_parent(source_obj)
        collections = list(drawable.users_collection) if drawable is not None else []
        if not collections:
            collections = [_get_or_create_collection(context, self.target_collection)]
        for collection in collections:
            collection.objects.link(new_obj)

        _select_only(context, new_obj)

        if drawable is not None:
            new_obj.parent = drawable
            new_obj.matrix_parent_inverse = drawable.matrix_world.inverted()
            try:
                convert_to_drawable_model(new_obj)
            except Exception as error:
                self.report({'WARNING'},
                            f"'{name}' was created, but could not be registered as a Drawable Model: {error}")

        warnings = []
        if self.material_mode != 'NONE':
            try:
                material, warnings = build_material(self, reuse=(self.material_mode != 'NEW'))
                assign_material(new_obj, material)
            except Exception as error:
                warnings.append(f"material assignment failed: {error}")

        centre_origin(new_obj, mesh)

        # Stamp everything needed to regenerate later. Suppressed because each
        # assignment fires the live-rebuild callback, which would regenerate
        # the mesh before the operator has finished setting the object up.
        global _rebuilding
        previous = _rebuilding
        _rebuilding = True
        try:
            data = new_obj.luman_baseboard_data
            data.is_baseboard = True
            data.source_object = source_obj
            data.edge_keys = serialise_edges(edges)
            copy_settings(self, data)
            data.status = ""
        finally:
            _rebuilding = previous

        # Push the values that actually produced this result back onto the
        # panel, so a tweak becomes the starting point for the next run.
        copy_settings(self, getattr(context.scene, self.panel_prop))

        message = f"Created '{name}': {chain_count} run(s), {len(mesh.polygons)} face(s)."
        if skipped:
            message += f" Skipped {skipped} unusable selection(s)."
        self.report({'INFO'}, message)

        if dropped:
            self.report({'WARNING'},
                        f"End Gap ({self.end_gap:.3f}) is longer than {dropped} run(s), "
                        f"so they were left out entirely.")
        if note:
            self.report({'WARNING'}, note)
        for warning in warnings:
            self.report({'WARNING'}, warning)
        return {'FINISHED'}


class LUMAN_OT_create_baseboard(_CreateTrim, bpy.types.Operator):
    """Generate a mitered baseboard along the selected wall faces or edges"""
    bl_idname = "object.luman_create_baseboard"
    bl_label = "Create Baseboard"

    # pointers=False: an operator property cannot be an ID pointer, so the
    # chosen material travels as a name here. Built per class rather than
    # shared - a property definition is consumed when its class registers.
    __annotations__ = settings_annotations(pointers=False)


class LUMAN_OT_create_door_frame(_CreateTrim, bpy.types.Operator):
    """Generate a door casing from the selected edges of an opening.

    Select the opening's outline - the two jambs and the head - on each face of
    the wall, six edges for a normal doorway, and the casing is swept around
    them OUTWARD: away from the opening so the clear width is never reduced,
    and proud of the wall face rather than into it. The two top corners are
    mitered at 45 degrees by the same solver the baseboard uses.
    """
    bl_idname = "object.luman_create_door_frame"
    bl_label = "Create Door Frame"

    __annotations__ = settings_annotations(pointers=False,
                                           overrides=DOORFRAME_DEFAULTS)

    panel_prop = "luman_doorframe"
    forced_kind = 'DOORFRAME'
    name_prefix = "doorframe"
    target_collection = DOORFRAME_COLLECTION_NAME
    # A doorway is open at the bottom, so it is selected on both faces of the
    # wall and there is nothing to mirror - see _FRAME_FORCED.
    forced_values = dict(_FRAME_FORCED, mirror=False)
    # Tolerances the tool needs but nobody should have to think about. Unset
    # rather than pinned to a number, so they follow the shipped default even
    # after it is changed, and so a stale value in an old file cannot break the
    # miter maths.
    forced_defaults = _FRAME_DEFAULTS


class LUMAN_OT_create_window_frame(_CreateTrim, bpy.types.Operator):
    """Generate a window casing from a selected loop of an opening.

    Select the outline of the opening on ONE face of the wall - four edges for
    a plain window - and the casing is swept round it OUTWARD, away from the
    opening so the daylight is never narrowed, and proud of the wall face
    rather than into it. All four corners are mitered by the same solver the
    baseboard uses.

    That gives the half of the frame belonging to this side of the wall. Turn
    on Mirror for the matching half on the far face: the wall's thickness is
    measured off the mesh, so the two halves line the reveal between them.
    """
    bl_idname = "object.luman_create_window_frame"
    bl_label = "Create Window Frame"

    __annotations__ = settings_annotations(pointers=False,
                                           overrides=WINDOW_DEFAULTS)

    panel_prop = "luman_window"
    forced_kind = 'WINDOW'
    name_prefix = "windowframe"
    target_collection = WINDOW_COLLECTION_NAME
    # Everything the casing settles for itself - but not Mirror, which is the
    # one thing about a window that only the user can know.
    forced_values = _FRAME_FORCED
    forced_defaults = _FRAME_DEFAULTS


class LUMAN_OT_baseboard_rebuild(bpy.types.Operator):
    """Regenerate this baseboard from its stored source run and settings"""
    bl_idname = "object.luman_baseboard_rebuild"
    bl_label = "Rebuild Now"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.luman_baseboard_data.is_baseboard

    def execute(self, context):
        obj = context.active_object
        message = rebuild(obj)
        obj.luman_baseboard_data.status = message or ""
        if message:
            self.report({'WARNING'}, message)
            return {'CANCELLED'}
        self.report({'INFO'}, f"Rebuilt '{obj.name}' ({len(obj.data.polygons)} face(s)).")
        return {'FINISHED'}


class LUMAN_OT_baseboard_update_material(bpy.types.Operator):
    """Rewrite this baseboard's material from the current textures and shader
    values, upgrading a preview material to normal_spec.sps if Sollumz is now
    available"""
    bl_idname = "object.luman_baseboard_update_material"
    bl_label = "Update Material"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.luman_baseboard_data.is_baseboard

    def execute(self, context):
        obj = context.active_object
        data = obj.luman_baseboard_data
        material = obj.active_material

        # A hand-picked material is simply assigned - never rebuilt, never
        # retextured. It is the user's.
        if data.material_mode == 'PICK':
            if data.material is None:
                self.report({'ERROR'}, "Material is set to Existing, but none is chosen.")
                return {'CANCELLED'}
            set_object_material(obj, data.material)
            self.report({'INFO'}, f"Assigned '{data.material.name}'.")
            return {'FINISHED'}

        ours = material is not None and material.name.startswith(MATERIAL_NAME_PREFIX)

        # The case this exists for: the baseboard was built with Sollumz
        # disabled, so it got a preview material, and Sollumz has since been
        # enabled. Replacing it here is the difference between "rebuild
        # everything" and "press one button".
        upgrade = ours and sollumz_available() and not _is_shader_material(material)
        # Switched back from Existing, or never had one: build the bundled
        # material. The material being replaced is the user's, so it is only
        # dropped from the slot, never deleted.
        adopt = material is None or not ours

        if upgrade or adopt:
            try:
                # An upgrade must not reuse: the material being replaced is
                # exactly the one a reuse lookup would find by name.
                new_material, warnings = build_material(
                    data, reuse=(not upgrade and data.material_mode != 'NEW'))
            except Exception as error:
                self.report({'ERROR'}, f"Could not create a material: {error}")
                return {'CANCELLED'}

            wanted = material.name if upgrade else None
            set_object_material(obj, new_material)
            if upgrade and material.users == 0:
                bpy.data.materials.remove(material)
                # Blender suffixed the new one because the old still held the
                # name; now that it is gone, take the name back.
                new_material.name = wanted
            material = new_material
            for warning in warnings:
                self.report({'WARNING'}, warning)
        else:
            missing = apply_material_settings(material, data)
            # Only worth reporting on a material that is supposed to have those
            # parameters; a preview material never does, and saying so on every
            # press would be noise.
            if missing and _is_shader_material(material):
                self.report({'WARNING'},
                            f"Parameter(s) not found on this material: {', '.join(missing)}. "
                            "Is it really a normal_spec.sps material?")

        self.report({'INFO'}, f"Updated '{material.name}'.")
        return {'FINISHED'}


class LUMAN_OT_reset_settings(bpy.types.Operator):
    """Put this tool's settings back to how they ship.

    Needed because a PropertyGroup's defaults only apply the first time the
    property is created: in a .blend saved before a default changed, the old
    value is stored and no amount of updating the add-on will shift it. This
    unsets them, which is what makes the stored value fall back to the default.
    """
    bl_idname = "object.luman_reset_settings"
    bl_label = "Reset to Defaults"
    bl_options = {'REGISTER', 'UNDO'}

    kind: bpy.props.StringProperty(default='BASEBOARD', options={'HIDDEN'})

    def execute(self, context):
        settings = scene_settings_for(context, self.kind)
        for name in SETTING_NAMES:
            try:
                settings.property_unset(name)
            except Exception:
                pass
        self.report({'INFO'}, "Settings reset to defaults.")
        return {'FINISHED'}


class LUMAN_OT_baseboard_material_only(bpy.types.Operator):
    """Create the normal_spec baseboard material and assign it to the selected objects"""
    bl_idname = "object.luman_baseboard_material_only"
    bl_label = "Create Baseboard Material"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.luman_baseboard
        try:
            material, warnings = build_material(settings, reuse=(settings.material_mode != 'NEW'))
        except Exception as error:
            self.report({'ERROR'}, f"Could not create the material: {error}")
            return {'CANCELLED'}

        targets = [obj for obj in context.selected_objects if obj.type == 'MESH']
        for obj in targets:
            assign_material(obj, material)

        for warning in warnings:
            self.report({'WARNING'}, warning)
        self.report({'INFO'}, f"'{material.name}' assigned to {len(targets)} object(s).")
        return {'FINISHED'}


# ===============================================================
# UI
# ===============================================================

#
# ONE set of controls, drawn against whichever settings are in play:
#
#   a baseboard selected  ->  that object's own settings, which rebuild it as
#                             you drag them
#   nothing selected      ->  the scene defaults, i.e. the next baseboard
#
# The two used to be separate panel trees showing the same sliders, and the
# result was that dragging Height in the top panel did nothing visible - it was
# setting a default for next time. Editing the selected object is what people
# mean by a size slider, so that is what the panel does whenever there is one.
#
# The controls are split into sub-panels, so a tool only takes the height of
# what is actually open. They are ordered by how often they are touched: Size &
# Profile first (open), then fit, then unwrap/shading/material, with the
# numerical tuning last.


def active_settings(context, kind='BASEBOARD'):
    """(settings, object_or_None) - what this tool's panel should be editing.

    A selected object only claims the panel of its OWN kind, so selecting a
    door frame does not silently repoint the baseboard sliders at it.
    """
    obj = context.active_object
    if (obj is not None and obj.type == 'MESH'
            and getattr(obj, "luman_baseboard_data", None) is not None
            and obj.luman_baseboard_data.is_baseboard
            and obj.luman_baseboard_data.kind == kind):
        return obj.luman_baseboard_data, obj
    return scene_settings_for(context, kind), None


def texture_names(textures):
    """(label, exported name) per sampler - the name without its extension is
    exactly what Sollumz writes into the .ydr, so it is what is worth showing."""
    return [
        ("Diffuse", os.path.splitext(os.path.basename(textures["diffuse"]))[0] if textures["diffuse"] else ""),
        ("Normal", os.path.splitext(os.path.basename(textures["normal"]))[0] if textures["normal"] else ""),
        ("Specular", os.path.splitext(os.path.basename(textures["specular"]))[0] if textures["specular"] else ""),
    ]


# Labels that mean something different once the same sweep is aimed at a door
# opening. The same properties underneath - a casing IS a moulding run - but
# "Height" on a jamb board reads as its length, which it is not.
_FRAME_LABELS = {
    "height": "Width",
    "depth": "Thickness",
    "vertical_offset": "Reveal",
}


def _prop(layout, settings, name, kind, **kwargs):
    if kind != 'BASEBOARD' and name in _FRAME_LABELS and "text" not in kwargs:
        kwargs["text"] = _FRAME_LABELS[name]
    layout.prop(settings, name, **kwargs)


def _section_profile(layout, settings, obj, kind):
    layout.use_property_split = True
    layout.use_property_decorate = False

    column = layout.column(align=True)
    _prop(column, settings, "height", kind)
    _prop(column, settings, "depth", kind)

    layout.separator()
    layout.prop(settings, "profile_type")
    if settings.profile_type == 'CUSTOM':
        column = layout.column()
        column.prop(settings, "custom_profile", text="Points")
        column.label(text="out,up pairs in fractions of Depth/Height", icon='INFO')
    else:
        if settings.profile_type != 'SQUARE':
            layout.prop(settings, "amount", text="Size")
        if settings.profile_type == 'STEP':
            layout.prop(settings, "steps", text="Steps")
            layout.prop(settings, "step_depth", text="Step Depth")

        layout.separator()
        layout.prop(settings, "outer_profile_type")
        if settings.outer_profile_type != 'SQUARE':
            layout.prop(settings, "outer_amount", text="Size")
        if settings.outer_profile_type == 'STEP':
            layout.prop(settings, "outer_steps", text="Steps")
            layout.prop(settings, "outer_step_depth", text="Step Depth")

        if {settings.profile_type, settings.outer_profile_type} & {'ROUND', 'COVE', 'OGEE'}:
            layout.prop(settings, "segments")

    layout.separator()
    if kind == 'BASEBOARD':
        # An opening has no top or bottom to choose between: the casing always
        # grows away from it.
        layout.prop(settings, "placement")
    # Right next to it, because "it came out on the wrong side" is the first
    # thing anyone reaches for.
    layout.prop(settings, "flip_side")
    if kind == 'WINDOW':
        # One loop of edges is one face of the wall, which is half a window.
        # This is how the other half is asked for, and it stays live on a
        # finished frame - the far side is derived, not selected.
        layout.prop(settings, "mirror")


def _section_fit(layout, settings, obj, kind):
    layout.use_property_split = True
    layout.use_property_decorate = False

    column = layout.column(align=True)
    column.prop(settings, "wall_inset")
    if kind == 'BASEBOARD':
        # On a casing this would push the profile INTO the opening, which is
        # the one thing a door frame must never do.
        column.prop(settings, "floor_sink")
    _prop(layout, settings, "vertical_offset", kind)

    if kind == 'BASEBOARD':
        # A casing decides these for itself - see the operator's forced_values.
        layout.separator()
        layout.prop(settings, "end_gap")
        layout.prop(settings, "cap_ends")


def _section_unwrap(layout, settings, obj, kind):
    layout.use_property_split = True
    layout.use_property_decorate = False

    layout.prop(settings, "uv_mode")
    layout.prop(settings, "uv_scale")
    column = layout.column(align=True)
    column.prop(settings, "uv_offset_u", text="Offset U")
    column.prop(settings, "uv_offset_v", text="V")
    layout.prop(settings, "uv_rotate")


def _section_shading(layout, settings, obj, kind):
    layout.use_property_split = True
    layout.use_property_decorate = False

    layout.prop(settings, "shading")
    if settings.shading == 'AUTO':
        layout.prop(settings, "sharp_angle")
    layout.separator()
    layout.prop(settings, "color_rgb", text="Vertex Color")
    layout.prop(settings, "alpha")
    layout.prop(settings, "ao_strength")


def _section_material(layout, settings, obj, kind):
    layout.use_property_split = True
    layout.use_property_decorate = False
    layout.prop(settings, "material_mode")

    if settings.material_mode == 'NONE':
        return

    if settings.material_mode == 'PICK':
        # Blender's own material browser - anything in the file, assigned
        # as-is. Nothing below applies, so nothing below is shown.
        layout.prop(settings, "material")
        if settings.material is None:
            layout.label(text="Nothing chosen - the bundled one is used", icon='INFO')
        layout.separator()
        layout.use_property_split = False
        if obj is not None:
            layout.operator("object.luman_baseboard_update_material", icon='MATERIAL')
        return

    available, message = sollumz_status()
    status = layout.column(align=True)
    status.label(text="normal_spec.sps" if available else "Preview material (no Sollumz)",
                 icon='CHECKMARK' if available else 'INFO')
    if not available:
        # The reason matters here: "not detected" alone sends people off to
        # reinstall something they already have.
        for line in _wrap(message, 34):
            status.label(text=line)
    layout.separator()

    layout.prop(settings, "texture_dir", text="Folder")

    column = layout.column(align=True)
    for label, name in texture_names(find_texture_set(settings.texture_dir)):
        row = column.row(align=True)
        row.label(text=label)
        row.label(text=name or "not found", icon='TEXTURE' if name else 'ERROR')

    layout.separator()
    column = layout.column(align=True)
    column.prop(settings, "bumpiness")
    column.prop(settings, "spec_intensity")
    column.prop(settings, "spec_falloff")
    column.prop(settings, "spec_fresnel")

    layout.separator()
    layout.use_property_split = False
    if obj is None:
        layout.operator("object.luman_baseboard_material_only", icon='MATERIAL')
    else:
        layout.operator("object.luman_baseboard_update_material", icon='MATERIAL')


def _section_advanced(layout, settings, obj, kind):
    layout.use_property_split = True
    layout.use_property_decorate = False

    layout.prop(settings, "source_mode")
    if settings.source_mode != 'EDGES':
        layout.prop(settings, "bottom_tolerance")

    layout.separator()
    column = layout.column(align=True)
    column.prop(settings, "make_bottom")
    column.prop(settings, "make_back")

    layout.separator()
    column = layout.column(align=True)
    column.prop(settings, "merge_distance")
    column.prop(settings, "miter_limit")


# Ordered by how often each is touched, so a panel reads from the everyday to
# the fiddly. Fit and Advanced are the baseboard's alone: a casing settles all
# of that for itself - see _FRAME_FORCED.
EVERY_TOOL = frozenset({'BASEBOARD', 'DOORFRAME', 'WINDOW'})
ONLY_BASEBOARD = frozenset({'BASEBOARD'})

# (suffix, label, header icon, starts closed, draw function, which tools show
#  it). The tuple is written in reading order, and the panels are generated
#  from it in that order via bl_order.
SECTIONS = (
    ("profile", "Size & Profile", 'MOD_LENGTH', False, _section_profile, EVERY_TOOL),
    ("fit", "Fit", 'MOD_OFFSET', True, _section_fit, ONLY_BASEBOARD),
    ("unwrap", "Unwrap", 'UV', True, _section_unwrap, EVERY_TOOL),
    ("shading", "Shading", 'SHADING_RENDERED', True, _section_shading, EVERY_TOOL),
    ("material", "Material", 'MATERIAL', True, _section_material, EVERY_TOOL),
    ("advanced", "Advanced", 'TOOL_SETTINGS', True, _section_advanced, ONLY_BASEBOARD),
)


def _draw_active_object(layout, settings, obj):
    """What the tool is currently driving, drawn into the tool's status card."""
    box = layout
    row = box.row()
    row.label(text=obj.name, icon='MOD_BEVEL')
    row.label(text="live" if settings.live_update else "paused",
              icon='FILE_REFRESH' if settings.live_update else 'PAUSE')

    row = box.row(align=True)
    row.prop(settings, "live_update", toggle=True, icon='FILE_REFRESH')
    sub = row.row(align=True)
    # Only useful while live update is off, or after the source was edited.
    sub.enabled = not settings.live_update or bool(settings.status)
    sub.operator("object.luman_baseboard_rebuild", text="Rebuild", icon='FILE_REFRESH')

    source = settings.source_object
    box.label(text=f"Source: {source.name if source else 'missing'}",
              icon='MESH_DATA' if source else 'ERROR')

    if settings.status:
        box.label(text=settings.status, icon='ERROR')


# ---------------------------------------------------------------
# What the panels draw
# ---------------------------------------------------------------
# The tools and their sections are real Blender panels now (see
# luman_tools.py), laid out the way Sollumz lays out its tab: one closed
# top-level header per area of work, sub-panels under it. Blender owns a
# sub-panel's open state and its indentation, so this module no longer stores
# either - what is left here is the tables the panels are generated from and
# the block of controls at the top of a tool.


# (kind, property prefix, header, header icon, create operator, hint outside
#  Edit Mode, lines shown while in Edit Mode).
#
# The icons are meant to be told apart at a glance, not to be clever: a
# chamfered edge for the swept profile, an empty opening for a door, a glazed
# one for a window.
TOOLS = (
    ('BASEBOARD', "baseboard", "Baseboard", 'MOD_BEVEL',
     "object.luman_create_baseboard",
     "Edit Mode: select wall faces or edges",
     ()),
    ('DOORFRAME', "doorframe", "Door Frame", 'MESH_PLANE',
     "object.luman_create_door_frame",
     "Edit Mode: select the opening outline",
     (("Two jambs and the head, on each wall face", 'INFO'),)),
    ('WINDOW', "window", "Window Frame", 'MESH_GRID',
     "object.luman_create_window_frame",
     "Edit Mode: select the opening outline",
     (("One loop, on one face of the wall", 'INFO'),
      ("Mirror adds the far half", 'MOD_MIRROR'))),
)


def draw_tool_intro(layout, context, tool):
    """The top of a tool's panel: what it makes, and what it is aimed at.

    Everything below it in the panel is a section of settings, so this block
    answers the other question - "what does this do and to what" - before any
    of them.
    """
    kind, _prefix, _label, icon, operator, hint, edit_lines = tool
    settings, obj = active_settings(context, kind)

    # Ordinary button height, like every button in Sollumz: a blown-up one
    # only shouts at the person who already opened the panel to press it.
    row = layout.row(align=True)
    row.operator(operator, icon=icon)
    row.operator("object.luman_reset_settings", text="",
                 icon='LOOP_BACK').kind = kind

    if context.mode != 'EDIT_MESH':
        layout.label(text=hint, icon='INFO')
    else:
        for text, line_icon in edit_lines:
            layout.label(text=text, icon=line_icon)

    if obj is None:
        # Short on purpose: the sidebar is narrow by default, and the tool's
        # own name is directly above this line.
        layout.label(text="Editing defaults", icon='PREFERENCES')
    else:
        _draw_active_object(layout, settings, obj)


def _wrap(text, width):
    """Break a message into label-sized lines. Blender labels do not wrap, so a
    long sentence is simply cut off at the panel edge without this."""
    lines = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


# ===============================================================
# Registration
# ===============================================================

_classes = (
    LUMAN_PG_baseboard_settings,
    LUMAN_PG_doorframe_settings,
    LUMAN_PG_window_settings,
    LUMAN_PG_baseboard_object,
    LUMAN_OT_create_baseboard,
    LUMAN_OT_create_door_frame,
    LUMAN_OT_create_window_frame,
    LUMAN_OT_baseboard_rebuild,
    LUMAN_OT_baseboard_update_material,
    LUMAN_OT_baseboard_material_only,
    LUMAN_OT_reset_settings,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.luman_baseboard = bpy.props.PointerProperty(type=LUMAN_PG_baseboard_settings)
    # A second, independent set of the same settings: a casing is not sized
    # like a skirting, and sharing them would make each tool clobber the other.
    bpy.types.Scene.luman_doorframe = bpy.props.PointerProperty(type=LUMAN_PG_doorframe_settings)
    bpy.types.Scene.luman_window = bpy.props.PointerProperty(type=LUMAN_PG_window_settings)
    bpy.types.Object.luman_baseboard_data = bpy.props.PointerProperty(type=LUMAN_PG_baseboard_object)


def unregister():
    if hasattr(bpy.types.Scene, "luman_ui"):
        # Left over from when the panel stored its own open/shut flags.
        del bpy.types.Scene.luman_ui
    if hasattr(bpy.types.Object, "luman_baseboard_data"):
        del bpy.types.Object.luman_baseboard_data
    if hasattr(bpy.types.Scene, "luman_window"):
        del bpy.types.Scene.luman_window
    if hasattr(bpy.types.Scene, "luman_doorframe"):
        del bpy.types.Scene.luman_doorframe
    if hasattr(bpy.types.Scene, "luman_baseboard"):
        del bpy.types.Scene.luman_baseboard
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
