"""PDF Page Editor - PDF ビューア + ページ編集（並べ替え/回転/削除/挿入/抽出）+ 保存。

PySide6 + PyMuPDF。Windows では build_windows.bat で単一 exe にできる。
"""
from __future__ import annotations

import os
import sys

import pymupdf as fitz
from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QPointF, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QDragEnterEvent, QDropEvent, QIcon, QImage,
                           QKeySequence, QPainter, QPixmap)
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QScrollArea, QSplitter, QStatusBar, QToolBar,
                               QVBoxLayout, QWidget)

from pdfmodel import IMAGE_EXTS, PdfModel
from stamp_ui import StampDock, png_to_qpixmap
from stamps import MM, StampLibrary, random_tilt

APP_NAME = "PDF Page Editor"
__version__ = "1.1.0"
THUMB_W = 140
PAGE_GAP = 12           # 連続表示でのページ間の余白 (px)
ZOOM_STEPS = [0.5, 0.67, 0.8, 1.0, 1.25, 1.5, 2.0, 3.0]
PDF_FILTER = "PDF ファイル (*.pdf)"
INSERT_FILTER = "PDF / 画像 (*.pdf *.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;PDF (*.pdf);;画像 (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)"


def resource_path(*parts: str) -> str:
    """同梱ファイルのパス。PyInstaller で exe 化したときは展開先 (_MEIPASS) を見る。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def app_icon() -> QIcon:
    return QIcon(resource_path("assets", "icon.ico"))


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

    def set_stamp_mode(self, on: bool, ghost: QPixmap | None = None):
        self.stamp_mode = on
        self.ghost = ghost if on else None
        self.ghost_pos = None
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
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

    def mouseMoveEvent(self, e):
        if self.stamp_mode:
            self.ghost_pos = e.position()
            self.update()
        super().mouseMoveEvent(e)

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
        if self.stamp_mode and e.button() == Qt.LeftButton:
            f = self._frac(e.position())
            if f is not None:
                self.clicked.emit(self.index, f)
                return
        super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        f = self._frac(e.pos())
        if f is not None:
            self.context_requested.emit(self.index, f, e.globalPos())
            e.accept()
            return
        super().contextMenuEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.stamp_mode and self.ghost and self.ghost_pos is not None:
            p = QPainter(self)
            p.setOpacity(0.6)
            gw = self.ghost.width() / self.ghost.devicePixelRatio()
            gh = self.ghost.height() / self.ghost.devicePixelRatio()
            p.drawPixmap(int(self.ghost_pos.x() - gw / 2), int(self.ghost_pos.y() - gh / 2), self.ghost)
            p.end()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.model = PdfModel()
        self.zoom = 1.0
        self.fit_width = True
        self.current = -1
        self.stamp_mode = False
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1280, 820)
        self.setAcceptDrops(True)
        self._build_ui()
        self._update_actions()
        self._restore_layout()

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

        self.setStatusBar(QStatusBar())
        self.page_label = QLabel("")
        self.statusBar().addPermanentWidget(self.page_label)

        tb = QToolBar("メイン")
        tb.setObjectName("mainToolBar")
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setMovable(True)   # 左端のグリップをドラッグして上下左右の辺に移動できる
        tb.setAllowedAreas(Qt.AllToolBarAreas)
        tb.setToolTip("右クリックで上下左右の辺に移動できます（左端のドラッグでも可）")
        self.addToolBar(tb)
        self.toolbar = tb
        fmenu = self.menuBar().addMenu("ファイル(&F)")
        emenu = self.menuBar().addMenu("編集(&E)")
        vmenu = self.menuBar().addMenu("表示(&V)")
        smenu = self.menuBar().addMenu("印鑑(&S)")
        hmenu = self.menuBar().addMenu("ヘルプ(&H)")

        def act(text, slot, key=None, menu=None, toolbar=True, icon=None, tip=None):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
            if tip:
                t = f"{tip}  [{key}]" if key else tip
                a.setToolTip(t)
                a.setStatusTip(t)
            if icon:
                a.setIcon(QIcon.fromTheme(icon))
            a.triggered.connect(slot)
            if menu is not None:
                menu.addAction(a)
            if toolbar:
                tb.addAction(a)
            return a

        self.a_open = act("開く", self.open_dialog, "Ctrl+O", fmenu, icon="document-open", tip="PDF ファイルを開く。ウィンドウへのドラッグ&ドロップでも開けます")
        self.a_save = act("保存", self.save, "Ctrl+S", fmenu, icon="document-save", tip="編集内容を元のファイルに上書き保存")
        self.a_saveas = act("名前を付けて保存", self.save_as, "Ctrl+Shift+S", fmenu, icon="document-save-as", tip="別名の PDF として保存（元ファイルは変更しない）")
        fmenu.addSeparator()
        self.a_extract = act("選択ページを抽出...", self.extract_selected, None, fmenu, toolbar=False, tip="選択中のページだけを新しい PDF として書き出す")
        fmenu.addSeparator()
        act("終了", self.close, "Ctrl+Q", fmenu, toolbar=False)

        tb.addSeparator()
        self.a_undo = act("元に戻す", self.undo, "Ctrl+Z", emenu, icon="edit-undo", tip="直前の操作（並べ替え・回転・削除・挿入・押印）を取り消す")
        self.a_redo = act("やり直し", self.redo, "Ctrl+Y", emenu, icon="edit-redo", tip="取り消した操作をもう一度実行する")
        emenu.addSeparator()
        tb.addSeparator()
        self.a_insert = act("ページ挿入", self.insert_dialog, "Ctrl+I", emenu, icon="list-add", tip="他の PDF や画像(PNG/JPG など)を、選択ページの後ろにページとして挿入")
        self.a_delete = act("削除", self.delete_selected, "Delete", emenu, icon="edit-delete", tip="選択中のページを削除（複数選択可）")
        self.a_rotl = act("左回転", lambda: self.rotate(-90), "Ctrl+L", emenu, icon="object-rotate-left", tip="選択ページを反時計回りに 90° 回転")
        self.a_rotr = act("右回転", lambda: self.rotate(90), "Ctrl+R", emenu, icon="object-rotate-right", tip="選択ページを時計回りに 90° 回転")
        self.a_up = act("前へ移動", lambda: self.move(-1), "Ctrl+Up", emenu, icon="go-up", tip="選択ページを 1 つ前へ移動。サムネイルのドラッグでも並べ替えできます")
        self.a_down = act("後へ移動", lambda: self.move(1), "Ctrl+Down", emenu, icon="go-down", tip="選択ページを 1 つ後ろへ移動")
        emenu.addSeparator()
        act("全選択", self.thumbs.selectAll, "Ctrl+A", emenu, toolbar=False, tip="すべてのページを選択")

        tb.addSeparator()
        self.a_stamp = act("押印モード", self._toggle_stamp_mode, "F2", smenu, icon="insert-image", tip="ON にしてページ上をクリックすると、右パネルで選んだ印鑑を押します。Esc で終了。押した印影は右クリックで取り消せます")
        self.a_stamp.setCheckable(True)
        a_dock = act("印鑑パネルを表示", lambda on: self.stamp_dock.setVisible(on), None, smenu, toolbar=False, tip="右側の電子印鑑パネルの表示/非表示")
        a_dock.setCheckable(True); a_dock.setChecked(True)
        self.stamp_dock.visibilityChanged.connect(a_dock.setChecked)
        # メニュー/ツールバーに載せないアクションは、ウィンドウに addAction しないとショートカットが効かない
        self.addAction(act("キャンセル（押印モード終了）", lambda: self._set_stamp_mode(False), "Esc", None, toolbar=False))
        act("バージョン情報", self.show_about, None, hmenu, toolbar=False)

        tb.addSeparator()
        act("縮小", lambda: self.zoom_step(-1), "Ctrl+-", vmenu, icon="zoom-out", tip="表示を縮小。Ctrl+マウスホイールでも可")
        act("拡大", lambda: self.zoom_step(1), "Ctrl+=", vmenu, icon="zoom-in", tip="表示を拡大。Ctrl+マウスホイールでも可")
        act("幅に合わせる", self.zoom_fit, "Ctrl+0", vmenu, icon="zoom-fit-best", tip="ページ幅をウィンドウに合わせて表示")
        act("100%", lambda: self.set_zoom(1.0), "Ctrl+1", vmenu, toolbar=False, tip="原寸(100%)で表示")
        vmenu.addSeparator()
        act("前のページ", lambda: self.goto(self.current - 1), "PgUp", vmenu, toolbar=False, tip="前のページを表示")
        act("次のページ", lambda: self.goto(self.current + 1), "PgDown", vmenu, toolbar=False, tip="次のページを表示")

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(lambda: (self._layout_pages(), self._refresh_ghost()))

    # ------------------------------------------------------------ 状態
    def _update_actions(self):
        has = self.model.doc is not None and self.model.page_count > 0
        sel = bool(self.thumbs.selectedItems())
        for a in (self.a_save, self.a_saveas, self.a_insert):
            a.setEnabled(has)
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
                f"ズーム {'幅合わせ' if self.fit_width else f'{self.zoom:.0%}'}")
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

    def createPopupMenu(self) -> QMenu:
        """ツールバーやドックのタイトルを右クリックしたときのメニュー。ツールバーの置き場所を選べる。"""
        menu = super().createPopupMenu() or QMenu(self)
        menu.addSeparator()
        cur = self.toolBarArea(self.toolbar)
        for label, area in (("ツールバーを上へ", Qt.TopToolBarArea), ("ツールバーを下へ", Qt.BottomToolBarArea),
                            ("ツールバーを左へ", Qt.LeftToolBarArea), ("ツールバーを右へ", Qt.RightToolBarArea)):
            act = menu.addAction(label, lambda a=area: self.move_toolbar(a))
            act.setCheckable(True)
            act.setChecked(area == cur)
        return menu

    def move_toolbar(self, area):
        self.addToolBar(area, self.toolbar)
        self.toolbar.show()

    # ------------------------------------------------------------ レイアウトの保存/復元
    def _settings(self) -> QSettings:
        return QSettings("PdfPageEditor", "PdfPageEditor")

    def _save_layout(self):
        s = self._settings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("state", self.saveState())   # ツールバーの位置・ドックの表示状態

    def _restore_layout(self):
        s = self._settings()
        g = s.value("geometry")
        if g is not None:
            self.restoreGeometry(g)
        st = s.value("state")
        if st is not None:
            self.restoreState(st)

    def show_about(self):
        QMessageBox.about(self, f"{APP_NAME} について",
                          f"<b>{APP_NAME}</b> {__version__}<br><br>"
                          "PDF の閲覧・ページ編集・電子印鑑ツール。<br>"
                          "最新版: <a href='https://github.com/hidetako/pdf-page-editor/releases'>GitHub Releases</a><br><br>"
                          "使用ライブラリ: PyMuPDF (AGPL-3.0), PySide6 (LGPL-3.0)")

    def _error(self, e: Exception):
        QMessageBox.critical(self, APP_NAME, f"エラー: {e}")

    # ------------------------------------------------------------ ファイル
    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "PDF を開く", "", PDF_FILTER)
        if path:
            self.open_file(path)

    def open_file(self, path: str):
        if not self._confirm_discard():
            return
        try:
            self.model.open(path)
        except Exception as e:
            self._error(e)
            return
        self.current = -1
        self.refresh_thumbs(select=[0])

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
                n = self.model.insert_file(p, at)
                at += n
                total += n
        except Exception as e:
            self._error(e)
        if total:
            self.refresh_thumbs(select=list(range(first, first + total)))
        else:
            self._update_actions()

    def delete_selected(self):
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

    def _set_stamp_mode(self, on: bool):
        if on and (self.model.doc is None or self.stamp_dock_widget.current_stamp() is None):
            if self.model.doc is None:
                self.statusBar().showMessage("先に PDF を開いてください。", 3000)
            on = False
        self.stamp_mode = on
        self.a_stamp.blockSignals(True); self.a_stamp.setChecked(on); self.a_stamp.blockSignals(False)
        self.stamp_dock_widget.btn_mode.blockSignals(True); self.stamp_dock_widget.set_mode(on); self.stamp_dock_widget.btn_mode.blockSignals(False)
        self._refresh_ghost()
        if on:
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

    def _on_page_clicked(self, index: int, frac: QPointF):
        if not getattr(self, "stamp_mode", False) or self.model.doc is None:
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
        menu = QMenu(self)
        if xref is None:
            none = menu.addAction("ここに取り消せる押印はありません")
            none.setEnabled(False)
        else:
            menu.addAction("この押印を取り消す", lambda: self.remove_stamp(index, xref))
        if self.stamp_mode:
            menu.addSeparator()
            menu.addAction("押印モードを終了", lambda: self._set_stamp_mode(False))
        self._popup_menu(menu, gpos)

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
        self._rebuild_views()
        self._update_actions()
        if keep_view:
            self.scroll.verticalScrollBar().setValue(vbar)
            self.scroll.horizontalScrollBar().setValue(hbar)
            self.thumbs.verticalScrollBar().setValue(tbar)
        elif self.current >= 0:
            self._scroll_to_page(self.current)
        self._render_visible()
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
            v.clicked.connect(self._on_page_clicked)
            v.context_requested.connect(self._on_page_context)
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
            qp = QPixmap.fromImage(pix_to_qimage(pix))
            qp.setDevicePixelRatio(dpr)
            v.setPixmap(qp)
            v.rendered_key = key

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

    def _relayout_keep_position(self):
        vbar = self.scroll.verticalScrollBar()
        ratio = vbar.value() / vbar.maximum() if vbar.maximum() > 0 else 0.0
        self._layout_pages()
        vbar.setValue(int(ratio * vbar.maximum()))
        self._render_visible()
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
        if obj is self.scroll.viewport() and event.type() == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            self.zoom_step(1 if event.angleDelta().y() > 0 else -1)
            return True
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
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    w = MainWindow()
    w.show()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        w.open_file(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
