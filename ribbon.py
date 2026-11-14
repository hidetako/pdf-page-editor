"""Microsoft 365 風のリボン（タブ + タブごとのリボン）。

画面の上部を「タブの行」と「リボン（白いカード）」の 2 段にする部品。
- タブ: ファイル（メニューを開く）/ ホーム / 書き込み / 表示 / ヘルプ …。選んだタブは色付きの文字と下線で示す
- リボン: グループ（下に名前）ごとに、大きいボタン（アイコンの下に文字）や小さいボタン（縦に 3 つまで）を並べる
- アイコンは Fluent UI System Icons（MIT ライセンス、assets/icons）。実行時に色を付けて描く

ボタンは QAction を見て表示（有効/無効・チェック・ヒント）を合わせ、押すと QAction を実行する。
ショートカットはウィンドウ側で QAction を addAction して効かせる（表示していないタブのボタンでも効くように）。
"""
from __future__ import annotations

import os
import sys
import tempfile

from PySide6.QtCore import QByteArray, QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy, QStackedWidget, QStyleFactory, QToolButton,
                               QVBoxLayout, QWidget)

ACCENT = "#C7332A"           # アプリの色（アイコンの赤）。選んだタブの文字と下線に使う
INK = "#242424"              # アイコン・文字
INK_SOFT = "#616161"         # グループ名
INK_DISABLED = "#B0B0B0"
STRIP_BG = "#F3F3F3"         # タブの行とリボンのまわり
CARD_BG = "#FFFFFF"          # リボン本体
CARD_BORDER = "#E0E0E0"
HOVER_BG = "#F0F0F0"
PRESSED_BG = "#E0E0E0"
CHECKED_BG = "#E8E8E8"
CHECKED_BORDER = "#C8C8C8"

LARGE_ICON = 26
SMALL_ICON = 16
# 高さは決め打ちにしない: 文字の大きさ（PC の設定や画面の拡大率で変わる）から計算する。
# 決め打ちにすると、文字が大きい PC でグループ名やボタンの文字が切れる。


def large_button_height(fm: QFontMetrics) -> int:
    """大きいボタンの高さ: アイコン + 文字 2 行ぶん。"""
    return LARGE_ICON + 2 * fm.lineSpacing() + 12


def field_height(fm: QFontMetrics) -> int:
    """リボンに置く入力欄（コンボボックスなど）の高さ。3 段重ねて大きいボタンとほぼ同じ高さになるようにする。"""
    return max(20, min(fm.height() + 8, large_button_height(fm) // 3))

missing_icons: list[str] = []      # 読めなかったアイコン（配布物に入れ忘れたときに気付けるように）
_icon_cache: dict[tuple[str, str], QIcon] = {}


def resource_path(rel: str) -> str:
    """PyInstaller 同梱時は展開先、通常はこのファイルと同じフォルダを基準にする。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _render_svg(data: bytes, color: str, size: int) -> QPixmap | None:
    from PySide6.QtSvg import QSvgRenderer
    svg = data.replace(b"<svg ", b'<svg fill="' + color.encode("ascii") + b'" ', 1)
    renderer = QSvgRenderer(QByteArray(svg))
    if not renderer.isValid():
        return None
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def icon(name: str, color: str = INK) -> QIcon:
    """assets/icons/<name>.svg を、指定の色（無効のときは薄い灰色）で描いたアイコン。読めなければ空のアイコン。"""
    key = (name, color)
    if key in _icon_cache:
        return _icon_cache[key]
    ic = QIcon()
    try:
        with open(resource_path(os.path.join("assets", "icons", name + ".svg")), "rb") as f:
            data = f.read()
        for size in (16, 20, 24, 32, 48, 64):       # 高 DPI の画面でもぼけないよう、大きさをいくつか用意する
            normal, disabled = _render_svg(data, color, size), _render_svg(data, INK_DISABLED, size)
            if normal is None or disabled is None:
                raise ValueError("invalid svg")
            ic.addPixmap(normal, QIcon.Normal)
            ic.addPixmap(disabled, QIcon.Disabled)
    except Exception:
        if name not in missing_icons:
            missing_icons.append(name)
        ic = QIcon()
    _icon_cache[key] = ic
    return ic


def stamp_icon(size: int = 64) -> QIcon:
    """印鑑（赤い丸に「印」）のアイコン。Fluent のアイコンに無いので自前で描く。"""
    ic = QIcon()
    for mode, col in ((QIcon.Normal, QColor(ACCENT)), (QIcon.Disabled, QColor(INK_DISABLED))):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(col, size * 0.075))
        p.drawEllipse(QRectF(size * 0.10, size * 0.10, size * 0.80, size * 0.80))
        f = p.font(); f.setPixelSize(int(size * 0.50)); f.setBold(True); p.setFont(f)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "印")
        p.end()
        ic.addPixmap(pm, mode)
    return ic


def _arrow_images() -> dict[str, str]:
    """コンボボックスなどの矢印の画像を作ってパスを返す（スタイルシートは画像ファイルしか指定できないため）。
    作れなかったものは入れない（そのときは矢印なしで表示される）。"""
    out: dict[str, str] = {}
    try:
        d = os.path.join(tempfile.gettempdir(), "PdfPageEditor-ui")
        os.makedirs(d, exist_ok=True)
        for name in ("chevron_down", "chevron_up"):
            with open(resource_path(os.path.join("assets", "icons", name + ".svg")), "rb") as f:
                data = f.read()
            ok = True
            for suffix, size in (("", 12), ("@2x", 24)):      # @2x は高 DPI の画面用
                pm = _render_svg(data, INK_SOFT, size)
                ok = ok and pm is not None and pm.save(os.path.join(d, f"{name}{suffix}.png"), "PNG")
            if ok:
                out[name] = os.path.join(d, name + ".png").replace("\\", "/")
    except Exception:
        pass
    return out


def style_sheet(fm: QFontMetrics) -> str:
    arrows = _arrow_images()
    FIELD_H = field_height(fm)
    title_px = max(10, round(fm.height() * 0.70))
    down = f'image: url("{arrows["chevron_down"]}");' if "chevron_down" in arrows else ""
    up = f'image: url("{arrows["chevron_up"]}");' if "chevron_up" in arrows else ""
    return f"""
QWidget#ribbonBar {{ background: {STRIP_BG}; }}
QFrame#ribbonBody {{ background: {CARD_BG}; border: 1px solid {CARD_BORDER}; border-radius: 8px; }}
QWidget#ribbonPage, QScrollArea#ribbonStrip, QWidget#ribbonGroup, QScrollArea#ribbonStrip > QWidget > QWidget {{ background: transparent; }}
QLabel#ribbonGroupTitle {{ color: {INK_SOFT}; font-size: {title_px}px; padding: 0; }}
QFrame#ribbonSeparator {{ background: {CARD_BORDER}; border: none; }}
QToolButton#ribbonLarge, QToolButton#ribbonSmall, QToolButton#ribbonSwatch, QToolButton#ribbonScroll {{
    border: 1px solid transparent; border-radius: 4px; color: {INK}; background: transparent; }}
QToolButton#ribbonLarge {{ padding: 3px 5px 2px 5px; }}
QToolButton#ribbonSmall {{ padding: 2px 8px 2px 4px; }}
QToolButton#ribbonSwatch {{ padding: 2px; }}
QToolButton#ribbonLarge:hover, QToolButton#ribbonSmall:hover, QToolButton#ribbonSwatch:hover, QToolButton#ribbonScroll:hover {{ background: {HOVER_BG}; }}
QToolButton#ribbonLarge:pressed, QToolButton#ribbonSmall:pressed, QToolButton#ribbonSwatch:pressed {{ background: {PRESSED_BG}; }}
QToolButton#ribbonLarge:checked, QToolButton#ribbonSmall:checked, QToolButton#ribbonSwatch:checked {{
    background: {CHECKED_BG}; border: 1px solid {CHECKED_BORDER}; }}
QToolButton#ribbonLarge:disabled, QToolButton#ribbonSmall:disabled {{ color: {INK_DISABLED}; }}
QToolButton#ribbonScroll {{ background: {CARD_BG}; border: 1px solid {CARD_BORDER}; }}
QLabel#ribbonFieldLabel {{ color: {INK}; }}
QFrame#ribbonBody QComboBox, QFrame#ribbonBody QDoubleSpinBox {{
    background: {CARD_BG}; color: {INK}; border: 1px solid #D1D1D1; border-bottom: 1px solid #8A8A8A; border-radius: 4px;
    padding: 0 4px; min-height: {FIELD_H - 2}px; max-height: {FIELD_H - 2}px; }}
QFrame#ribbonBody QComboBox:hover, QFrame#ribbonBody QDoubleSpinBox:hover {{ background: #FAFAFA; }}
QFrame#ribbonBody QComboBox:focus, QFrame#ribbonBody QDoubleSpinBox:focus {{ border-bottom: 2px solid {ACCENT}; }}
QFrame#ribbonBody QComboBox:disabled, QFrame#ribbonBody QDoubleSpinBox:disabled {{ color: {INK_DISABLED}; }}
QFrame#ribbonBody QComboBox::drop-down {{ border: none; width: 20px; }}
QFrame#ribbonBody QComboBox::down-arrow {{ {down} width: 12px; height: 12px; }}
QFrame#ribbonBody QDoubleSpinBox::up-button, QFrame#ribbonBody QDoubleSpinBox::down-button {{ border: none; width: 16px; background: transparent; }}
QFrame#ribbonBody QDoubleSpinBox::up-button:hover, QFrame#ribbonBody QDoubleSpinBox::down-button:hover {{ background: {HOVER_BG}; }}
QFrame#ribbonBody QDoubleSpinBox::up-arrow {{ {up} width: 9px; height: 9px; }}
QFrame#ribbonBody QDoubleSpinBox::down-arrow {{ {down} width: 9px; height: 9px; }}
QFrame#ribbonBody QCheckBox {{ color: {INK}; spacing: 5px; }}
QFrame#ribbonBody QCheckBox:disabled {{ color: {INK_DISABLED}; }}
QMenu#ribbonMenu {{ background: {CARD_BG}; border: 1px solid {CARD_BORDER}; border-radius: 8px; padding: 6px; }}
QMenu#ribbonMenu::item {{ padding: 7px 28px 7px 10px; border-radius: 4px; color: {INK}; }}
QMenu#ribbonMenu::item:selected {{ background: {HOVER_BG}; }}
QMenu#ribbonMenu::item:disabled {{ color: {INK_DISABLED}; }}
QMenu#ribbonMenu::separator {{ height: 1px; background: {CARD_BORDER}; margin: 5px 6px; }}
QMenu#ribbonMenu::icon {{ padding-left: 8px; }}
"""


class RibbonButton(QToolButton):
    """QAction に連動するボタン。large=True はアイコンの下に文字、False はアイコンの右に文字。"""

    def __init__(self, action: QAction, label: str | None = None, large: bool = True, parent: QWidget | None = None):
        super().__init__(parent)
        self.action_ = action
        self._label = label
        self.setObjectName("ribbonLarge" if large else "ribbonSmall")
        self.setFocusPolicy(Qt.NoFocus)
        self.setAutoRaise(True)
        if large:
            self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            self.setIconSize(QSize(LARGE_ICON, LARGE_ICON))
            self.setMinimumWidth(46)
        else:
            self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.setIconSize(QSize(SMALL_ICON, SMALL_ICON))
            self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.clicked.connect(self._on_clicked)
        action.changed.connect(self.sync)
        self.sync()

    def sync(self):
        a = self.action_
        self.setText(self._label or a.text())
        self.setIcon(a.icon())
        self.setEnabled(a.isEnabled())
        self.setVisible(a.isVisible())
        self.setCheckable(a.isCheckable())
        self.setChecked(a.isChecked())
        self.setToolTip(a.toolTip())
        self.setStatusTip(a.statusTip())
        self._fit_width()

    def _fit_width(self):
        """文字が「名…保存」のように省略されない幅を最小幅にする（窓が狭いときは、リボン全体を左右に送って見る）。"""
        self.ensurePolished()
        large = self.objectName() == "ribbonLarge"
        if large:
            # 1 行の名前でも 2 行の名前でも同じ高さにそろえる。高さは、2 行の名前を入れたときに実際に必要な大きさを測って決める
            text = self.text()
            self.setMinimumHeight(0); self.setMaximumHeight(16777215)
            self.setText("あ\nあ")
            h = max(self.sizeHint().height(), large_button_height(self.fontMetrics()))
            self.setText(text)
            self.setFixedHeight(h)
        self.setMinimumWidth(0)
        self.setMinimumWidth(max(46 if large else 0, self.sizeHint().width()))

    def showEvent(self, e):
        self._fit_width()
        super().showEvent(e)

    def changeEvent(self, e):
        if e.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._fit_width()
        super().changeEvent(e)

    def _on_clicked(self):
        self.action_.trigger()
        self.sync()        # 排他のグループで「選択中をもう一度押した」ときなど、QAction が変わらない場合もボタンを合わせ直す


class RibbonCheck(QCheckBox):
    """QAction（チェック式の設定）に連動するチェックボックス。"""

    def __init__(self, action: QAction, label: str | None = None, parent: QWidget | None = None):
        super().__init__(label or action.text(), parent)
        self.action_ = action
        self.setFocusPolicy(Qt.NoFocus)
        self.clicked.connect(self._on_clicked)
        action.changed.connect(self.sync)
        self.sync()

    def sync(self):
        a = self.action_
        self.setEnabled(a.isEnabled())
        self.setChecked(a.isChecked())
        self.setToolTip(a.toolTip())
        self.setStatusTip(a.statusTip())

    def _on_clicked(self):
        self.action_.trigger()
        self.sync()


class RibbonGroup(QWidget):
    """リボンの 1 グループ。上に部品、下にグループ名。"""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ribbonGroup")
        self.buttons: dict[QAction, RibbonButton] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 2)
        outer.setSpacing(1)
        self._row = QHBoxLayout()
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(1)
        outer.addLayout(self._row, 1)
        self.title = QLabel(title, self)
        self.title.setObjectName("ribbonGroupTitle")
        self.title.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        outer.addWidget(self.title)

    def add_large(self, action: QAction, label: str | None = None) -> RibbonButton:
        b = RibbonButton(action, label, True, self)
        self._row.addWidget(b, 0, Qt.AlignTop)
        self.buttons[action] = b
        return b

    def add_column(self, items: list) -> QWidget:
        """小さい部品を縦に並べた 1 列。items は QAction（小さいボタンになる）か、(QAction, 表示名) か、QWidget。"""
        col = QWidget(self)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(0)
        for it in items:
            if isinstance(it, QWidget):
                lay.addWidget(it, 0, Qt.AlignLeft | Qt.AlignVCenter)
                continue
            action, label = it if isinstance(it, tuple) else (it, None)
            b = RibbonButton(action, label, False, col)
            lay.addWidget(b, 0, Qt.AlignLeft)
            self.buttons[action] = b
        lay.addStretch(1)
        self._row.addWidget(col, 0, Qt.AlignTop)
        return col

    def add_widget(self, w: QWidget):
        self._row.addWidget(w, 0, Qt.AlignTop)


class RibbonPage(QWidget):
    """1 つのタブのリボン。グループを左から並べる。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ribbonPage")
        self.groups: list[RibbonGroup] = []
        self._separators: dict[RibbonGroup, QFrame] = {}
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 2, 4, 2)
        self._lay.setSpacing(0)
        self._lay.addStretch(1)

    def add_group(self, title: str) -> RibbonGroup:
        g = RibbonGroup(title, self)
        n = self._lay.count() - 1           # 末尾の伸び代の手前に入れる
        if self.groups:
            sep = QFrame(self)
            sep.setObjectName("ribbonSeparator")
            sep.setFixedWidth(1)
            holder = QWidget(self)
            hl = QVBoxLayout(holder)
            hl.setContentsMargins(3, 8, 3, 8)
            hl.addWidget(sep)
            self._lay.insertWidget(n, holder)
            self._separators[g] = holder
            n += 1
        self._lay.insertWidget(n, g)
        self.groups.append(g)
        return g

    def set_group_visible(self, group: RibbonGroup, on: bool):
        group.setVisible(on)
        sep = self._separators.get(group)
        if sep is not None:
            sep.setVisible(on)

    def button(self, action: QAction) -> RibbonButton | None:
        for g in self.groups:
            if action in g.buttons:
                return g.buttons[action]
        return None


class _Strip(QScrollArea):
    """リボンが窓より長いとき、左右の「‹ ›」ボタンやホイールで送れるようにする入れ物。"""

    def __init__(self, page: RibbonPage, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ribbonStrip")
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)
        self.setWidget(page)
        self.viewport().setAutoFillBackground(False)
        self._left = self._arrow("chevron_left", -1)
        self._right = self._arrow("chevron_right", 1)
        bar = self.horizontalScrollBar()
        bar.rangeChanged.connect(lambda *_: self._update_arrows())
        bar.valueChanged.connect(lambda *_: self._update_arrows())

    def _arrow(self, icon_name: str, direction: int) -> QToolButton:
        b = QToolButton(self)
        b.setObjectName("ribbonScroll")
        b.setIcon(icon(icon_name))
        b.setIconSize(QSize(14, 14))
        b.setFocusPolicy(Qt.NoFocus)
        b.setAutoRepeat(True)
        b.setToolTip("リボンの続きを表示")
        b.clicked.connect(lambda: self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + direction * 160))
        b.hide()
        return b

    def _update_arrows(self):
        bar = self.horizontalScrollBar()
        h = self.height()
        self._left.setGeometry(0, 0, 20, h)
        self._right.setGeometry(self.width() - 20, 0, 20, h)
        self._left.setVisible(bar.value() > bar.minimum())
        self._right.setVisible(bar.value() < bar.maximum())
        self._left.raise_(); self._right.raise_()

    def sizeHint(self) -> QSize:
        hint = self.widget().sizeHint()
        return QSize(hint.width(), hint.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(60, self.widget().minimumSizeHint().height())      # 幅は縮められる（左右に送って見る）。高さは中身ぶん必要

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_arrows()

    def wheelEvent(self, e):
        bar = self.horizontalScrollBar()
        if bar.maximum() > bar.minimum():
            delta = e.angleDelta().y() or e.angleDelta().x()
            bar.setValue(bar.value() - delta // 2)
            e.accept()
            return
        super().wheelEvent(e)


class _Tab(QToolButton):
    """タブの見出し。選ぶと色付きの太字になり、下に短い線が付く。"""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.ArrowCursor)
        self._hover = False

    def _fonts(self) -> tuple[QFont, QFont]:
        normal = QFont(self.font())
        bold = QFont(normal)
        bold.setWeight(QFont.DemiBold)
        return normal, bold

    def sizeHint(self) -> QSize:
        _normal, bold = self._fonts()
        fm = QFontMetrics(bold)         # 太字の幅で決めておく（選んだときに幅が変わって、隣のタブが動かないように）
        return QSize(fm.horizontalAdvance(self.text()) + 26, fm.height() + 16)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def enterEvent(self, e):
        self._hover = True; self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False; self.update(); super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        normal, bold = self._fonts()
        r = QRectF(self.rect()).adjusted(2, 3, -2, -3)
        if self.isEnabled() and (self._hover or self.isDown()) and not self.isChecked():
            p.setPen(Qt.NoPen); p.setBrush(QColor("#E6E6E6" if not self.isDown() else "#DADADA"))
            p.drawRoundedRect(r, 4, 4)
        checked = self.isChecked()
        p.setFont(bold if checked else normal)
        p.setPen(QColor(INK_DISABLED) if not self.isEnabled() else QColor(ACCENT if checked else "#424242"))
        p.drawText(self.rect().adjusted(0, 0, 0, -2), Qt.AlignCenter, self.text())
        if checked:
            w = QFontMetrics(bold).horizontalAdvance(self.text())
            bar = QRectF((self.width() - w) / 2, self.height() - 5, w, 3)
            p.setPen(Qt.NoPen); p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(bar, 1.5, 1.5)
        p.end()


class RibbonBar(QWidget):
    """タブの行 + リボン本体。QMainWindow.setMenuWidget() でメニューバーの位置に置く。"""

    tab_changed = Signal(str)       # 選んだタブのキー

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ribbonBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # 部品の下地の描き方を Fusion にそろえる。Windows 標準の描き方（版によって違う）に任せると、
        # スタイルシートで指定していない部分（チェックボックスなど）の見た目や大きさが PC ごとに変わってしまう
        self._base_style = QStyleFactory.create("Fusion")
        if self._base_style is not None:
            self.setStyle(self._base_style)
        self.setStyleSheet(style_sheet(self.fontMetrics()))
        self.pages: dict[str, RibbonPage] = {}
        self.tabs: dict[str, _Tab] = {}
        self._strips: dict[str, _Strip] = {}
        self._current = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 2, 8, 6)
        outer.setSpacing(2)
        self._tab_row = QHBoxLayout()
        self._tab_row.setContentsMargins(2, 0, 2, 0)
        self._tab_row.setSpacing(2)
        self._tab_row.addStretch(1)
        outer.addLayout(self._tab_row)
        self.body = QFrame(self)
        self.body.setObjectName("ribbonBody")
        self.body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)     # 高さは、いちばん背の高いタブの中身に合わせる（どのタブでも同じ）
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(2, 2, 2, 2)
        self.stack = QStackedWidget(self.body)
        bl.addWidget(self.stack)
        outer.addWidget(self.body)
        self.file_tab: _Tab | None = None
        self.file_menu: QMenu | None = None

    # ------------------------------------------------------------ 組み立て
    def add_file_menu(self, title: str = "ファイル") -> QMenu:
        """左端の「ファイル」。押すとメニューを開く（365 の Backstage の代わり）。"""
        menu = QMenu(self)
        menu.setObjectName("ribbonMenu")
        menu.setAttribute(Qt.WA_TranslucentBackground, True)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        tab = _Tab(title, self)
        tab.setCheckable(False)
        tab.setToolTip("開く・保存・印刷など、ファイルの操作")
        tab.clicked.connect(self.show_file_menu)
        self._tab_row.insertWidget(0, tab)
        self.file_tab, self.file_menu = tab, menu
        return menu

    def show_file_menu(self):
        if self.file_tab is not None and self.file_menu is not None:
            self.file_menu.popup(self.file_tab.mapToGlobal(QPoint(0, self.file_tab.height())))

    def add_tab(self, key: str, title: str, page: RibbonPage | None = None) -> RibbonPage:
        page = page or RibbonPage()
        strip = _Strip(page, self.stack)
        self.stack.addWidget(strip)
        tab = _Tab(title, self)
        tab.clicked.connect(lambda _=False, k=key: self.set_current(k))
        self._tab_row.insertWidget(self._tab_row.count() - 1, tab)
        self.pages[key], self.tabs[key], self._strips[key] = page, tab, strip
        if not self._current:
            self._current = key
            tab.setChecked(True)
        return page

    # ------------------------------------------------------------ タブ
    def current(self) -> str:
        return self._current

    def set_current(self, key: str):
        if key not in self.pages or not self.tabs[key].isEnabled():
            key = self._current
        for k, t in self.tabs.items():
            t.setChecked(k == key)
        if key == self._current:
            return
        self._current = key
        self.stack.setCurrentWidget(self._strips[key])
        self.tab_changed.emit(key)

    def set_tab_enabled(self, key: str, on: bool):
        self.tabs[key].setEnabled(on)
        self.tabs[key].update()

    def button(self, action: QAction, tab: str | None = None) -> RibbonButton | None:
        """action のボタン。tab を省くと、今のタブ → ほかのタブの順に探す。"""
        keys = [tab] if tab else [self._current] + [k for k in self.pages if k != self._current]
        for k in keys:
            b = self.pages[k].button(action)
            if b is not None:
                return b
        return None
