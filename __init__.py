bl_info = {
    "name": "Topology Doctor Ultimate (Interactive)",
    "author": "Gemini AI",
    "version": (4, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Topo Doctor",
    "description": "Interactive topology analysis with 'Focus Next' features.",
    "category": "Mesh",
}

import bpy
import bmesh
import textwrap

# --- 1. DATA STRUCTURES ---

class TopoIssueItem(bpy.types.PropertyGroup):
    """Stores a single issue found in the mesh"""
    severity: bpy.props.StringProperty() 
    name: bpy.props.StringProperty()
    description: bpy.props.StringProperty()
    
    # NEW: Store the IDs of the bad geometry (e.g., "1,5,22")
    element_indices: bpy.props.StringProperty() 
    element_type: bpy.props.StringProperty()    # 'FACE', 'EDGE', 'VERT'
    current_pointer: bpy.props.IntProperty(default=0) # To track which one we are looking at

class TopoStats(bpy.types.PropertyGroup):
    """Stores global stats"""
    vert_count: bpy.props.IntProperty()
    face_count: bpy.props.IntProperty()
    tri_count: bpy.props.IntProperty()
    ngon_count: bpy.props.IntProperty()
    score: bpy.props.IntProperty(default=100)
    target_name: bpy.props.StringProperty()

# --- 2. INTERACTIVE SELECTOR ---

class MESH_OT_FocusIssue(bpy.types.Operator):
    """Selects the next bad element in the list and zooms to it"""
    bl_idname = "mesh.focus_issue_element"
    bl_label = "Focus Next"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty() # Which issue in the UI list to focus on

    def execute(self, context):
        scene = context.scene
        item = scene.topo_report_list[self.index]
        obj = context.active_object

        # 1. Ensure Edit Mode
        if obj.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')
        
        # 2. Parse Indices
        if not item.element_indices:
            self.report({'WARNING'}, "No specific geometry to select for this issue.")
            return {'FINISHED'}
            
        try:
            # Convert string "1,2,3" back to list of integers [1, 2, 3]
            ids = [int(i) for i in item.element_indices.split(',')]
        except:
            return {'FINISHED'}

        # 3. Cycle Pointer (Loop back to start if we reach the end)
        if item.current_pointer >= len(ids):
            item.current_pointer = 0
        
        target_id = ids[item.current_pointer]

        # 4. BMesh Selection
        bm = bmesh.from_edit_mesh(obj.data)
        
        # Deselect Everything first
        for v in bm.verts: v.select = False
        for e in bm.edges: e.select = False
        for f in bm.faces: f.select = False
        
        # Select the specific Target
        bm.select_history.clear()
        
        try:
            if item.element_type == 'FACE':
                bm.faces.ensure_lookup_table()
                if target_id < len(bm.faces):
                    target = bm.faces[target_id]
                    target.select = True
                    # Also select verts so it lights up clearly
                    for v in target.verts: v.select = True 

            elif item.element_type == 'EDGE':
                bm.edges.ensure_lookup_table()
                if target_id < len(bm.edges):
                    target = bm.edges[target_id]
                    target.select = True
                    for v in target.verts: v.select = True

            elif item.element_type == 'VERT':
                bm.verts.ensure_lookup_table()
                if target_id < len(bm.verts):
                    bm.verts[target_id].select = True
        except IndexError:
            self.report({'ERROR'}, "Geometry changed. Please Re-Analyze.")
            return {'CANCELLED'}

        # Flush selection (Update Blender's internal state)
        bmesh.update_edit_mesh(obj.data)
        
        # 5. Zoom View to Selection
        bpy.ops.view3d.view_selected(use_all_regions=False)

        # 6. Prepare for next click
        item.current_pointer += 1
        msg = f"Selected {item.element_type} #{target_id} ({item.current_pointer}/{len(ids)})"
        self.report({'INFO'}, msg)

        return {'FINISHED'}


# --- 3. ANALYSIS ENGINE ---

class MESH_OT_AnalyzeTopo(bpy.types.Operator):
    """Analyzes the mesh and populates the report"""
    bl_idname = "mesh.analyze_topo_ultimate"
    bl_label = "Run Full Analysis"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        scene.topo_report_list.clear()
        stats = scene.topo_stats
        obj = context.active_object
        
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Please select a Mesh object.")
            return {'CANCELLED'}

        stats.target_name = obj.name
        
        # Switch to Object mode for clean reading
        previous_mode = obj.mode
        if previous_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        
        # Stats
        total_faces = len(bm.faces)
        if total_faces == 0:
            bm.free()
            return {'FINISHED'}

        stats.vert_count = len(bm.verts)
        stats.face_count = total_faces
        stats.tri_count = sum(1 for f in bm.faces if len(f.verts) == 3)
        stats.ngon_count = sum(1 for f in bm.faces if len(f.verts) > 4)
        
        # --- COLLECT ISSUES ---

        # 1. N-Gons
        ngon_faces = [f for f in bm.faces if len(f.verts) > 4]
        if ngon_faces:
            # Store first 200 indices to prevent memory overflow on huge meshes
            indices_str = ",".join([str(f.index) for f in ngon_faces[:200]])
            
            ratio = (len(ngon_faces) / total_faces) * 100
            severity = "HIGH" if ratio > 1 else "MEDIUM"
            
            self.add_issue(scene, severity, 
                f"{len(ngon_faces)} N-Gons Detected",
                "Faces with >4 edges. Click the button to cycle through them and fix.",
                indices_str, "FACE"
            )

        # 2. Non-Manifold (Holes)
        bad_edges = [e for e in bm.edges if not e.is_manifold]
        if bad_edges:
            indices_str = ",".join([str(e.index) for e in bad_edges[:200]])
            self.add_issue(scene, "CRITICAL",
                "Non-Manifold Geometry",
                "Edges creating holes or internal faces.",
                indices_str, "EDGE"
            )

        # 3. Slivers (Thin Faces)
        sliver_faces = []
        for f in bm.faces:
            if len(f.edges) > 2:
                lengths = [e.calc_length() for e in f.edges]
                if min(lengths) > 0 and (max(lengths) / min(lengths)) > 15:
                    sliver_faces.append(f)
        
        if sliver_faces:
            indices_str = ",".join([str(f.index) for f in sliver_faces[:200]])
            self.add_issue(scene, "MEDIUM",
                f"{len(sliver_faces)} Sliver Faces",
                "Long, thin faces causing shading artifacts.",
                indices_str, "FACE"
            )

        # 4. Poles (>5 edges)
        pole_verts = [v for v in bm.verts if len(v.link_edges) > 5]
        if pole_verts:
            indices_str = ",".join([str(v.index) for v in pole_verts[:200]])
            self.add_issue(scene, "INFO",
                f"{len(pole_verts)} High-Valence Poles",
                "Vertices with >5 edges (Stars). Check placement.",
                indices_str, "VERT"
            )

        # --- SCORING ---
        current_score = 100.0
        current_score -= ((len(ngon_faces) / total_faces) * 200)
        if bad_edges: current_score -= 25
        current_score -= ((len(sliver_faces) / total_faces) * 100)
        stats.score = int(max(0, min(100, current_score)))

        if len(scene.topo_report_list) == 0:
            self.add_issue(scene, "GOOD", "Immaculate Topology", "No issues found.", "", "")

        bm.free()
        if previous_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=previous_mode)
            
        return {'FINISHED'}

    def add_issue(self, scene, severity, name, description, indices, type_name):
        item = scene.topo_report_list.add()
        item.severity = severity
        item.name = name
        item.description = description
        item.element_indices = indices
        item.element_type = type_name
        item.current_pointer = 0

# --- 4. UI PANEL ---

class VIEW3D_PT_TopoDoctorPanel(bpy.types.Panel):
    bl_label = "Topology Doctor"
    bl_idname = "VIEW3D_PT_topo_doctor_interactive"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Topo Doctor'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        stats = scene.topo_stats
        
        # Header
        row = layout.row()
        row.scale_y = 1.6
        row.operator("mesh.analyze_topo_ultimate", icon='SHADING_WIRE', text="Analyze Mesh")

        if stats.target_name == "":
            return

        # Score
        box = layout.box()
        row = box.row()
        row.label(text=f"Health: {stats.score}/100")
        if stats.score >= 90: row.label(icon='CHECKBOX_HLT')
        elif stats.score >= 50: row.label(icon='ERROR')
        else: row.label(icon='CANCEL')

        # Report List
        layout.label(text="Issues (Click icon to Focus):")
        
        for index, item in enumerate(scene.topo_report_list):
            box = layout.box()
            
            # Header Row: Icon + Title + FOCUS BUTTON
            row = box.row()
            
            # Severity Icon
            icon = 'INFO'
            if item.severity == 'CRITICAL': icon = 'CANCEL'
            elif item.severity == 'HIGH': icon = 'ERROR'
            elif item.severity == 'MEDIUM': icon = 'SOLO_ON'
            elif item.severity == 'GOOD': icon = 'CHECKBOX_HLT'
            
            row.label(text=f"[{item.severity}]", icon=icon)
            
            # THE MAGIC BUTTON
            # Only show button if there are elements to select
            if item.element_indices:
                op = row.operator("mesh.focus_issue_element", text="", icon='VIEWZOOM')
                op.index = index
            
            row.label(text=item.name)

            # Description
            col = box.column()
            lines = textwrap.wrap(item.description, width=35)
            for line in lines:
                col.label(text=" " + line)

# --- 5. REGISTRATION ---

classes = (
    TopoIssueItem,
    TopoStats,
    MESH_OT_FocusIssue,
    MESH_OT_AnalyzeTopo,
    VIEW3D_PT_TopoDoctorPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.topo_report_list = bpy.props.CollectionProperty(type=TopoIssueItem)
    bpy.types.Scene.topo_stats = bpy.props.PointerProperty(type=TopoStats)

def unregister():
    del bpy.types.Scene.topo_report_list
    del bpy.types.Scene.topo_stats
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
