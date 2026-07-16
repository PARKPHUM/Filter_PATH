import os
import urllib.request
import zipfile
import shutil
import tempfile
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon, QCursor, QColor
from qgis.PyQt.QtWidgets import (QAction, QDialog, QVBoxLayout, QLabel,
                                 QLineEdit, QPushButton, QMessageBox, QFormLayout, QGroupBox,
                                 QMenu, QHBoxLayout, QListWidget, QListWidgetItem)
from qgis.gui import (QgsMapLayerComboBox, QgsMapToolEmitPoint, QgsRubberBand,
                      QgsVertexMarker)
from qgis.core import (QgsMapLayerProxyModel, QgsRectangle, QgsFeatureRequest,
                       QgsGeometry, QgsCoordinateTransform, QgsProject, QgsWkbTypes,
                       QgsPointXY)

# =====================================================================
# ตั้งค่าลิงก์ GitHub Repository ของคุณที่นี่ (ต้องเป็นลิงก์ดาวน์โหลดแบบ .zip)
# เช่น "https://github.com/my-name/my-plugin/archive/refs/heads/main.zip"
# =====================================================================
GITHUB_UPDATE_URL = "https://github.com/PARKPHUM/Filter_PATH/archive/refs/heads/main.zip"

# ลิงก์ไฟล์ metadata.txt บน GitHub (ใช้ตรวจสอบเลขเวอร์ชันล่าสุดก่อนอัปเดต)
GITHUB_METADATA_URL = "https://raw.githubusercontent.com/PARKPHUM/Filter_PATH/main/metadata.txt"

# =====================================================================
# ผู้พัฒนาปลั๊กอิน: นายภาคภูมิ สูบกำปัง
# ตำแหน่ง: วิศวกรรังวัดปฏิบัติการ กองเทคโนโลยีทำแผนที่ กรมที่ดิน
# =====================================================================
PLUGIN_AUTHOR = "นายภาคภูมิ สูบกำปัง (วิศวกรรังวัดปฏิบัติการ กองเทคโนโลยีทำแผนที่)"

# ระยะคลาดเคลื่อนสูงสุด (หน่วยแผนที่ เช่น เมตร) ที่ยอมรับว่าหมุดอยู่ตรงมุมเขต (Vertex) ของแปลง
VERTEX_TOLERANCE = 0.10


def escape_sql(value):
    """กัน single quote ในค่าที่ผู้ใช้กรอก ไม่ให้ expression พัง"""
    return str(value).replace("'", "''")


def parse_version(text):
    """แปลงข้อความเวอร์ชัน เช่น '3.0' ให้เป็น tuple (3, 0) เพื่อใช้เปรียบเทียบ"""
    try:
        return tuple(int(x) for x in str(text).strip().split("."))
    except (ValueError, AttributeError):
        return None


def format_area_value(v):
    """จัดรูปตัวเลขเนื้อที่: ตัด .0 ท้ายจำนวนเต็ม, ค่าว่าง/NULL แสดงเป็น -"""
    if v is None:
        return "-"
    s = str(v).strip()
    if s in ("", "NULL"):
        return "-"
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except ValueError:
        pass
    return s


# --- 1. หน้าต่างสำหรับการกรอกชื่อหมุดหลักเขตใหม่ (POINT) ---
class EditBndNameDialog(QDialog):
    def __init__(self, layer, features, parent=None):
        super(EditBndNameDialog, self).__init__(parent)
        self.setWindowTitle("บันทึกชื่อหมุดหลักเขตใหม่")
        self.setMinimumWidth(320)
        self.layer = layer
        self.features = features

        self.setStyleSheet("""
            QLabel { font-size: 10pt; font-weight: bold; }
            QLineEdit { font-size: 12pt; padding: 4px; }
            QPushButton { font-size: 10pt; font-weight: bold; padding: 6px; background-color: #28a745; color: white; border-radius: 4px; }
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


# --- 2. หน้าต่างสำหรับแก้ไข Attribute (แปลงที่คลิกเลือก + หมุดที่อยู่ตรงมุมเขตของแปลง) ---
class EditAttributesDialog(QDialog):
    def __init__(self, point_layer, poly_layer, selected_point_ids, selected_poly_ids,
                 parent=None, multi_mode=False):
        super(EditAttributesDialog, self).__init__(parent)
        self.setWindowTitle("แก้ไข Attribute และคำนวณ PATH ใหม่")
        self.setMinimumWidth(400)
        self.point_layer = point_layer
        self.poly_layer = poly_layer
        self.selected_point_ids = selected_point_ids
        self.selected_poly_ids = selected_poly_ids
        # โหมดแก้ไขหลายแปลงพร้อมกัน: ไม่แสดงช่อง LANDNO และคง LANDNO เดิมของแต่ละแปลง
        self.multi_mode = multi_mode

        self.fields_to_edit = ["UTMMAP1", "UTMMAP2", "UTMMAP3", "UTMSCALE", "UTMMAP4"]
        self.landno_alias = ["LANDNO", "LAND_NO"]
        self.inputs = {}

        self.setStyleSheet("""
            QLabel { font-size: 10pt; font-weight: bold; }
            QLineEdit { font-size: 11pt; padding: 4px; }
            QPushButton { font-size: 11pt; font-weight: bold; padding: 8px; background-color: #17a2b8; color: white; border-radius: 4px; }
        """)

        main_layout = QVBoxLayout()

        # สรุปว่ากำลังจะแก้ไขอะไรบ้าง
        info_text = f"จะแก้ไข: แปลง {len(selected_poly_ids)} แปลง | หมุด {len(selected_point_ids)} จุด"
        if self.multi_mode:
            info_text += "\n(แก้ไขหลายแปลง: LANDNO ของแต่ละแปลงจะคงเดิม)"
        info_label = QLabel(info_text)
        info_label.setStyleSheet("color: #c0392b; font-size: 10pt;")
        main_layout.addWidget(info_label)

        form_group = QGroupBox("ระบุข้อมูลใหม่")
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

        self.landno_input = None
        if not self.multi_mode:
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
        landno_val = self.landno_input.text().strip() if self.landno_input else ""
        sample_new_path = ""
        errors = []

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

                if landno_idx != -1 and not self.multi_mode:
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

                    # โหมดหลายแปลง: ใช้ LANDNO เดิมของแปลงนั้นๆ ต่อท้าย PATH แทนค่าจากช่องกรอก
                    if self.multi_mode:
                        own = f.attribute(landno_idx) if landno_idx != -1 else None
                        landno_for_path = str(own).strip() if own is not None and str(own).strip() not in ("", "NULL") else ""
                    else:
                        landno_for_path = landno_val

                    suffix_parts = [data[field] for field in self.fields_to_edit if data[field]]
                    if landno_for_path:
                        suffix_parts.append(landno_for_path)

                    new_path = prefix + "\\" + "\\".join(suffix_parts)
                    sample_new_path = new_path

                    layer.changeAttributeValue(f.id(), path_idx, new_path)

            if not layer.commitChanges():
                errors.append(f"{layer.name()}: " + "; ".join(layer.commitErrors()))
                layer.rollBack()

        if errors:
            QMessageBox.critical(self, "บันทึกไม่สำเร็จ",
                                 "บาง Layer บันทึกลงไฟล์ไม่ได้:\n" + "\n".join(errors))
            return

        QMessageBox.information(self, "สำเร็จ",
                                f"อัปเดตข้อมูลและคำนวณ PATH ใหม่เรียบร้อยแล้ว\nตัวอย่างที่ได้:\n{sample_new_path}")
        self.accept()


# --- 3. เครื่องมือเมาส์: คลิกแปลงบนแผนที่เพื่อแก้ไข Attribute ---
class ParcelClickTool(QgsMapToolEmitPoint):
    """คลิกซ้ายเลือกแปลง (ถ้าแปลงซ้อนกันจะมีเมนูให้เลือก + ไฮไลท์ขอบเขต), คลิกขวายกเลิกเครื่องมือ"""

    def __init__(self, canvas, parent_dialog):
        super(ParcelClickTool, self).__init__(canvas)
        self.canvas = canvas
        self.parent_dialog = parent_dialog
        self.setCursor(Qt.CrossCursor)
        self.hover_rb = None
        self.hover_fid = None
        self.selected_feats = {}   # fid -> (feature, rubber band) สำหรับโหมดเลือกหลายแปลง

    def clear_hover(self):
        if self.hover_rb:
            self.canvas.scene().removeItem(self.hover_rb)
            self.hover_rb = None
        self.hover_fid = None

    def clear_selection(self):
        for feat, rb in self.selected_feats.values():
            self.canvas.scene().removeItem(rb)
        self.selected_feats = {}

    def toggle_selection(self, feature, poly_layer):
        """Shift+คลิก: เพิ่ม/เอาออกแปลงจากรายการเลือกสะสม พร้อมกรอบสีน้ำเงิน"""
        fid = feature.id()
        if fid in self.selected_feats:
            _, rb = self.selected_feats.pop(fid)
            self.canvas.scene().removeItem(rb)
        else:
            rb = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
            rb.setToGeometry(feature.geometry(), poly_layer)
            rb.setColor(QColor(0, 120, 255, 60))
            rb.setStrokeColor(QColor(0, 90, 220))
            rb.setWidth(3)
            self.selected_feats[fid] = (feature, rb)
        self.parent_dialog.iface.messageBar().pushMessage(
            "แจ้ง", f"เลือกไว้ {len(self.selected_feats)} แปลง (คลิกขวาเพื่อเปิดหน้าต่างแก้ไข)", level=0)

    def show_hover(self, geom, layer):
        self.clear_hover()
        rb = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        rb.setToGeometry(geom, layer)
        rb.setColor(QColor(255, 200, 0, 70))
        rb.setStrokeColor(QColor(255, 140, 0))
        rb.setWidth(3)
        self.hover_rb = rb

    def canvasMoveEvent(self, e):
        """ชี้เมาส์โดนแปลงไหน ให้ไฮไลท์แปลงนั้นทันที"""
        poly_layer = self.parent_dialog.poly_combo.currentLayer()
        if not poly_layer:
            self.clear_hover()
            return

        map_point = self.toMapCoordinates(e.pos())
        tol = self.canvas.mapUnitsPerPixel() * 2
        rect = QgsRectangle(map_point.x() - tol, map_point.y() - tol,
                            map_point.x() + tol, map_point.y() + tol)
        try:
            transform = QgsCoordinateTransform(
                self.canvas.mapSettings().destinationCrs(), poly_layer.crs(), QgsProject.instance())
            layer_rect = transform.transformBoundingBox(rect)
            layer_point = transform.transform(map_point)
        except Exception:
            layer_rect = rect
            layer_point = map_point

        pt_geom = QgsGeometry.fromPointXY(QgsPointXY(layer_point))
        request = QgsFeatureRequest().setFilterRect(layer_rect).setSubsetOfAttributes([])

        found = None
        for f in poly_layer.getFeatures(request):
            if f.geometry() and f.geometry().intersects(pt_geom):
                found = f
                break

        if found is None:
            self.clear_hover()
        elif found.id() != self.hover_fid:
            self.show_hover(found.geometry(), poly_layer)
            self.hover_fid = found.id()

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.RightButton:
            self.clear_hover()
            # ถ้ามีแปลงที่เลือกสะสมไว้ (Shift+คลิก) -> คลิกขวาเปิดหน้าต่างแก้ไข
            if self.selected_feats:
                feats = [feat for feat, _ in self.selected_feats.values()]
                self.clear_selection()
                self.parent_dialog.start_edit_for_polygons(feats)
            else:
                self.canvas.unsetMapTool(self)
                self.parent_dialog.iface.messageBar().pushMessage(
                    "แจ้ง", "ยกเลิกเครื่องมือคลิกเลือกแปลงแล้ว", level=0)
            return

        poly_layer = self.parent_dialog.poly_combo.currentLayer()
        if not poly_layer:
            QMessageBox.warning(self.parent_dialog, "แจ้งเตือน", "กรุณาเลือก Layer POLYGON ก่อน")
            return

        map_point = self.toMapCoordinates(e.pos())
        tol = self.canvas.mapUnitsPerPixel() * 5
        rect = QgsRectangle(map_point.x() - tol, map_point.y() - tol,
                            map_point.x() + tol, map_point.y() + tol)

        # แปลงพิกัดจาก CRS ของแผนที่ ไปเป็น CRS ของ Layer (กันกรณี CRS ไม่ตรงกัน)
        try:
            transform = QgsCoordinateTransform(
                self.canvas.mapSettings().destinationCrs(), poly_layer.crs(), QgsProject.instance())
            layer_rect = transform.transformBoundingBox(rect)
        except Exception:
            layer_rect = rect

        click_geom = QgsGeometry.fromRect(layer_rect)
        request = QgsFeatureRequest().setFilterRect(layer_rect)
        candidates = [f for f in poly_layer.getFeatures(request)
                      if f.geometry() and f.geometry().intersects(click_geom)]

        if not candidates:
            self.parent_dialog.iface.messageBar().pushMessage(
                "แจ้งเตือน", "ไม่พบแปลงบริเวณที่คลิก (ถ้ามีการ Filter อยู่ จะหาเจอเฉพาะแปลงที่ผ่าน Filter)", level=1)
            return

        multi = bool(e.modifiers() & Qt.ShiftModifier)

        if len(candidates) == 1:
            self.clear_hover()
            if multi:
                self.toggle_selection(candidates[0], poly_layer)
            else:
                self.clear_selection()
                self.parent_dialog.start_edit_for_polygons([candidates[0]])
            return

        # กรณีแปลงซ้อนกัน: แสดงเมนูรายการให้เลือก พร้อมไฮไลท์ขอบเขตแปลงตอนเลื่อนเมาส์บนเมนู
        menu = QMenu()
        menu.setStyleSheet("QMenu { font-size: 11pt; } QMenu::item:selected { background-color: #ff8c00; color: white; }")
        title = QAction(f"พบ {len(candidates)} แปลงซ้อนกัน - เลือกแปลงที่ต้องการ:", menu)
        title.setEnabled(False)
        menu.addAction(title)
        menu.addSeparator()

        action_map = {}
        for f in candidates:
            action = QAction(self.parent_dialog.describe_polygon(poly_layer, f), menu)
            menu.addAction(action)
            action_map[action] = f

        def on_hover(act):
            feat = action_map.get(act)
            if feat is not None:
                self.show_hover(feat.geometry(), poly_layer)
        menu.hovered.connect(on_hover)

        chosen = menu.exec_(QCursor.pos())
        self.clear_hover()
        if chosen in action_map:
            if multi:
                self.toggle_selection(action_map[chosen], poly_layer)
            else:
                self.clear_selection()
                self.parent_dialog.start_edit_for_polygons([action_map[chosen]])

    def deactivate(self):
        self.clear_hover()
        self.clear_selection()
        super(ParcelClickTool, self).deactivate()


# --- 4. เครื่องมือเมาส์สำหรับคลิกเลือกหมุดบนแผนที่ (สำหรับ BND_NAME) ---
class PointSelectTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, layer, parent_dialog):
        super(PointSelectTool, self).__init__(canvas)
        self.canvas = canvas
        self.layer = layer
        self.parent_dialog = parent_dialog
        self.setCursor(Qt.CrossCursor)
        self.snap_marker = None

    def clear_snap_marker(self):
        if self.snap_marker:
            self.canvas.scene().removeItem(self.snap_marker)
            self.snap_marker = None

    def _rect_around_cursor(self, e):
        """คืนค่า (สี่เหลี่ยมค้นหาใน CRS ของ Layer, จุด cursor ใน CRS ของ Layer)"""
        map_point = self.toMapCoordinates(e.pos())
        tol = self.canvas.mapUnitsPerPixel() * 10
        rect = QgsRectangle(map_point.x() - tol, map_point.y() - tol,
                            map_point.x() + tol, map_point.y() + tol)
        try:
            transform = QgsCoordinateTransform(
                self.canvas.mapSettings().destinationCrs(), self.layer.crs(), QgsProject.instance())
            return transform.transformBoundingBox(rect), transform.transform(map_point)
        except Exception:
            return rect, map_point

    def canvasMoveEvent(self, e):
        """แสดงสัญลักษณ์ Snapping เมื่อเมาส์เข้าใกล้หมุด"""
        layer_rect, cursor_pt = self._rect_around_cursor(e)
        request = QgsFeatureRequest().setFilterRect(layer_rect).setSubsetOfAttributes([])

        nearest_pt = None
        nearest_d = None
        for f in self.layer.getFeatures(request):
            g = f.geometry()
            if not g:
                continue
            try:
                p = g.asPoint()
            except Exception:
                p = g.centroid().asPoint()
            d = (p.x() - cursor_pt.x()) ** 2 + (p.y() - cursor_pt.y()) ** 2
            if nearest_d is None or d < nearest_d:
                nearest_d = d
                nearest_pt = p

        if nearest_pt is None:
            self.clear_snap_marker()
            return

        # แปลงตำแหน่งหมุดกลับเป็น CRS ของแผนที่ เพื่อวาด marker
        try:
            to_map = QgsCoordinateTransform(
                self.layer.crs(), self.canvas.mapSettings().destinationCrs(), QgsProject.instance())
            marker_pt = to_map.transform(nearest_pt)
        except Exception:
            marker_pt = nearest_pt

        if not self.snap_marker:
            m = QgsVertexMarker(self.canvas)
            m.setColor(QColor(255, 0, 255))
            m.setIconType(QgsVertexMarker.ICON_BOX)
            m.setIconSize(14)
            m.setPenWidth(3)
            self.snap_marker = m
        self.snap_marker.setCenter(QgsPointXY(marker_pt))

    def canvasReleaseEvent(self, e):
        layer_rect, _ = self._rect_around_cursor(e)
        request = QgsFeatureRequest().setFilterRect(layer_rect)
        features = [f for f in self.layer.getFeatures(request)]

        self.clear_snap_marker()
        if features:
            dlg = EditBndNameDialog(self.layer, features, self.parent_dialog)
            dlg.exec_()
        else:
            self.parent_dialog.iface.messageBar().pushMessage("แจ้งเตือน", "ไม่พบหมุดบริเวณที่คลิก", level=1)
        self.canvas.unsetMapTool(self)

    def deactivate(self):
        self.clear_snap_marker()
        super(PointSelectTool, self).deactivate()


# --- 4.5 หน้าต่างแสดงผลการค้นหา (คลิกรายการเพื่อซูมไปที่รายการนั้น) ---
class FilterResultsDialog(QDialog):
    MAX_ITEMS = 300

    def __init__(self, iface, results, full_extent, parent=None):
        super(FilterResultsDialog, self).__init__(parent)
        self.iface = iface
        self.full_extent = QgsRectangle(full_extent)
        self.setWindowTitle("ผลการค้นหา")
        self.setMinimumSize(400, 320)

        self.setStyleSheet("""
            QLabel { font-size: 10pt; font-weight: bold; }
            QListWidget { font-size: 10pt; background-color: white; }
            QPushButton { font-size: 10pt; font-weight: bold; padding: 6px; border-radius: 4px; }
        """)

        layout = QVBoxLayout()
        layout.addWidget(QLabel(
            f"พบแปลงทั้งหมด {len(results)} แปลง\n"
            "คลิกรายการเพื่อซูมไปที่แปลงนั้น"))

        self.list_widget = QListWidget()
        for typ, layer, fid, desc in results[:self.MAX_ITEMS]:
            item = QListWidgetItem("[แปลง] " + desc)
            item.setData(Qt.UserRole, (layer, fid))
            self.list_widget.addItem(item)
        if len(results) > self.MAX_ITEMS:
            note = QListWidgetItem(
                f"... และอีก {len(results) - self.MAX_ITEMS} รายการ (แสดงเฉพาะ {self.MAX_ITEMS} รายการแรก)")
            note.setFlags(Qt.NoItemFlags)
            self.list_widget.addItem(note)
        self.list_widget.itemClicked.connect(self.zoom_to_item)
        layout.addWidget(self.list_widget)

        row = QHBoxLayout()
        btn_zoom_all = QPushButton("ซูมดูทั้งหมด")
        btn_zoom_all.setStyleSheet("background-color: #007bff; color: white;")
        btn_zoom_all.clicked.connect(self.zoom_all)
        row.addWidget(btn_zoom_all)

        btn_close = QPushButton("ปิด")
        btn_close.setStyleSheet("background-color: #6c757d; color: white;")
        btn_close.clicked.connect(self.close)
        row.addWidget(btn_close)
        layout.addLayout(row)

        self.setLayout(layout)

        # ขยายความกว้างหน้าต่างอัตโนมัติให้พอดีกับข้อความที่ยาวที่สุดในรายการ
        content_w = self.list_widget.sizeHintForColumn(0) + 60
        self.resize(max(420, min(content_w, 1000)), 380)

    def zoom_to_item(self, item):
        data = item.data(Qt.UserRole)
        if not data:
            return
        layer, fid = data
        try:
            f = layer.getFeature(fid)
            if not f.isValid() or not f.geometry():
                return
            canvas = self.iface.mapCanvas()
            bbox = f.geometry().boundingBox()
            try:
                transform = QgsCoordinateTransform(
                    layer.crs(), canvas.mapSettings().destinationCrs(), QgsProject.instance())
                bbox = transform.transformBoundingBox(bbox)
            except Exception:
                pass
            if bbox.width() == 0 and bbox.height() == 0:
                bbox.grow(25)   # จุดเดี่ยว: ซูมเข้าไปรอบๆ รัศมี 25 หน่วยแผนที่
            else:
                bbox.scale(1.5)
            canvas.setExtent(bbox)
            canvas.refresh()
            # กระพริบ Feature บนแผนที่ให้เห็นชัดๆ ว่าอยู่ตรงไหน
            canvas.flashFeatureIds(layer, [fid])
        except Exception:
            pass

    def zoom_all(self):
        canvas = self.iface.mapCanvas()
        canvas.setExtent(QgsRectangle(self.full_extent))
        canvas.refresh()


# --- 5. หน้าต่างเครื่องมือหลัก ---
class PathFilterTool(QDialog):
    def __init__(self, iface, parent=None):
        super(PathFilterTool, self).__init__(parent)
        self.iface = iface
        self.edit_tool = None
        self.parcel_tool = None
        self.highlight_rbs = []
        self.results_dlg = None
        self.setWindowTitle("PATH Filter & Edit Attribute UTM Version 3.5")
        self.setMinimumWidth(420)

        self.setStyleSheet("""
            QDialog { background-color: #f8f9fa; }
            QLabel { font-size: 10pt; font-weight: bold; color: #333; }
            QLineEdit { font-size: 11pt; padding: 4px; border: 1px solid #ccc; border-radius: 4px; }
            QComboBox { font-size: 10pt; padding: 3px; min-height: 24px; }
            QPushButton { font-size: 10pt; font-weight: bold; padding: 6px; border-radius: 4px; }
        """)

        layout = QVBoxLayout()
        layout.setSpacing(6)

        # --- ส่วนกรอกเงื่อนไขค้นหา (ใช้ FormLayout ให้กะทัดรัด) ---
        form = QFormLayout()
        form.setSpacing(5)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("ว่างได้")
        form.addRow(QLabel("PATH:"), self.search_input)

        self.filename_input = QLineEdit()
        self.filename_input.setPlaceholderText("ว่างได้")
        form.addRow(QLabel("FILE_NAME:"), self.filename_input)

        self.point_combo = QgsMapLayerComboBox()
        self.point_combo.setAllowEmptyLayer(True)
        self.point_combo.setFilters(QgsMapLayerProxyModel.PointLayer)
        form.addRow(QLabel("Layer หมุด (POINT):"), self.point_combo)

        self.poly_combo = QgsMapLayerComboBox()
        self.poly_combo.setAllowEmptyLayer(True)
        self.poly_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        form.addRow(QLabel("Layer แปลง (POLYGON):"), self.poly_combo)

        layout.addLayout(form)

        # --- ปุ่ม Filter / Clear ---
        row_filter = QHBoxLayout()
        self.btn_filter = QPushButton("เริ่ม Filter (ซูมอัตโนมัติ)")
        self.btn_filter.setStyleSheet("background-color: #007bff; color: white;")
        self.btn_filter.clicked.connect(self.apply_filter_and_zoom)
        row_filter.addWidget(self.btn_filter)

        self.btn_clear = QPushButton("แสดงทั้งหมด (Clear)")
        self.btn_clear.setStyleSheet("background-color: #6c757d; color: white;")
        self.btn_clear.clicked.connect(self.clear_filter)
        row_filter.addWidget(self.btn_clear)
        layout.addLayout(row_filter)

        # --- ปุ่มแก้ไข Attribute แบบคลิกแปลงบนแผนที่ ---
        self.btn_edit_attr = QPushButton("แก้ไข Attribute (คลิกเลือกแปลงบนแผนที่)")
        self.btn_edit_attr.setStyleSheet("background-color: #28a745; color: white; padding: 8px;")
        self.btn_edit_attr.clicked.connect(self.activate_parcel_edit_tool)
        layout.addWidget(self.btn_edit_attr)

        hint = QLabel("* คลิกซ้าย = แก้ไข 1 แปลง | Shift+คลิกซ้าย = เลือกหลายแปลง | คลิกขวา = เปิดแก้ไข/ยกเลิก")
        hint.setStyleSheet("font-size: 9pt; font-weight: normal; color: #666;")
        layout.addWidget(hint)

        # --- ปุ่มอื่นๆ ---
        row_misc = QHBoxLayout()
        self.btn_edit_point = QPushButton("แก้ไขชื่อหมุดหลักเขต")
        self.btn_edit_point.setStyleSheet("background-color: #ffc107; color: black;")
        self.btn_edit_point.clicked.connect(self.activate_edit_point_tool)
        row_misc.addWidget(self.btn_edit_point)

        self.btn_update = QPushButton("อัปเดตปลั๊กอิน")
        self.btn_update.setStyleSheet("background-color: #dc3545; color: white;")
        self.btn_update.clicked.connect(self.update_plugin)
        row_misc.addWidget(self.btn_update)
        layout.addLayout(row_misc)

        self.setLayout(layout)

    # ---------- Filter ----------
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
        results = []

        for layer, typ in [(poly_layer, "Polygon"), (p_layer, "Point")]:
            if layer:
                filters = []
                if path_val:
                    filters.append(f"\"PATH\" = '{escape_sql(path_val)}'")
                if file_val:
                    filters.append(f"\"FILE_NAME\" = '{escape_sql(file_val)}'")

                subset_str = " AND ".join(filters)
                layer.setSubsetString(subset_str)
                layer.updateExtents()

                # แสดงในหน้าต่างผลการค้นหาเฉพาะแปลง (Polygon) เท่านั้น
                if typ == "Polygon":
                    for f in layer.getFeatures():
                        results.append((typ, layer, f.id(), self.describe_polygon(layer, f)))
                    has_features = len(results) > 0
                else:
                    has_features = next(layer.getFeatures(QgsFeatureRequest().setLimit(1)), None) is not None

                if has_features and not layer.extent().isEmpty():
                    combined_extent.combineExtentWith(layer.extent())
                    has_data = True

        # ปิดหน้าต่างผลการค้นหาอันเก่า (ถ้ามี)
        if self.results_dlg:
            self.results_dlg.close()
            self.results_dlg = None

        if has_data:
            canvas = self.iface.mapCanvas()
            combined_extent.scale(1.1)
            canvas.setExtent(combined_extent)
            canvas.refresh()

            # ถ้าพบมากกว่า 1 รายการ เปิดหน้าต่างรายการให้เลือกซูมไปทีละรายการได้
            if len(results) > 1:
                self.results_dlg = FilterResultsDialog(self.iface, results, combined_extent, self)
                self.results_dlg.show()
        else:
            self.iface.messageBar().pushMessage("แจ้งเตือน", "ไม่พบข้อมูลที่ตรงกับเงื่อนไขที่ค้นหา", level=1)

    def clear_filter(self):
        for combo in [self.point_combo, self.poly_combo]:
            l = combo.currentLayer()
            if l:
                l.setSubsetString("")
                l.updateExtents()
                l.removeSelection()
        self.clear_highlight()
        if self.results_dlg:
            self.results_dlg.close()
            self.results_dlg = None

    # ---------- แก้ไข Attribute แบบคลิกแปลง ----------
    def activate_parcel_edit_tool(self):
        poly_layer = self.poly_combo.currentLayer()
        if not poly_layer:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาเลือก Layer POLYGON ก่อน")
            return

        canvas = self.iface.mapCanvas()
        self.parcel_tool = ParcelClickTool(canvas, self)
        canvas.setMapTool(self.parcel_tool)
        self.iface.messageBar().pushMessage(
            "เครื่องมือ", "คลิกซ้าย = แก้ไข 1 แปลง | Shift+คลิกซ้าย = เลือกสะสมหลายแปลง แล้วคลิกขวาเพื่อเปิดหน้าต่างแก้ไข", level=0)

    def describe_polygon(self, poly_layer, feature):
        """สร้างข้อความอธิบายแปลงสำหรับแสดงในเมนูเลือกแปลงและรายการผลการค้นหา"""
        idx_landno = poly_layer.fields().indexOf("LANDNO")
        if idx_landno == -1: idx_landno = poly_layer.fields().indexOf("LAND_NO")
        idx_parcel = poly_layer.fields().indexOf("PARCELNO")
        idx_survey = poly_layer.fields().indexOf("SURVEYNO")

        landno = feature.attribute(idx_landno) if idx_landno != -1 else "-"
        parcel = feature.attribute(idx_parcel) if idx_parcel != -1 else "-"
        survey = feature.attribute(idx_survey) if idx_survey != -1 else "-"

        # เนื้อที่ ไร่-งาน-ตารางวา จากคอลัมน์ RAI, NGAN, WA
        area_parts = []
        for name in ("RAI", "NGAN", "WA"):
            idx = poly_layer.fields().indexOf(name)
            area_parts.append(format_area_value(feature.attribute(idx)) if idx != -1 else "-")
        area = "-".join(area_parts)

        return (f"LANDNO: {landno} | PARCELNO: {parcel} | SURVEYNO: {survey} | "
                f"AREA: {area} | ID: {feature.id()}")

    def clear_highlight(self):
        for rb in self.highlight_rbs:
            self.iface.mapCanvas().scene().removeItem(rb)
        self.highlight_rbs = []

    def find_points_on_vertices(self, p_layer, poly_layer, poly_features, tol=VERTEX_TOLERANCE):
        """หาหมุดที่ตำแหน่งตรงกับมุมเขต (Vertex) ของแปลงที่เลือก
        โดยยอมรับระยะคลาดเคลื่อนไม่เกิน tol หน่วยแผนที่ (ค้นเฉพาะหมุดที่ผ่าน Filter อยู่)"""
        # แปลงขอบเขตแปลงให้อยู่ใน CRS ของ Layer หมุด (กันกรณี CRS ไม่ตรงกัน)
        transform = None
        if poly_layer.crs() != p_layer.crs():
            try:
                transform = QgsCoordinateTransform(poly_layer.crs(), p_layer.crs(), QgsProject.instance())
            except Exception:
                transform = None

        tol2 = tol * tol
        point_ids = []
        seen = set()
        for pf in poly_features:
            geom = QgsGeometry(pf.geometry())
            if geom.isEmpty():
                continue
            if transform:
                try:
                    geom.transform(transform)
                except Exception:
                    continue

            bbox = geom.boundingBox()
            bbox.grow(tol)
            request = QgsFeatureRequest().setFilterRect(bbox).setSubsetOfAttributes([])
            for f in p_layer.getFeatures(request):
                if f.id() in seen:
                    continue
                g = f.geometry()
                if not g:
                    continue
                try:
                    p = g.asPoint()
                except Exception:
                    p = g.centroid().asPoint()
                # ระยะจากหมุดไปยัง Vertex ที่ใกล้ที่สุดของแปลง
                _, _, _, _, sqr_dist = geom.closestVertex(p)
                if 0 <= sqr_dist <= tol2:
                    seen.add(f.id())
                    point_ids.append(f.id())
        return point_ids

    def start_edit_for_polygons(self, poly_features):
        poly_layer = self.poly_combo.currentLayer()
        p_layer = self.point_combo.currentLayer()
        if not poly_layer or not poly_features:
            return

        multi_mode = len(poly_features) > 1

        # ไฮไลท์แปลงที่เลือกไว้ (กรอบแดง) ค้างไว้ระหว่างแก้ไข
        self.clear_highlight()
        for pf in poly_features:
            rb = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.PolygonGeometry)
            rb.setToGeometry(pf.geometry(), poly_layer)
            rb.setColor(QColor(255, 0, 0, 50))
            rb.setStrokeColor(QColor(220, 0, 0))
            rb.setWidth(3)
            self.highlight_rbs.append(rb)

        # หาหมุดที่ตำแหน่งตรงกับมุมเขต (Vertex) ของแปลงที่เลือกเท่านั้น
        point_ids = []
        if p_layer:
            point_ids = self.find_points_on_vertices(p_layer, poly_layer, poly_features)
            if not point_ids:
                self.iface.messageBar().pushMessage(
                    "แจ้งเตือน", "ไม่พบหมุดที่ตำแหน่งตรงกับมุมเขตของแปลงที่เลือก จะแก้ไขเฉพาะแปลงเท่านั้น", level=1)

        # เลือก Feature บนแผนที่ให้เห็นชัดๆ ว่าจะแก้ตัวไหนบ้าง
        poly_ids = [pf.id() for pf in poly_features]
        poly_layer.selectByIds(poly_ids)
        if p_layer:
            p_layer.selectByIds(point_ids)

        dlg = EditAttributesDialog(p_layer, poly_layer, point_ids, poly_ids,
                                   self, multi_mode=multi_mode)
        dlg.exec_()

        # เคลียร์ไฮไลท์และ selection หลังปิดหน้าต่างแก้ไข
        self.clear_highlight()
        poly_layer.removeSelection()
        if p_layer:
            p_layer.removeSelection()

    # ---------- แก้ไขชื่อหมุดหลักเขต ----------
    def activate_edit_point_tool(self):
        p_layer = self.point_combo.currentLayer()
        if not p_layer:
            QMessageBox.warning(self, "แจ้งเตือน", "กรุณาเลือก Layer POINT ก่อน")
            return

        canvas = self.iface.mapCanvas()
        self.edit_tool = PointSelectTool(canvas, p_layer, self)
        canvas.setMapTool(self.edit_tool)
        self.iface.messageBar().pushMessage("แจ้ง", "คลิกที่หมุดบนแผนที่เพื่อแก้ไข BND_NAME", level=0)

    # ---------- ปิดหน้าต่าง: เก็บกวาดเครื่องมือและไฮไลท์ ----------
    def closeEvent(self, event):
        self.clear_highlight()
        if self.results_dlg:
            self.results_dlg.close()
            self.results_dlg = None
        canvas = self.iface.mapCanvas()
        if self.parcel_tool and canvas.mapTool() == self.parcel_tool:
            canvas.unsetMapTool(self.parcel_tool)
        if self.edit_tool and canvas.mapTool() == self.edit_tool:
            canvas.unsetMapTool(self.edit_tool)
        super(PathFilterTool, self).closeEvent(event)

    # ---------- อัปเดตปลั๊กอิน ----------
    def get_local_version(self):
        """อ่านเลขเวอร์ชันจาก metadata.txt ในเครื่อง"""
        try:
            meta_path = os.path.join(os.path.dirname(__file__), "metadata.txt")
            with open(meta_path, "r", encoding="utf-8-sig") as fh:
                for line in fh:
                    if line.strip().lower().startswith("version="):
                        return line.split("=", 1)[1].strip()
        except Exception:
            pass
        return None

    def get_remote_version(self):
        """อ่านเลขเวอร์ชันล่าสุดจาก metadata.txt บน GitHub"""
        try:
            with urllib.request.urlopen(GITHUB_METADATA_URL, timeout=15) as resp:
                remote_text = resp.read().decode("utf-8-sig", errors="ignore")
            for line in remote_text.splitlines():
                if line.strip().lower().startswith("version="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
        return None

    def update_plugin(self):
        if "USERNAME" in GITHUB_UPDATE_URL:
            QMessageBox.warning(self, "ไม่พบลิงก์อัปเดต", "นักพัฒนาโปรดไปตั้งค่า GITHUB_UPDATE_URL ภายในไฟล์ main_logic.py ก่อนใช้งานฟังก์ชันนี้")
            return

        local_ver = self.get_local_version()
        remote_ver = self.get_remote_version()
        local_t = parse_version(local_ver)
        remote_t = parse_version(remote_ver)

        # เช็คเวอร์ชันจาก GitHub ไม่ได้ (เน็ตมีปัญหา ฯลฯ) -> ถามว่าจะดาวน์โหลดทับเลยหรือไม่
        if remote_t is None:
            reply = QMessageBox.question(self, "ตรวจสอบเวอร์ชันไม่ได้",
                "ไม่สามารถตรวจสอบเวอร์ชันล่าสุดจาก GitHub ได้\n(อินเทอร์เน็ตหรือลิงก์อาจมีปัญหา)\n\nต้องการดาวน์โหลดอัปเดตทับไปเลยหรือไม่?",
                QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.download_and_install_update()
            return

        # เป็นเวอร์ชันล่าสุดอยู่แล้ว
        if local_t is not None and remote_t <= local_t:
            QMessageBox.information(self, "เป็นเวอร์ชันล่าสุดแล้ว",
                f"คุณใช้เวอร์ชันล่าสุดอยู่แล้ว (เวอร์ชัน {local_ver})")
            return

        # พบเวอร์ชันใหม่กว่า -> มีปุ่มกดอัปเดตได้เลย
        msg = QMessageBox(self)
        msg.setWindowTitle("พบเวอร์ชันใหม่")
        msg.setIcon(QMessageBox.Information)
        msg.setText(f"พบเวอร์ชันใหม่: {remote_ver}\nเวอร์ชันปัจจุบันของคุณ: {local_ver if local_ver else 'ไม่ทราบ'}")
        btn_update = msg.addButton("อัปเดตเลย", QMessageBox.AcceptRole)
        msg.addButton("ไว้ภายหลัง", QMessageBox.RejectRole)
        msg.exec_()
        if msg.clickedButton() == btn_update:
            self.download_and_install_update()

    def download_and_install_update(self):
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


# --- 6. ส่วนการจัดการตัวปลั๊กอิน ---
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
