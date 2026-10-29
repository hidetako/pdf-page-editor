"""電子印鑑の GUI 部品: 印鑑ドック（一覧・押印設定）と印鑑編集ダイアログ。"""
from __future__ import annotations

import base64
import os
import datetime as _dt

import pymupdf as fitz
from PySide6.QtCore import QDate, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDateEdit, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QSlider, QVBoxLayout,
                               QWidget, QLineEdit, QGridLayout, QGroupBox)

from stamps import (DATE_FORMATS, KINDS, Stamp, StampLibrary, image_to_stamp_png, render_png)


def png_to_qpixmap(png: bytes) -> QPixmap:
    img = QImage.fromData(png, "PNG")
    return QPixmap.fromImage(img)


def preview_pixmap(stamp: Stamp, max_px: int = 96) -> QPixmap:
    try:
        png = render_png(stamp, dpi=150)
    except Exception:
        return QPixmap()
    pm = png_to_qpixmap(png)
    return pm.scaled(max_px, max_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class StampEditDialog(QDialog):
    def __init__(self, stamp: Stamp, parent=None):
        super().__init__(parent)
        self.setWindowTitle("印鑑の作成・編集")
        self.stamp = Stamp.from_dict(stamp.to_dict())
        self.resize(640, 480)
        form = QFormLayout()

        self.ed_name = QLineEdit(self.stamp.name)
        self.ed_name.setToolTip("一覧に表示する名前（印影には出ない）")
        form.addRow("表示名", self.ed_name)
        self.cb_kind = QComboBox()
        for k, label in KINDS.items():
            self.cb_kind.addItem(label, k)
        self.cb_kind.setCurrentIndex(list(KINDS).index(self.stamp.kind))
        self.cb_kind.setToolTip("印鑑の形式。丸・四角・長方形・画像から選ぶ")
        form.addRow("種類", self.cb_kind)
        self.ed_lines = QPlainTextEdit("\n".join(self.stamp.lines))
        self.ed_lines.setPlaceholderText("1 行 1 段。角印は右から左の列になります。\nデータネーム印は 1 行目=上段, 2 行目=下段（日付は自動）")
        self.ed_lines.setMaximumHeight(90)
        form.addRow("文字", self.ed_lines)
        self.sp_size = QDoubleSpinBox(); self.sp_size.setRange(3, 200); self.sp_size.setSuffix(" mm"); self.sp_size.setValue(self.stamp.size_mm)
        self.sp_size.setToolTip("丸印は直径、角印は一辺、住所印は幅(mm)")
        form.addRow("直径／一辺／幅", self.sp_size)
        self.sp_h = QDoubleSpinBox(); self.sp_h.setRange(0, 200); self.sp_h.setSuffix(" mm"); self.sp_h.setSpecialValueText("自動"); self.sp_h.setValue(self.stamp.height_mm)
        self.sp_h.setToolTip("住所印の高さ(mm)。0 で自動")
        form.addRow("高さ（住所印）", self.sp_h)
        self.sp_border = QDoubleSpinBox(); self.sp_border.setRange(0.05, 3); self.sp_border.setSingleStep(0.05); self.sp_border.setSuffix(" mm"); self.sp_border.setValue(self.stamp.border)
        self.sp_border.setToolTip("外枠の線の太さ(mm)。角印は太め(0.4〜0.6)が一般的")
        form.addRow("枠線の太さ", self.sp_border)
        self.sp_font = QDoubleSpinBox(); self.sp_font.setRange(0.3, 2.0); self.sp_font.setSingleStep(0.05); self.sp_font.setValue(self.stamp.font_scale)
        self.sp_font.setToolTip("自動で決まる文字サイズに掛ける倍率。1.0 が標準")
        form.addRow("文字の大きさ倍率", self.sp_font)
        self.cb_date = QComboBox()
        for k, ex in DATE_FORMATS.items():
            self.cb_date.addItem(ex, k)
        self.cb_date.setCurrentIndex(list(DATE_FORMATS).index(self.stamp.date_format))
        self.cb_date.setToolTip("データネーム印の中段に入る日付の書き方")
        form.addRow("日付書式（データネーム印）", self.cb_date)
        hl = QHBoxLayout()
        self.btn_color = QPushButton("色を選ぶ")
        self.btn_color.clicked.connect(self.pick_color)
        self.btn_color.setToolTip("印影の色。既定は朱色")
        self.lbl_color = QLabel()
        hl.addWidget(self.btn_color); hl.addWidget(self.lbl_color); hl.addStretch()
        form.addRow("印色", hl)
        self.btn_img = QPushButton("画像を読み込む（白地を透過）...")
        self.btn_img.clicked.connect(self.load_image)
        self.btn_img.setToolTip("スキャン画像や手書きサインを読み込み、白い背景を透明にして印影にする")
        form.addRow("画像印", self.btn_img)
        self.cb_recolor = QCheckBox("画像を印色で塗り直す")
        self.cb_recolor.setChecked(self.stamp.recolor)
        self.cb_recolor.setToolTip("ON にすると画像印も上の「印色」で描く。白地は透明、濃淡はそのまま残る。色を選ぶと自動で ON")
        form.addRow("", self.cb_recolor)

        self.preview = QLabel()
        self.preview.setFixedSize(220, 220)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background:white; border:1px solid #aaa;")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)

        top = QHBoxLayout()
        left = QWidget(); left.setLayout(form)
        top.addWidget(left, 1)
        right = QVBoxLayout(); right.addWidget(QLabel("プレビュー")); right.addWidget(self.preview); right.addStretch()
        top.addLayout(right)
        root = QVBoxLayout(self)
        root.addLayout(top); root.addWidget(buttons)

        for w in (self.ed_name, ):
            w.textChanged.connect(self.update_preview)
        self.ed_lines.textChanged.connect(self.update_preview)
        for w in (self.sp_size, self.sp_h, self.sp_border, self.sp_font):
            w.valueChanged.connect(self.update_preview)
        self.cb_kind.currentIndexChanged.connect(self.update_preview)
        self.cb_date.currentIndexChanged.connect(self.update_preview)
        self.cb_recolor.toggled.connect(self.update_preview)
        self._set_color_label()
        self.update_preview()

    def _set_color_label(self):
        c = self.stamp.color
        self.lbl_color.setText("■")
        self.lbl_color.setStyleSheet(f"color: rgb({int(c[0]*255)},{int(c[1]*255)},{int(c[2]*255)}); font-size:22px;")

    def pick_color(self):
        c = self.stamp.color
        col = QColorDialog.getColor(QColor.fromRgbF(*c), self, "印色")
        if col.isValid():
            self.stamp.color = [col.redF(), col.greenF(), col.blueF()]
            if self.cb_kind.currentData() == "image":
                self.cb_recolor.setChecked(True)
            self._set_color_label()
            self.update_preview()

    def load_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "印影画像を選択", "", "画像 (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)")
        if not path:
            return
        knock = QMessageBox.question(self, "白地の透過", "白い背景を透明にしますか？（スキャン画像向け）",
                                     QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
        try:
            png = image_to_stamp_png(path, knockout_white=knock)
        except Exception as e:
            QMessageBox.critical(self, "エラー", str(e)); return
        self.stamp.image_b64 = base64.b64encode(png).decode("ascii")
        self.cb_kind.setCurrentIndex(list(KINDS).index("image"))
        self.update_preview()

    def collect(self) -> Stamp:
        s = self.stamp
        s.name = self.ed_name.text().strip() or "印鑑"
        s.kind = self.cb_kind.currentData()
        s.lines = [l.strip() for l in self.ed_lines.toPlainText().splitlines()]
        s.size_mm = self.sp_size.value()
        s.height_mm = self.sp_h.value()
        s.border = self.sp_border.value()
        s.font_scale = self.sp_font.value()
        s.date_format = self.cb_date.currentData()
        s.recolor = self.cb_recolor.isChecked()
        return s

    def update_preview(self):
        s = self.collect()
        pm = preview_pixmap(s, 210)
        self.preview.setPixmap(pm if not pm.isNull() else QPixmap())
        if pm.isNull():
            self.preview.setText("（画像未設定）" if s.kind == "image" else "")


class StampDock(QWidget):
    """印鑑一覧 + 押印設定。押印モードの ON/OFF は stamp_mode_changed で通知。"""

    stamp_mode_changed = Signal(bool)

    def __init__(self, library: StampLibrary, parent=None):
        super().__init__(parent)
        self.lib = library
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        self.list = QListWidget()
        self.list.setIconSize(QSize(64, 64))
        self.list.itemDoubleClicked.connect(lambda _: self.edit_stamp())
        self.list.setToolTip("印鑑の一覧。クリックで押す印鑑を選択、ダブルクリックで編集")
        root.addWidget(self.list, 1)

        grid = QGridLayout()
        btns = [
            ("新規", self.new_stamp, "新しい印鑑を作る（認印・データネーム印・角印・ユーザー印・住所印）"),
            ("編集", self.edit_stamp, "選択中の印鑑の文字・大きさ・色などを変更（一覧のダブルクリックでも可）"),
            ("複製", self.dup_stamp, "選択中の印鑑をコピーして、少しだけ違う印鑑を作る"),
            ("削除", self.del_stamp, "選択中の印鑑を一覧から削除"),
            ("画像から", self.from_image, "スキャンした印影や手書きサインの画像を読み込んで印鑑にする（白地は自動で透過）"),
            ("PNG保存", self.export_png, "選択中の印影を透過 PNG 画像として保存（Excel や Word に貼る用）"),
            ("インポート", self.import_json, "印鑑データを読み込む。本アプリの JSON のほか、おまかせ電子印鑑の .entstmp も可（複数選択可）"),
            ("エクスポート", self.export_json, "印鑑データを JSON ファイルに書き出す（バックアップ・別 PC への移行用）"),
        ]
        for i, (t, fn, tip) in enumerate(btns):
            b = QPushButton(t); b.clicked.connect(fn); b.setToolTip(tip); grid.addWidget(b, i // 4, i % 4)
        root.addLayout(grid)

        box = QGroupBox("押印設定")
        form = QFormLayout(box)
        self.sp_size = QDoubleSpinBox(); self.sp_size.setRange(0, 200); self.sp_size.setSuffix(" mm")
        self.sp_size.setSpecialValueText("印鑑の設定値のまま"); self.sp_size.setValue(0); self.sp_size.setSingleStep(1)
        self.sp_size.setToolTip("押す大きさ(mm)。0 のままなら印鑑ごとの設定値を使う")
        form.addRow("押印サイズ", self.sp_size)
        self.date = QDateEdit(QDate.currentDate()); self.date.setCalendarPopup(True); self.date.setDisplayFormat("yyyy/MM/dd")
        self.date.setToolTip("データネーム印に入る日付。既定は今日")
        form.addRow("日付", self.date)
        self.sl_opacity = QSlider(Qt.Horizontal); self.sl_opacity.setRange(20, 100); self.sl_opacity.setValue(100)
        self.sl_opacity.setToolTip("印影の濃さ。左ほど薄く、下の文字が透けて見える")
        form.addRow("不透明度", self.sl_opacity)
        self.sl_kasure = QSlider(Qt.Horizontal); self.sl_kasure.setRange(0, 100); self.sl_kasure.setValue(0)
        self.sl_kasure.setToolTip("インクのかすれ具合。右ほど本物の押印らしくなる")
        form.addRow("かすれ", self.sl_kasure)
        self.sp_tilt = QDoubleSpinBox(); self.sp_tilt.setRange(0, 30); self.sp_tilt.setSuffix(" °"); self.sp_tilt.setValue(0)
        self.sp_tilt.setToolTip("押すたびにこの範囲でランダムに傾ける。0 なら常にまっすぐ")
        form.addRow("傾きランダム(±)", self.sp_tilt)
        root.addWidget(box)

        self.btn_mode = QPushButton("押印モード（ページをクリックで押印）")
        self.btn_mode.setCheckable(True)
        self.btn_mode.setToolTip("ON にしてページ上をクリックすると、選択中の印鑑を押します（F2 でも切替、Esc で終了）")
        self.btn_mode.toggled.connect(self.stamp_mode_changed)
        root.addWidget(self.btn_mode)
        self.refresh()

    # ---------------------------------------------------------- 一覧
    def refresh(self, select_id: str | None = None):
        self.list.clear()
        for s in self.lib.stamps:
            it = QListWidgetItem(QIcon(preview_pixmap(s, 64)), f"{s.name}\n{KINDS.get(s.kind, s.kind).split('（')[0]} {s.size_mm:g}mm")
            it.setData(Qt.UserRole, s.id)
            self.list.addItem(it)
            if select_id == s.id:
                self.list.setCurrentItem(it)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)

    def current_stamp(self) -> Stamp | None:
        it = self.list.currentItem()
        if not it:
            return None
        sid = it.data(Qt.UserRole)
        return next((s for s in self.lib.stamps if s.id == sid), None)

    def new_stamp(self):
        dlg = StampEditDialog(Stamp(), self)
        if dlg.exec() == QDialog.Accepted:
            s = dlg.collect(); self.lib.stamps.append(s); self.lib.save(); self.refresh(s.id)

    def edit_stamp(self):
        s = self.current_stamp()
        if not s:
            return
        dlg = StampEditDialog(s, self)
        if dlg.exec() == QDialog.Accepted:
            new = dlg.collect()
            self.lib.stamps[self.lib.stamps.index(s)] = new
            self.lib.save(); self.refresh(new.id)

    def dup_stamp(self):
        s = self.current_stamp()
        if not s:
            return
        d = s.to_dict(); d.pop("id"); d["name"] = s.name + " のコピー"
        new = Stamp.from_dict(d); self.lib.stamps.append(new); self.lib.save(); self.refresh(new.id)

    def del_stamp(self):
        s = self.current_stamp()
        if not s:
            return
        if QMessageBox.question(self, "削除", f"「{s.name}」を削除しますか？") == QMessageBox.Yes:
            self.lib.stamps.remove(s); self.lib.save(); self.refresh()

    def from_image(self):
        dlg = StampEditDialog(Stamp(name="画像印", kind="image", size_mm=15), self)
        dlg.load_image()
        if dlg.stamp.image_b64 and dlg.exec() == QDialog.Accepted:
            s = dlg.collect(); self.lib.stamps.append(s); self.lib.save(); self.refresh(s.id)

    def export_png(self):
        s = self.current_stamp()
        if not s:
            return
        path, _ = QFileDialog.getSaveFileName(self, "印影を PNG で保存", f"{s.name}.png", "PNG (*.png)")
        if not path:
            return
        try:
            png = self.render_current(for_export=True)
            with open(path, "wb") as f:
                f.write(png)
        except Exception as e:
            QMessageBox.critical(self, "エラー", str(e))

    def import_json(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "印鑑データをインポート", "",
            "印鑑データ (*.json *.entstmp);;本アプリの JSON (*.json);;おまかせ電子印鑑 (*.entstmp)")
        if not paths:
            return
        n, failed = 0, []
        for path in paths:
            try:
                n += self.lib.import_file(path)
            except Exception as e:
                failed.append(f"{os.path.basename(path)}: {e}")
        self.refresh()
        msg = f"{n} 件を取り込みました。"
        if any(p.lower().endswith(".entstmp") for p in paths):
            msg += "\n.entstmp には大きさが記録されていないため 15 mm で登録しました。「編集」で直せます。"
        if failed:
            msg += "\n\n読み込めなかったファイル:\n" + "\n".join(failed)
        QMessageBox.information(self, "インポート", msg)

    def export_json(self):
        path, _ = QFileDialog.getSaveFileName(self, "印鑑データをエクスポート", "stamps.json", "JSON (*.json)")
        if path:
            self.lib.export(path)

    # ---------------------------------------------------------- 押印
    def settings(self) -> dict:
        s = self.current_stamp()
        size = self.sp_size.value()
        return {
            "stamp": s,
            "size_mm": size if size >= 1 else (s.size_mm if s else 10.0),
            "date": _dt.date(self.date.date().year(), self.date.date().month(), self.date.date().day()),
            "opacity": self.sl_opacity.value() / 100,
            "kasure": self.sl_kasure.value() / 100,
            "tilt_max": self.sp_tilt.value(),
        }

    def render_current(self, tilt: float = 0.0, for_export: bool = False) -> bytes:
        st = self.settings()
        s = st["stamp"]
        if s is None:
            raise ValueError("印鑑が選択されていません。")
        if for_export:
            return render_png(s, dpi=600, date=st["date"])
        return render_png(s, dpi=600, date=st["date"], tilt_deg=tilt, opacity=st["opacity"], kasure=st["kasure"])

    def set_mode(self, on: bool):
        self.btn_mode.setChecked(on)
