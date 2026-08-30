bl_info = {
    "name": "Luman Tools",
    "blender": (5, 0, 0),
    "category": "Object",
    "author": "Your Name",
    "version": (3, 0),
    "description": "Distributes props, renames objects, auto-links texture maps (_N, _S, _normal, _specular), shows polygon tilt, and places meshes along curves",
}

import bpy
from mathutils import Vector, Matrix
import os
import re
import math
import sys
import importlib

# ---------------------------------------------------------------
# Sub-modules living next to this file
# ---------------------------------------------------------------
# This file is loaded in two quite different ways and both have to work:
#
#   * as part of the add-on package, when Blender enables the folder. The
#     package is named after the folder - "Luman Tool", with a space - which is
#     not something any import statement can spell, so the RELATIVE import is
#     the only one available here.
#   * on its own, run straight from the Text Editor. There is no package to be
#     relative to then, so our own folder goes on sys.path instead.
#
# reload() is what makes editing the sub-module take effect on the next
# Reload Scripts, without restarting Blender.

try:
    from . import luman_baseboard
except ImportError:
    _ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
    if _ADDON_DIR not in sys.path:
        sys.path.append(_ADDON_DIR)
    import luman_baseboard

importlib.reload(luman_baseboard)

# ===============================================================
# Core utility
# ===============================================================

def get_mesh_bounds(obj):
    min_x, max_x = None, None
    def check_mesh(mesh_obj):
        nonlocal min_x, max_x
        bbox_corners = [mesh_obj.matrix_world @ Vector(corner) for corner in mesh_obj.bound_box]
        obj_min_x = min(v.x for v in bbox_corners)
        obj_max_x = max(v.x for v in bbox_corners)
        if min_x is None or obj_min_x < min_x:
            min_x = obj_min_x
        if max_x is None or obj_max_x > max_x:
            max_x = obj_max_x
    # Check the object itself if it's a mesh
    if obj.type == 'MESH':
        check_mesh(obj)
    # Also traverse children
    def traverse_children(parent_obj):
        for child in parent_obj.children:
            if child.type == 'MESH':
                check_mesh(child)
            elif child.type == 'EMPTY':
                traverse_children(child)
    traverse_children(obj)
    if min_x is not None and max_x is not None:
        return min_x, max_x
    return None

def distribute_props(spacing=0.5):
    selected_props = [obj for obj in bpy.context.selected_objects if obj.type in {'EMPTY', 'MESH'}]
    props_with_data = []
    for prop in selected_props:
        bounds = get_mesh_bounds(prop)
        if bounds:
            min_x, max_x = bounds
            origin_x = prop.location.x
            left_offset = origin_x - min_x
            right_offset = max_x - origin_x
            props_with_data.append((prop, left_offset, right_offset))
    if not props_with_data:
        print("No props with meshes found.")
        return
    props_with_data.sort(key=lambda pair: pair[0].location.x)
    start_x = 0
    for i, (prop, left_offset, right_offset) in enumerate(props_with_data):
        if i == 0:
            # Place so the left edge of the mesh is at start_x
            prop.location.x = start_x + left_offset
        else:
            prev_prop, _, prev_right_offset = props_with_data[i - 1]
            # Previous object's right edge in world space
            prev_right_edge = prev_prop.location.x + prev_right_offset
            # Place so the left edge of this mesh is spacing away from the previous right edge
            prop.location.x = prev_right_edge + spacing + left_offset
    print("Props successfully distributed.")

def update_mesh_distance(self, context):
    """Callback для обновления меша при изменении расстояния"""
    try:
        if all(prop in self for prop in ['curve_distance', 'curve_name']):
            place_single_mesh_along_curve(
                self.name,
                self['curve_name'],
                float(self['curve_distance']),
                float(self.get('visual_rotation', -90)),
                bool(self.get('enable_180_rotation', False))
            )
    except:
        pass

def update_mesh_rotation(self, context):
    """Callback для обновления меша при изменении ротации"""
    try:
        if all(prop in self for prop in ['curve_distance', 'curve_name']):
            place_single_mesh_along_curve(
                self.name,
                self['curve_name'],
                float(self['curve_distance']),
                float(self.get('visual_rotation', -90)),
                bool(self.get('enable_180_rotation', False))
            )
    except:
        pass

def update_mesh_180(self, context):
    """Callback для обновления меша при изменении 180° ротации"""
    try:
        if all(prop in self for prop in ['curve_distance', 'curve_name']):
            place_single_mesh_along_curve(
                self.name,
                self['curve_name'],
                float(self['curve_distance']),
                float(self.get('visual_rotation', -90)),
                bool(self.get('enable_180_rotation', False))
            )
    except:
        pass

# ===============================================================
# Place meshes along curve
# ===============================================================

def place_meshes_along_curve(curve_name, collection_name, visual_rotation_deg, enable_180_rotation, spacing_distance, start_from_beginning=True):
    """
    Размещает меши вдоль кривой с учётом наклона по всем осям
    
    Args:
        curve_name: название кривой
        collection_name: коллекция с мешами
        visual_rotation_deg: угол поворота для визуального направления
        enable_180_rotation: включить ли поворот на 180 градусов
        spacing_distance: дистанция между объектами вдоль кривой
    """
    try:
        curve_obj = bpy.data.objects[curve_name]
        meshes = list(bpy.data.collections[collection_name].objects)
    except KeyError as e:
        print(f"Error: {e}")
        return
    
    if not meshes:
        print("No meshes found in collection.")
        return
    
    depsgraph = bpy.context.evaluated_depsgraph_get()
    curve_eval = curve_obj.evaluated_get(depsgraph)
    
    # Временный меш кривой для точек
    mesh_temp = curve_eval.to_mesh()
    curve_points = [curve_obj.matrix_world @ v.co for v in mesh_temp.vertices]
    curve_eval.to_mesh_clear()
    
    num_points = len(curve_points)
    if num_points < 2:
        print("На кривой слишком мало точек.")
        return
    
    # Вычисляем расстояния вдоль кривой (кумулятивные)
    distances = [0.0]
    for i in range(1, num_points):
        dist = (curve_points[i] - curve_points[i-1]).length
        distances.append(distances[-1] + dist)
    
    total_curve_length = distances[-1]
    
    # Функция для получения точки и тангента на кривой по параметру расстояния
    def get_point_at_distance(target_distance):
        # Убеждаемся, что расстояние в пределах кривой
        target_distance = max(0, min(total_curve_length, target_distance))
        
        # Находим сегмент, на котором лежит это расстояние
        for i in range(num_points - 1):
            if distances[i] <= target_distance <= distances[i + 1]:
                # Интерполируем между двумя точками
                seg_dist = distances[i + 1] - distances[i]
                if seg_dist == 0:
                    return curve_points[i], (curve_points[i+1] - curve_points[i]).normalized()
                
                t = (target_distance - distances[i]) / seg_dist
                point = curve_points[i] * (1 - t) + curve_points[i + 1] * t
                tangent = (curve_points[i + 1] - curve_points[i]).normalized()
                return point, tangent
        
        # На случай если расстояние за границей
        if total_curve_length > 0:
            return curve_points[-1], (curve_points[-1] - curve_points[-2]).normalized()
        else:
            return curve_points[0], Vector((1, 0, 0))
    
    # Сортируем меши по их позиции вдоль кривой для правильного расположения
    meshes.sort(key=lambda m: m.location.x)
    
    # Вычисляем начальное расстояние на кривой для первого меша
    if start_from_beginning:
        # Начинаем с начала кривой
        start_distance = 0
    else:
        # Начинаем с текущей позиции первого меша
        first_mesh = meshes[0]
        min_dist = float("inf")
        start_distance = 0
        
        for i in range(num_points - 1):
            p1, p2 = curve_points[i], curve_points[i + 1]
            seg_vec = p2 - p1
            seg_len = seg_vec.length
            if seg_len == 0:
                continue
            
            t = max(0, min(1, ((first_mesh.location - p1).dot(seg_vec)) / seg_len**2))
            proj = p1 + seg_vec * t
            dist = (first_mesh.location - proj).length
            
            if dist < min_dist:
                min_dist = dist
                # Вычисляем расстояние на кривой до этой точки
                start_distance = distances[i] + t * seg_len
    
    # Для каждого меша
    for mesh_idx, mesh in enumerate(meshes):
        # Вычисляем расстояние для этого меша на кривой
        current_distance = start_distance + mesh_idx * spacing_distance
        
        # Получаем точку и тангент на кривой
        closest_point, tangent = get_point_at_distance(current_distance)
        
        # Правильное построение локальной системы координат с учётом 3D наклона
        x_axis = tangent
        
        # Выбираем вектор "вверх" в зависимости от ориентации кривой
        up_guess = Vector((0, 0, 1))
        
        # Проверка параллельности
        if abs(x_axis.dot(up_guess)) > 0.99:
            up_guess = Vector((0, 1, 0))
        
        # Построение ортонормальной системы координат методом Фрэнe-Serret
        z_axis = x_axis.cross(up_guess).normalized()
        
        # Проверка на нулевой вектор
        if z_axis.length < 0.0001:
            up_guess = Vector((1, 0, 0))
            z_axis = x_axis.cross(up_guess).normalized()
        
        y_axis = z_axis.cross(x_axis).normalized()
        
        # Переортогонализация для точности
        z_axis = x_axis.cross(y_axis).normalized()
        
        # Правильное построение матрицы трансформации
        mat = Matrix([x_axis, y_axis, z_axis]).transposed().to_4x4()
        mat.translation = closest_point
        
        # В Blender 5 обнуляем локальный поворот перед установкой матрицы
        mesh.rotation_euler = (0, 0, 0)
        mesh.scale = (1, 1, 1)
        
        # Устанавливаем новую матрицу трансформации
        mesh.matrix_world = mat
        
        # Применяем визуальный поворот после установки матрицы
        rot_rad = math.radians(visual_rotation_deg)
        
        # Создаём матрицу поворота вокруг локальной оси X
        rot_matrix = Matrix.Rotation(rot_rad, 4, 'X')
        mesh.matrix_world = mesh.matrix_world @ rot_matrix
        
        # Поворот на 180 градусов вокруг оси Z если включен
        if enable_180_rotation:
            rot_180 = Matrix.Rotation(math.radians(180), 4, 'Z')
            mesh.matrix_world = mesh.matrix_world @ rot_180
        
        # Сохраняем параметры в свойства объекта
        mesh['curve_distance'] = current_distance
        mesh['visual_rotation'] = visual_rotation_deg
        mesh['enable_180_rotation'] = enable_180_rotation
        mesh['curve_name'] = curve_name
    
    print(f"✓ Размещено {len(meshes)} объектов вдоль кривой '{curve_name}'")
    print(f"✓ Расстояние между объектами: {spacing_distance}")
    print(f"✓ Учитан наклон кривой по всем осям (X, Y, Z)")

# ===============================================================
# Place single mesh along curve
# ===============================================================

def place_single_mesh_along_curve(mesh_name, curve_name, distance_along_curve, visual_rotation_deg, enable_180_rotation):
    """
    Размещает один меш вдоль кривой на определённом расстоянии
    
    Args:
        mesh_name: название меша для размещения
        curve_name: название кривой
        distance_along_curve: расстояние вдоль кривой
        visual_rotation_deg: угол поворота для визуального направления
        enable_180_rotation: включить ли поворот на 180 градусов
    """
    try:
        mesh = bpy.data.objects[mesh_name]
        curve_obj = bpy.data.objects[curve_name]
    except KeyError as e:
        print(f"Error: {e}")
        return
    
    depsgraph = bpy.context.evaluated_depsgraph_get()
    curve_eval = curve_obj.evaluated_get(depsgraph)
    
    # Временный меш кривой для точек
    mesh_temp = curve_eval.to_mesh()
    curve_points = [curve_obj.matrix_world @ v.co for v in mesh_temp.vertices]
    curve_eval.to_mesh_clear()
    
    num_points = len(curve_points)
    if num_points < 2:
        print("На кривой слишком мало точек.")
        return
    
    # Вычисляем расстояния вдоль кривой (кумулятивные)
    distances = [0.0]
    for i in range(1, num_points):
        dist = (curve_points[i] - curve_points[i-1]).length
        distances.append(distances[-1] + dist)
    
    total_curve_length = distances[-1]
    
    # Функция для получения точки и тангента на кривой по параметру расстояния
    def get_point_at_distance(target_distance):
        # Убеждаемся, что расстояние в пределах кривой
        target_distance = max(0, min(total_curve_length, target_distance))
        
        # Находим сегмент, на котором лежит это расстояние
        for i in range(num_points - 1):
            if distances[i] <= target_distance <= distances[i + 1]:
                # Интерполируем между двумя точками
                seg_dist = distances[i + 1] - distances[i]
                if seg_dist == 0:
                    return curve_points[i], (curve_points[i+1] - curve_points[i]).normalized()
                
                t = (target_distance - distances[i]) / seg_dist
                point = curve_points[i] * (1 - t) + curve_points[i + 1] * t
                tangent = (curve_points[i + 1] - curve_points[i]).normalized()
                return point, tangent
        
        # На случай если расстояние за границей
        if total_curve_length > 0:
            return curve_points[-1], (curve_points[-1] - curve_points[-2]).normalized()
        else:
            return curve_points[0], Vector((1, 0, 0))
    
    # Получаем точку и тангент на кривой
    closest_point, tangent = get_point_at_distance(distance_along_curve)
    
    # Правильное построение локальной системы координат с учётом 3D наклона
    x_axis = tangent
    
    # Выбираем вектор "вверх" в зависимости от ориентации кривой
    up_guess = Vector((0, 0, 1))
    
    # Проверка параллельности
    if abs(x_axis.dot(up_guess)) > 0.99:
        up_guess = Vector((0, 1, 0))
    
    # Построение ортонормальной системы координат методом Фрэнe-Serret
    z_axis = x_axis.cross(up_guess).normalized()
    
    # Проверка на нулевой вектор
    if z_axis.length < 0.0001:
        up_guess = Vector((1, 0, 0))
        z_axis = x_axis.cross(up_guess).normalized()
    
    y_axis = z_axis.cross(x_axis).normalized()
    
    # Переортогонализация для точности
    z_axis = x_axis.cross(y_axis).normalized()
    
    # Правильное построение матрицы трансформации
    mat = Matrix([x_axis, y_axis, z_axis]).transposed().to_4x4()
    mat.translation = closest_point
    
    # В Blender 5 обнуляем локальный поворот перед установкой матрицы
    mesh.rotation_euler = (0, 0, 0)
    mesh.scale = (1, 1, 1)
    
    # Устанавливаем новую матрицу трансформации
    mesh.matrix_world = mat
    
    # Применяем визуальный поворот после установки матрицы
    rot_rad = math.radians(visual_rotation_deg)
    
    # Создаём матрицу поворота вокруг локальной оси X
    rot_matrix = Matrix.Rotation(rot_rad, 4, 'X')
    mesh.matrix_world = mesh.matrix_world @ rot_matrix
    
    # Поворот на 180 градусов вокруг оси Z если включен
    if enable_180_rotation:
        rot_180 = Matrix.Rotation(math.radians(180), 4, 'Z')
        mesh.matrix_world = mesh.matrix_world @ rot_180
    
    # Сохраняем параметры в свойства объекта
    mesh['curve_distance'] = distance_along_curve
    mesh['visual_rotation'] = visual_rotation_deg
    mesh['enable_180_rotation'] = enable_180_rotation
    mesh['curve_name'] = curve_name

# ===============================================================
# Shader simplification and robust autolink
# ===============================================================

def simplify_and_autolink_textures(material):
    if not material.use_nodes:
        material.use_nodes = True

    nt = material.node_tree
    nodes = nt.nodes
    links = nt.links

    image_nodes = [n for n in nodes if n.type == "TEX_IMAGE"]

    bsdf_node = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf_node is None:
        bsdf_node = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf_node.location = (0, 0)

    output_node = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
    if output_node is None:
        output_node = nodes.new("ShaderNodeOutputMaterial")
        output_node.location = (400, 0)

    for n in list(nodes):
        if n not in image_nodes and n not in (bsdf_node, output_node):
            nodes.remove(n)

    for l in list(links):
        links.remove(l)

    try:
        links.new(bsdf_node.outputs.get("BSDF"), output_node.inputs.get("Surface"))
    except Exception:
        pass

    def node_basename_lower(img_node):
        name = ""
        if getattr(img_node, "image", None) and getattr(img_node.image, "name", None):
            name = img_node.image.name
        else:
            name = img_node.name
        if not name:
            return ""
        base = os.path.basename(name)
        base = re.split(r'\.', base)[0]
        return base.lower()

    def classify_image_node(img_node):
        base = node_basename_lower(img_node)
        base = base.replace('-', '_')
        if re.search(r'(_n|_normal)(_\d+)?$', base):
            return "normal"
        if re.search(r'(_s|_spec|_specular)(_\d+)?$', base):
            return "specular"
        return "base"

    candidates = {"base": [], "normal": [], "specular": []}
    for img in image_nodes:
        cls = classify_image_node(img)
        candidates.setdefault(cls, []).append(img)

    def choose_best(lst):
        if not lst:
            return None
        return max(lst, key=lambda n: len(node_basename_lower(n)))

    normal_node = choose_best(candidates.get("normal", []))
    specular_node = choose_best(candidates.get("specular", []))
    base_node = choose_best(candidates.get("base", []))

    if base_node is None:
        for img in image_nodes:
            if img not in (normal_node, specular_node):
                base_node = img
                break

    x = -600
    y = 0
    for img in image_nodes:
        img.location = (x, y)
        y -= 260
    bsdf_node.location = (0, 0)
    output_node.location = (400, 0)

    def safe_link(out_node, out_name, in_node, in_name):
        if out_node is None or in_node is None:
            return False
        out_socket = out_node.outputs.get(out_name)
        in_socket = in_node.inputs.get(in_name)
        if out_socket is None or in_socket is None:
            return False
        try:
            links.new(out_socket, in_socket)
            return True
        except Exception:
            return False

    if base_node:
        safe_link(base_node, "Color", bsdf_node, "Base Color")

    if specular_node:
        try:
            if getattr(specular_node, "image", None):
                specular_node.image.colorspace_settings.name = 'Non-Color'
        except Exception:
            pass
        if not safe_link(specular_node, "Color", bsdf_node, "Specular Tint"):
            safe_link(specular_node, "Color", bsdf_node, "Specular")

    if normal_node:
        try:
            if getattr(normal_node, "image", None):
                normal_node.image.colorspace_settings.name = 'Non-Color'
        except Exception:
            pass
        normal_map_node = nodes.new("ShaderNodeNormalMap")
        normal_map_node.location = (-300, -300)
        safe_link(normal_node, "Color", normal_map_node, "Color")
        safe_link(normal_map_node, "Normal", bsdf_node, "Normal")

    def img_name_or_none(node):
        if node and getattr(node, "image", None) and getattr(node.image, "name", None):
            return node.image.name
        if node:
            return node.name
        return "None"

    print(f"Material '{material.name}' results:")
    print("  Base:", img_name_or_none(base_node))
    print("  Specular:", img_name_or_none(specular_node))
    print("  Normal:", img_name_or_none(normal_node))

class OBJECT_OT_LumanPlaceSingleMeshAlongCurve(bpy.types.Operator):
    """Place a single mesh along a curve at a specific distance"""
    bl_idname = "object.luman_place_single_mesh_along_curve"
    bl_label = "Place Single Mesh Along Curve"
    bl_options = {'REGISTER', 'UNDO'}
    
    # Свойства оператора
    mesh_name: bpy.props.StringProperty(
        name="Mesh Name",
        description="Name of the mesh to place",
        default=""
    )
    
    curve_name: bpy.props.StringProperty(
        name="Curve Name",
        description="Name of the curve object",
        default="BezierCurve"
    )
    
    distance_along_curve: bpy.props.FloatProperty(
        name="Distance Along Curve",
        description="Distance along the curve to place the mesh",
        default=0.0,
        min=0.0,
        max=10000
    )
    
    visual_rotation_deg: bpy.props.FloatProperty(
        name="Visual Rotation (°)",
        description="Rotation angle for visual direction (X-axis)",
        default=-90,
        min=-180,
        max=180
    )
    
    enable_180_rotation: bpy.props.BoolProperty(
        name="Enable 180° Rotation",
        description="Rotate mesh 180 degrees around Z-axis",
        default=False
    )
    
    def execute(self, context):
        try:
            if not self.mesh_name:
                self.report({'ERROR'}, "Please select a mesh name")
                return {'CANCELLED'}
            
            place_single_mesh_along_curve(
                self.mesh_name,
                self.curve_name,
                self.distance_along_curve,
                self.visual_rotation_deg,
                self.enable_180_rotation
            )
            self.report({'INFO'}, f"Mesh '{self.mesh_name}' placed successfully")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")
            return {'CANCELLED'}
    
    def invoke(self, context, event):
        # Если активный объект есть, используем его имя
        if context.active_object:
            self.mesh_name = context.active_object.name
        return context.window_manager.invoke_props_dialog(self)

# ===============================================================
# Operators
# ===============================================================

class OBJECT_OT_LumanEditMeshAlongCurve(bpy.types.Operator):
    """Edit mesh position along curve in real-time with mouse"""
    bl_idname = "object.luman_edit_mesh_along_curve"
    bl_label = "Edit Mesh Along Curve"
    bl_options = {'REGISTER'}
    
    # Для modal операции
    _timer = None
    _last_mouse_x = 0
    _mesh_obj = None
    _curve_obj = None
    _curve_data = None
    _distance = 0.0
    
    # Свойства оператора
    curve_name: bpy.props.StringProperty(default="BezierCurve")
    visual_rotation_deg: bpy.props.FloatProperty(default=-90, min=-180, max=180)
    enable_180_rotation: bpy.props.BoolProperty(default=False)
    
    def modal(self, context, event):
        if event.type == 'TIMER':
            if self._mesh_obj and self._curve_obj and self._curve_data:
                try:
                    self._update_mesh_position()
                    for area in context.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.tag_redraw()
                except:
                    pass
            return {'RUNNING_MODAL'}
        
        elif event.type == 'MOUSEMOVE':
            # Используем относительное движение мыши внутри региона
            delta = event.mouse_x - self._last_mouse_x
            self._distance += delta * 0.01
            self._last_mouse_x = event.mouse_x
            
            # Если мышь упёрлась в край, переместим её обратно в центр
            if context.area and context.area.type == 'VIEW_3D':
                region = context.region
                # Если мышь близко к краю, телепортируем её в противоположный край
                if event.mouse_region_x < 50:
                    # Мышь слева, переместим вправо
                    new_x = region.width - 100
                    context.window.cursor_warp(
                        context.area.x + new_x,
                        event.mouse_y
                    )
                    self._last_mouse_x = new_x
                elif event.mouse_region_x > region.width - 50:
                    # Мышь справа, переместим влево
                    new_x = 100
                    context.window.cursor_warp(
                        context.area.x + new_x,
                        event.mouse_y
                    )
                    self._last_mouse_x = new_x
            
            return {'RUNNING_MODAL'}
        
        elif event.type in {'RET', 'SPACE'}:
            # SPACE или ENTER - подтверждаем и сохраняем
            try:
                wm = context.window_manager
                if self._timer:
                    wm.event_timer_remove(self._timer)
                
                if self._mesh_obj:
                    self._mesh_obj['curve_distance'] = self._distance
                    self._mesh_obj['visual_rotation'] = self.visual_rotation_deg
                    self._mesh_obj['enable_180_rotation'] = self.enable_180_rotation
                    self._mesh_obj['curve_name'] = self.curve_name
                
                self.report({'INFO'}, f"Mesh positioned at distance {self._distance:.2f}")
            except:
                pass
            return {'FINISHED'}
        
        elif event.type in {'ESC'}:
            # ESC - отмена
            try:
                wm = context.window_manager
                if self._timer:
                    wm.event_timer_remove(self._timer)
            except:
                pass
            return {'CANCELLED'}
        
        return {'RUNNING_MODAL'}
    
    def _update_mesh_position(self):
        """Обновляет положение меша без задержек"""
        if not self._curve_data or not self._mesh_obj:
            return
        
        curve_points, distances, total_length = self._curve_data
        distance = max(0, min(total_length, self._distance))
        closest_point, tangent = self._get_point_at_distance(distance, curve_points, distances)
        
        x_axis = tangent
        up_guess = Vector((0, 0, 1))
        
        if abs(x_axis.dot(up_guess)) > 0.99:
            up_guess = Vector((0, 1, 0))
        
        z_axis = x_axis.cross(up_guess).normalized()
        
        if z_axis.length < 0.0001:
            up_guess = Vector((1, 0, 0))
            z_axis = x_axis.cross(up_guess).normalized()
        
        y_axis = z_axis.cross(x_axis).normalized()
        z_axis = x_axis.cross(y_axis).normalized()
        
        mat = Matrix([x_axis, y_axis, z_axis]).transposed().to_4x4()
        mat.translation = closest_point
        
        self._mesh_obj.rotation_euler = (0, 0, 0)
        self._mesh_obj.scale = (1, 1, 1)
        self._mesh_obj.matrix_world = mat
        
        visual_rotation = self._mesh_obj.get('visual_rotation', self.visual_rotation_deg)
        enable_180 = self._mesh_obj.get('enable_180_rotation', self.enable_180_rotation)
        
        rot_rad = math.radians(visual_rotation)
        rot_matrix = Matrix.Rotation(rot_rad, 4, 'X')
        self._mesh_obj.matrix_world = self._mesh_obj.matrix_world @ rot_matrix
        
        if enable_180:
            rot_180 = Matrix.Rotation(math.radians(180), 4, 'Z')
            self._mesh_obj.matrix_world = self._mesh_obj.matrix_world @ rot_180
    
    @staticmethod
    def _get_point_at_distance(target_distance, curve_points, distances):
        """Получает точку и тангент на кривой"""
        num_points = len(curve_points)
        total_length = distances[-1] if distances else 0
        target_distance = max(0, min(total_length, target_distance))
        
        for i in range(num_points - 1):
            if distances[i] <= target_distance <= distances[i + 1]:
                seg_dist = distances[i + 1] - distances[i]
                if seg_dist == 0:
                    tangent = (curve_points[i+1] - curve_points[i]).normalized()
                    return curve_points[i], tangent
                
                t = (target_distance - distances[i]) / seg_dist
                point = curve_points[i] * (1 - t) + curve_points[i + 1] * t
                tangent = (curve_points[i + 1] - curve_points[i]).normalized()
                return point, tangent
        
        if total_length > 0:
            tangent = (curve_points[-1] - curve_points[-2]).normalized()
            return curve_points[-1], tangent
        else:
            return curve_points[0], Vector((1, 0, 0))
    
    def execute(self, context):
        self._mesh_obj = context.active_object
        if not self._mesh_obj:
            self.report({'ERROR'}, "Please select a mesh object")
            return {'CANCELLED'}
        
        if 'curve_name' in self._mesh_obj:
            self.curve_name = self._mesh_obj['curve_name']
        if 'visual_rotation' in self._mesh_obj:
            self.visual_rotation_deg = float(self._mesh_obj['visual_rotation'])
        if 'enable_180_rotation' in self._mesh_obj:
            self.enable_180_rotation = bool(self._mesh_obj['enable_180_rotation'])
        
        try:
            self._curve_obj = bpy.data.objects[self.curve_name]
        except KeyError:
            self.report({'ERROR'}, f"Curve '{self.curve_name}' not found")
            return {'CANCELLED'}
        
        try:
            depsgraph = context.evaluated_depsgraph_get()
            curve_eval = self._curve_obj.evaluated_get(depsgraph)
            mesh_temp = curve_eval.to_mesh()
            curve_points = [self._curve_obj.matrix_world @ v.co for v in mesh_temp.vertices]
            curve_eval.to_mesh_clear()
            
            distances = [0.0]
            for i in range(1, len(curve_points)):
                dist = (curve_points[i] - curve_points[i-1]).length
                distances.append(distances[-1] + dist)
            
            total_length = distances[-1] if distances else 0
            self._curve_data = (curve_points, distances, total_length)
            
            if 'curve_distance' in self._mesh_obj:
                self._distance = float(self._mesh_obj['curve_distance'])
            else:
                min_dist = float("inf")
                self._distance = 0
                
                for i in range(len(curve_points) - 1):
                    p1, p2 = curve_points[i], curve_points[i + 1]
                    seg_vec = p2 - p1
                    seg_len = seg_vec.length
                    if seg_len == 0:
                        continue
                    
                    t = max(0, min(1, ((self._mesh_obj.location - p1).dot(seg_vec)) / seg_len**2))
                    proj = p1 + seg_vec * t
                    dist = (self._mesh_obj.location - proj).length
                    
                    if dist < min_dist:
                        min_dist = dist
                        self._distance = distances[i] + t * seg_len
            
        except Exception as e:
            self.report({'ERROR'}, f"Error preparing curve data: {str(e)}")
            return {'CANCELLED'}
        
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.016, window=context.window)
        self._last_mouse_x = 0
        wm.modal_handler_add(self)
        
        self.report({'INFO'}, "Drag mouse left/right. SPACE/ENTER to confirm, ESC to cancel")
        return {'RUNNING_MODAL'}

class OBJECT_OT_LumanAutoLinkTextures(bpy.types.Operator):
    """Auto-link texture nodes by robust suffix detection (_N, _S, _normal, _specular)"""
    bl_idname = "object.luman_autolink_textures"
    bl_label = "Auto-Link Shader Textures"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'WARNING'}, "No active object")
            return {'CANCELLED'}

        mats = []
        if obj.active_material:
            mats.append(obj.active_material)

        if not mats:
            self.report({'WARNING'}, "No active material on the selected object")
            return {'CANCELLED'}

        for m in mats:
            simplify_and_autolink_textures(m)

        self.report({'INFO'}, f"Auto-linked textures for {len(mats)} material(s)")
        return {'FINISHED'}

class OBJECT_OT_LumanDistributeProps(bpy.types.Operator):
    bl_idname = "object.luman_distribute_props"
    bl_label = "Distribute Props"
    bl_options = {'REGISTER', 'UNDO'}

    spacing: bpy.props.FloatProperty(
        name="Spacing",
        description="Gap between mesh edges",
        default=0.5,
        min=0.0,
        max=100.0,
    )

    def execute(self, context):
        distribute_props(self.spacing)
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

class OBJECT_OT_LumanRenameObjects(bpy.types.Operator):
    bl_idname = "object.luman_rename_objects"
    bl_label = "Rename Objects"
    bl_options = {'REGISTER', 'UNDO'}
    new_name: bpy.props.StringProperty(name="New Name", default="Object")
    number_objects: bpy.props.BoolProperty(name="Number Objects", default=False)
    def execute(self, context):
        for i, obj in enumerate(context.selected_objects):
            obj.name = f"{self.new_name}_{i+1}" if self.number_objects else self.new_name
        self.report({'INFO'}, "Objects renamed successfully")
        return {'FINISHED'}
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

class OBJECT_OT_LumanPolygonZRotation(bpy.types.Operator):
    bl_idname = "object.luman_polygon_z_rotation"
    bl_label = "Show Polygon Z Rotation"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        import bmesh
        from mathutils import Vector

        obj = context.edit_object
        if not obj or obj.type != 'MESH':
            self.report({'WARNING'}, "Switch to Edit Mode and select a mesh face")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        sel_faces = [f for f in bm.faces if f.select]
        if not sel_faces:
            self.report({'WARNING'}, "No selected face")
            return {'CANCELLED'}

        face = sel_faces[0]
        n = obj.matrix_world.to_3x3() @ face.normal
        n.normalize()

        # проекция нормали на XY-плоскость
        n_xy = Vector((n.x, n.y, 0))
        if n_xy.length == 0:
            angle_z = 0.0
        else:
            n_xy.normalize()
            # угол относительно глобального X, в градусах
            angle_z = math.degrees(math.atan2(n_xy.y, n_xy.x))

        msg = f"Глобальный угол полигона по Z: {round(angle_z, 2)}°"
        print(msg)
        self.report({'INFO'}, msg)
        return {'FINISHED'}

class OBJECT_OT_LumanPlaceMeshesAlongCurve(bpy.types.Operator):
    """Place meshes along a curve with proper orientation"""
    bl_idname = "object.luman_place_meshes_along_curve"
    bl_label = "Place Meshes Along Curve"
    bl_options = {'REGISTER', 'UNDO'}
    
    # Свойства оператора
    curve_name: bpy.props.StringProperty(
        name="Curve Name",
        description="Name of the curve object",
        default="BezierCurve"
    )
    
    collection_name: bpy.props.StringProperty(
        name="Collection Name",
        description="Name of the collection containing meshes",
        default="Meshes"
    )
    
    visual_rotation_deg: bpy.props.FloatProperty(
        name="Visual Rotation (°)",
        description="Rotation angle for visual direction (X-axis)",
        default=-90,
        min=-180,
        max=180
    )
    
    enable_180_rotation: bpy.props.BoolProperty(
        name="Enable 180° Rotation",
        description="Rotate meshes 180 degrees around Z-axis",
        default=False
    )
    
    spacing_distance: bpy.props.FloatProperty(
        name="Spacing Distance",
        description="Distance between meshes along the curve",
        default=0.5,
        min=0.01,
        max=100
    )
    
    start_from_beginning: bpy.props.BoolProperty(
        name="Start From Curve Beginning",
        description="Place first mesh at the beginning of the curve instead of near current position",
        default=True
    )
    
    def execute(self, context):
        try:
            place_meshes_along_curve(
                self.curve_name,
                self.collection_name,
                self.visual_rotation_deg,
                self.enable_180_rotation,
                self.spacing_distance,
                self.start_from_beginning
            )
            self.report({'INFO'}, "Meshes placed successfully along curve")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")
            return {'CANCELLED'}
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

class OBJECT_PT_LumanCurveProperties(bpy.types.Panel):
    """Panel to edit curve mesh properties"""
    bl_label = "Curve Mesh Properties"
    bl_idname = "OBJECT_PT_luman_curve_properties"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"
    
    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        
        if not obj:
            layout.label(text="No object selected")
            return
        
        # Проверяем, есть ли свойства кривой
        has_curve_props = any(prop in obj for prop in ['curve_distance', 'curve_name'])
        
        if not has_curve_props:
            layout.label(text="Object not placed on curve")
            return
        
        # Отображаем свойства
        layout.label(text="Object on Curve Settings", icon='CURVE_DATA')
        
        if 'curve_name' in obj:
            row = layout.row()
            row.label(text="Curve:")
            row.label(text=str(obj['curve_name']))
        
        # Слайдер для расстояния вдоль кривой
        if 'curve_distance' in obj:
            try:
                # Получаем длину кривой для ограничения слайдера
                curve_name = obj.get('curve_name', 'BezierCurve')
                curve_obj = bpy.data.objects.get(curve_name)
                
                if curve_obj:
                    depsgraph = context.evaluated_depsgraph_get()
                    curve_eval = curve_obj.evaluated_get(depsgraph)
                    mesh_temp = curve_eval.to_mesh()
                    curve_points = [curve_obj.matrix_world @ v.co for v in mesh_temp.vertices]
                    curve_eval.to_mesh_clear()
                    
                    # Вычисляем длину кривой
                    total_length = 0
                    for i in range(1, len(curve_points)):
                        total_length += (curve_points[i] - curve_points[i-1]).length
                    
                    row = layout.row()
                    row.prop(obj, '["curve_distance"]', slider=True, text="Distance Along Curve")
                    
                    # Отображаем численное значение
                    row = layout.row()
                    row.label(text=f"Value: {obj['curve_distance']:.2f} / {total_length:.2f}")
                else:
                    row = layout.row()
                    row.label(text=f"Distance: {obj['curve_distance']:.2f}")
            except:
                row = layout.row()
                row.label(text=f"Distance: {obj['curve_distance']:.2f}")
        
        # Параметры ротации
        if 'visual_rotation' in obj:
            row = layout.row()
            row.prop(obj, '["visual_rotation"]', slider=True, text="Visual Rotation (°)")
        
        if 'enable_180_rotation' in obj:
            row = layout.row()
            row.prop(obj, '["enable_180_rotation"]', text="180° Rotation")
        
        layout.separator()
        
        # Кнопка для применения изменений
        row = layout.row()
        row.scale_y = 1.5
        row.operator("object.luman_apply_curve_properties", text="Apply Changes", icon='CHECKMARK')
        
        # Кнопка для сброса
        row = layout.row()
        row.operator("object.luman_reset_mesh_position", text="Reset Position", icon='LOOP_BACK')

class OBJECT_OT_LumanApplyCurveProperties(bpy.types.Operator):
    """Apply curve mesh properties"""
    bl_idname = "object.luman_apply_curve_properties"
    bl_label = "Apply Curve Properties"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "No object selected")
            return {'CANCELLED'}
        
        # Получаем параметры
        if not all(prop in obj for prop in ['curve_name', 'curve_distance', 'visual_rotation', 'enable_180_rotation']):
            self.report({'ERROR'}, "Object missing curve properties")
            return {'CANCELLED'}
        
        try:
            curve_name = obj['curve_name']
            curve_distance = float(obj['curve_distance'])
            visual_rotation = float(obj['visual_rotation'])
            enable_180 = bool(obj['enable_180_rotation'])
            
            # Применяем изменения
            place_single_mesh_along_curve(
                obj.name,
                curve_name,
                curve_distance,
                visual_rotation,
                enable_180
            )
            
            self.report({'INFO'}, "Properties applied successfully")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")
            return {'CANCELLED'}

class OBJECT_OT_LumanResetMeshPosition(bpy.types.Operator):
    """Reset mesh to original position on curve"""
    bl_idname = "object.luman_reset_mesh_position"
    bl_label = "Reset Mesh Position"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "No object selected")
            return {'CANCELLED'}
        
        if not all(prop in obj for prop in ['curve_name', 'curve_distance']):
            self.report({'ERROR'}, "Object not on curve")
            return {'CANCELLED'}
        
        # Просто применяем текущие свойства
        try:
            place_single_mesh_along_curve(
                obj.name,
                obj['curve_name'],
                float(obj['curve_distance']),
                float(obj.get('visual_rotation', -90)),
                bool(obj.get('enable_180_rotation', False))
            )
            self.report({'INFO'}, "Position reset")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")
            return {'CANCELLED'}

class OBJECT_OT_LumanToggleMeshRotation(bpy.types.Operator):
    """Toggle 180 degree rotation safely"""
    bl_idname = "object.luman_toggle_mesh_rotation"
    bl_label = "Toggle 180° Rotation"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "No object selected")
            return {'CANCELLED'}
        
        # Получаем текущее значение
        enable_180 = obj.get('enable_180_rotation', False)
        
        # Переключаем
        obj['enable_180_rotation'] = not enable_180
        
        # Перемещаем меш с новым параметром если он на кривой
        if 'curve_name' in obj and 'curve_distance' in obj:
            try:
                curve_name = obj['curve_name']
                curve_distance = obj['curve_distance']
                visual_rotation = obj.get('visual_rotation', -90)
                enable_180_new = obj['enable_180_rotation']
                
                # Перепозиционируем меш с новым параметром
                place_single_mesh_along_curve(
                    obj.name,
                    curve_name,
                    curve_distance,
                    visual_rotation,
                    enable_180_new
                )
                
                self.report({'INFO'}, f"180° rotation {'enabled' if enable_180_new else 'disabled'}")
            except Exception as e:
                self.report({'ERROR'}, f"Error: {str(e)}")
                return {'CANCELLED'}
        else:
            self.report({'WARNING'}, "Object not placed on curve")
        
        return {'FINISHED'}
# ===============================================================
# UI Panels
# ===============================================================
# The sidebar tab is laid out the way Sollumz lays out its own: a top-level
# panel per area of work, real sub-panels under it, an icon in every header.
# Blender owns a sub-panel's open state and its indentation, so nothing here
# stores a flag or inserts a spacer - which is also why the spacing now
# matches every other add-on in the sidebar.
#
#   Interior      the three trim tools; each is a sub-panel, and each of its
#                 groups of settings is a sub-panel of that
#   Experimental  the older utilities, kept out of that workflow
#   Support       one button, at the bottom
#
# The tool and section panels are generated from luman_baseboard.TOOLS and
# .SECTIONS, so a tool or a section added there gets its panel for free.

_CATEGORY = "Luman Tools"

DONATE_URL = "https://www.paypal.com/donate/?hosted_button_id=QPA3YGUVXVWKY"


class LUMAN_PT_interior(bpy.types.Panel):
    """Baseboards, door casings and window casings"""
    bl_label = "Interior"
    bl_idname = "LUMAN_PT_interior"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CATEGORY
    bl_order = 0

    def draw_header(self, context):
        self.layout.label(text="", icon='HOME')

    def draw(self, context):
        # Nothing of its own: the three tools are its sub-panels.
        pass


class LUMAN_PT_experimental(bpy.types.Panel):
    """Older utilities kept outside the interior workflow"""
    bl_label = "Experimental"
    bl_idname = "LUMAN_PT_experimental"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CATEGORY
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 1

    def draw_header(self, context):
        self.layout.label(text="", icon='EXPERIMENTAL')

    def draw(self, context):
        self.layout.label(text="Rough edges - not part of the interior workflow",
                          icon='ERROR')


class LUMAN_PT_support(bpy.types.Panel):
    """Support the add-on"""
    bl_label = "Support"
    bl_idname = "LUMAN_PT_support"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CATEGORY
    # Left open: it is a single button, and a shut header would hide the only
    # thing in it behind a click for no gain.
    bl_order = 2

    def draw_header(self, context):
        self.layout.label(text="", icon='FUND')

    def draw(self, context):
        # wm.url_open is Blender's own operator - it hands the URL to the
        # system browser, so nothing here has to touch the network.
        self.layout.operator("wm.url_open", text="Donate via PayPal",
                             icon='FUND').url = DONATE_URL


def _panel(idname, label, icon, parent, order, closed, draw, doc):
    """One generated sidebar panel, in the shape every panel here has."""
    return type(idname, (bpy.types.Panel,), {
        "__doc__": doc,
        "bl_label": label,
        "bl_idname": idname,
        "bl_space_type": 'VIEW_3D',
        "bl_region_type": 'UI',
        "bl_category": _CATEGORY,
        "bl_parent_id": parent,
        "bl_options": {'DEFAULT_CLOSED'} if closed else set(),
        "bl_order": order,
        "draw_header": lambda self, context: self.layout.label(text="", icon=icon),
        "draw": draw,
    })


def _tool_panel(tool, order):
    kind, prefix, label, icon, _operator, _hint, _lines = tool
    return _panel(
        f"LUMAN_PT_{prefix}", label, icon, LUMAN_PT_interior.bl_idname, order,
        # Baseboard is the one people reach for most, so it is the tool
        # already open when the sidebar is first drawn.
        closed=(kind != 'BASEBOARD'),
        draw=lambda self, context: luman_baseboard.draw_tool_intro(
            self.layout, context, tool),
        doc=f"{label}: what it makes, and what it is aimed at")


def _section_panel(tool, section, order):
    kind, prefix, tool_label, _icon, _operator, _hint, _lines = tool
    suffix, label, icon, closed, draw_section, _kinds = section

    def draw(self, context):
        settings, obj = luman_baseboard.active_settings(context, kind)
        draw_section(self.layout, settings, obj, kind)

    return _panel(
        f"LUMAN_PT_{prefix}_{suffix}", label, icon, f"LUMAN_PT_{prefix}",
        order, closed, draw, f"{label} of the {tool_label.lower()}")


# (suffix, header, header icon, [(operator, button text)]) - the Experimental
# utilities, grouped the way they were grouped before they were panels.
_EXPERIMENTAL = (
    ("props", "Props", 'MOD_SCATTER_ON_SURFACE', (
        ("object.luman_distribute_props", "Distribute Props"),
        ("object.luman_rename_objects", "Rename Objects"),
    )),
    ("materials", "Materials", 'TEXTURE', (
        ("object.luman_autolink_textures", "Auto-Link Shader Textures"),
        ("object.luman_polygon_z_rotation", "Show Polygon Z Rotation"),
    )),
    ("curve", "Curve Tools", 'MOD_CURVE', (
        ("object.luman_place_meshes_along_curve", "Place Meshes Along Curve"),
        ("object.luman_place_single_mesh_along_curve", "Place Single Mesh Along Curve"),
        ("object.luman_edit_mesh_along_curve", "Edit Mesh Along Curve"),
    )),
)


def _experimental_panel(group, order):
    suffix, label, icon, operators = group

    def draw(self, context):
        column = self.layout.column(align=True)
        for operator, text in operators:
            column.operator(operator, text=text)

    return _panel(
        f"LUMAN_PT_experimental_{suffix}", label, icon,
        LUMAN_PT_experimental.bl_idname, order, False, draw,
        f"{label}, outside the interior workflow")


def _build_panels():
    """Every sidebar panel, parents before their children."""
    panels = [LUMAN_PT_interior, LUMAN_PT_experimental, LUMAN_PT_support]

    for order, tool in enumerate(luman_baseboard.TOOLS):
        panels.append(_tool_panel(tool, order))
        for section_order, section in enumerate(luman_baseboard.SECTIONS):
            if tool[0] in section[5]:
                panels.append(_section_panel(tool, section, section_order))

    for order, group in enumerate(_EXPERIMENTAL):
        panels.append(_experimental_panel(group, order))

    return tuple(panels)


_PANELS = _build_panels()

# ===============================================================
# Registration
# ===============================================================

classes = (
    OBJECT_OT_LumanDistributeProps,
    OBJECT_OT_LumanRenameObjects,
    OBJECT_OT_LumanAutoLinkTextures,
    OBJECT_OT_LumanPolygonZRotation,
    OBJECT_OT_LumanPlaceMeshesAlongCurve,
    OBJECT_OT_LumanPlaceSingleMeshAlongCurve,
    OBJECT_OT_LumanEditMeshAlongCurve,
    OBJECT_OT_LumanToggleMeshRotation,
    OBJECT_OT_LumanApplyCurveProperties,
    OBJECT_OT_LumanResetMeshPosition,
    OBJECT_PT_LumanCurveProperties,
)


def register():
    # Before the panels: their draw calls into luman_baseboard, which needs
    # its operators and its scene properties to already exist.
    luman_baseboard.register()
    for cls in classes:
        bpy.utils.register_class(cls)
    # _PANELS is already parents-first, which is what bl_parent_id needs.
    for cls in _PANELS:
        bpy.utils.register_class(cls)


def unregister():
    if hasattr(bpy.types.Scene, "luman_experimental_ui"):
        # Left over from when the Experimental group stored its own flags.
        del bpy.types.Scene.luman_experimental_ui
    for cls in reversed(_PANELS):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    luman_baseboard.unregister()


if __name__ == "__main__":
    register()
