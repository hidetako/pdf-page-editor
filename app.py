"""PDF Page Editor - PDF ビューア + ページ編集（並べ替え/回転/削除/挿入/抽出）+ 保存。

PySide6 + PyMuPDF。Windows では build_windows.bat で単一 exe にできる。
"""
from __future__ import annotations

import datetime
import os
import subprocess
import sys
import webbrowser

import numpy as np
import pymupdf as fitz
from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QPointF, QRectF, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QDragEnterEvent, QDropEvent, QIcon, QImage,
                           QKeySequence, QShortcut, QPainter, QPainterPath, QPen, QPixmap, QPolygonF)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QDialog, QDockWidget, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSplitter, QStatusBar, QToolTip,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from pdfmodel import IMAGE_EXTS, PasswordRequired, PdfModel
import updater
from write_ui import DRAW_TOOLS, WriteRibbon, tool_icon
from stamp_ui import StampDock, png_to_qpixmap
from stamps import MM, StampLibrary, config_dir, random_tilt
import fields
import fonts
import ribbon
from field_ui import FillDialog, RulesDialog

APP_NAME = "PDF Page Editor"
__version__ = "1.7.0"


def resource_path(rel: str) -> str:
    """PyInstaller 同梱時は展開先、通常はスクリプトと同じフォルダを基準にする。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)
THUMB_W = 140
PAGE_GAP = 12           # 連続表示でのページ間の余白 (px)
PRINT_MAX_DPI = 300     # 印刷時の描画解像度の上限（これ以上はプリンター側で拡大。メモリと速度のため）
ZOOM_STEPS = [0.5, 0.67, 0.8, 1.0, 1.25, 1.5, 2.0, 3.0]
PDF_FILTER = "PDF ファイル (*.pdf)"
# 表示モード: 名前 → (メニュー表示, ページ背景色, 説明)
DISPLAY_MODES = {
    "normal": ("ノーマル（白地）", "#ffffff", "紙の色のまま表示"),
    "warm": ("暖色（薄いクリーム色）", "#faf0dc", "白地を薄いクリーム色にして目の負担を減らす"),
    "invert": ("白黒反転", "#000000", "黒地に白文字。暗い場所での閲覧向け"),
}
WARM_TINT = np.array([250, 240, 220], dtype=np.float32) / 255.0   # 白 → #FAF0DC
NOTE_HIT_PAD = 4.0   # 付箋アイコンのクリック判定を広げる量 (pt)
CLICK_SLOP_PX = 3          # 押してから離すまでの動きがこれ以下（画面のピクセル）なら「クリック」。それより動かしたら「ドラッグ」
ANNOT_HIT_PAD = 2.0  # 注釈（マーカー・文字・図形など）のクリック判定を広げる量 (pt)
HANDLE_PX = 9        # 選択した注釈の右下に出す「大きさ変更」のつまみの一辺 (px)
TOOL_HINTS = {
    "select": "選択: 書き込んだものをクリックで選ぶ。ドラッグで移動、右下の四角で大きさ変更、Delete キーで削除",
    "pen": "ペン: ドラッグして線を引きます（Esc で選択に戻る）",
    "line": "直線: ドラッグして線を引きます（Esc で選択に戻る）",
    "arrow": "矢印: ドラッグして矢印を引きます。離した側が矢の先（Esc で選択に戻る）",
    "rect": "四角: ドラッグして囲みます（Esc で選択に戻る）",
    "ellipse": "丸: ドラッグして囲みます（Esc で選択に戻る）",
    "text": "文字: クリックした場所に文字を書き込みます。書いた文字はクリックで直せます（Esc で選択に戻る）",
}
INSERT_FILTER = "PDF / 画像 (*.pdf *.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;PDF (*.pdf);;画像 (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)"


def app_icon() -> QIcon:
    icon_file = resource_path(os.path.join("assets", "icon.ico"))
    return QIcon(icon_file) if os.path.exists(icon_file) else QIcon()


def apply_display_mode(pix: fitz.Pixmap, mode: str) -> QImage:
    """描画済み Pixmap に表示モード（暖色 / 白黒反転）を適用して QImage にする。"""
    if mode == "normal" or pix.n != 3:
        return pix_to_qimage(pix)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    if mode == "invert":
        out = 255 - arr
    else:  # warm: 白を薄いクリーム色へ（黒はそのまま）
        out = (arr.astype(np.float32) * WARM_TINT).astype(np.uint8)
    out = np.ascontiguousarray(out)
    return QImage(out.data, pix.width, pix.height, pix.width * 3, QImage.Format_RGB888).copy()


def pix_to_qimage(pix: fitz.Pixmap) -> QImage:
    fmt = QImage.Format_RGB888 if pix.n == 3 else QImage.Format_RGBA8888
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)
    return img.copy()  # samples バッファ寿命から切り離す


class ThumbList(QListWidget):
    """サムネイル一覧。ドラッグ&ドロップで並べ替え、完了時に order_changed を発火。"""

    order_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(THUMB_W, int(THUMB_W * 1.4)))
        self.setGridSize(QSize(THUMB_W + 24, int(THUMB_W * 1.4) + 36))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Snap)
        self.setFlow(QListWidget.LeftToRight)
        self.setWrapping(True)
        self.setSpacing(6)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setUniformItemSizes(True)
        self.setStyleSheet("QListWidget{background:#8f8f8f;} QListWidget::item{color:#fff;} "
                           "QListWidget::item:selected{background:#2d7fd3;}")

    def dropEvent(self, event: QDropEvent):
        if event.source() is self:
            super().dropEvent(event)
            self.order_changed.emit()
        else:
            event.ignore()

    def current_order(self) -> list[int]:
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())]

    def selected_indices(self) -> list[int]:
        return sorted(self.row(it) for it in self.selectedItems())


class PageView(QLabel):
    """1 ページ分の表示。全ページを縦に並べて連続スクロールする。
    押印モード中はマウス位置に印影のゴーストを描き、クリックで位置を通知する。"""

    clicked = Signal(int, QPointF)  # ページ番号, ページ内の相対座標 (0〜1)
    context_requested = Signal(int, QPointF, QPoint)  # 右クリック: ページ番号, 相対座標, 画面座標
    hovered = Signal(int, QPointF, QPoint)  # マウス移動: ページ番号, 相対座標, 画面座標（付箋のツールチップ用）
    drag_started = Signal(int, QPointF)      # 左ドラッグ開始（文字の選択・注釈の移動・描画）
    drag_moved = Signal(int, QPointF)
    drag_finished = Signal(int, QPointF)
    click_released = Signal(int, QPointF, bool)   # 動かさずに離した（= クリック）: ページ番号, 相対座標, ダブルクリックか

    def __init__(self, index: int):
        super().__init__()
        self.index = index
        self.rendered_key = None            # 描画済みのスケール（None なら未描画のプレースホルダ）
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:#fff;")
        self.ghost: QPixmap | None = None   # 表示スケールに合わせた印影
        self.ghost_pos: QPointF | None = None
        self.stamp_mode = False
        self.note_mode = False
        self.text_mode = False
        self.draw_mode = False              # ペン・直線・矢印・四角・丸（ドラッグして描く）
        self.selection_rects: list[tuple[float, float, float, float]] = []   # 選択強調（相対座標 0〜1）
        self.move_rect: tuple[float, float, float, float] | None = None       # ドラッグ移動中の注釈の枠（相対座標）
        self.annot_box: tuple[float, float, float, float] | None = None       # 選択中の注釈の枠（相対座標）
        self.annot_handle = False           # 右下に大きさ変更のつまみを出すか
        self.preview: dict | None = None    # 描いている途中の線・図形 {"kind", "pts"(相対座標), "color", "width"(px), "opacity", "fill"}
        self.field_rects: list[tuple[float, float, float, float]] = []       # 差し替え枠の場所（相対座標）。画面にだけ目印を出す
        self._dragging = False
        self._press_pos = QPointF()
        self._press_double = False

    def set_selection(self, rects: list[tuple[float, float, float, float]]):
        self.selection_rects = rects
        self.update()

    def set_background(self, color: str):
        self.setStyleSheet(f"background:{color};")

    @property
    def click_mode(self) -> bool:
        """押した瞬間にその場所へ置くモード（押印・付箋）か。
        文字の道具は含めない: 書いてある文字を押してそのままドラッグで動かせるよう、離したときにクリックかどうかを決める。"""
        return self.stamp_mode or self.note_mode

    def _update_cursor(self):
        self.setCursor(Qt.CrossCursor if (self.click_mode or self.draw_mode or self.text_mode) else Qt.ArrowCursor)

    # モードの設定は、再レイアウトのたびに同じ値で呼ばれる。実際に変わったときだけカーソルを付け直す
    # （毎回付け直すと、注釈の上で出している移動カーソルなどが矢印に戻ってしまう）
    def set_stamp_mode(self, on: bool, ghost: QPixmap | None = None):
        changed = self.stamp_mode != on
        self.stamp_mode = on
        self.ghost = ghost if on else None
        if not on:
            self.ghost_pos = None   # 押印モード継続中（再レイアウトでゴーストを差し替えるだけ）は位置を保つ
        if changed:
            self._update_cursor()
        self.update()

    def set_note_mode(self, on: bool):
        changed = self.note_mode != on
        self.note_mode = on
        if changed:
            self._update_cursor()

    def set_text_mode(self, on: bool):
        changed = self.text_mode != on
        self.text_mode = on
        if changed:
            self._update_cursor()

    def set_draw_mode(self, on: bool):
        changed = self.draw_mode != on
        self.draw_mode = on
        if changed:
            self._update_cursor()

    def set_move_rect(self, rect: tuple[float, float, float, float] | None):
        self.move_rect = rect
        self.update()

    def set_annot_box(self, rect: tuple[float, float, float, float] | None, handle: bool = False):
        self.annot_box = rect
        self.annot_handle = handle and rect is not None
        self.update()

    def set_field_rects(self, rects: list[tuple[float, float, float, float]]):
        if rects != self.field_rects:
            self.field_rects = rects
            self.update()

    def set_preview(self, preview: dict | None):
        self.preview = preview
        self.update()

    def pix_geometry(self) -> tuple[float, float, float, float] | None:
        """ページ画像の (左, 上, 幅, 高さ)。未描画でもウィジェットの大きさ＝ページの大きさとして返す。"""
        if self.width() <= 0 or self.height() <= 0:
            return None
        pm = self.pixmap()
        if pm is None or pm.isNull():
            return 0.0, 0.0, float(self.width()), float(self.height())
        pw = pm.width() / pm.devicePixelRatio()
        ph = pm.height() / pm.devicePixelRatio()
        return (self.width() - pw) / 2, (self.height() - ph) / 2, pw, ph

    def _frac_clamped(self, pos) -> QPointF:
        g = self.pix_geometry() or (0.0, 0.0, float(max(1, self.width())), float(max(1, self.height())))
        ox, oy, pw, ph = g
        return QPointF(min(1.0, max(0.0, (pos.x() - ox) / pw)), min(1.0, max(0.0, (pos.y() - oy) / ph)))

    def mouseMoveEvent(self, e):
        if self.stamp_mode:
            self.ghost_pos = e.position()
            self.update()
        if self._dragging and (e.buttons() & Qt.LeftButton):
            self.drag_moved.emit(self.index, self._frac_clamped(e.position()))
        else:
            f = self._frac(e.position())
            if f is not None:
                self.hovered.emit(self.index, f, e.globalPosition().toPoint())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging and e.button() == Qt.LeftButton:
            self._dragging = False
            pos = QPointF(e.position())
            self.drag_finished.emit(self.index, self._frac_clamped(pos))
            # ほとんど動かさずに離したら「クリック」。編集画面を開くなどの動作は、押した瞬間ではなくここで行う
            # （押した瞬間に開くと、書いたものを押してそのままドラッグで動かすことができない）
            if abs(pos.x() - self._press_pos.x()) <= CLICK_SLOP_PX and abs(pos.y() - self._press_pos.y()) <= CLICK_SLOP_PX:
                f = self._frac(pos)
                if f is not None:
                    self.click_released.emit(self.index, f, self._press_double)
            return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e):
        self.ghost_pos = None
        self.update()
        super().leaveEvent(e)

    def _frac(self, pos) -> QPointF | None:
        """ウィジェット座標 → ページ内の相対座標 (0〜1)。ページ外なら None。"""
        g = self.pix_geometry()
        if not g:
            return None
        ox, oy, pw, ph = g
        fx = (pos.x() - ox) / pw
        fy = (pos.y() - oy) / ph
        return QPointF(fx, fy) if 0 <= fx <= 1 and 0 <= fy <= 1 else None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            f = self._frac(e.position())
            if f is not None:
                if self.click_mode:
                    self.clicked.emit(self.index, f)
                else:
                    self._begin_drag(e, f, double=False)
                return
        super().mousePressEvent(e)

    def _begin_drag(self, e, f: QPointF, double: bool):
        self._dragging = True
        self._press_pos = QPointF(e.position())
        self._press_double = double
        self.drag_started.emit(self.index, f)

    def mouseDoubleClickEvent(self, e):
        # 素早い 2 回目の押下。ここで編集画面を開くと「選んで、すぐドラッグで動かす」ができないので、
        # これもドラッグの始まりとして扱い、動かさずに離したときだけダブルクリックとして知らせる
        if e.button() == Qt.LeftButton and not self.click_mode:
            f = self._frac(e.position())
            if f is not None:
                self._begin_drag(e, f, double=not self.draw_mode and not self.text_mode)
                return
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        f = self._frac(e.pos())
        if f is not None:
            self.context_requested.emit(self.index, f, e.globalPos())
            e.accept()
            return
        super().contextMenuEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.field_rects:
            # 差し替え枠の目印（緑の破線と、角の小さな印）。画面にだけ出し、印刷や保存には出ない
            g = self.pix_geometry()
            if g:
                ox, oy, pw, ph = g
                p = QPainter(self)
                p.setRenderHint(QPainter.Antialiasing, True)
                for x0, y0, x1, y1 in self.field_rects:
                    r = QRectF(ox + x0 * pw, oy + y0 * ph, (x1 - x0) * pw, (y1 - y0) * ph).adjusted(-2, -2, 2, 2)
                    p.setPen(QPen(QColor(0, 140, 110), 1.2, Qt.DashLine)); p.setBrush(Qt.NoBrush)
                    p.drawRect(r)
                    p.setPen(Qt.NoPen); p.setBrush(QColor(0, 140, 110))
                    p.drawPolygon(QPolygonF([r.topLeft(), r.topLeft() + QPointF(8, 0), r.topLeft() + QPointF(0, 8)]))
                p.end()
        if self.selection_rects:
            g = self.pix_geometry()
            if g:
                ox, oy, pw, ph = g
                p = QPainter(self)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(30, 120, 255, 90))
                for x0, y0, x1, y1 in self.selection_rects:
                    p.drawRect(int(ox + x0 * pw), int(oy + y0 * ph), max(1, int((x1 - x0) * pw)), max(1, int((y1 - y0) * ph)))
                p.end()
        if self.move_rect:
            g = self.pix_geometry()
            if g:
                ox, oy, pw, ph = g
                x0, y0, x1, y1 = self.move_rect
                p = QPainter(self)
                p.setPen(QPen(QColor(30, 120, 255), 2, Qt.DashLine))
                p.setBrush(QColor(30, 120, 255, 40))
                p.drawRect(int(ox + x0 * pw), int(oy + y0 * ph), max(1, int((x1 - x0) * pw)), max(1, int((y1 - y0) * ph)))
                p.end()
        if self.preview and len(self.preview.get("pts") or []) >= 2:
            g = self.pix_geometry()
            if g:
                ox, oy, pw, ph = g
                pv = self.preview
                pts = [QPointF(ox + x * pw, oy + y * ph) for x, y in pv["pts"]]
                p = QPainter(self)
                p.setRenderHint(QPainter.Antialiasing)
                p.setOpacity(float(pv.get("opacity", 1.0)))
                col = pv["color"]
                p.setPen(QPen(col, max(1.0, float(pv.get("width", 2.0))), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.setBrush(col if pv.get("fill") else Qt.NoBrush)
                kind = pv["kind"]
                if kind == "pen":
                    path = QPainterPath(pts[0])
                    for q in pts[1:]:
                        path.lineTo(q)
                    p.setBrush(Qt.NoBrush)
                    p.drawPath(path)
                elif kind in ("line", "arrow"):
                    p.drawLine(pts[0], pts[-1])
                    if kind == "arrow":
                        import math
                        a, b = pts[0], pts[-1]
                        ang = math.atan2(b.y() - a.y(), b.x() - a.x())
                        size = max(8.0, float(pv.get("width", 2.0)) * 4)
                        head = [b] + [QPointF(b.x() - size * math.cos(ang + d), b.y() - size * math.sin(ang + d)) for d in (0.45, -0.45)]
                        p.setBrush(col); p.drawPolygon(QPolygonF(head))
                else:
                    r = QRectF(pts[0], pts[-1]).normalized()
                    if kind == "ellipse":
                        p.drawEllipse(r)
                    else:
                        p.drawRect(r)
                p.end()
        if self.annot_box:
            g = self.pix_geometry()
            if g:
                ox, oy, pw, ph = g
                x0, y0, x1, y1 = self.annot_box
                r = QRectF(ox + x0 * pw, oy + y0 * ph, max(1.0, (x1 - x0) * pw), max(1.0, (y1 - y0) * ph))
                p = QPainter(self)
                p.setPen(QPen(QColor(30, 120, 255), 1, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRect(r)
                if self.annot_handle:
                    h = HANDLE_PX
                    p.setPen(QPen(QColor(30, 120, 255), 1.5))
                    p.setBrush(QColor(255, 255, 255))
                    p.drawRect(QRectF(r.right() - h / 2, r.bottom() - h / 2, h, h))
                p.end()
        if self.stamp_mode and self.ghost and self.ghost_pos is not None:
            p = QPainter(self)
            p.setOpacity(0.6)
            gw = self.ghost.width() / self.ghost.devicePixelRatio()
            gh = self.ghost.height() / self.ghost.devicePixelRatio()
            p.drawPixmap(int(self.ghost_pos.x() - gw / 2), int(self.ghost_pos.y() - gh / 2), self.ghost)
            p.end()


class _UpdateCheckThread(QThread):
    done = Signal(object, str)   # UpdateInfo | None, エラー文

    def __init__(self, current: str, fetch, parent=None):
        super().__init__(parent)
        self._current, self._fetch = current, fetch

    def run(self):
        try:
            self.done.emit(updater.check_for_update(self._current, self._fetch), "")
        except Exception as e:
            self.done.emit(None, updater.describe_error(e))


class _DownloadThread(QThread):
    progress = Signal(int, int)
    done = Signal()

    def __init__(self, info, parent=None, opener=None):
        super().__init__(parent)
        self._info, self._opener = info, opener
        self._cancel = False
        self.path, self.error = "", ""

    def cancel(self):
        self._cancel = True

    def run(self):
        def prog(r, t):
            self.progress.emit(r, t)
            return not self._cancel
        try:
            self.path = updater.download_installer(self._info, prog, self._opener)
        except Exception as e:
            self.error = updater.describe_error(e)
        self.done.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.model = PdfModel()
        self.zoom = 1.0
        self.fit_width = True
        self.current = -1
        self.stamp_mode = False
        self.note_mode = False
        fonts.set_cache_file(os.path.join(config_dir(), "fonts.json"))    # フォントの確認結果を覚えておく（2 回目以降のリボン表示を速くする）
        self.sel_annot: tuple[int, int] | None = None     # 選択中の注釈 (ページ, xref)
        self._drag: dict | None = None                    # ドラッグ中の操作（描画・移動・大きさ変更）
        self._drag_changed = False                        # 直前のドラッグで何か（移動・大きさ変更・描画・文字の選択）をしたか
        self.rules = fields.RuleBook(os.path.join(config_dir(), "rules.json"))    # 差し替え枠のルール（日付・送付元・住所・独自ルール…）
        self.field_mode = False                           # 差し替え枠を置いている最中か（ページ上をドラッグ / クリックで置く）
        self.display_mode = "normal"
        self.auto_toc_panel = True
        self._words_cache: dict[int, list[dict]] = {}
        self.sel_page: int | None = None          # テキスト選択中のページ
        self.sel_anchor: tuple[float, float] | None = None
        self.sel_range: tuple[int, int] | None = None   # 選択した単語の範囲 (start, end) 読み順
        self.selected_text = ""
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1280, 820)
        self.setAcceptDrops(True)
        self._build_ui()
        self._update_actions()
        self._restore_layout()
        self._update_thread = None
        if self.auto_update_enabled():
            QTimer.singleShot(1500, lambda: self.check_for_update(manual=False))   # 起動直後は画面を優先
        # 起動時は印鑑パネルを閉じておく（押印モードに入ると自動で開く。メニュー「印鑑 > 印鑑パネルを表示」でも開ける）
        self.stamp_dock.hide()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        self.thumbs = ThumbList()
        self.thumbs.currentRowChanged.connect(self._on_current_changed)
        self.thumbs.order_changed.connect(self._on_reordered)
        self.thumbs.itemSelectionChanged.connect(self._update_actions)
        self.thumbs.setToolTip("ページ一覧。クリックで表示、Ctrl/Shift+クリックで複数選択、ドラッグで並べ替え")

        # 全ページを縦に並べたウィジェットをスクロールする（表示範囲のページだけ描画）
        self.views: list[PageView] = []
        self.pages_widget = QWidget()
        self.pages_widget.setStyleSheet("background:#555;")
        self.pages_layout = QVBoxLayout(self.pages_widget)
        self.pages_layout.setContentsMargins(PAGE_GAP, PAGE_GAP, PAGE_GAP, PAGE_GAP)
        self.pages_layout.setSpacing(PAGE_GAP)
        self.pages_layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.empty_label = QLabel("PDF をここにドラッグ&ドロップ、または「開く」")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color:#ddd; font-size:16px; background:transparent;")
        self.pages_layout.addWidget(self.empty_label)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.pages_widget)
        self.scroll.setWidgetResizable(False)   # 大きさは _resize_pages_widget で同期的に決める（スクロール範囲を即時更新するため）
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.viewport().installEventFilter(self)   # Ctrl+ホイールでズーム
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._on_scrolled)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _: self._scroll_timer.start(30))
        self._pin_current: int | None = None

        split = QSplitter()
        split.addWidget(self.thumbs)
        split.addWidget(self.scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 900])
        self.setCentralWidget(split)

        self.stamp_lib = StampLibrary()
        self.stamp_dock_widget = StampDock(self.stamp_lib)
        self.stamp_dock_widget.stamp_mode_changed.connect(self._set_stamp_mode)
        self.stamp_dock_widget.list.currentRowChanged.connect(lambda _: self._refresh_ghost())
        for w in (self.stamp_dock_widget.sp_size, self.stamp_dock_widget.sl_opacity, self.stamp_dock_widget.sl_kasure):
            w.valueChanged.connect(lambda _: self._refresh_ghost())
        self.stamp_dock_widget.date.dateChanged.connect(lambda _: self._refresh_ghost())
        dock = QDockWidget("電子印鑑", self)
        dock.setObjectName("stampDock")
        dock.setWidget(self.stamp_dock_widget)
        dock.setMinimumWidth(300)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.stamp_dock = dock

        # 目次・しおりパネル（左）
        toc_widget = QWidget()
        tl = QVBoxLayout(toc_widget)
        tl.setContentsMargins(4, 4, 4, 4)
        self.toc_tree = QTreeWidget()
        self.toc_tree.setHeaderLabels(["項目", "ページ"])
        self.toc_tree.setColumnWidth(0, 200)
        self.toc_tree.setToolTip("PDF の目次（索引）と自分で付けたしおり。クリックでそのページへ移動")
        self.toc_tree.itemClicked.connect(self._on_toc_clicked)
        self.toc_tree.currentItemChanged.connect(lambda *_: self._update_actions())   # キーボード選択でもボタンを有効化
        self.toc_tree.itemDoubleClicked.connect(lambda *_: self.rename_bookmark())
        tl.addWidget(self.toc_tree, 1)
        bl = QHBoxLayout()
        self.btn_bm_add = QPushButton("現在ページをしおりに追加")
        self.btn_bm_add.setToolTip("表示中のページへのしおりを目次に追加する（PDF に保存される）  [Ctrl+D]")
        self.btn_bm_add.clicked.connect(self.add_bookmark)
        self.btn_bm_rename = QPushButton("名前変更")
        self.btn_bm_rename.setToolTip("選択した項目の名前を変える（ダブルクリックでも可）")
        self.btn_bm_rename.clicked.connect(self.rename_bookmark)
        self.btn_bm_del = QPushButton("削除")
        self.btn_bm_del.setToolTip("選択した目次項目・しおりを削除する")
        self.btn_bm_del.clicked.connect(self.remove_bookmark)
        bl.addWidget(self.btn_bm_add); bl.addWidget(self.btn_bm_rename); bl.addWidget(self.btn_bm_del)
        tl.addLayout(bl)
        self.toc_dock = QDockWidget("目次・しおり", self)
        self.toc_dock.setObjectName("tocDock")
        self.toc_dock.setWidget(toc_widget)
        self.toc_dock.setMinimumWidth(240)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.toc_dock)
        self.toc_dock.hide()

        self.setStatusBar(QStatusBar())
        self.page_label = QLabel("")
        self.statusBar().addPermanentWidget(self.page_label)

        # ---- アクション。ショートカットをどのタブでも効かせるため、すべてウィンドウに addAction する
        def act(text, slot, key=None, icon=None, tip=None, checkable=False):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
            if tip:
                t = f"{tip}  [{key}]" if key else tip
                a.setToolTip(t)
                a.setStatusTip(t)
            if icon is not None:
                a.setIcon(icon if isinstance(icon, QIcon) else ribbon.icon(icon))
            a.setCheckable(checkable)
            a.triggered.connect(slot)
            self.addAction(a)
            return a

        # ファイル
        self.a_open = act("開く", self.open_dialog, "Ctrl+O", "folder_open", "PDF ファイルを開く。ウィンドウへのドラッグ&ドロップでも開けます")
        self.a_save = act("保存", self.save, "Ctrl+S", "save", "編集内容を元のファイルに上書き保存")
        self.a_saveas = act("名前を付けて保存", self.save_as, "Ctrl+Shift+S", "save_edit", "別名の PDF として保存（元ファイルは変更しない）")
        self.a_password = act("パスワードを設定・解除...", self.set_password_dialog, None, "lock_closed",
                              "保存時に PDF をパスワードで保護する。空にすると解除。パスワード付きで開いた PDF は同じパスワードで保存される")
        self.a_print = act("印刷...", self.print_dialog, "Ctrl+P", "print", "プリンターで印刷。すべて / 現在のページ / 選択ページ / 範囲指定、部数、用紙に合わせて拡大縮小")
        self.a_extract = act("選択ページを抽出...", self.extract_selected, None, "document_arrow_right", "選択中のページだけを新しい PDF として書き出す")
        self.a_quit = act("終了", self.close, "Ctrl+Q", "arrow_exit", "アプリを終了する")
        # 編集
        self.a_copy = act("コピー", self.copy_selection, "Ctrl+C", "copy", "ページ上でドラッグして選択した文字をクリップボードにコピー")
        self.a_copy.setEnabled(False)
        self.a_undo = act("元に戻す", self.undo, "Ctrl+Z", "arrow_undo", "直前の操作（並べ替え・回転・削除・挿入・押印）を取り消す")
        self.a_redo = act("やり直し", self.redo, "Ctrl+Y", "arrow_redo", "取り消した操作をもう一度実行する")
        self.a_select_all = act("全選択", self.thumbs.selectAll, "Ctrl+A", "select_all_on", "すべてのページを選択")
        # ページ
        self.a_insert = act("ページ挿入", self.insert_dialog, "Ctrl+I", "document_add", "他の PDF や画像(PNG/JPG など)を、選択ページの後ろにページとして挿入")
        self.a_delete = act("削除", self.delete_selected, "Delete", "delete", "選択中のページを削除（複数選択可）")
        self.a_rotl = act("左回転", lambda: self.rotate(-90), "Ctrl+L", "arrow_rotate_counterclockwise", "選択ページを反時計回りに 90° 回転")
        self.a_rotr = act("右回転", lambda: self.rotate(90), "Ctrl+R", "arrow_rotate_clockwise", "選択ページを時計回りに 90° 回転")
        self.a_up = act("前へ移動", lambda: self.move(-1), "Ctrl+Up", "arrow_up", "選択ページを 1 つ前へ移動。サムネイルのドラッグでも並べ替えできます")
        self.a_down = act("後へ移動", lambda: self.move(1), "Ctrl+Down", "arrow_down", "選択ページを 1 つ後ろへ移動")
        # 印鑑
        self.a_stamp = act("押印モード", self._toggle_stamp_mode, "F2", ribbon.stamp_icon(),
                           "ON にしてページ上をクリックすると、右パネルで選んだ印鑑を押します。Esc で終了。押した印影は右クリックで取り消せます", checkable=True)
        self.a_stamp_dock = act("印鑑パネルを表示", lambda on: self.stamp_dock.setVisible(on), None, "panel_right",
                                "右側の電子印鑑パネルの表示/非表示", checkable=True)
        self.a_stamp_dock.setChecked(True)
        self.stamp_dock.visibilityChanged.connect(self.a_stamp_dock.setChecked)
        act("キャンセル（押印・付箋・書き込みの終了）", self._cancel_modes, "Esc")
        # しおり・付箋
        self.a_note = act("付箋", self._toggle_note_mode, "F3", "note",
                          "ON にしてページ上をクリックすると、その位置にメモ（付箋）を貼ります。Esc で終了。付箋は右クリックで編集・削除", checkable=True)
        self.a_bookmark = act("現在ページをしおりに追加", self.add_bookmark, "Ctrl+D", "bookmark_add", "表示中のページへのしおりを目次に追加（PDF に保存される）")
        self.a_toc_dock = act("目次・しおりパネルを表示", lambda on: self.toc_dock.setVisible(on), "F9", "panel_left_text",
                              "左側の目次・しおりパネルの表示/非表示", checkable=True)
        self.toc_dock.visibilityChanged.connect(self.a_toc_dock.setChecked)
        self.a_auto_toc = act("目次のある PDF を開いたら自動でパネルを開く", self._set_auto_toc, None, None,
                              "索引・目次付きの PDF を開いたとき、目次パネルを自動で表示する", checkable=True)
        self.a_auto_toc.setChecked(True)
        # 書き込み（「書き込み」タブ）。ペン・図形・文字・マーカーを使う。書いたものはクリックで選び、移動・大きさ変更・削除ができる
        self.a_write = act("書き込み", self._toggle_write, "F4", tool_icon("pen"),
                           "「書き込み」タブ（ペン・直線・矢印・四角・丸・文字・蛍光ペン）を開く。もう一度押すか Esc で「ホーム」に戻る", checkable=True)
        self.a_highlight = act("蛍光ペン", lambda: self.apply_markup("highlight"), "Ctrl+Shift+H", tool_icon("highlight"),
                               "ドラッグで選択した文字に蛍光ペンを引く（PDF の注釈として保存。右クリックで色変更・削除）")
        self.a_underline = act("下線", lambda: self.apply_markup("underline"), "Ctrl+Shift+U", tool_icon("underline"), "選択した文字に下線を引く")
        self.a_strikeout = act("取り消し線", lambda: self.apply_markup("strikeout"), "Ctrl+Shift+K", tool_icon("strikeout"), "選択した文字に取り消し線を引く")
        self.a_del_annot = act("選択した書き込みを削除", self.delete_selected_annot, None, "delete",
                               "クリックで選んだ書き込み（線・図形・文字・マーカー）を削除する。Delete キーでも可")
        self.a_del_annot.setEnabled(False)
        for a in (self.a_highlight, self.a_underline, self.a_strikeout):
            a.setEnabled(False)
        # 差し替え（雛形の PDF に枠を置き、印刷のたびに中身を入れ替える）
        self.a_field = act("差し替え枠を置く", self._toggle_field_mode, "F6", "textbox",
                           "ON にしてページ上をドラッグ（またはクリック）すると、選んでいるルールの枠を置きます。日付の枠は自動で今日の日付になります。Esc で終了",
                           checkable=True)
        self.a_rules = act("ルールの管理...", self.edit_rules, None, "settings",
                           "枠に入れる内容のルール（日付・送付元・住所・宛先など）を直す。自分のルールも足せます")
        self.a_fill_print = act("差し替え印刷...", self.fill_print, None, "print_add",
                                "枠に入れる内容を確かめて（入力して）印刷する。開いている PDF は書き換えません。内容を入れた PDF を別名で保存することもできます")
        # 表示
        self.mode_group = QActionGroup(self)
        self.mode_actions: dict[str, QAction] = {}
        mode_icons = {"normal": "document", "warm": "lightbulb", "invert": "dark_theme"}
        for key, (label, _bg, tip) in DISPLAY_MODES.items():
            a = act(label, lambda _=False, k=key: self.set_display_mode(k), None, mode_icons.get(key), tip, checkable=True)
            self.mode_group.addAction(a)
            self.mode_actions[key] = a
        self.mode_actions["normal"].setChecked(True)
        self.a_zoom_out = act("縮小", lambda: self.zoom_step(-1), "Ctrl+-", "zoom_out", "表示を縮小。Ctrl+マウスホイールでも可")
        self.a_zoom_in = act("拡大", lambda: self.zoom_step(1), "Ctrl+=", "zoom_in", "表示を拡大。Ctrl+マウスホイールでも可")
        self.a_zoom_fit = act("幅に合わせる", self.zoom_fit, "Ctrl+0", "arrow_autofit_width", "ページ幅をウィンドウに合わせて表示")
        self.a_zoom_100 = act("100%", lambda: self.set_zoom(1.0), "Ctrl+1", "page_fit", "原寸(100%)で表示")
        self.a_prev_page = act("前のページ", lambda: self.goto(self.current - 1), "PgUp", "arrow_circle_up", "前のページを表示")
        self.a_next_page = act("次のページ", lambda: self.goto(self.current + 1), "PgDown", "arrow_circle_down", "次のページを表示")
        # ヘルプ
        self.a_check_update = act("更新を確認...", lambda: self.check_for_update(manual=True), None, "arrow_sync",
                                  "GitHub Releases に新しいバージョンがあるか調べ、あればダウンロードして更新します")
        self.a_auto_update = act("起動時に更新を確認", self._toggle_auto_update, None, None,
                                 "起動時に自動で新しいバージョンを調べる（インストール版のみ）", checkable=True)
        self.a_auto_update.setChecked(self._settings().value("update/check_on_start", True, type=bool))
        self.a_about = act("バージョン情報", self.show_about, None, "info", "このアプリのバージョンとライセンス")

        self._build_ribbon()

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(lambda: (self._relayout_keep_page(), self._refresh_ghost()))

    def _build_ribbon(self):
        """画面上部を Microsoft 365 風にする: タブの行（ファイル / ホーム / 書き込み / 表示 / ヘルプ）と、タブごとのリボン。"""
        rb = self.rib = ribbon.RibbonBar(self)
        self._last_tab = "home"

        # 「ファイル」はメニューを開く（365 の Backstage 画面の代わり）
        fm = rb.add_file_menu("ファイル")
        for a in (self.a_open, self.a_save, self.a_saveas, None, self.a_print, self.a_fill_print, self.a_extract, self.a_password, None, self.a_quit):
            fm.addSeparator() if a is None else fm.addAction(a)
        QShortcut(QKeySequence("Alt+F"), self, activated=rb.show_file_menu)

        home = rb.add_tab("home", "ホーム")
        g = home.add_group("ファイル")
        g.add_large(self.a_open); g.add_large(self.a_save); g.add_large(self.a_saveas, "名前を付けて\n保存"); g.add_large(self.a_print, "印刷")
        g = home.add_group("編集")
        g.add_large(self.a_undo); g.add_large(self.a_redo)
        g.add_column([self.a_copy, self.a_select_all])
        g = home.add_group("ページ")
        g.add_large(self.a_insert, "ページ\n挿入"); g.add_large(self.a_delete)
        g.add_column([self.a_rotl, self.a_rotr, (self.a_extract, "抽出")])
        g.add_column([self.a_up, self.a_down])
        g = home.add_group("印鑑")
        g.add_large(self.a_stamp, "押印\nモード"); g.add_large(self.a_stamp_dock, "印鑑\nパネル")
        g = home.add_group("しおり・付箋")
        g.add_large(self.a_note); g.add_large(self.a_bookmark, "しおりに\n追加"); g.add_large(self.a_toc_dock, "目次\nパネル")

        # 「書き込み」タブ: 選んでいる間だけ書き込みの道具が使える（F4 でも開く）
        self.ribbon = WriteRibbon()
        self.ribbon.ask_color = lambda initial, title: self._ask_color(initial, title)
        self.ribbon.tool_changed.connect(self._on_tool_changed)
        self.ribbon.style_changed.connect(self._on_style_changed)
        self.ribbon.markup_requested.connect(self.apply_markup)
        self.ribbon.load_settings(self._settings())
        self.ribbon.add_action_group("編集", [(self.a_del_annot, "削除"), (self.a_undo, None), (self.a_redo, None)])
        rb.add_tab("write", "書き込み", self.ribbon)

        # 「差し替え」タブ: FAX の送付状のような雛形に、日付・宛先・送付元などの枠を置いて、中身を入れ替えて印刷する
        fill = rb.add_tab("fill", "差し替え")
        g = fill.add_group("枠")
        box = QWidget(g)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(4, 1, 4, 0); bl.setSpacing(1)
        lb = QLabel("枠のルール", box); lb.setObjectName("ribbonFieldLabel")
        self.cb_rule = QComboBox(box)
        self.cb_rule.setMinimumContentsLength(10)
        self.cb_rule.setToolTip("これから置く枠に入れるもの。日付は自動、送付元・住所などは毎回同じ内容、宛先などは印刷のたびに入力します")
        bl.addWidget(lb); bl.addWidget(self.cb_rule); bl.addStretch(1)
        g.add_widget(box)
        g.add_large(self.a_field, "枠を\n置く"); g.add_large(self.a_rules, "ルールの\n管理")
        g = fill.add_group("印刷")
        g.add_large(self.a_fill_print, "差し替え\n印刷")
        self._fill_rule_combo()

        view = rb.add_tab("view", "表示")
        g = view.add_group("ズーム")
        g.add_large(self.a_zoom_out); g.add_large(self.a_zoom_in); g.add_large(self.a_zoom_fit, "幅に\n合わせる"); g.add_large(self.a_zoom_100)
        g = view.add_group("表示モード")
        for key, short in (("normal", "ノーマル"), ("warm", "暖色"), ("invert", "白黒反転")):
            if key in self.mode_actions:
                g.add_large(self.mode_actions[key], short)
        g = view.add_group("ページ移動")
        g.add_large(self.a_prev_page, "前の\nページ"); g.add_large(self.a_next_page, "次の\nページ")
        g = view.add_group("パネル")
        g.add_large(self.a_toc_dock, "目次\nパネル"); g.add_large(self.a_stamp_dock, "印鑑\nパネル")
        g.add_column([ribbon.RibbonCheck(self.a_auto_toc, "目次のある PDF では\n自動で目次パネルを開く")])

        hp = rb.add_tab("help", "ヘルプ")
        g = hp.add_group("更新")
        g.add_large(self.a_check_update, "更新を\n確認")
        g.add_column([ribbon.RibbonCheck(self.a_auto_update, "起動時に更新を確認")])
        g = hp.add_group("情報")
        g.add_large(self.a_about, "バージョン\n情報")

        rb.tab_changed.connect(self._on_tab_changed)
        self.setMenuWidget(rb)

    def _fill_rule_combo(self, select_id: str | None = None):
        """「枠のルール」の一覧を、ルールの管理の内容に合わせる。"""
        keep = select_id or self.cb_rule.currentData()
        self.cb_rule.blockSignals(True)
        self.cb_rule.clear()
        for r in self.rules.rules:
            self.cb_rule.addItem(r.name, r.id)
        i = self.cb_rule.findData(keep)
        self.cb_rule.setCurrentIndex(i if i >= 0 else 0)
        self.cb_rule.blockSignals(False)

    def _on_tab_changed(self, key: str):
        """タブを切り替えた。「書き込み」タブを選んでいる間だけ、書き込みの道具を使える状態にする。"""
        if key == "write":
            if not self.a_write.isChecked():
                self._toggle_write(True)
        else:
            self._last_tab = key
            if self.a_write.isChecked():
                self._toggle_write(False)

    # ------------------------------------------------------------ 状態
    def _update_actions(self):
        has = self.model.doc is not None and self.model.page_count > 0
        sel = bool(self.thumbs.selectedItems())
        for a in (self.a_save, self.a_saveas, self.a_insert, self.a_print, self.a_note, self.a_write, self.a_password,
                  self.a_bookmark, self.btn_bm_add):
            a.setEnabled(has)
        if not has and self.a_write.isChecked():
            self._toggle_write(False)
        self.rib.set_tab_enabled("write", has)
        self.rib.set_tab_enabled("fill", has)
        for a in (self.a_field, self.a_fill_print):
            a.setEnabled(has)
        if not has and self.field_mode:
            self._set_field_mode(False)
        self.a_del_annot.setEnabled(has and self.sel_annot is not None)
        has_toc_sel = has and self.toc_tree.currentItem() is not None
        self.btn_bm_rename.setEnabled(has_toc_sel)
        self.btn_bm_del.setEnabled(has_toc_sel)
        for a in (self.a_delete, self.a_rotl, self.a_rotr, self.a_up, self.a_down, self.a_extract):
            a.setEnabled(has and sel)
        self.a_undo.setEnabled(self.model.can_undo())
        self.a_redo.setEnabled(self.model.can_redo())
        title = APP_NAME
        if self.model.path:
            title = f"{os.path.basename(self.model.path)}{' *' if self.model.modified else ''} - {APP_NAME}"
        elif has:
            title = f"無題{' *' if self.model.modified else ''} - {APP_NAME}"
        self.setWindowTitle(title)
        if has and 0 <= self.current < self.model.page_count:
            self.page_label.setText(
                f"ページ {self.current + 1} / {self.model.page_count}   {self.model.page_info(self.current)}   "
                f"ズーム {'幅合わせ' if self.fit_width else f'{self.zoom:.0%}'}   表示 {DISPLAY_MODES[self.display_mode][0].split('（')[0]}"
                + ("   パスワード保護" if self.model.password else ""))
        else:
            self.page_label.setText("")

    def _confirm_discard(self) -> bool:
        if not self.model.modified:
            return True
        r = QMessageBox.question(self, APP_NAME, "変更が保存されていません。保存しますか？",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def closeEvent(self, event):
        if self._confirm_discard():
            self._save_layout()
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------ レイアウトの保存/復元
    def _settings(self) -> QSettings:
        return QSettings("PdfPageEditor", "PdfPageEditor")

    def _save_layout(self):
        s = self._settings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("state", self.saveState())   # ツールバーの位置・ドックの表示状態
        s.setValue("display_mode", self.display_mode)
        s.setValue("auto_toc_panel", self.auto_toc_panel)

    def _restore_layout(self):
        s = self._settings()
        g = s.value("geometry")
        if g is not None:
            self.restoreGeometry(g)
        st = s.value("state")
        if st is not None:
            self.restoreState(st)
        mode = s.value("display_mode", "normal")
        if mode in DISPLAY_MODES:
            self.set_display_mode(mode)
        auto = s.value("auto_toc_panel", True)
        self.auto_toc_panel = auto in (True, "true", "True", 1, "1")
        self.a_auto_toc.setChecked(self.auto_toc_panel)

    # ------------------------------------------------------------ 自動アップデート
    def auto_update_enabled(self) -> bool:
        """起動時チェックを行うか。インストール版（PyInstaller で exe 化）だけ。開発時は環境変数 PDFPE_UPDATE_CHECK=1 で有効化。"""
        frozen = bool(getattr(sys, "frozen", False)) or os.environ.get("PDFPE_UPDATE_CHECK") == "1"
        return frozen and self._settings().value("update/check_on_start", True, type=bool)

    def _toggle_auto_update(self, on: bool):
        self._settings().setValue("update/check_on_start", bool(on))

    def check_for_update(self, manual: bool = False, fetch=None):
        """バックグラウンドで最新版を調べる。manual=True ならメニューからの実行で、結果を必ず知らせる。"""
        if self._update_thread is not None and self._update_thread.isRunning():
            return
        self._update_thread = _UpdateCheckThread(__version__, fetch, self)
        self._update_thread.done.connect(lambda info, err: self._on_update_checked(info, err, manual))
        self._update_thread.start()

    def _on_update_checked(self, info, err: str, manual: bool):
        if err:
            if manual:
                QMessageBox.warning(self, "更新の確認", f"更新を確認できませんでした。\n{err}")
            return
        if info is None:
            if manual:
                QMessageBox.information(self, "更新の確認", f"お使いのバージョン {__version__} は最新です。")
            return
        if not manual and self._settings().value("update/skip_version", "") == info.version:
            return   # スキップ指定済み
        self._offer_update(info)

    def _offer_update(self, info):
        box = QMessageBox(self)
        box.setWindowTitle("新しいバージョン")
        box.setIcon(QMessageBox.Information)
        box.setText(f"<b>{APP_NAME} {info.version}</b> が公開されています（お使いのバージョンは {__version__}）。<br>"
                    "今すぐ更新しますか？ 更新中はアプリをいったん終了し、完了後に自動で起動します。")
        if info.notes:
            box.setDetailedText(info.notes)
        b_now = box.addButton("更新する", QMessageBox.AcceptRole)
        box.addButton("後で", QMessageBox.RejectRole)
        b_skip = box.addButton("このバージョンをスキップ", QMessageBox.DestructiveRole)
        box.setDefaultButton(b_now)
        self._exec_dialog(box)
        clicked = box.clickedButton()
        if clicked is b_skip:
            self._settings().setValue("update/skip_version", info.version)
        elif clicked is b_now:
            self.apply_update(info)

    def _exec_dialog(self, dlg) -> int:
        """モーダルダイアログを表示する（テストではここを差し替える）。"""
        return dlg.exec()

    def apply_update(self, info, opener=None):
        """インストーラーをダウンロードして実行し、アプリを終了する。ソースから実行中ならリリースページを開く。"""
        if not getattr(sys, "frozen", False) and os.environ.get("PDFPE_UPDATE_CHECK") != "1":
            webbrowser.open(info.page_url)
            return
        if not info.installer_url:
            QMessageBox.warning(self, "更新", "このリリースにはインストーラーが含まれていません。リリースページを開きます。")
            webbrowser.open(info.page_url)
            return
        if not self._confirm_discard():
            return
        path = self._download_with_progress(info, opener)
        if not path:
            return
        self._save_layout()
        self.model.modified = False   # 確認済みなので closeEvent で再度聞かない
        try:
            self._launch_installer(path)
        except Exception as e:
            self._error(e)
            return
        QTimer.singleShot(0, self.close)

    def _download_with_progress(self, info, opener=None) -> str | None:
        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog(f"{info.installer_name} をダウンロードしています...", "中止", 0, 100, self)
        dlg.setWindowTitle("更新をダウンロード")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setValue(0)
        th = _DownloadThread(info, self, opener)
        th.progress.connect(lambda r, t: dlg.setValue(int(r * 100 / t) if t else 0))
        th.done.connect(dlg.accept)
        dlg.canceled.connect(th.cancel)
        th.start()
        self._exec_dialog(dlg)
        th.wait()
        if th.error:
            if "中止" not in th.error:
                QMessageBox.warning(self, "更新", f"ダウンロードに失敗しました。\n{th.error}")
            return None
        return th.path

    def _launch_installer(self, path: str):
        # 今動いている exe の場所（ユーザー用 / 全ユーザー用）に合わせて上書きさせる
        # 環境変数は PyInstaller 用のものを外して渡す（そのまま渡すと、更新後に起動し直したアプリが
        # 古いアプリの一時フォルダーを使おうとして起動に失敗する）
        subprocess.Popen(updater.installer_args(path, sys.executable), env=updater.installer_env(bundle_dir=getattr(sys, "_MEIPASS", None)), close_fds=True)

    def show_about(self):
        QMessageBox.about(self, f"{APP_NAME} について",
                          f"<b>{APP_NAME}</b> {__version__}<br><br>"
                          "PDF の閲覧・ページ編集・電子印鑑ツール。<br>"
                          "Copyright (c) 2026 <a href='https://www.yorozusolutions.com/'>Yorozu Solutions</a> (MIT License)<br>"
                          "最新版: <a href='https://github.com/hidetako/pdf-page-editor/releases'>GitHub Releases</a><br><br>"
                          "使用ライブラリ: PyMuPDF (AGPL-3.0), PySide6 (LGPL-3.0)")

    def _error(self, e: Exception):
        QMessageBox.critical(self, APP_NAME, f"エラー: {e}")

    # ------------------------------------------------------------ ファイル
    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "PDF を開く", "", PDF_FILTER)
        if path:
            self.open_file(path)

    def _ask_password(self, path: str, wrong: bool = False) -> str | None:
        """パスワード入力（テストではここを差し替える）。キャンセルなら None。"""
        head = "パスワードが違います。\n" if wrong else ""
        text, ok = QInputDialog.getText(self, "パスワード付き PDF", f"{head}{os.path.basename(path)} を開くパスワード:", QLineEdit.Password)
        return text if ok else None

    def _ask_new_password(self, current: str | None) -> str | None:
        """保存時のパスワードの入力。キャンセルなら None、空文字なら解除。"""
        text, ok = QInputDialog.getText(self, "パスワードを設定・解除", "保存時に掛けるパスワード（空にすると解除）:", QLineEdit.Password, current or "")
        return text if ok else None

    def open_file(self, path: str):
        if not self._confirm_discard():
            return
        password = None
        while True:
            try:
                self.model.open(path, password)
                break
            except PasswordRequired as e:
                password = self._ask_password(path, wrong=e.wrong)
                if password is None:
                    return
            except Exception as e:
                self._error(e)
                return
        self.current = -1
        updated = self._refresh_fields(redraw=False)       # 日付の枠を今日の日付に（開いただけでは「変更あり」にしない）
        self.refresh_thumbs(select=[0])
        if updated:
            self.statusBar().showMessage(f"差し替え枠 {updated} か所を、今日の日付・今のルールの内容に更新しました。", 6000)
        if self.auto_toc_panel and self.model.get_toc():
            self.toc_dock.show()
        limits = self.model.restrictions()
        if self.model.password:
            self.statusBar().showMessage("パスワード付き PDF を開きました。保存時も同じパスワードで保護されます（ファイル > パスワードを設定・解除 で変更）", 8000)
        elif limits:
            self.statusBar().showMessage(f"この PDF には制限（{'・'.join(limits)}の禁止）が設定されています。このアプリでは編集でき、保存すると制限は外れます", 10000)

    def set_password_dialog(self):
        if self.model.doc is None:
            return
        pw = self._ask_new_password(self.model.password)
        if pw is None:
            return
        self.model.set_password(pw or None)
        self._update_actions()
        if pw:
            self.statusBar().showMessage("保存するとパスワードで保護されます（開くときに入力が必要になります）", 6000)
        else:
            self.statusBar().showMessage("パスワード保護を解除しました（保存すると反映されます）", 6000)

    def save(self) -> bool:
        if not self.model.path:
            return self.save_as()
        try:
            self.model.save()
        except Exception as e:
            self._error(e)
            return False
        self.statusBar().showMessage(f"保存しました: {self.model.path}", 4000)
        self._update_actions()
        return True

    def save_as(self) -> bool:
        start = self.model.path or "untitled.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "名前を付けて保存", start, PDF_FILTER)
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            self.model.save(path)
        except Exception as e:
            self._error(e)
            return False
        self.statusBar().showMessage(f"保存しました: {path}", 4000)
        self._update_actions()
        return True

    def extract_selected(self):
        sel = self.thumbs.selected_indices()
        if not sel:
            return
        path, _ = QFileDialog.getSaveFileName(self, "選択ページを抽出", "extract.pdf", PDF_FILTER)
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            self.model.extract(sel, path)
        except Exception as e:
            self._error(e)
            return
        self.statusBar().showMessage(f"{len(sel)} ページを抽出: {path}", 4000)

    # ------------------------------------------------------------ 印刷
    def print_dialog(self, model: PdfModel | None = None):
        """印刷する。model を渡すとその文書（差し替え印刷で中身を入れた複製）を、渡さなければ開いている文書を印刷する。
        開いている文書に差し替え枠があるときは、先に中身を確かめる画面（差し替え印刷）を出す。"""
        if self.model.doc is None or self.model.page_count == 0:
            return
        if not isinstance(model, PdfModel):
            model = None
        if model is None and self.model.fields():
            self.fill_print()
            return
        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName(os.path.basename(self.model.path) if self.model.path else "無題")
        printer.setFromTo(1, self.model.page_count)
        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("印刷")
        opts = QPrintDialog.PrintToFile | QPrintDialog.PrintPageRange | QPrintDialog.PrintCurrentPage | QPrintDialog.PrintCollateCopies
        if len(self.thumbs.selected_indices()) > 0:
            opts |= QPrintDialog.PrintSelection
        dlg.setOptions(opts)
        dlg.setMinMax(1, self.model.page_count)
        if self._exec_print_dialog(dlg) != QPrintDialog.Accepted:
            return
        pages = self._print_page_list(printer)
        if not pages:
            self.statusBar().showMessage("印刷するページがありません。", 4000)
            return
        try:
            self.print_pages(printer, pages, model)
        except Exception as e:
            self._error(e)
            return
        dest = printer.outputFileName() or printer.printerName() or "プリンター"
        self.statusBar().showMessage(f"{len(pages)} ページを印刷しました: {dest}", 5000)

    def _exec_print_dialog(self, dlg: QPrintDialog) -> int:
        """印刷ダイアログを表示して結果を返す（テストではここを差し替える）。"""
        return dlg.exec()

    def _print_page_list(self, printer: QPrinter) -> list[int]:
        """ダイアログの選択（すべて / 現在のページ / 選択 / 範囲）から印刷するページ番号 (0 始まり) を決める。"""
        n = self.model.page_count
        rng = printer.printRange()
        if rng == QPrinter.CurrentPage:
            return [self.current] if 0 <= self.current < n else []
        if rng == QPrinter.Selection:
            return [i for i in self.thumbs.selected_indices() if 0 <= i < n]
        if rng == QPrinter.PageRange:
            lo, hi = printer.fromPage(), printer.toPage()
            if lo <= 0 and hi <= 0:
                return list(range(n))
            lo = max(1, lo)
            hi = n if hi <= 0 else min(hi, n)
            return list(range(lo - 1, hi))
        return list(range(n))

    def print_pages(self, printer: QPrinter, pages: list[int], model: PdfModel | None = None):
        """各ページを印刷解像度で描画し、用紙の印刷可能領域に収めて（縦横比維持・中央）出力する。
        横長のページは用紙に合わせて自動で回転する。表示モード（暖色・反転）は印刷には適用しない。"""
        model = model or self.model
        doc = model.doc
        painter = QPainter()
        if not painter.begin(printer):
            raise RuntimeError("プリンターを開けませんでした。")
        try:
            dpi = max(72, min(printer.resolution(), PRINT_MAX_DPI))
            for k, idx in enumerate(pages):
                if k > 0:
                    printer.newPage()
                rect = doc[idx].rect
                landscape = rect.width > rect.height
                area = printer.pageRect(QPrinter.DevicePixel)
                area_landscape = area.width() > area.height()
                img = pix_to_qimage(model.render(idx, scale=dpi / 72))
                painter.save()
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                if landscape != area_landscape:
                    # 用紙の向きと合わなければ 90° 回して収める。/Rotate 90 のページ（スキャンの横長原稿に多い）が
                    # 元の向きで出るよう反時計回りにする
                    painter.translate(area.center())
                    painter.rotate(-90)
                    painter.translate(-area.center())
                    aw, ah = area.height(), area.width()
                else:
                    aw, ah = area.width(), area.height()
                s = min(aw / img.width(), ah / img.height())
                w, h = img.width() * s, img.height() * s
                cx, cy = area.center().x(), area.center().y()
                painter.drawImage(QRectF(cx - w / 2, cy - h / 2, w, h), img)
                painter.restore()
        finally:
            painter.end()

    # ------------------------------------------------------------ 編集
    def insert_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "挿入するファイル（選択ページの後に挿入）", "", INSERT_FILTER)
        if paths:
            self.insert_files(paths)

    def insert_files(self, paths: list[str]):
        if self.model.doc is None:
            self.model.new()
        sel = self.thumbs.selected_indices()
        at = (sel[-1] + 1) if sel else self.model.page_count
        first = at
        total = 0
        try:
            for p in paths:
                password = None
                while True:
                    try:
                        n = self.model.insert_file(p, at, password)
                        break
                    except PasswordRequired as e:
                        password = self._ask_password(p, wrong=e.wrong)
                        if password is None:
                            n = 0
                            break
                at += n
                total += n
        except Exception as e:
            self._error(e)
        if total:
            self.refresh_thumbs(select=list(range(first, first + total)))
        else:
            self._update_actions()

    def delete_selected(self):
        if self.sel_annot is not None:
            # 書き込みを選んでいるときの Delete は、ページではなく書き込みを消す（ページを誤って消さないため）
            self.delete_selected_annot()
            return
        sel = self.thumbs.selected_indices()
        if not sel:
            return
        try:
            self.model.delete(sel)
        except Exception as e:
            self._error(e)
            return
        nxt = min(sel[0], self.model.page_count - 1)
        self.refresh_thumbs(select=[nxt])

    def rotate(self, deg: int):
        sel = self.thumbs.selected_indices()
        if not sel:
            return
        self.model.rotate(sel, deg)
        self.refresh_thumbs(select=sel)

    def move(self, delta: int):
        sel = self.thumbs.selected_indices()
        if not sel:
            return
        new_sel = self.model.move_pages(sel, delta)
        self.refresh_thumbs(select=new_sel)

    def _on_reordered(self):
        order = self.thumbs.current_order()
        try:
            self.model.reorder(order)
        except Exception as e:
            self._error(e)
        sel = self.thumbs.selected_indices()
        self.refresh_thumbs(select=sel)

    def undo(self):
        self.model.undo()
        self.refresh_thumbs(select=[min(self.current, self.model.page_count - 1)] if self.model.page_count else [])

    def redo(self):
        self.model.redo()
        self.refresh_thumbs(select=[min(self.current, self.model.page_count - 1)] if self.model.page_count else [])

    # ------------------------------------------------------------ 電子印鑑
    def _toggle_stamp_mode(self, on: bool):
        self._set_stamp_mode(on)

    def _cancel_modes(self):
        """Esc: 押印・付箋モードを終える。書き込み中は、まず道具を「選択」に戻し、次の Esc でリボンを閉じる。"""
        if self._drag is not None:
            self._abort_drag()
        self._set_field_mode(False)
        self._set_stamp_mode(False)
        self._set_note_mode(False)
        had_selection = self.sel_annot is not None or bool(self.selected_text)
        self.clear_selection()
        self._select_annot(None)
        if self.ribbon.isVisible():
            if self.ribbon.tool() != "select":
                self.ribbon.set_tool("select")
            elif not had_selection:
                self._toggle_write(False)

    # ------------------------------------------------------------ 書き込みリボン
    @property
    def write_tool(self) -> str:
        """今の道具。リボンを閉じているときは「選択」。"""
        return self.ribbon.tool() if self.ribbon.isVisible() else "select"

    @property
    def text_mode(self) -> bool:
        return self.write_tool == "text"

    @property
    def drag_annot(self):
        """注釈をドラッグで移動している最中なら (ページ, 注釈, 掴んだ点)。"""
        d = self._drag
        return (d["index"], d["annot"], d["start"]) if d and d["kind"] == "move" else None

    def _toggle_write(self, on: bool):
        if on and self.model.doc is None:
            self.statusBar().showMessage("先に PDF を開いてください。", 3000)
            on = False
        if on:
            self._set_field_mode(False)
            self._set_stamp_mode(False)
            self._set_note_mode(False)
            if not self.ribbon._fonts_loaded:
                QApplication.setOverrideCursor(Qt.WaitCursor)     # PC のフォントを調べるので少し時間がかかる
                try:
                    self.ribbon.ensure_fonts()
                finally:
                    QApplication.restoreOverrideCursor()
        self.a_write.setChecked(on)
        if not on and self.ribbon.tool() != "select":
            self.ribbon.blockSignals(True); self.ribbon.set_tool("select"); self.ribbon.blockSignals(False)
        # 「書き込み」タブを開く / 元のタブへ戻す（タブ側からの切り替えで呼ばれたときは、すでにそのタブになっている）
        if on:
            self.rib.set_current("write")
        elif self.rib.current() == "write":
            self.rib.set_current(self._last_tab)
        self._apply_tool()
        if on:
            self.statusBar().showMessage("書き込み: 上のリボンで道具を選びます。終えるには「ホーム」タブか Esc")
        else:
            self.statusBar().clearMessage()
        # リボンの出し入れでページの表示幅は変わらないが、高さが変わるので見えている範囲を描き直す
        QTimer.singleShot(0, self._render_visible)

    def _on_tool_changed(self, tool: str):
        if self._drag is not None:
            self._abort_drag()
        if tool != "select":
            self.clear_selection()
            self._select_annot(None)
        self._apply_tool()
        self.statusBar().showMessage(TOOL_HINTS.get(tool, ""))

    def _apply_tool(self):
        """今の道具をすべてのページ表示に反映する（カーソルと、クリック / ドラッグのどちらで受けるか）。"""
        tool = self.write_tool
        for v in self.views:
            v.set_text_mode(tool == "text")
            v.set_draw_mode(tool in DRAW_TOOLS or self.field_mode)     # 差し替え枠もドラッグで置く
        self._update_ribbon_context()

    def _update_ribbon_context(self):
        info = self._selected_info()
        text = self.write_tool == "text" or (self.write_tool == "select" and info is not None and info["type"] == fitz.PDF_ANNOT_FREE_TEXT)
        self.ribbon.set_context("text" if text else "shape")

    def _on_style_changed(self, what: str):
        """リボンの設定が変わった。次に書くものに使うほか、選択中の書き込みがあればそれにも適用する。"""
        self.ribbon.save_settings(self._settings())
        info = self._selected_info()
        if info is None or self.sel_annot is None:
            return
        index, xref = self.sel_annot
        rb = self.ribbon
        t = info["type"]
        kw: dict = {}
        if t == fitz.PDF_ANNOT_FREE_TEXT:
            if what == "color":
                kw["color"] = rb.color()
            elif what == "fontsize":
                kw["fontsize"] = rb.fontsize()
            elif info["font"] is not None:          # フォント・背景・枠は、このアプリで書いた文字だけ
                if what == "font":
                    kw["font"] = rb.font_key()
                elif what == "textbg":
                    kw["fill"] = rb.text_bg()
                elif what == "border":
                    kw["border"] = rb.text_border()
                    kw["border_style"] = rb.text_border_style()     # 枠を付けた瞬間に、リボンで選んでいる線と太さにする
                    kw["border_width"] = rb.text_border_width()
                elif what == "borderstyle" and info["border"]:
                    kw["border_style"] = rb.text_border_style()
                elif what == "borderwidth" and info["border"]:
                    kw["border_width"] = rb.text_border_width()
        else:
            shape = t in (fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE)
            if what == "color":
                kw["color"] = rb.color()
                if shape and info["fill"] is not None:
                    kw["fill"] = rb.color()
            elif what == "width" and t not in (fitz.PDF_ANNOT_TEXT, fitz.PDF_ANNOT_HIGHLIGHT, fitz.PDF_ANNOT_UNDERLINE,
                                               fitz.PDF_ANNOT_STRIKE_OUT, fitz.PDF_ANNOT_SQUIGGLY):
                kw["width"] = rb.width()
            elif what == "opacity":
                kw["opacity"] = rb.opacity()
            elif what == "fill" and shape:
                kw["fill"] = rb.color() if rb.fill_enabled() else None
        if not kw:
            return
        try:
            xref = self.model.set_annot_style(index, xref, **kw)
        except Exception as e:
            self._error(e)
            return
        self.sel_annot = (index, xref)
        self._refresh_page(index)

    # ------------------------------------------------------------ 注釈の選択
    def _selected_info(self) -> dict | None:
        if self.sel_annot is None or self.model.doc is None:
            return None
        return self.model.annot_info(*self.sel_annot)

    def _select_annot(self, index: int | None, xref: int | None = None, reflect: bool = True):
        """注釈を選択する（index=None で選択解除）。選択中は枠と、大きさを変えられるものには右下のつまみを出す。"""
        self.sel_annot = (index, xref) if index is not None and xref is not None else None
        self._update_annot_box()
        info = self._selected_info()
        if info is not None and reflect and self.ribbon.isVisible():
            self.ribbon.reflect(info)
        self._update_ribbon_context()
        if hasattr(self, "a_del_annot"):
            self.a_del_annot.setEnabled(self.sel_annot is not None)

    def _update_annot_box(self):
        info = self._selected_info()
        if info is None:
            self.sel_annot = None
        for v in self.views:
            if info is not None and v.index == self.sel_annot[0]:
                v.set_annot_box(self._frac_rect(v.index, info["rect"]), handle=info["resizable"])
            elif v.annot_box is not None:
                v.set_annot_box(None)

    def _on_handle(self, index: int, pt: tuple[float, float]) -> bool:
        """pt が、選択中の注釈の右下のつまみの上か。"""
        info = self._selected_info()
        if info is None or self.sel_annot[0] != index or not info["resizable"]:
            return False
        tol = (HANDLE_PX / 2 + 3) / max(0.01, self._view_scale(index))
        r = info["rect"]
        return abs(pt[0] - r.x1) <= tol and abs(pt[1] - r.y1) <= tol

    def delete_selected_annot(self):
        if self.sel_annot is None:
            return
        info = self._selected_info()
        index, xref = self.sel_annot
        self.remove_annot(index, xref, info["kind"] if info else "書き込み")

    def _refresh_page(self, index: int):
        """1 ページだけ描き直す（書き込みのたびに全ページを作り直すと遅いため）。"""
        if self.model.doc is None or not (0 <= index < self.model.page_count) or index >= len(self.views):
            return
        it = self.thumbs.item(index)
        if it is not None:
            it.setIcon(QIcon(QPixmap.fromImage(pix_to_qimage(self.model.render(index, max_width=THUMB_W)))))
        self._words_cache.pop(index, None)
        self.views[index].rendered_key = None
        self._render_visible()
        self._update_annot_box()
        self._update_actions()

    def _toggle_note_mode(self, on: bool):
        self._set_note_mode(on)

    def _set_note_mode(self, on: bool):
        if on and self.model.doc is None:
            self.statusBar().showMessage("先に PDF を開いてください。", 3000)
            on = False
        if on:
            self._set_field_mode(False)
            self._set_stamp_mode(False)
            if self.ribbon.isVisible():
                self._toggle_write(False)
        self.note_mode = on
        self.a_note.setChecked(on)
        for v in self.views:
            v.set_note_mode(on)
        if on:
            self.statusBar().showMessage("付箋モード: ページ上をクリックするとメモを貼ります（Esc で終了）")
        else:
            self.statusBar().clearMessage()

    def _set_stamp_mode(self, on: bool):
        if on:
            self._set_field_mode(False)
            self._set_note_mode(False)
            if self.ribbon.isVisible():
                self._toggle_write(False)
        if on and (self.model.doc is None or self.stamp_dock_widget.current_stamp() is None):
            if self.model.doc is None:
                self.statusBar().showMessage("先に PDF を開いてください。", 3000)
            on = False
        self.stamp_mode = on
        self.a_stamp.setChecked(on)
        self.stamp_dock_widget.btn_mode.blockSignals(True); self.stamp_dock_widget.set_mode(on); self.stamp_dock_widget.btn_mode.blockSignals(False)
        self._refresh_ghost()
        if on:
            # 印鑑パネルが閉じていると印鑑を選べないので、押印モードに入ったら自動で開いて前面に出す
            self.stamp_dock.show()
            self.stamp_dock.raise_()
            self.statusBar().showMessage("押印モード: ページ上をクリックすると押印します（Esc で終了）")
        else:
            self.statusBar().clearMessage()

    @property
    def view(self) -> PageView | None:
        """現在ページの PageView（文書なしなら None）。"""
        return self.views[self.current] if 0 <= self.current < len(self.views) else None

    def _refresh_ghost(self):
        if not getattr(self, "stamp_mode", False):
            for v in self.views:
                v.set_stamp_mode(False)
            return
        try:
            png = self.stamp_dock_widget.render_current()
        except Exception:
            for v in self.views:
                v.set_stamp_mode(False)
            return
        st = self.stamp_dock_widget.settings()
        w_pt, h_pt = self._stamp_size_pt(st)
        base = png_to_qpixmap(png)
        cache: dict[int, QPixmap] = {}
        for v in self.views:
            k = self._view_scale(v.index)
            key = int(w_pt * k)
            if key not in cache:
                cache[key] = base.scaled(max(1, key), max(1, int(h_pt * k)), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            v.set_stamp_mode(True, cache[key])

    @staticmethod
    def _stamp_size_pt(st: dict) -> tuple[float, float]:
        s = st["stamp"]
        w0, h0 = s.size_pt()
        w = st["size_mm"] * MM
        return w, w * h0 / (w0 or 1)

    def _page_point(self, index: int, frac: QPointF) -> tuple[float, float]:
        rect = self.model.doc[index].rect
        return frac.x() * rect.width, frac.y() * rect.height

    def _on_page_clicked(self, index: int, frac: QPointF):
        if self.model.doc is None:
            return
        if self.note_mode:
            self._set_current(index)
            self.add_note_at(index, self._page_point(index, frac))
            return
        if self.text_mode:
            self._text_click(index, frac)
            return
        if not getattr(self, "stamp_mode", False):
            return
        st = self.stamp_dock_widget.settings()
        if st["stamp"] is None:
            return
        self._set_current(index)
        tilt = random_tilt(st["tilt_max"])
        try:
            png = self.stamp_dock_widget.render_current(tilt=tilt)
            w_pt, h_pt = self._stamp_size_pt(st)
            if tilt:
                # 傾けると外接矩形が広がるので、画像の縦横比に合わせて矩形を拡大
                import math
                a = math.radians(abs(tilt))
                w_pt, h_pt = w_pt * math.cos(a) + h_pt * math.sin(a), w_pt * math.sin(a) + h_pt * math.cos(a)
            rect = self.model.doc[index].rect
            center = (frac.x() * rect.width, frac.y() * rect.height)
            self.model.stamp(index, png, center, w_pt, h_pt)
        except Exception as e:
            self._error(e)
            return
        self.refresh_thumbs(select=[index], keep_view=True)
        self.statusBar().showMessage(f"押印しました（ページ {index + 1}）。続けてクリックで押印、Esc で終了", 5000)

    def _on_page_context(self, index: int, frac: QPointF, gpos: QPoint):
        """ページ上の右クリック。押印の上なら「この押印を取り消す」を出す。"""
        if self.model.doc is None or not (0 <= index < self.model.page_count):
            return
        self._set_current(index)
        rect = self.model.doc[index].rect
        pt = (frac.x() * rect.width, frac.y() * rect.height)
        xref = self.model.stamp_at(index, pt)
        note = self._note_hit(index, pt)
        annot = None if note is not None else self._annot_hit(index, pt)
        menu = QMenu(self)
        if self.selected_text and self.sel_page == index:
            menu.addAction("コピー", self.copy_selection)
            menu.addAction("蛍光ペン", lambda: self.apply_markup("highlight"))
            menu.addAction("下線", lambda: self.apply_markup("underline"))
            menu.addAction("取り消し線", lambda: self.apply_markup("strikeout"))
            menu.addSeparator()
        if note is not None:
            menu.addAction("付箋を編集...", lambda: self.edit_note(index, note))
            menu.addAction("付箋の色を変更...", lambda: self.recolor_annot(index, note["xref"]))
            menu.addAction("付箋を削除", lambda: self.remove_note(index, note["xref"]))
            menu.addSeparator()
        elif annot is not None:
            kind = annot["kind"]
            self._select_annot(index, annot["xref"])
            if annot.get("field"):
                menu.addAction("この枠のルールを変える...", lambda: self.edit_field(index, annot))
                menu.addAction("文字サイズを変更...", lambda: self.resize_annot_text(index, annot))
            elif annot["type"] == fitz.PDF_ANNOT_FREE_TEXT:
                menu.addAction("文字を編集...", lambda: self.edit_annot_text(index, annot))
                menu.addAction("文字サイズを変更...", lambda: self.resize_annot_text(index, annot))
            menu.addAction(f"{kind}の色を変更...", lambda: self.recolor_annot(index, annot["xref"]))
            menu.addAction(f"この{kind}を削除", lambda: self.remove_annot(index, annot["xref"], kind))
            menu.addSeparator()
        if xref is None:
            none = menu.addAction("ここに取り消せる押印はありません")
            none.setEnabled(False)
        else:
            menu.addAction("この押印を取り消す", lambda: self.remove_stamp(index, xref))
        menu.addSeparator()
        menu.addAction("ここに付箋を追加...", lambda: self.add_note_at(index, pt))
        menu.addAction("このページをしおりに追加...", lambda: self.add_bookmark(index))
        if self.stamp_mode or self.note_mode or self.ribbon.isVisible():
            menu.addSeparator()
            menu.addAction("押印・付箋・書き込みを終了", self._end_all_modes)
        self._popup_menu(menu, gpos)

    def _end_all_modes(self):
        self._set_field_mode(False)
        self._set_stamp_mode(False)
        self._set_note_mode(False)
        if self.ribbon.isVisible():
            self._toggle_write(False)

    def _annot_hit(self, index: int, pt: tuple[float, float]) -> dict | None:
        """付箋以外の注釈の当たり判定。"""
        a = self.model.annot_at(index, pt, pad=ANNOT_HIT_PAD)
        return a if a is not None and a["type"] != fitz.PDF_ANNOT_TEXT else None

    def _note_hit(self, index: int, pt: tuple[float, float]) -> dict | None:
        """付箋アイコンの当たり判定（少し広めに取る）。"""
        p = fitz.Point(*pt)
        hits = []
        for n in self.model.notes_on_page(index):
            r = fitz.Rect(n["rect"])
            r.x0 -= NOTE_HIT_PAD; r.y0 -= NOTE_HIT_PAD; r.x1 += NOTE_HIT_PAD; r.y1 += NOTE_HIT_PAD
            if r.contains(p):
                hits.append(n)
        return hits[-1] if hits else None

    def _on_page_hovered(self, index: int, frac: QPointF, gpos: QPoint):
        """付箋の上にマウスが来たら内容をツールチップで出す。"""
        if self.model.doc is None or not (0 <= index < self.model.page_count):
            return
        pt = self._page_point(index, frac)
        note = self._note_hit(index, pt)
        annot = None if note is not None else self._annot_hit(index, pt)
        v = self.views[index]
        if self._drag is not None:
            return
        if not v.click_mode and not v.draw_mode:
            if self._on_handle(index, pt):
                v.setCursor(Qt.SizeFDiagCursor)
            elif note is not None or (annot is not None and annot["movable"]):
                v.setCursor(Qt.SizeAllCursor)
            elif v.text_mode:
                v.setCursor(Qt.CrossCursor)
            else:
                over_text = any(w["rect"].contains(fitz.Point(*pt)) for w in self._words(index))
                v.setCursor(Qt.IBeamCursor if over_text and annot is None else Qt.ArrowCursor)
        if v.draw_mode:
            QToolTip.hideText()
            return
        if note is None and annot is None:
            QToolTip.hideText()
            return
        if note is not None:
            head = f"【{note['title']}】" if note["title"] else ""
            QToolTip.showText(gpos, f"{head}{note['text']}", v)
        else:
            name = f"（{annot['field'].get('name', '')}）" if annot.get("field") else ""
            tip = annot["kind"] + name + (f": {annot['text']}" if annot["text"] else "") + ("（ドラッグで移動）" if annot["movable"] else "")
            QToolTip.showText(gpos, tip, v)

    # ------------------------------------------------------------ テキスト選択・コピー
    def _words(self, index: int) -> list[dict]:
        if index not in self._words_cache:
            try:
                self._words_cache[index] = self.model.page_words(index)
            except Exception:
                self._words_cache[index] = []
        return self._words_cache[index]

    def _nearest_word(self, index: int, pt: tuple[float, float]) -> int | None:
        """表示座標 pt に最も近い単語の番号（読み順）。行のずれを横ずれより重く見る。"""
        words = self._words(index)
        if not words:
            return None
        px, py = pt
        best, best_d = None, None
        for i, w in enumerate(words):
            r = w["rect"]
            dy = 0.0 if r.y0 <= py <= r.y1 else min(abs(py - r.y0), abs(py - r.y1))
            dx = 0.0 if r.x0 <= px <= r.x1 else min(abs(px - r.x0), abs(px - r.x1))
            d = dy * 4 + dx
            if best_d is None or d < best_d:
                best, best_d = i, d
        return best

    def _text_click(self, index: int, frac: QPointF):
        """文字の道具でクリックした: 書いてある文字の上なら直す。それ以外の場所なら、そこに新しく書く。"""
        self._set_current(index)
        pt = self._page_point(index, frac)
        a = self.model.annot_at(index, pt, pad=ANNOT_HIT_PAD)
        if a is not None and a["type"] == fitz.PDF_ANNOT_FREE_TEXT:
            self._select_annot(index, a["xref"])
            self.edit_field(index, a) if a.get("field") else self.edit_annot_text(index, a)
        else:
            self.add_text_at(index, pt)

    def _on_click_released(self, index: int, frac: QPointF, double: bool):
        """ページの上で、動かさずにボタンを離した（= クリック）。ドラッグで何かをした直後は何もしない。"""
        if self.model.doc is None or not (0 <= index < self.model.page_count) or self._drag_changed:
            return
        if self.text_mode:
            self._text_click(index, frac)
        elif double:
            self._on_page_double_clicked(index, frac)

    def _click_slop_pt(self, index: int) -> float:
        """クリックとみなす動きの大きさを、ページ上の長さ (pt) に直したもの。"""
        return CLICK_SLOP_PX / max(self._view_scale(index), 1e-6)

    def _on_drag_started(self, index: int, frac: QPointF):
        self._drag_changed = False
        self.clear_selection()
        self._set_current(index)
        pt = self._page_point(index, frac)
        tool = self.write_tool
        if self.field_mode:
            # 差し替え枠: ドラッグした幅の枠を置く（クリックだけなら、中身に合わせた幅）
            self._select_annot(None)
            self._drag = {"kind": "field", "index": index, "pts": [pt]}
            return
        if tool in DRAW_TOOLS:
            # ペン・直線・矢印・四角・丸: ドラッグで描く
            self._select_annot(None)
            self._drag = {"kind": tool, "index": index, "pts": [pt]}
            return
        if self._on_handle(index, pt):
            # 選択中の注釈の右下のつまみ: 大きさ変更
            info = self._selected_info()
            self._drag = {"kind": "resize", "index": index, "annot": info, "start": pt}
            self.views[index].set_move_rect(self._frac_rect(index, info["rect"]))
            return
        a = self.model.annot_at(index, pt, pad=ANNOT_HIT_PAD)
        if a is not None:
            self._select_annot(index, a["xref"])
            if a["movable"]:
                # 注釈の上から始まるドラッグは移動
                self._drag = {"kind": "move", "index": index, "annot": a, "start": pt}
                self.views[index].set_move_rect(self._frac_rect(index, a["rect"]))
                return
        else:
            self._select_annot(None)
        if tool == "text":
            return      # 文字の道具: 余白や本文の上のドラッグでは何もしない（動かさずに離したら、そこに文字を書く）
        # それ以外（余白・本文・マーカーの上）は文字の選択
        self.sel_page = index
        self.sel_anchor = pt

    def _frac_rect(self, index: int, rect) -> tuple[float, float, float, float]:
        pr = self.model.doc[index].rect
        return rect.x0 / pr.width, rect.y0 / pr.height, rect.x1 / pr.width, rect.y1 / pr.height

    def _resized_rect(self, d: dict, pt: tuple[float, float]) -> fitz.Rect:
        r = fitz.Rect(d["annot"]["rect"])
        dx, dy = pt[0] - d["start"][0], pt[1] - d["start"][1]
        return fitz.Rect(r.x0, r.y0, max(r.x0 + 6, r.x1 + dx), max(r.y0 + 6, r.y1 + dy))

    def _preview_for(self, d: dict) -> dict:
        index = d["index"]
        pr = self.model.doc[index].rect
        rb = self.ribbon
        kind = d["kind"]
        pts = d["pts"] if kind == "pen" else [d["pts"][0], d["pts"][-1]]
        if kind == "field":         # 置こうとしている枠の範囲（緑の四角）
            return {"kind": "rect", "pts": [(x / pr.width, y / pr.height) for x, y in pts], "color": QColor(0, 140, 110),
                    "width": 1.5, "opacity": 1.0, "fill": False}
        return {"kind": kind, "pts": [(x / pr.width, y / pr.height) for x, y in pts],
                "color": QColor.fromRgbF(*rb.color()), "width": rb.width() * self._view_scale(index),
                "opacity": rb.opacity(), "fill": kind in ("rect", "ellipse") and rb.fill_enabled()}

    def _abort_drag(self):
        d, self._drag = self._drag, None
        if d and d["index"] < len(self.views):
            self.views[d["index"]].set_move_rect(None)
            self.views[d["index"]].set_preview(None)

    def _on_drag_moved(self, index: int, frac: QPointF):
        d = self._drag
        if d is not None:
            if index != d["index"]:
                return
            pt = self._page_point(index, frac)
            v = self.views[index]
            if d["kind"] == "move":
                start = d["start"]
                r = fitz.Rect(d["annot"]["rect"]) + (pt[0] - start[0], pt[1] - start[1], pt[0] - start[0], pt[1] - start[1])
                v.set_move_rect(self._frac_rect(index, r))
            elif d["kind"] == "resize":
                v.set_move_rect(self._frac_rect(index, self._resized_rect(d, pt)))
            else:
                last = d["pts"][-1]
                if d["kind"] != "pen" or abs(pt[0] - last[0]) + abs(pt[1] - last[1]) >= 0.8:
                    d["pts"].append(pt)
                v.set_preview(self._preview_for(d))
            return
        if self.sel_page is None or index != self.sel_page or self.sel_anchor is None:
            return
        self._update_selection(self._page_point(index, frac))

    def _on_drag_finished(self, index: int, frac: QPointF):
        d = self._drag
        if d is not None:
            self._abort_drag()
            i = d["index"]
            if index != i:
                return
            pt = self._page_point(index, frac)
            try:
                if d["kind"] == "move":
                    a, start = d["annot"], d["start"]
                    dx, dy = pt[0] - start[0], pt[1] - start[1]
                    slop = self._click_slop_pt(i)
                    if abs(dx) <= slop and abs(dy) <= slop:
                        return                      # クリックだけ（選択のみ）
                    xref = self.model.move_annot(i, a["xref"], dx, dy)
                    self.sel_annot = (i, xref)
                    msg = f"{a['kind']}を移動しました。Ctrl+Z で戻せます"
                elif d["kind"] == "resize":
                    a = d["annot"]
                    new = self._resized_rect(d, pt)
                    slop = self._click_slop_pt(i)
                    if abs(new.x1 - a["rect"].x1) <= slop and abs(new.y1 - a["rect"].y1) <= slop:
                        return
                    xref = self.model.resize_annot(i, a["xref"], new)
                    self.sel_annot = (i, xref)
                    msg = f"{a['kind']}の大きさを変えました。Ctrl+Z で戻せます"
                elif d["kind"] == "field":
                    msg = self._finish_field(d, pt)
                    if msg is None:
                        return
                else:
                    msg = self._finish_drawing(d, pt)
                    if msg is None:
                        return
            except Exception as e:
                self._error(e)
                return
            self._drag_changed = True
            self._refresh_page(i)
            self.statusBar().showMessage(msg, 4000)
            return
        if self.sel_page is None or index != self.sel_page:
            return
        self._update_selection(self._page_point(index, frac))
        if self.selected_text:
            self._drag_changed = True
            n = len(self.selected_text)
            self.statusBar().showMessage(f"{n} 文字を選択中。Ctrl+C でコピー、右クリックでも可", 4000)

    def _finish_drawing(self, d: dict, pt: tuple[float, float]) -> str | None:
        """ドラッグで描いた線・図形を注釈として書き込む。短すぎて何も書かなかったら None。"""
        rb = self.ribbon
        i, kind, pts = d["index"], d["kind"], d["pts"]
        color, width, opacity = rb.color(), rb.width(), rb.opacity()
        a, b = pts[0], pt
        if kind == "pen":
            if pts[-1] != pt:
                pts.append(pt)
            if len(pts) < 2 or max(abs(p[0] - a[0]) + abs(p[1] - a[1]) for p in pts) < 1.5:
                return None
            self.model.add_ink(i, [pts], color, width, opacity)
            return "線を書きました。続けて書けます。Ctrl+Z で取り消し"
        if abs(b[0] - a[0]) + abs(b[1] - a[1]) < 4:
            return None
        if kind in ("line", "arrow"):
            self.model.add_line(i, a, b, color, width, opacity, arrow=kind == "arrow")
            return ("矢印" if kind == "arrow" else "直線") + "を引きました。Ctrl+Z で取り消し"
        if abs(b[0] - a[0]) < 3 or abs(b[1] - a[1]) < 3:
            return None
        self.model.add_shape(i, kind, (a[0], a[1], b[0], b[1]), color, width, opacity, fill=color if rb.fill_enabled() else None)
        return ("四角" if kind == "rect" else "丸") + "を書きました。Ctrl+Z で取り消し"

    def _on_page_double_clicked(self, index: int, frac: QPointF):
        """書いた文字・付箋をダブルクリックしたら内容を直す。"""
        if self.model.doc is None or not (0 <= index < self.model.page_count) or self.write_tool != "select":
            return
        if self._drag is not None:
            self._abort_drag()
        pt = self._page_point(index, frac)
        note = self._note_hit(index, pt)
        if note is not None:
            self.edit_note(index, note)
            return
        a = self._annot_hit(index, pt)
        if a is not None and a["type"] == fitz.PDF_ANNOT_FREE_TEXT:
            self._select_annot(index, a["xref"])
            self.edit_field(index, a) if a.get("field") else self.edit_annot_text(index, a)

    def _update_selection(self, pt: tuple[float, float]):
        index = self.sel_page
        a = self._nearest_word(index, self.sel_anchor)
        b = self._nearest_word(index, pt)
        if a is None or b is None:
            return
        lo, hi = (a, b) if a <= b else (b, a)
        words = self._words(index)
        # 開始点・終了点が同じ単語で、その単語の上にもいない場合（余白クリック）は選択なし
        if lo == hi and not words[lo]["rect"].contains(fitz.Point(*pt)) and not words[lo]["rect"].contains(fitz.Point(*self.sel_anchor)):
            self.views[index].set_selection([])
            self.sel_range = None; self.selected_text = ""
            self._set_selection_actions(False)
            return
        self.sel_range = (lo, hi)
        pr = self.model.doc[index].rect
        rects = [(w["rect"].x0 / pr.width, w["rect"].y0 / pr.height, w["rect"].x1 / pr.width, w["rect"].y1 / pr.height)
                 for w in words[lo:hi + 1]]
        self.views[index].set_selection(rects)
        self.selected_text = self._join_words(words[lo:hi + 1])
        self._set_selection_actions(bool(self.selected_text))

    def _set_selection_actions(self, on: bool):
        for a in (self.a_copy, self.a_highlight, self.a_underline, self.a_strikeout):
            a.setEnabled(on)
        self.ribbon.set_markup_enabled(on)

    @staticmethod
    def _join_words(words: list[dict]) -> str:
        """単語を文章に戻す。行が変われば改行。同じ行の単語境界は元の PDF に空白があった箇所なので空白で結ぶ
        （日本語の続き文字は MuPDF が 1 単語として返すため、ここで空白が増えることはない）。"""
        out = []
        prev = None
        for w in words:
            if prev is not None:
                out.append("\n" if (w["block"], w["line"]) != (prev["block"], prev["line"]) else " ")
            out.append(w["text"])
            prev = w
        return "".join(out)

    def clear_selection(self):
        if self.sel_page is not None and 0 <= self.sel_page < len(self.views):
            self.views[self.sel_page].set_selection([])
        self.sel_page = None; self.sel_anchor = None; self.sel_range = None
        self.selected_text = ""
        if hasattr(self, "a_strikeout"):
            self._set_selection_actions(False)

    def copy_selection(self):
        if not self.selected_text:
            return
        QApplication.clipboard().setText(self.selected_text)
        self.statusBar().showMessage(f"{len(self.selected_text)} 文字をコピーしました", 4000)

    # ------------------------------------------------------------ 付箋
    def _ask_text(self, title: str, label: str, default: str = "") -> str | None:
        """複数行テキストの入力（テストではここを差し替える）。"""
        text, ok = QInputDialog.getMultiLineText(self, title, label, default)
        return text if ok else None

    def _ask_line(self, title: str, label: str, default: str = "") -> str | None:
        text, ok = QInputDialog.getText(self, title, label, text=default)
        return text if ok else None

    def add_note_at(self, index: int, pt: tuple[float, float]):
        text = self._ask_text("付箋を追加", f"ページ {index + 1} に貼るメモ:")
        if text is None or not text.strip():
            return
        try:
            self.model.add_note(index, pt, text.strip(), title=os.environ.get("USERNAME") or os.environ.get("USER") or "付箋")
        except Exception as e:
            self._error(e)
            return
        self._refresh_page(index)
        self.statusBar().showMessage(f"付箋を追加しました（ページ {index + 1}）。右クリックで編集・削除、Ctrl+Z で取り消し", 5000)

    def edit_note(self, index: int, note: dict):
        text = self._ask_text("付箋を編集", "メモ:", note["text"])
        if text is None:
            return
        try:
            self.model.update_note(index, note["xref"], text.strip())
        except Exception as e:
            self._error(e)
            return
        self._refresh_page(index)

    def remove_note(self, index: int, xref: int):
        try:
            self.model.remove_note(index, xref)
        except Exception as e:
            self._error(e)
            return
        self._refresh_page(index)
        self.statusBar().showMessage(f"付箋を削除しました（ページ {index + 1}）。Ctrl+Z で戻せます", 5000)

    # ------------------------------------------------------------ 書き込み（マーカー・文字・既存注釈の編集）
    def _ask_color(self, initial: QColor, title: str) -> QColor | None:
        """色の選択（テストではここを差し替える）。キャンセルなら None。"""
        c = QColorDialog.getColor(initial, self, title)
        return c if c.isValid() else None

    def apply_markup(self, kind: str):
        """選択中の文字に蛍光ペン / 下線 / 取り消し線を付ける。"""
        if self.sel_page is None or self.sel_range is None or not self.selected_text:
            self.statusBar().showMessage("先にページ上をドラッグして文字を選択してください。", 4000)
            return
        index = self.sel_page
        lo, hi = self.sel_range
        rects = [w["rect"] for w in self._words(index)[lo:hi + 1]]
        label = {"highlight": "蛍光ペン", "underline": "下線", "strikeout": "取り消し線"}[kind]
        try:
            self.model.add_markup(index, kind, rects)
        except Exception as e:
            self._error(e)
            return
        self.clear_selection()
        self._refresh_page(index)
        self.statusBar().showMessage(f"{label}を付けました（ページ {index + 1}）。右クリックで色変更・削除、Ctrl+Z で取り消し", 5000)

    def add_text_at(self, index: int, pt: tuple[float, float]):
        """リボンで選んだフォント・大きさ・色で、pt を左上として文字を書き込む。"""
        text = self._ask_text("文字を書き込む", f"ページ {index + 1} に書き込む文字:")
        if text is None or not text.strip():
            return
        rb = self.ribbon
        try:
            xref = self.model.add_text_box(index, pt, text.strip("\n"), font=rb.font_key(), fontsize=rb.fontsize(),
                                           color=rb.color(), fill=rb.text_bg(), border=rb.text_border(),
                                           border_style=rb.text_border_style(), border_width=rb.text_border_width(),
                                           title=os.environ.get("USERNAME") or os.environ.get("USER") or "")
        except Exception as e:
            self._error(e)
            return
        self.sel_annot = (index, xref)
        self._refresh_page(index)
        self.statusBar().showMessage(f"文字を書き込みました（ページ {index + 1}）。クリックで直す、ドラッグで移動、Ctrl+Z で取り消し", 5000)

    # ------------------------------------------------------------ 差し替え枠
    def _toggle_field_mode(self, on: bool):
        self._set_field_mode(on)

    def _set_field_mode(self, on: bool):
        if on and self.model.doc is None:
            self.statusBar().showMessage("先に PDF を開いてください。", 3000)
            on = False
        if on == self.field_mode and self.a_field.isChecked() == on:
            return
        if on:
            self._set_stamp_mode(False)
            self._set_note_mode(False)
            if self.a_write.isChecked():
                self._toggle_write(False)
        self.field_mode = on
        self.a_field.setChecked(on)
        self._apply_tool()
        if on:
            rule = self.current_rule()
            self.statusBar().showMessage(f"差し替え枠（{rule.name}）: ページ上をドラッグして枠を置きます。クリックだけでも置けます（Esc で終了）")
        else:
            self.statusBar().clearMessage()

    def current_rule(self) -> fields.Rule:
        """「枠のルール」で選んでいるルール。"""
        return self.rules.get(self.cb_rule.currentData()) or self.rules.rules[0]

    def _field_text(self, rule: fields.Rule) -> str:
        """枠に今入れておく文字。決まっていないときは〔名前〕という目印。"""
        return fields.resolve(rule) or fields.placeholder(rule.name)

    def _finish_field(self, d: dict, pt: tuple[float, float]) -> str | None:
        """ドラッグ（またはクリック）した場所に差し替え枠を置く。見た目は「書き込み」タブの文字の設定を使う。"""
        i = d["index"]
        a, b = d["pts"][0], pt
        x, y = min(a[0], b[0]), min(a[1], b[1])
        width = abs(b[0] - a[0])
        rule = self.current_rule()
        rb = self.ribbon
        xref = self.model.add_text_box(i, (x, y) if width >= 24 else a, self._field_text(rule), font=rb.font_key(), fontsize=rb.fontsize(),
                                       color=rb.color(), fill=rb.text_bg(), border=rb.text_border(),
                                       border_style=rb.text_border_style(), border_width=rb.text_border_width(),
                                       wrap_width=width if width >= 24 else None, field=fields.field_spec(rule))
        self.sel_annot = (i, xref)
        self._set_field_mode(False)         # 1 つ置いたら終わる（続けて置くときは、もう一度「枠を置く」）
        return f"差し替え枠（{rule.name}）を置きました。ドラッグで移動、右下の四角で幅を変えられます。「差し替え印刷」で中身を入れて印刷します"

    def edit_field(self, index: int, annot: dict):
        """枠のルールを選び直す。"""
        names = [r.name for r in self.rules.rules]
        cur = self.rules.get(str(annot["field"].get("id")))
        choice = self._ask_choice("差し替え枠", "この枠に入れるもの（ルール）:", names, names.index(cur.name) if cur else 0)
        if choice is None:
            return
        rule = self.rules.rules[names.index(choice)]
        try:
            xref = self.model.set_field_rule(index, annot["xref"], fields.field_spec(rule), self._field_text(rule))
        except Exception as e:
            self._error(e)
            return
        self.sel_annot = (index, xref)
        self._refresh_page(index)

    def _ask_choice(self, title: str, label: str, items: list[str], current: int = 0) -> str | None:
        """一覧から 1 つ選ぶ（テストではここを差し替える）。"""
        text, ok = QInputDialog.getItem(self, title, label, items, current, False)
        return text if ok else None

    def _refresh_fields(self, redraw: bool = True) -> int:
        """日付の枠を今日の日付に、決まった文字の枠を今のルールの内容に合わせる。変えた枠の数を返す。
        利用者の操作ではないので、元に戻す対象にも「変更あり」にもしない。"""
        if self.model.doc is None:
            return 0
        texts: dict[str, str] = {}
        try:
            for f in self.model.fields():
                rule = self.rules.for_spec(f["field"])
                if rule.kind == "date" or (rule.kind == "fixed" and rule.value):
                    texts[rule.id] = fields.resolve(rule)
            n = self.model.set_field_texts(texts, undo=False, mark_modified=False) if texts else 0
        except Exception:
            return 0                        # 更新できなくても、開くこと自体は続ける
        if n and redraw:
            self._select_annot(None)
            self.refresh_thumbs(select=[self.current] if self.current >= 0 else None, keep_view=True)
        return n

    def edit_rules(self):
        """ルールの管理。閉じたあと、枠のルールの一覧と、開いている文書の枠の中身を合わせ直す。"""
        dlg = RulesDialog(self.rules, self, select_id=self.cb_rule.currentData())
        if self._exec_dialog(dlg) != QDialog.Accepted:
            return
        self._fill_rule_combo()
        self._refresh_fields()

    def fill_print(self):
        """差し替え印刷: 枠に入れる内容を確かめて印刷する（または別名で保存する）。開いている文書は書き換えない。"""
        if self.model.doc is None:
            return
        found = self.model.fields()
        if not found:
            QMessageBox.information(self, "差し替え印刷", "この PDF には差し替え枠がありません。\n"
                                    "「差し替え」タブの「枠を置く」で、日付や宛先などの枠を置いてから使います。")
            return
        rows: list[tuple[fields.Rule, str]] = []
        seen: set[str] = set()
        for f in found:
            rule = self.rules.for_spec(f["field"])
            if rule.id in seen:
                continue
            seen.add(rule.id)
            rows.append((rule, fields.resolve(rule)))
        dlg = FillDialog(rows, self)
        action = self._exec_fill_dialog(dlg)
        if action not in ("print", "save"):
            return
        values = dlg.values()
        if self.rules.remember_values(values):      # 送付元・住所など、決まった文字はここで直した内容を次回も使う
            try:
                self.rules.save()
            except OSError:
                pass
        try:
            filled = self.model.snapshot()
            filled.set_field_texts(values, undo=False, mark_modified=False)
        except Exception as e:
            self._error(e)
            return
        try:
            if action == "print":
                self.print_dialog(filled)
            else:
                base = os.path.splitext(os.path.basename(self.model.path))[0] if self.model.path else "無題"
                path, _ = QFileDialog.getSaveFileName(self, "内容を入れた PDF を保存", f"{base}_{datetime.date.today():%Y%m%d}.pdf", PDF_FILTER)
                if path:
                    if not os.path.splitext(path)[1]:
                        path += ".pdf"
                    if self.model.path and os.path.abspath(path) == os.path.abspath(self.model.path):
                        raise ValueError("開いている PDF（雛形）と同じ名前には保存できません。別の名前にしてください。")
                    filled.save(path)
                    self.statusBar().showMessage(f"内容を入れた PDF を保存しました: {path}", 6000)
        except Exception as e:
            self._error(e)
        finally:
            filled.close()
        self._refresh_fields()                      # 直した「決まった文字」を、画面の枠にも反映する

    def _exec_fill_dialog(self, dlg: FillDialog) -> str | None:
        """差し替え印刷の画面を出して、押されたボタン（"print" / "save" / None）を返す（テストではここを差し替える）。"""
        return dlg.action if dlg.exec() == QDialog.Accepted else None

    def edit_annot_text(self, index: int, annot: dict):
        text = self._ask_text("文字を編集", "文字:", annot["text"])
        if text is None:
            return
        try:
            if not text.strip():
                self.model.remove_annot(index, annot["xref"])       # 空にしたら消す
                xref = None
            else:
                xref = self.model.set_annot_text(index, annot["xref"], text.strip("\n"))
        except Exception as e:
            self._error(e)
            return
        self.sel_annot = (index, xref) if xref is not None else None
        self._refresh_page(index)

    def resize_annot_text(self, index: int, annot: dict):
        cur = annot.get("fontsize") or 12.0
        val = self._ask_line("文字サイズ", "文字サイズ（pt）:", f"{cur:g}")
        if val is None:
            return
        try:
            size = float(val)
            if not (4 <= size <= 200):
                raise ValueError("文字サイズは 4〜200 の範囲で指定してください。")
            xref = self.model.set_annot_fontsize(index, annot["xref"], size)
        except Exception as e:
            self._error(e)
            return
        self.sel_annot = (index, xref)
        self._refresh_page(index)

    def recolor_annot(self, index: int, xref: int):
        cur = self.model.annot_info(index, xref)
        if cur is None:
            return
        init = QColor.fromRgbF(*cur["stroke"]) if cur["stroke"] else QColor(255, 0, 0)
        c = self._ask_color(init, f"{cur['kind']}の色")
        if c is None:
            return
        try:
            new = self.model.set_annot_color(index, xref, (c.redF(), c.greenF(), c.blueF()))
        except Exception as e:
            self._error(e)
            return
        if self.sel_annot == (index, xref):
            self.sel_annot = (index, new)
        self._refresh_page(index)

    def remove_annot(self, index: int, xref: int, kind: str = "注釈"):
        try:
            self.model.remove_annot(index, xref)
        except Exception as e:
            self._error(e)
            return
        if self.sel_annot == (index, xref):
            self.sel_annot = None
        self._refresh_page(index)
        self.statusBar().showMessage(f"{kind}を削除しました（ページ {index + 1}）。Ctrl+Z で戻せます", 5000)

    # ------------------------------------------------------------ 目次・しおり
    def refresh_toc(self):
        self.toc_tree.clear()
        if self.model.doc is None:
            return
        stack: list[tuple[int, QTreeWidgetItem]] = []
        for pos, (lvl, title, page) in enumerate(self.model.get_toc()):
            it = QTreeWidgetItem([title or "(無題)", str(page) if page > 0 else ""])
            it.setData(0, Qt.UserRole, pos)
            it.setData(0, Qt.UserRole + 1, page - 1)
            it.setToolTip(0, f"{title}  → ページ {page}")
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            if stack:
                stack[-1][1].addChild(it)
            else:
                self.toc_tree.addTopLevelItem(it)
            stack.append((lvl, it))
        self.toc_tree.expandAll()
        self._update_actions()

    def _on_toc_clicked(self, item: QTreeWidgetItem, _col: int):
        page = item.data(0, Qt.UserRole + 1)
        if page is not None and 0 <= page < self.model.page_count:
            self.goto(page)
        self._update_actions()

    def add_bookmark(self, index: int | None = None):
        if self.model.doc is None:
            return
        if not isinstance(index, int) or isinstance(index, bool):
            index = self.current
        if not (0 <= index < self.model.page_count):
            return
        title = self._ask_line("しおりを追加", f"ページ {index + 1} のしおり名:", f"ページ {index + 1}")
        if title is None or not title.strip():
            return
        try:
            self.model.add_bookmark(index, title.strip())
        except Exception as e:
            self._error(e)
            return
        self.refresh_toc()
        self.toc_dock.show()
        self._update_actions()
        self.statusBar().showMessage(f"しおり「{title.strip()}」を追加しました（PDF の目次に保存されます）", 5000)

    def _selected_toc_pos(self) -> int | None:
        it = self.toc_tree.currentItem()
        return None if it is None else it.data(0, Qt.UserRole)

    def rename_bookmark(self):
        pos = self._selected_toc_pos()
        if pos is None:
            return
        toc = self.model.get_toc()
        title = self._ask_line("名前の変更", "項目名:", toc[pos][1])
        if title is None or not title.strip():
            return
        try:
            self.model.rename_toc_entry(pos, title.strip())
        except Exception as e:
            self._error(e)
            return
        self.refresh_toc()
        self._update_actions()

    def remove_bookmark(self):
        pos = self._selected_toc_pos()
        if pos is None:
            return
        try:
            self.model.remove_toc_entry(pos)
        except Exception as e:
            self._error(e)
            return
        self.refresh_toc()
        self._update_actions()
        self.statusBar().showMessage("目次項目を削除しました。Ctrl+Z で戻せます", 5000)

    # ------------------------------------------------------------ 表示モード
    def set_display_mode(self, mode: str):
        if mode not in DISPLAY_MODES:
            return
        self.display_mode = mode
        a = self.mode_actions.get(mode)
        if a is not None and not a.isChecked():
            a.setChecked(True)
        bg = DISPLAY_MODES[mode][1]
        for v in self.views:
            v.set_background(bg)
            v.rendered_key = None
        self._render_visible()
        self._update_actions()

    def _set_auto_toc(self, on: bool):
        self.auto_toc_panel = bool(on)

    def _popup_menu(self, menu: QMenu, gpos: QPoint):
        """メニューを表示して選択を待つ（テストではここを差し替える）。"""
        menu.exec(gpos)

    def remove_stamp(self, index: int, xref: int):
        try:
            self.model.remove_stamp(index, xref)
        except Exception as e:
            self._error(e)
            return
        self.refresh_thumbs(select=[index], keep_view=True)
        self.statusBar().showMessage(f"押印を取り消しました（ページ {index + 1}）。Ctrl+Z で戻せます", 5000)

    # ------------------------------------------------------------ 表示
    def refresh_thumbs(self, select: list[int] | None = None, keep_view: bool = False):
        vbar, hbar = self.scroll.verticalScrollBar().value(), self.scroll.horizontalScrollBar().value()
        tbar = self.thumbs.verticalScrollBar().value()
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        if self.model.doc is not None:
            for i in range(self.model.page_count):
                pix = self.model.render(i, max_width=THUMB_W)
                it = QListWidgetItem(QIcon(QPixmap.fromImage(pix_to_qimage(pix))), str(i + 1))
                it.setData(Qt.UserRole, i)
                it.setTextAlignment(Qt.AlignHCenter)
                it.setFlags(it.flags() & ~Qt.ItemIsDropEnabled)
                self.thumbs.addItem(it)
        select = [i for i in (select or []) if 0 <= i < self.thumbs.count()]
        if select:
            for i in select:
                self.thumbs.item(i).setSelected(True)
            self.thumbs.setCurrentRow(select[0], QItemSelectionModel.Current)
            self.current = select[0]
        else:
            self.current = -1
        self.thumbs.blockSignals(False)
        self._words_cache.clear()
        self.sel_page = None; self.sel_range = None; self.selected_text = ""
        self._set_selection_actions(False)
        self._rebuild_views()
        self.refresh_toc()
        self._update_actions()
        if keep_view:
            self.scroll.verticalScrollBar().setValue(vbar)
            self.scroll.horizontalScrollBar().setValue(hbar)
            self.thumbs.verticalScrollBar().setValue(tbar)
        elif self.current >= 0:
            self._scroll_to_page(self.current)
        self._render_visible()
        self._drag = None
        self._update_annot_box()
        self.a_del_annot.setEnabled(self.sel_annot is not None)
        if getattr(self, "stamp_mode", False):
            self._refresh_ghost()

    def _rebuild_views(self):
        """ページ数ぶんの PageView を作り直す（描画はしない）。"""
        for v in self.views:
            self.pages_layout.removeWidget(v)
            v.deleteLater()
        self.views = []
        n = self.model.page_count if self.model.doc is not None else 0
        self.empty_label.setVisible(n == 0)
        for i in range(n):
            v = PageView(i)
            v.set_background(DISPLAY_MODES[self.display_mode][1])
            v.set_note_mode(self.note_mode)
            v.set_text_mode(self.write_tool == "text")
            v.set_draw_mode(self.write_tool in DRAW_TOOLS)
            v.click_released.connect(self._on_click_released)
            v.clicked.connect(self._on_page_clicked)
            v.context_requested.connect(self._on_page_context)
            v.hovered.connect(self._on_page_hovered)
            v.drag_started.connect(self._on_drag_started)
            v.drag_moved.connect(self._on_drag_moved)
            v.drag_finished.connect(self._on_drag_finished)
            self.pages_layout.addWidget(v)
            self.views.append(v)
        self._layout_pages()

    def _view_scale(self, index: int) -> float:
        """ページ index の 1pt あたりの論理ピクセル数。"""
        if self.model.doc is None:
            return 1.0
        if self.fit_width:
            avail = max(100, self.scroll.viewport().width() - 2 * PAGE_GAP)
            return avail / (self.model.doc[index].rect.width or 1)
        return self.zoom

    def _layout_pages(self):
        """各ページの表示サイズを決めてプレースホルダにする（描画は _render_visible で遅延）。"""
        for v in self.views:
            r = self.model.doc[v.index].rect
            k = self._view_scale(v.index)
            v.setFixedSize(max(1, int(r.width * k)), max(1, int(r.height * k)))
            v.rendered_key = None
            v.setPixmap(QPixmap())
        self._resize_pages_widget()
        self._render_visible()

    def _resize_pages_widget(self):
        """ページ全体を収める大きさ（最低でもビューポートの大きさ）にする。resize は同期なのでスクロール範囲がすぐ更新される。"""
        vp = self.scroll.viewport()
        if self.views:
            cw = max(v.width() for v in self.views) + 2 * PAGE_GAP
            ch = sum(v.height() for v in self.views) + (len(self.views) + 1) * PAGE_GAP
        else:
            cw, ch = 0, 0
        self.pages_widget.resize(max(cw, vp.width()), max(ch, vp.height()))

    def _page_top(self, index: int) -> int:
        """ページ index の上端の y（pages_widget 座標）。"""
        y = PAGE_GAP
        for v in self.views[:index]:
            y += v.height() + PAGE_GAP
        return y

    def _render_visible(self):
        """表示範囲（前後 1 画面ぶんの余裕込み）のページだけ描画し、離れたページの画像は捨てる。"""
        if not self.views:
            return
        vbar = self.scroll.verticalScrollBar()
        y0, vh = vbar.value(), max(1, self.scroll.viewport().height())
        lo, hi = y0 - vh, y0 + 2 * vh
        dpr = self.devicePixelRatioF()
        y = PAGE_GAP
        for v in self.views:
            top, bottom = y, y + v.height()
            y = bottom + PAGE_GAP
            if bottom < lo or top > hi:
                if v.rendered_key is not None:
                    v.rendered_key = None
                    v.setPixmap(QPixmap())
                continue
            k = self._view_scale(v.index)
            key = (round(k, 4), dpr)
            if v.rendered_key == key:
                continue
            pix = self.model.render(v.index, scale=k * dpr)
            qp = QPixmap.fromImage(apply_display_mode(pix, self.display_mode))
            qp.setDevicePixelRatio(dpr)
            v.setPixmap(qp)
            v.rendered_key = key
            v.set_field_rects([self._frac_rect(v.index, a["rect"]) for a in self.model.annots_on_page(v.index) if a.get("field")])

    def _on_scrolled(self):
        self._render_visible()
        if not self.views:
            return
        if self._pin_current is not None:
            # 直前の goto のスクロールでは現在ページを変えない（末尾の短いページは上端まで来られないため）
            self._pin_current = None
            return
        # 一番大きく見えているページを現在ページにする
        vbar = self.scroll.verticalScrollBar()
        y0, vh = vbar.value(), max(1, self.scroll.viewport().height())
        best, best_vis = self.current, -1
        y = PAGE_GAP
        for v in self.views:
            top, bottom = y, y + v.height()
            y = bottom + PAGE_GAP
            vis = min(bottom, y0 + vh) - max(top, y0)
            if vis > best_vis:
                best, best_vis = v.index, vis
        if best != self.current:
            self._set_current(best)

    def _set_current(self, index: int):
        """スクロールやクリックで現在ページが変わったとき、サムネイルの選択を追従させる（スクロールはしない）。"""
        if index == self.current:
            return
        self.current = index
        self.thumbs.blockSignals(True)
        self.thumbs.clearSelection()
        self.thumbs.setCurrentRow(index)
        self.thumbs.blockSignals(False)
        self.thumbs.scrollToItem(self.thumbs.item(index))
        self._update_actions()

    def _scroll_to_page(self, index: int):
        if not (0 <= index < len(self.views)):
            return
        self._pin_current = index
        self.scroll.verticalScrollBar().setValue(max(0, self._page_top(index) - PAGE_GAP))
        self._scroll_timer.start(30)   # 値が変わらなかった場合もピンを消費させる
        self._render_visible()

    def _on_current_changed(self, row: int):
        """サムネイルのクリック・PgUp/PgDn などで現在ページが選ばれた → そのページへスクロール。"""
        if row < 0:
            return
        self.current = row
        self._scroll_to_page(row)
        self._update_actions()

    def goto(self, index: int):
        if self.model.doc is None:
            return
        index = max(0, min(index, self.model.page_count - 1))
        self.thumbs.clearSelection()
        if self.thumbs.currentRow() == index:
            self._on_current_changed(index)   # currentRowChanged が出ないので直接
            self.thumbs.item(index).setSelected(True)
        else:
            self.thumbs.setCurrentRow(index)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_width:
            self._resize_timer.start(80)
        else:
            self._resize_pages_widget()

    def _relayout_keep_page(self):
        """ページの大きさが変わっても（幅合わせの再計算・ズーム変更）、今見ているページと
        そのページ内の相対位置を保ったまま並べ直す。"""
        vbar = self.scroll.verticalScrollBar()
        idx = self.current if 0 <= self.current < len(self.views) else -1
        frac = 0.0
        if idx >= 0 and self.views[idx].height() > 0:
            frac = (vbar.value() - (self._page_top(idx) - PAGE_GAP)) / self.views[idx].height()
        self._layout_pages()
        if idx >= 0:
            self._pin_current = idx   # このスクロールでは現在ページを変えない
            vbar.setValue(max(0, int(self._page_top(idx) - PAGE_GAP + frac * self.views[idx].height())))
            self._scroll_timer.start(30)
        self._render_visible()

    def _relayout_keep_position(self):
        self._relayout_keep_page()
        self._update_actions()
        self._refresh_ghost()

    def set_zoom(self, z: float):
        self.fit_width = False
        self.zoom = z
        self._relayout_keep_position()

    def zoom_fit(self):
        self.fit_width = True
        self._relayout_keep_position()

    def zoom_step(self, direction: int):
        if self.fit_width and self.view is not None and self.model.doc is not None:
            w = self.model.doc[self.current].rect.width or 1
            self.zoom = self.view.width() / w
        steps = [z for z in ZOOM_STEPS if (z > self.zoom + 1e-6 if direction > 0 else z < self.zoom - 1e-6)]
        if steps:
            self.set_zoom(steps[0] if direction > 0 else steps[-1])

    def eventFilter(self, obj, event):
        if obj is self.scroll.viewport():
            if event.type() == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
                self.zoom_step(1 if event.angleDelta().y() > 0 else -1)
                return True
            if event.type() == QEvent.Resize and self.views:
                # 分割バーのドラッグやドックの開閉で表示領域の幅が変わったら、幅合わせを計算し直す
                if self.fit_width:
                    self._resize_timer.start(80)
                else:
                    self._resize_pages_widget()
        return super().eventFilter(obj, event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.zoom_step(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)

    # ------------------------------------------------------------ D&D（ファイル）
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        pdfs = [p for p in paths if p.lower().endswith(".pdf")]
        imgs = [p for p in paths if os.path.splitext(p)[1].lower() in IMAGE_EXTS]
        if not paths:
            return
        if self.model.doc is None and pdfs:
            self.open_file(pdfs[0])
            rest = pdfs[1:] + imgs
            if rest:
                self.thumbs.clearSelection()  # 開いた直後は 1 ページ目が選択されているので、残りは末尾に連結する
                self.insert_files(rest)
        else:
            self.insert_files(pdfs + imgs)
        event.acceptProposedAction()


def main():
    if "--selftest" in sys.argv:
        # 配布用 exe の自己診断（CI 用）。画面を出さずに書き込み機能を試し、結果をファイルに書いて終了する
        import selftest
        i = sys.argv.index("--selftest")
        sys.exit(selftest.run(sys.argv[i + 1] if len(sys.argv) > i + 1 else None))
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    w = MainWindow()
    w.show()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        w.open_file(sys.argv[1])
    _write_started_marker()
    sys.exit(app.exec())


def _write_started_marker():
    """PDFPE_STARTED_MARKER にファイル名が入っていれば、画面を出せた印としてバージョンを書く
    （CI で「インストーラーが更新後にアプリを起動し直せるか」を確かめるためのもの。普段は何もしない）。"""
    marker = os.environ.get("PDFPE_STARTED_MARKER")
    if not marker:
        return
    try:
        with open(marker, "w", encoding="utf-8") as f:
            # 配布物にアイコンを入れ忘れると起動はできても絵が出ないので、読めなかった数も書く
            f.write(f"{__version__} missing_icons={len(ribbon.missing_icons)}")
    except OSError:
        pass


if __name__ == "__main__":
    main()
