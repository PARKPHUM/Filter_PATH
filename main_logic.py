import os
import urllib.request
import zipfile
import shutil
import tempfile
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon, QCursor
from qgis.PyQt.QtWidgets import (QAction, QDialog, QVBoxLayout, QLabel, 
                                 QLineEdit, QPushButton, QMessageBox, QFormLayout, QGroupBox,
                                 QTableWidget, QTableWidgetItem, QHeaderView, QMenu, QHBoxLayout, QAbstractItemView)
from qgis.gui import QgsMapLayerComboBox, QgsMapToolEmitPoint
from qgis.core import QgsMapLayerProxyModel, QgsRectangle, QgsFeatureRequest

# =====================================================================
# ตั้งค่าลิงก์ GitHub Repository ของคุณที่นี่ (ต้องเป็นลิงก์ดาวน์โหลดแบบ .zip)
# เช่น "https://github.com/my-name/my-plugin/archive/refs/heads/main.zip"
# =====================================================================
GITHUB_UPDATE_URL = "https://github.com/PARKPHUM/Filter_PATH/archive/refs/heads/main.zip"

# --- 1. หน้าต่างสำหรับการกรอกชื่อหมุดหลักเขตใหม่ (POINT) ---
class EditBndNameDialog(QDialog):
    def __init__(self, layer, features, parent=None):
        super(EditBndNameDialog, self).__init__(parent)
        self.setWindowTitle("บันทึกชื่อหมุดหลักเขตใหม่")
        self.setMinimumWidth(350)
        self.layer = layer
        self.features = features
        
        self.setStyleSheet("""
            QLabel { font-size: 12pt; font-weight: bold; }
            QLineEdit { font-size: 14pt; padding: 5px; }
            QPushButton { font-size: 12pt; font-weight: bold; padding: 8px; background-color: #28a745; color: white; border-radius: 5px; }
        """)

        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"แก้ไขข้อมูลหมุดจำนวน {len(features)} จุด"))
        
        self.name_input = QLineEdit()
        
        idx = self.layer.fields().indexOf("BND_NAME")
        if idx != -1 and len(self.features) > 0:
            current_val = self.features[0].attribute(idx)
            if current_val:
                self.name_input.setText(str(current_val))
                
        self.name_input.setPlaceholderText("ระบุชื่อ BND_NAME ใหม่...")
        layout.addWidget(self.name_input)
        
        self.btn_save = QPushButton("Save & Commit (บันทึกลงไฟล์)")
        self.btn_save.clicked.connect(self.save_data)
        layout.addWidget(self.btn_save)
        
        self.setLayout(layout)

    def save_data(self):
        new_name = self.name_input.text().strip()
        idx = self.layer.fields().indexOf("BND_NAME")
        if idx == -1:
            QMessageBox.critical(self, "ข้อผิดพลาด", "ไม่พบคอลัมน์ 'BND_NAME'")
            return

        if not self.layer.isEditable():
            self.layer.startEditing()

        for f in self.features:
            self.layer.changeAttributeValue(f.id(), idx, new_name)
        
        if self.layer.commitChanges():
            QMessageBox.information(self, "สำเร็จ", "บันทึกชื่อหมุดหลักเขตลงในไฟล์เรียบร้อยแล้ว")
            self.accept()
        else:
            self.layer.rollBack()
            QMessageBox.warning(self, "ข้อผิดพลาด", "ไม่สามารถบันทึกข้อมูลลงไฟล์ได้")

# --- 2. หน้าต่างสำหรับแก้ไข Attribute (Selective Editing) ---
class EditAttributesDialog(QDialog):
    def __init__(self, point_layer, poly_layer, selected_point_ids, selected_poly_ids, parent=None):
        super(EditAttributesDialog, self).__init__(parent)
        self.setWindowTitle("แก้ไข Attribute และคำนวณ PATH ใหม่")
        self.setMinimumWidth(450)
        self.point_layer = point_layer
        self.poly_layer = poly_layer
        self.selected_point_ids = selected_point_ids
        self.selected_poly_ids = selected_poly_ids
        
        self.fields_to_edit = ["UTMMAP1", "UTMMAP2", "UTMMAP3", "UTMSCALE", "UTMMAP4"]
        self.landno_alias = ["LANDNO", "LAND_NO"]
        self.inputs = {}

        self.setStyleSheet("""
            QLabel { font-size: 11pt; font-weight: bold; }
            QLineEdit { font-size: 12pt; padding: 5px; }
            QPushButton { font-size: 12pt; font-weight: bold; padding: 10px; background-color: #17a2b8; color: white; border-radius: 5px; }
        """)

        main_layout = QVBoxLayout()
        form_group = QGroupBox("ระบุข้อมูล (จะอัปเดตเฉพาะรายการที่ติ๊กเลือกไว้ในตาราง)")
        form_layout = QFormLayout()

        # ดึงค่าตัวอย่างจากอันแรกที่เลือก
        sample_feature = None
        if self.poly_layer and self.selected_poly_ids:
            sample_feature = self.poly_layer.getFeature(self.selected_poly_ids[0])
        elif self.point_layer and self.selected_point_ids:
            sample_feature = self.point_layer.getFeature(self.selected_point_ids[0])

        for field in self.fields_to_edit:
            line_edit = QLineEdit()
            if sample_feature:
                base_layer = self.poly_layer if self.selected_poly_ids else self.point_layer
                idx = base_layer.fields().indexOf(field)
                if idx != -1:
                    val = sample_feature.attribute(idx)
                    if val: line_edit.setText(str(val))
            self.inputs[field] = line_edit
            form_layout.addRow(QLabel(field + ":"), line_edit)

        self.actual_landno_field = "LANDNO"
        base_layer_for_landno = self.poly_layer if self.poly_layer else self.point_layer
        if base_layer_for_landno:
            for name in self.landno_alias:
                if base_layer_for_landno.fields().indexOf(name) != -1:
                    self.actual_landno_field = name
                    break
        
        self.landno_input = QLineEdit()
        if sample_feature:
            idx = base_layer_for_landno.fields().indexOf(self.actual_landno_field)
            if idx != -1:
                val = sample_feature.attribute(idx)
                if val: self.landno_input.setText(str(val))
        
        form_layout.addRow(QLabel(f"LANDNO ({self.actual_landno_field}):"), self.landno_input)
        form_group.setLayout(form_layout)
        main_layout.addWidget(form_group)

        self.btn_save = QPushButton("บันทึกการแก้ไขและคำนวณ PATH ใหม่")
        self.btn_save.clicked.connect(self.save_attributes)
        main_layout.addWidget(self.btn_save)

        self.setLayout(main_layout)

    def save_attributes(self):
        layers_data = []
        if self.point_layer and self.selected_point_ids:
            layers_data.append((self.point_layer, self.selected_point_ids))
        if self.poly_layer and self.selected_poly_ids:
            layers_data.append((self.poly_layer, self.selected_poly_ids))
            
        data = {f: self.inputs[f].text().strip() for f in self.fields_to_edit}
        landno_val = self.landno_input.text().strip()
        sample_new_path = ""

        for layer, selected_ids in layers_data:
            if not layer.isEditable():
                layer.startEditing()
            
            path_idx = layer.fields().indexOf("PATH")
            landno_idx = -1
            for name in self.landno_alias:
                idx = layer.fields().indexOf(name)
                if idx != -1:
                    landno_idx = idx
                    break

            for f_id in selected_ids:
                f = layer.getFeature(f_id)
                if not f.isValid(): continue
                
                for field_name, val in data.items():
                    f_idx = layer.fields().indexOf(field_name)
                    if f_idx != -1:
                        layer.changeAttributeValue(f.id(), f_idx, val)
                
                if landno_idx != -1:
                    layer.changeAttributeValue(f.id(), landno_idx, landno_val)
                
                if path_idx != -1:
                    old_path = f.attribute(path_idx)
                    old_path_str = str(old_path).strip() if old_path else ""
                    
                    prefix = "\\\\192.168.99.25\\ภาพลักษณ์ศรีราชา"
                    parts = old_path_str.replace('/', '\\').split('\\')
                    
                    if old_path_str.startswith("\\\\") and len(parts) >= 4:
                        prefix = f"\\\\{parts[2]}\\{parts[3]}"
                    elif len(parts) >= 2 and not old_path_str.startswith("\\\\"):
                        prefix = f"{parts[0]}\\{parts[1]}"
                        
                    suffix_parts = [data[field] for field in self.fields_to_edit if data[field]]
                    if landno_val:
                        suffix_parts.append(landno_val)
                        
                    new_path = prefix + "\\" + "\\".join(suffix_parts)
                    sample_new_path = new_path
                    
                    layer.changeAttributeValue(f.id(), path_idx, new_path)
            
            layer.commitChanges()
        
        QMessageBox.information(self, "สำเร็จ", f"อัปเดตข้อมูลและคำนวณ PATH ใหม่เรียบร้อยแล้ว\nตัวอย่างที่ได้:\n{sample_new_path}")
        self.accept()

# --- 2.5 เครื่องมือ Hybrid Select สำหรับแก้ปัญหา Overlap ---
class FeatureSelectTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, parent_dialog):
        super(FeatureSelectTool, self).__init__(canvas)
        self.canvas = canvas
        self.parent_dialog = parent_dialog
        self.setCursor(Qt.CrossCursor)

    def canvasReleaseEvent(self, e):
        point = self.toMapCoordinates(e.pos())
        tol = self.canvas.mapUnitsPerPixel() * 10
        rect = QgsRectangle(point.x() - tol, point.y() - tol, point.x() + tol, point.y() + tol)
        
        request = QgsFeatureRequest().setFilterRect(rect)
        
        p_layer = self.parent_dialog.point_combo.currentLayer()
        poly_layer = self.parent_dialog.poly_combo.currentLayer()
        
        found_features = []
        
        if p_layer:
            for f in p_layer.getFeatures(request):
                row = self.parent_dialog.find_row_by_id_and_type(f.id(), "Point")
                if row != -1:
                    found_features.append({"type": "Point", "id": f.id(), "desc": f"หมุด ID: {f.id()}"})
                    
        if poly_layer:
            for f in poly_layer.getFeatures(request):
                row = self.parent_dialog.find_row_by_id_and_type(f.id(), "Polygon")
                if row != -1:
                    idx = poly_layer.fields().indexOf("LANDNO")
                    if idx == -1: idx = poly_layer.fields().indexOf("LAND_NO")
                    landno = f.attribute(idx) if idx != -1 else f.id()
                    found_features.append({"type": "Polygon", "id": f.id(), "desc": f"แปลง LANDNO: {landno}"})

        if not found_features:
            self.parent_dialog.iface.messageBar().pushMessage("แจ้งเตือน", "ไม่พบรายการที่ตรงกับในตาราง Filter", level=1)
            return

        if len(found_features) == 1:
            self.parent_dialog.toggle_table_row(found_features[0]["id"], found_features[0]["type"])
        else:
            # Overlap case - popup menu
            menu = QMenu()
            for item in found_features:
                action = QAction(item["desc"], menu)
                action.triggered.connect(lambda checked, i=item: self.parent_dialog.toggle_table_row(i["id"], i["type"]))
                menu.addAction(action)
            menu.exec_(QCursor.pos())

# --- 3. เครื่องมือเมาส์สำหรับคลิกเลือกหมุดบนแผนที่ (อันเก่า สำหรับ BND_NAME) ---
class PointSelectTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, layer, parent_dialog):
        super(PointSelectTool, self).__init__(canvas)
        self.canvas = canvas
        self.layer = layer
        self.parent_dialog = parent_dialog
        self.setCursor(Qt.CrossCursor)

    def canvasReleaseEvent(self, e):
        point = self.toMapCoordinates(e.pos())
        tol = self.canvas.mapUnitsPerPixel() * 10
        rect = QgsRectangle(point.x() - tol, point.y() - tol, point.x() + tol, point.y() + tol)
        
        request = QgsFeatureRequest().setFilterRect(rect)
        features = [f for f in self.layer.getFeatures(request)]
        
        if features:
            dlg = EditBndNameDialog(self.layer, features, self.parent_dialog)
            dlg.exec_()
        else:
            self.parent_dialog.iface.messageBar().pushMessage("แจ้งเตือน", "ไม่พบหมุดบริเวณที่คลิก", level=1)
        self.canvas.unsetMapTool(self)


# --- 4. หน้าต่างเครื่องมือหลัก ---
class PathFilterTool(QDialog):
    def __init__(self, iface, parent=None):
        super(PathFilterTool, self).__init__(parent)
        self.iface = iface
        self.edit_tool = None 
        self.hybrid_select_tool = None
        self.setWindowTitle("PATH Filter & Edit Attribute UTM Version 2.7")
        self.setMinimumWidth(600)
        self.resize(650, 600)
        
        self.setStyleSheet("""
            QDialog { background-color: #f8f9fa; }
            QLabel { font-size: 11pt; font-weight: bold; color: #333; margin-top: 5px; }
            QLineEdit { font-size: 12pt; padding: 5px; border: 1px solid #ccc; border-radius: 4px; }
            QComboBox { font-size: 11pt; padding: 5px; min-height: 30px; }
            QPushButton { font-size: 11pt; font-weight: bold; padding: 8px; border-radius: 4px; }
            QTableWidget { font-size: 11pt; background-color: white; }
        """)

        layout = QVBoxLayout()

        layout.addWidget(QLabel("1. ระบุค่าที่ต้องการค้นหา (PATH) - ว่างได้:"))
        self.search_input = QLineEdit()
        layout.addWidget(self.search_input)

        layout.addWidget(QLabel("2. เลือก Layer (POINT) - ว่างได้:"))
        self.point_combo = QgsMapLayerComboBox()
        self.point_combo.setAllowEmptyLayer(True) 
        self.point_combo.setFilters(QgsMapLayerProxyModel.PointLayer)
        layout.addWidget(self.point_combo)

        layout.addWidget(QLabel("3. เลือก Layer (POLYGON) - ว่างได้:"))
        self.poly_combo = QgsMapLayerComboBox()
        self.poly_combo.setAllowEmptyLayer(True) 
        self.poly_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        layout.addWidget(self.poly_combo)

        layout.addWidget(QLabel("4. ระบุค่าที่ต้องการค้นหา (FILE_NAME) - ว่างได้:"))
        self.filename_input = QLineEdit()
        layout.addWidget(self.filename_input)

        self.btn_filter = QPushButton("เริ่ม Filter ข้อมูล (ซูมอัตโนมัติ)")
        self.btn_filter.setStyleSheet("background-color: #007bff; color: white; margin-top: 10px;")
        self.btn_filter.clicked.connect(self.apply_filter_and_zoom)
        layout.addWidget(self.btn_filter)

        # --- ส่วนแสดงตาราง List View ---
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["เลือก", "ประเภท", "รายละเอียด (LANDNO/ID)"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemChanged.connect(self.update_map_selection)
        layout.addWidget(QLabel("รายการที่ค้นพบ (ติ๊กหน้ารายการที่ต้องการแก้ไข):"))
        layout.addWidget(self.table)
        
        # ปุ่ม Select Actions
        btn_layout = QHBoxLayout()
        self.btn_hybrid_select = QPushButton("จิ้มเลือกบนแผนที่")
        self.btn_hybrid_select.setStyleSheet("background-color: #6f42c1; color: white;")
        self.btn_hybrid_select.clicked.connect(self.activate_hybrid_tool)
        btn_layout.addWidget(self.btn_hybrid_select)
        
        self.btn_auto_select = QPushButton("เลือกหมุดที่ LANDNO ตรงกับแปลง")
        self.btn_auto_select.setStyleSheet("background-color: #17a2b8; color: white;")
        self.btn_auto_select.clicked.connect(self.auto_select_related_points)
        btn_layout.addWidget(self.btn_auto_select)
        layout.addLayout(btn_layout)

        # --- ส่วน Action แก้ไข ---
        self.btn_edit_attr = QPushButton("แก้ไข Attribute และบันทึก (สำหรับตัวที่ติ๊กถูก)")
        self.btn_edit_attr.setStyleSheet("background-color: #28a745; color: white; margin-top: 10px;")
        self.btn_edit_attr.clicked.connect(self.open_edit_attributes)
        layout.addWidget(self.btn_edit_attr)

        self.btn_edit_point = QPushButton("แก้ไข ชื่อหมุดหลักเขต (คลิกบนแผนที่)")
        self.btn_edit_point.setStyleSheet("background-color: #ffc107; color: black;")
        self.btn_edit_point.clicked.connect(self.activate_edit_point_tool)
        layout.addWidget(self.btn_edit_point)

        self.btn_clear = QPushButton("แสดงข้อมูลทั้งหมด (Clear)")
        self.btn_clear.setStyleSheet("background-color: #6c757d; color: white;")
        self.btn_clear.clicked.connect(self.clear_filter)
        layout.addWidget(self.btn_clear)

        self.btn_update = QPushButton("อัปเดตปลั๊กอินล่าสุด (Auto-Update)")
        self.btn_update.setStyleSheet("background-color: #dc3545; color: white; margin-top: 15px;")
        self.btn_update.clicked.connect(self.update_plugin)
        layout.addWidget(self.btn_update)

        self.setLayout(layout)

    def apply_filter_and_zoom(self):
        path_val = self.search_input.text().strip()
        file_val = self.filename_input.text().strip()
        p_layer = self.point_combo.currentLayer()
        poly_layer = self.poly_combo.currentLayer()
        
        if not path_val and not file_val:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาระบุคำค้นหา (PATH หรือ FILE_NAME) อย่างน้อย 1 ช่อง")
            return

        combined_extent = QgsRectangle()
        combined_extent.setMinimal() 
        has_data = False
        
        for layer in [p_layer, poly_layer]:
            if layer: 
                filters = []
                if path_val:
                    filters.append(f"\"PATH\" = '{path_val}'")
                if file_val:
                    filters.append(f"\"FILE_NAME\" = '{file_val}'")
                
                subset_str = " AND ".join(filters)
                layer.setSubsetString(subset_str)
                layer.updateExtents()
                
                if not layer.extent().isEmpty():
                    combined_extent.combineExtentWith(layer.extent())
                    has_data = True

        if has_data:
            canvas = self.iface.mapCanvas()
            combined_extent.scale(1.1)
            canvas.setExtent(combined_extent)
            canvas.refresh()
            self.populate_table()
        else:
            self.table.setRowCount(0)

    def populate_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        p_layer = self.point_combo.currentLayer()
        poly_layer = self.poly_combo.currentLayer()
        
        row_idx = 0
        if poly_layer and poly_layer.subsetString():
            idx_landno = poly_layer.fields().indexOf("LANDNO")
            if idx_landno == -1: idx_landno = poly_layer.fields().indexOf("LAND_NO")
            idx_parcel = poly_layer.fields().indexOf("PARCELNO")
            idx_survey = poly_layer.fields().indexOf("SURVEYNO")
            
            for f in poly_layer.getFeatures():
                landno = f.attribute(idx_landno) if idx_landno != -1 else str(f.id())
                parcelno = f.attribute(idx_parcel) if idx_parcel != -1 else "-"
                surveyno = f.attribute(idx_survey) if idx_survey != -1 else "-"
                
                self.table.insertRow(row_idx)
                
                chk = QTableWidgetItem("")
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Unchecked)
                chk.setData(Qt.UserRole, f.id())
                self.table.setItem(row_idx, 0, chk)
                
                type_item = QTableWidgetItem("Polygon")
                self.table.setItem(row_idx, 1, type_item)
                
                desc_item = QTableWidgetItem(f"{landno} - {parcelno} - {surveyno}")
                desc_item.setData(Qt.UserRole, str(landno))
                self.table.setItem(row_idx, 2, desc_item)
                row_idx += 1
                
        if p_layer and p_layer.subsetString():
            idx_landno = p_layer.fields().indexOf("LANDNO")
            if idx_landno == -1: idx_landno = p_layer.fields().indexOf("LAND_NO")
            idx_parcel = p_layer.fields().indexOf("PARCELNO")
            idx_survey = p_layer.fields().indexOf("SURVEYNO")
            
            for f in p_layer.getFeatures():
                landno = f.attribute(idx_landno) if idx_landno != -1 else str(f.id())
                parcelno = f.attribute(idx_parcel) if idx_parcel != -1 else "-"
                surveyno = f.attribute(idx_survey) if idx_survey != -1 else "-"
                
                self.table.insertRow(row_idx)
                
                chk = QTableWidgetItem("")
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Unchecked)
                chk.setData(Qt.UserRole, f.id())
                self.table.setItem(row_idx, 0, chk)
                
                type_item = QTableWidgetItem("Point")
                self.table.setItem(row_idx, 1, type_item)
                
                desc_item = QTableWidgetItem(f"ID:{f.id()}, {landno} - {parcelno} - {surveyno}")
                desc_item.setData(Qt.UserRole, str(landno))
                self.table.setItem(row_idx, 2, desc_item)
                row_idx += 1
                
        self.table.blockSignals(False)

    def update_map_selection(self, item=None):
        p_layer = self.point_combo.currentLayer()
        poly_layer = self.poly_combo.currentLayer()
        
        selected_points = []
        selected_polys = []
        
        for i in range(self.table.rowCount()):
            if self.table.item(i, 0).checkState() == Qt.Checked:
                f_type = self.table.item(i, 1).text()
                f_id = self.table.item(i, 0).data(Qt.UserRole)
                if f_type == "Point":
                    selected_points.append(f_id)
                elif f_type == "Polygon":
                    selected_polys.append(f_id)
                    
        if p_layer:
            p_layer.selectByIds(selected_points)
        if poly_layer:
            poly_layer.selectByIds(selected_polys)

    def find_row_by_id_and_type(self, f_id, f_type):
        for i in range(self.table.rowCount()):
            t_item = self.table.item(i, 1)
            c_item = self.table.item(i, 0)
            if t_item and c_item and t_item.text() == f_type and c_item.data(Qt.UserRole) == f_id:
                return i
        return -1

    def toggle_table_row(self, f_id, f_type):
        row = self.find_row_by_id_and_type(f_id, f_type)
        if row != -1:
            item = self.table.item(row, 0)
            new_state = Qt.Checked if item.checkState() == Qt.Unchecked else Qt.Unchecked
            item.setCheckState(new_state)
            self.table.selectRow(row)
            self.iface.messageBar().pushMessage("แจ้ง", f"เลือกรายการแถวที่ {row+1} แล้ว", level=0)

    def activate_hybrid_tool(self):
        canvas = self.iface.mapCanvas()
        self.hybrid_select_tool = FeatureSelectTool(canvas, self)
        canvas.setMapTool(self.hybrid_select_tool)
        self.iface.messageBar().pushMessage("เครื่องมือ", "กรุณาคลิกบนแผนที่เพื่อเลือกหรือยกเลิกรายการ", level=0)

    def auto_select_related_points(self):
        selected_landnos = set()
        for i in range(self.table.rowCount()):
            if self.table.item(i, 0).checkState() == Qt.Checked and self.table.item(i, 1).text() == "Polygon":
                landno = self.table.item(i, 2).data(Qt.UserRole)
                if landno:
                    selected_landnos.add(str(landno))
        
        if not selected_landnos:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาติ๊กเลือก Polygon (แปลง) อย่างน้อย 1 อันก่อนกดปุ่มนี้")
            return
            
        count = 0
        for i in range(self.table.rowCount()):
            if self.table.item(i, 1).text() == "Point":
                landno = self.table.item(i, 2).data(Qt.UserRole)
                if landno and str(landno) in selected_landnos:
                    self.table.item(i, 0).setCheckState(Qt.Checked)
                    count += 1
        
        QMessageBox.information(self, "สำเร็จ", f"ติ๊กเลือกหมุดที่มี LANDNO ตรงกับแปลงอัตโนมัติจำนวน {count} จุด")

    def open_edit_attributes(self):
        p_layer = self.point_combo.currentLayer()
        poly_layer = self.poly_combo.currentLayer()
        
        selected_point_ids = []
        selected_poly_ids = []
        for i in range(self.table.rowCount()):
            if self.table.item(i, 0).checkState() == Qt.Checked:
                f_type = self.table.item(i, 1).text()
                f_id = self.table.item(i, 0).data(Qt.UserRole)
                if f_type == "Point":
                    selected_point_ids.append(f_id)
                elif f_type == "Polygon":
                    selected_poly_ids.append(f_id)
        
        if not selected_point_ids and not selected_poly_ids:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาติ๊กเลือกรายการในตารางอย่างน้อย 1 รายการก่อนทำการแก้ไข Attribute")
            return
            
        dlg = EditAttributesDialog(p_layer, poly_layer, selected_point_ids, selected_poly_ids, self)
        dlg.exec_()

    def activate_edit_point_tool(self):
        p_layer = self.point_combo.currentLayer()
        if not p_layer:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาเลือก Layer POINT ก่อน")
            return
        
        canvas = self.iface.mapCanvas()
        self.edit_tool = PointSelectTool(canvas, p_layer, self)
        canvas.setMapTool(self.edit_tool)
        self.iface.messageBar().pushMessage("แจ้ง", "คลิกที่หมุดบนแผนที่เพื่อแก้ไข BND_NAME", level=0)

    def clear_filter(self):
        for combo in [self.point_combo, self.poly_combo]:
            l = combo.currentLayer()
            if l:
                l.setSubsetString("")
                l.updateExtents()
                l.removeSelection()
        self.table.setRowCount(0)

    def update_plugin(self):
        if "USERNAME" in GITHUB_UPDATE_URL:
            QMessageBox.warning(self, "ไม่พบลิงก์อัปเดต", "นักพัฒนาโปรดไปตั้งค่า GITHUB_UPDATE_URL ภายในไฟล์ main_logic.py ก่อนใช้งานฟังก์ชันนี้")
            return

        reply = QMessageBox.question(self, "ยืนยันอัปเดต", 
            "อัปเดตเป็นเวอร์ชันล่าสุดเลยหรือไม่?",
            QMessageBox.Yes | QMessageBox.No)
            
        if reply == QMessageBox.No: return
        
        try:
            plugin_dir = os.path.dirname(__file__)
            temp_dir = tempfile.mkdtemp()
            zip_path = os.path.join(temp_dir, "update.zip")
            
            urllib.request.urlretrieve(GITHUB_UPDATE_URL, zip_path)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            
            # ปกติการแตกไฟล์จาก GitHub จะมี Folder หุ้มไว้ 1 ชั้น
            extracted_folders = [os.path.join(temp_dir, d) for d in os.listdir(temp_dir) if os.path.isdir(os.path.join(temp_dir, d))]
            if extracted_folders:
                source_dir = extracted_folders[0]
                for item in os.listdir(source_dir):
                    s = os.path.join(source_dir, item)
                    d = os.path.join(plugin_dir, item)
                    if os.path.isdir(s):
                        if os.path.exists(d): shutil.rmtree(d)
                        shutil.copytree(s, d)
                    else:
                        shutil.copy2(s, d)
                        
            shutil.rmtree(temp_dir)
            QMessageBox.information(self, "อัปเดตสำเร็จ!", 
                "ทำการคัดลอกไฟล์เวอร์ชันล่าสุดเรียบร้อยแล้ว\n\n** กรุณาปิดโปรแกรม QGIS และเปิดใหม่อีกครั้ง **\nเพื่อให้โปรแกรมโหลดเวอร์ชันล่าสุดขึ้นมาทำงาน")
            
        except Exception as e:
            QMessageBox.critical(self, "อัปเดตล้มเหลว", f"เกิดข้อผิดพลาดในการดาวน์โหลดหรือเขียนไฟล์ทับ:\n{str(e)}")

# --- 5. ส่วนการจัดการตัวปลั๊กอิน ---
class PathFilterPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), 'icon.png')
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self.action = QAction(icon, "Path Filter & Edit Tool (DOL)", self.iface.mainWindow())
        self.action.triggered.connect(self.run)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToVectorMenu("Land Dept Tools", self.action)

    def unload(self):
        self.iface.removePluginVectorMenu("Land Dept Tools", self.action)
        self.iface.removeToolBarIcon(self.action)

    def run(self):
        self.dlg = PathFilterTool(self.iface, self.iface.mainWindow())
        self.dlg.show()