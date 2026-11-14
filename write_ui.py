"""「書き込み」タブのリボン。タブ（F4）を選んでいる間だけ表示する。

道具（選択・ペン・直線・矢印・四角・丸・文字）、マーカー、色・太さ・濃さ・塗り、フォント・文字サイズ・背景・枠をまとめる。
文字用の設定は、文字の道具を選んでいるとき（または文字を選択しているとき）だけ表示し、それ以外では線用の設定を表示する。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QGridLayout, QLabel, QMenu, QToolButton,
                               QWidget)

import fonts
import ribbon

DRAW_TOOLS = ("pen", "line", "arrow", "rect", "ellipse")      # ドラッグして描く道具
TOOLS = [
    ("select", "選択", "書き込んだものをクリックで選ぶ。ドラッグで移動、右下の四角をドラッグで大きさを変える。文字の上をドラッグすると文字を選択"),
    ("pen", "ペン", "ドラッグして手書きの線を引く"),
    ("line", "直線", "ドラッグしてまっすぐな線を引く"),
    ("arrow", "矢印", "ドラッグして矢印を引く（離した側が矢の先）"),
    ("rect", "四角", "ドラッグして四角で囲む"),
    ("ellipse", "丸", "ドラッグして丸で囲む"),
    ("text", "文字", "クリックした場所に文字を書き込む。書いた文字はダブルクリックで直せる"),
]
COLORS = [
    ("赤", (0.85, 0.10, 0.10)),
    ("青", (0.10, 0.30, 0.85)),
    ("黒", (0.0, 0.0, 0.0)),
    ("緑", (0.0, 0.55, 0.20)),
    ("橙", (1.0, 0.50, 0.0)),
    ("黄", (1.0, 0.92, 0.20)),
]
WIDTHS = [("細い", 1.0), ("ふつう", 2.0), ("太い", 4.0), ("極太", 8.0), ("マーカー", 14.0)]
OPACITIES = [("100%", 1.0), ("75%", 0.75), ("50%", 0.5), ("25%", 0.25)]
TEXT_BGS = [("なし", None), ("白", (1.0, 1.0, 1.0)), ("薄い黄", (1.0, 1.0, 0.8)), ("薄い青", (0.86, 0.93, 1.0)),
            ("薄い緑", (0.87, 0.96, 0.86)), ("薄い桃", (1.0, 0.88, 0.90)), ("薄い灰", (0.92, 0.92, 0.92))]     # 文字の背景（既定は なし）
BORDER_COLORS = [("なし", None), ("黒", (0.0, 0.0, 0.0)), ("赤", (0.85, 0.10, 0.10)), ("青", (0.10, 0.30, 0.85)),
                 ("緑", (0.0, 0.55, 0.20)), ("橙", (1.0, 0.50, 0.0)), ("灰", (0.5, 0.5, 0.5))]                 # 文字の枠（既定は なし）
BORDER_STYLE_ITEMS = [("1本線", "solid"), ("点線", "dotted"), ("2重線", "double")]
BORDER_WIDTHS = [("細い", 0.5), ("ふつう", 1.0), ("太い", 2.0), ("極太", 3.0)]


def _qcolor(c) -> QColor:
    return QColor.fromRgbF(c[0], c[1], c[2])


TOOL_ICONS = {"select": "cursor", "pen": "ink_stroke", "line": "line", "arrow": "arrow_up_right", "rect": "rectangle_landscape",
              "ellipse": "circle", "text": "text_t", "highlight": "highlight", "underline": "text_underline",
              "strikeout": "text_strikethrough"}


def tool_icon(kind: str, size: int = 26) -> QIcon:
    """道具のアイコン。Fluent のアイコン（assets/icons）を使い、読めないときは自前で描く。"""
    name = TOOL_ICONS.get(kind)
    if name:
        ic = ribbon.icon(name)
        if not ic.isNull():
            return ic
    return _drawn_tool_icon(kind, size)


def _drawn_tool_icon(kind: str, size: int = 26) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    ink = QColor(40, 40, 40)
    accent = QColor(200, 30, 30)
    s = float(size)
    if kind == "select":
        path = QPainterPath(QPointF(s * 0.25, s * 0.12))
        for x, y in ((0.25, 0.80), (0.42, 0.63), (0.56, 0.90), (0.67, 0.84), (0.54, 0.58), (0.78, 0.56)):
            path.lineTo(s * x, s * y)
        path.closeSubpath()
        p.setPen(QPen(ink, 1.4)); p.setBrush(QColor(255, 255, 255)); p.drawPath(path)
    elif kind == "pen":
        path = QPainterPath(QPointF(s * 0.12, s * 0.70))
        path.cubicTo(s * 0.30, s * 0.10, s * 0.45, s * 0.95, s * 0.62, s * 0.45)
        path.cubicTo(s * 0.70, s * 0.25, s * 0.80, s * 0.40, s * 0.90, s * 0.30)
        p.setPen(QPen(accent, 2.2, Qt.SolidLine, Qt.RoundCap)); p.setBrush(Qt.NoBrush); p.drawPath(path)
    elif kind == "line":
        p.setPen(QPen(accent, 2.4, Qt.SolidLine, Qt.RoundCap)); p.drawLine(QPointF(s * 0.15, s * 0.82), QPointF(s * 0.85, s * 0.18))
    elif kind == "arrow":
        p.setPen(QPen(accent, 2.4, Qt.SolidLine, Qt.RoundCap)); p.drawLine(QPointF(s * 0.15, s * 0.82), QPointF(s * 0.72, s * 0.30))
        p.setPen(Qt.NoPen); p.setBrush(accent)
        p.drawPolygon(QPolygonF([QPointF(s * 0.90, s * 0.12), QPointF(s * 0.56, s * 0.24), QPointF(s * 0.78, s * 0.46)]))
    elif kind == "rect":
        p.setPen(QPen(accent, 2.2)); p.setBrush(Qt.NoBrush); p.drawRect(QRectF(s * 0.15, s * 0.25, s * 0.70, s * 0.52))
    elif kind == "ellipse":
        p.setPen(QPen(accent, 2.2)); p.setBrush(Qt.NoBrush); p.drawEllipse(QRectF(s * 0.12, s * 0.22, s * 0.76, s * 0.56))
    elif kind == "text":
        f = p.font(); f.setPixelSize(int(s * 0.80)); f.setBold(True); p.setFont(f)
        p.setPen(accent); p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, "A")
    elif kind == "highlight":
        p.setPen(Qt.NoPen); p.setBrush(QColor(255, 235, 60)); p.drawRect(QRectF(s * 0.10, s * 0.30, s * 0.80, s * 0.42))
        f = p.font(); f.setPixelSize(int(s * 0.50)); f.setBold(True); p.setFont(f)
        p.setPen(ink); p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, "あ")
    elif kind in ("underline", "strikeout"):
        f = p.font(); f.setPixelSize(int(s * 0.50)); f.setBold(True); p.setFont(f)
        p.setPen(ink); p.drawText(QRectF(0, 0, s, s * 0.92), Qt.AlignCenter, "あ")
        y = s * (0.86 if kind == "underline" else 0.48)
        p.setPen(QPen(accent, 2.0)); p.drawLine(QPointF(s * 0.14, y), QPointF(s * 0.86, y))
    elif kind == "close":
        p.setPen(QPen(ink, 2.2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(s * 0.25, s * 0.25), QPointF(s * 0.75, s * 0.75)); p.drawLine(QPointF(s * 0.75, s * 0.25), QPointF(s * 0.25, s * 0.75))
    p.end()
    return QIcon(pm)


def _swatch_icon(color, size: int = 18) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setPen(QPen(QColor(90, 90, 90), 1)); p.setBrush(_qcolor(color))
    p.drawRect(1, 1, size - 3, size - 3)
    p.end()
    return QIcon(pm)


def _none_icon(size: int = 18) -> QIcon:
    """「色なし」の見本（白地に赤い斜線）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(90, 90, 90), 1)); p.setBrush(QColor(255, 255, 255))
    p.drawRect(1, 1, size - 3, size - 3)
    p.setPen(QPen(QColor(210, 40, 40), 1.6)); p.drawLine(QPointF(2.5, size - 3.5), QPointF(size - 3.5, 2.5))
    p.end()
    return QIcon(pm)


class ColorMenuButton(QToolButton):
    """色を一覧から選ぶボタン（なし / 決まった色 / 他の色…）。今の色を見本と名前で表示する。"""

    color_changed = Signal()

    def __init__(self, choices: list, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ribbonSmall")
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(14, 14))
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setPopupMode(QToolButton.InstantPopup)
        self.ask_color = None                 # 「他の色」のダイアログ（テストで差し替える）。None なら QColorDialog
        self._choices = list(choices)
        self._title = title
        self._color = None
        menu = QMenu(self)
        menu.setObjectName("ribbonMenu")
        self.choice_actions: dict[str, QAction] = {}
        for label, col in self._choices:
            a = menu.addAction(_none_icon() if col is None else _swatch_icon(col), label)
            a.triggered.connect(lambda _=False, c=col: self.choose(c))
            self.choice_actions[label] = a
        menu.addSeparator()
        self.a_other = menu.addAction(ribbon.icon("color"), "他の色...")
        self.a_other.triggered.connect(self._pick_other)
        self.setMenu(menu)
        self._refresh()

    def color(self):
        """今の色 (r, g, b)。「なし」は None。"""
        return self._color

    def set_color(self, color):
        """表示だけを変える（変更の通知は出さない）。"""
        self._color = None if color is None else tuple(float(c) for c in color[:3])
        self._refresh()

    def choose(self, color):
        """色を選ぶ（一覧から選んだときと同じ。変更を通知する）。"""
        self.set_color(color)
        self.color_changed.emit()

    def _label(self) -> str:
        for label, col in self._choices:
            if (col is None) == (self._color is None) and (col is None or all(abs(a - b) < 0.02 for a, b in zip(col, self._color))):
                return label
        return "他の色"

    def _refresh(self):
        self.setIcon(_none_icon() if self._color is None else _swatch_icon(self._color))
        self.setText(self._label())

    def _pick_other(self):
        initial = _qcolor(self._color) if self._color is not None else QColor(255, 255, 255)
        if self.ask_color is not None:
            c = self.ask_color(initial, self._title)
        else:
            c = QColorDialog.getColor(initial, self, self._title)
            c = c if c.isValid() else None
        if c is not None:
            self.choose((c.redF(), c.greenF(), c.blueF()))


class WriteRibbon(ribbon.RibbonPage):
    """「書き込み」タブの中身。道具 / マーカー / 色 / 線（または文字）の設定をグループに分けて並べる。"""

    tool_changed = Signal(str)        # 選んだ道具
    style_changed = Signal(str)       # 変わった設定: color / width / opacity / fill / font / fontsize / textbg / border / borderstyle / borderwidth
    markup_requested = Signal(str)    # highlight / underline / strikeout

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ask_color = None             # 「他の色」のダイアログ（テストで差し替える）。None なら QColorDialog
        self._color = COLORS[0][1]
        self._fonts_loaded = False
        self._mute = False
        self._context = "shape"

        # ---- 道具
        g = self.add_group("道具")
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions: dict[str, QAction] = {}
        for key, label, tip in TOOLS:
            a = QAction(tool_icon(key), label, self)
            a.setCheckable(True)
            a.setToolTip(tip); a.setStatusTip(tip)
            a.triggered.connect(lambda _=False, k=key: self._on_tool(k))
            self.tool_group.addAction(a)
            g.add_large(a)
            self.tool_actions[key] = a
        self.tool_actions["select"].setChecked(True)

        # ---- マーカー（選択した文字に付ける）
        g = self.add_group("マーカー")
        self.markup_actions: dict[str, QAction] = {}
        for key, label, tip in (("highlight", "蛍光ペン", "選択した文字に蛍光ペンを引く（先に文字の上をドラッグして選ぶ）"),
                                ("underline", "下線", "選択した文字に下線を引く"),
                                ("strikeout", "取り消し線", "選択した文字に取り消し線を引く")):
            a = QAction(tool_icon(key), label, self)
            a.setToolTip(tip); a.setStatusTip(tip)
            a.setEnabled(False)
            a.triggered.connect(lambda _=False, k=key: self.markup_requested.emit(k))
            self.markup_actions[key] = a
        g.add_column(list(self.markup_actions.values()))

        # ---- 色（3 列 × 2 段の色見本 + 他の色）
        g = self.add_group("色")
        box = QWidget(g)
        grid = QGridLayout(box)
        grid.setContentsMargins(2, 1, 2, 0)
        grid.setHorizontalSpacing(1); grid.setVerticalSpacing(0)
        self.color_buttons: list[QToolButton] = []
        for i, (label, col) in enumerate(COLORS):
            b = QToolButton(box)
            b.setObjectName("ribbonSwatch")
            b.setIcon(_swatch_icon(col)); b.setIconSize(QSize(16, 16))
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setCheckable(True); b.setAutoRaise(True); b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(f"{label}（線・文字の色）")
            b.clicked.connect(lambda _=False, c=col: self.set_color(c, emit=True))
            grid.addWidget(b, i // 3, i % 3)
            self.color_buttons.append(b)
        self.btn_other = QToolButton(box)
        self.btn_other.setObjectName("ribbonSmall")
        self.btn_other.setText("他の色")
        self.btn_other.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_other.setIconSize(QSize(14, 14))
        self.btn_other.setAutoRaise(True); self.btn_other.setFocusPolicy(Qt.NoFocus)
        self.btn_other.setToolTip("一覧に無い色を選ぶ")
        self.btn_other.clicked.connect(self._pick_other)
        grid.addWidget(self.btn_other, 2, 0, 1, 3)
        g.add_widget(box)

        def field(text: str, parent: QWidget) -> QLabel:
            lb = QLabel(text, parent)
            lb.setObjectName("ribbonFieldLabel")
            return lb

        # ---- 線の設定（ペン・直線・矢印・四角・丸）
        self.line_group = self.add_group("線")
        box = QWidget(self.line_group)
        grid = QGridLayout(box)
        grid.setContentsMargins(4, 1, 4, 0)
        grid.setHorizontalSpacing(6); grid.setVerticalSpacing(1)
        self.cb_width = QComboBox(box)
        for label, w in WIDTHS:
            self.cb_width.addItem(label, w)
        self.cb_width.setCurrentIndex(1)
        self.cb_width.setToolTip("線の太さ。「マーカー」は濃さ 50% と合わせると蛍光ペンのように使える")
        self.cb_width.currentIndexChanged.connect(lambda _: self._emit("width"))
        self.cb_opacity = QComboBox(box)
        for label, o in OPACITIES:
            self.cb_opacity.addItem(label, o)
        self.cb_opacity.setToolTip("線の濃さ（下の文字が透けて見える度合い）")
        self.cb_opacity.currentIndexChanged.connect(lambda _: self._emit("opacity"))
        self.chk_fill = QCheckBox("塗りつぶす", box)
        self.chk_fill.setToolTip("四角・丸の中を、選んでいる色で塗りつぶす")
        self.chk_fill.toggled.connect(lambda _: self._emit("fill"))
        grid.addWidget(field("太さ", box), 0, 0); grid.addWidget(self.cb_width, 0, 1)
        grid.addWidget(field("濃さ", box), 1, 0); grid.addWidget(self.cb_opacity, 1, 1)
        grid.addWidget(self.chk_fill, 2, 0, 1, 2)
        self.line_group.add_widget(box)

        # ---- 文字の設定
        self.text_group = self.add_group("文字")
        box = QWidget(self.text_group)
        grid = QGridLayout(box)
        grid.setContentsMargins(4, 1, 4, 0)
        grid.setHorizontalSpacing(6); grid.setVerticalSpacing(1)
        self.cb_font = QComboBox(box)
        self.cb_font.addItem(fonts.LABELS[fonts.BUILTIN], fonts.BUILTIN)
        self.cb_font.setMinimumContentsLength(9)
        self.cb_font.setToolTip("文字の書体。フォントは PDF に埋め込むので、どのパソコンで開いても同じ見た目になる")
        self.cb_font.currentIndexChanged.connect(lambda _: self._emit("font"))
        self.sp_size = QDoubleSpinBox(box)
        self.sp_size.setRange(6, 96); self.sp_size.setDecimals(0); self.sp_size.setSingleStep(1); self.sp_size.setValue(12)
        self.sp_size.setSuffix(" pt")
        self.sp_size.setKeyboardTracking(False)
        self.sp_size.setToolTip("文字の大きさ")
        self.sp_size.valueChanged.connect(lambda _: self._emit("fontsize"))
        grid.addWidget(field("フォント", box), 0, 0); grid.addWidget(self.cb_font, 0, 1)
        grid.addWidget(field("大きさ", box), 1, 0); grid.addWidget(self.sp_size, 1, 1, Qt.AlignLeft)
        grid.setRowStretch(2, 1)
        self.text_group.add_widget(box)

        # ---- 文字の背景と枠（どちらも最初は「なし」）
        self.box_group = self.add_group("背景と枠")
        box = QWidget(self.box_group)
        grid = QGridLayout(box)
        grid.setContentsMargins(4, 1, 4, 0)
        grid.setHorizontalSpacing(6); grid.setVerticalSpacing(1)
        self.btn_bg = ColorMenuButton(TEXT_BGS, "背景の色を選ぶ", box)
        self.btn_bg.setToolTip("文字の後ろを塗る色。「白」にすると下の文字を隠して上書きしたように見せられる")
        self.btn_bg.color_changed.connect(lambda: self._emit("textbg"))
        self.btn_border = ColorMenuButton(BORDER_COLORS, "枠の色を選ぶ", box)
        self.btn_border.setToolTip("文字のまわりを囲む線の色。「なし」で枠を消す")
        self.btn_border.color_changed.connect(self._on_border_color)
        self.cb_border_style = QComboBox(box)
        for label, key in BORDER_STYLE_ITEMS:
            self.cb_border_style.addItem(label, key)
        self.cb_border_style.setToolTip("枠の線の種類（1 本線 / 点線 / 2 重線）")
        self.cb_border_style.currentIndexChanged.connect(lambda _: self._emit("borderstyle"))
        self.cb_border_width = QComboBox(box)
        for label, w in BORDER_WIDTHS:
            self.cb_border_width.addItem(label, w)
        self.cb_border_width.setCurrentIndex(1)
        self.cb_border_width.setToolTip("枠の線の太さ")
        self.cb_border_width.currentIndexChanged.connect(lambda _: self._emit("borderwidth"))
        for b in (self.btn_bg, self.btn_border):
            b.ask_color = lambda initial, title: self.ask_color(initial, title) if self.ask_color is not None else self._ask_qt(initial, title)
        grid.addWidget(field("背景", box), 0, 0); grid.addWidget(self.btn_bg, 0, 1)
        grid.addWidget(field("枠", box), 1, 0); grid.addWidget(self.btn_border, 1, 1)
        grid.addWidget(field("枠の線", box), 0, 2); grid.addWidget(self.cb_border_style, 0, 3)
        grid.addWidget(field("枠の太さ", box), 1, 2); grid.addWidget(self.cb_border_width, 1, 3)
        grid.setRowStretch(2, 1)
        self.box_group.add_widget(box)
        self._sync_border_enabled()

        self.set_color(self._color)
        self.set_context("shape")

    def add_action_group(self, title: str, actions: list[tuple[QAction, str]]):
        """ウィンドウ側のアクション（選択した書き込みの削除など）を、大きいボタンのグループとして末尾に足す。"""
        g = self.add_group(title)
        for action, label in actions:
            g.add_large(action, label)
        return g

    # ------------------------------------------------------------ 道具
    def tool(self) -> str:
        return next((k for k, a in self.tool_actions.items() if a.isChecked()), "select")

    def set_tool(self, key: str):
        if key not in self.tool_actions:
            key = "select"
        changed = self.tool() != key
        self.tool_actions[key].setChecked(True)
        if changed:
            self.tool_changed.emit(key)

    def _on_tool(self, key: str):
        self.tool_actions[key].setChecked(True)
        self.tool_changed.emit(key)

    def set_markup_enabled(self, on: bool):
        for a in self.markup_actions.values():
            a.setEnabled(on)

    def set_context(self, kind: str):
        """kind="text" なら文字の設定、"shape" なら線の設定を表示する。"""
        self._context = "text" if kind == "text" else "shape"
        self.set_group_visible(self.text_group, self._context == "text")
        self.set_group_visible(self.box_group, self._context == "text")
        self.set_group_visible(self.line_group, self._context != "text")

    def context(self) -> str:
        return self._context

    # ------------------------------------------------------------ 設定値
    def color(self) -> tuple[float, float, float]:
        return self._color

    def set_color(self, color, emit: bool = False):
        self._color = tuple(float(c) for c in color[:3])
        hit = False
        for b, (_label, col) in zip(self.color_buttons, COLORS):
            same = all(abs(a - c) < 0.01 for a, c in zip(col, self._color))
            b.setChecked(same)
            hit = hit or same
        self.btn_other.setIcon(_swatch_icon(self._color) if not hit else QIcon())
        if emit:
            self._emit("color")

    def _pick_other(self):
        if self.ask_color is not None:
            c = self.ask_color(_qcolor(self._color), "色を選ぶ")
        else:
            c = QColorDialog.getColor(_qcolor(self._color), self, "色を選ぶ")
            c = c if c.isValid() else None
        if c is not None:
            self.set_color((c.redF(), c.greenF(), c.blueF()), emit=True)
        else:
            self.set_color(self._color)     # ボタンの選択表示を戻す

    def width(self) -> float:
        return float(self.cb_width.currentData())

    def opacity(self) -> float:
        return float(self.cb_opacity.currentData())

    def fill_enabled(self) -> bool:
        return self.chk_fill.isChecked()

    def font_key(self) -> str:
        return self.cb_font.currentData() or fonts.BUILTIN

    def fontsize(self) -> float:
        return float(self.sp_size.value())

    def text_bg(self):
        """文字の背景の色。なしは None。"""
        return self.btn_bg.color()

    def text_border(self):
        """文字の枠の色。枠なしは None。"""
        return self.btn_border.color()

    def text_border_style(self) -> str:
        return self.cb_border_style.currentData() or "solid"

    def text_border_width(self) -> float:
        return float(self.cb_border_width.currentData() or 1.0)

    def _on_border_color(self):
        self._sync_border_enabled()
        self._emit("border")

    def _sync_border_enabled(self):
        """枠が「なし」のあいだは、線の種類と太さは選べなくする。"""
        on = self.btn_border.color() is not None
        self.cb_border_style.setEnabled(on)
        self.cb_border_width.setEnabled(on)

    def _ask_qt(self, initial, title):
        c = QColorDialog.getColor(initial, self, title)
        return c if c.isValid() else None

    def _emit(self, what: str):
        if not self._mute:
            self.style_changed.emit(what)

    # ------------------------------------------------------------ フォント一覧
    def ensure_fonts(self):
        """使えるフォントを調べて一覧に入れる（PC のフォントを 1 つずつ試すので、初めて開いたときだけ行う）。"""
        if self._fonts_loaded:
            return
        self._fonts_loaded = True
        cur = self.font_key()
        self._mute = True
        try:
            self.cb_font.clear()
            for key, label in fonts.available_fonts():
                self.cb_font.addItem(label, key)
            i = self.cb_font.findData(getattr(self, "_wanted_font", cur))
            self.cb_font.setCurrentIndex(i if i >= 0 else 0)
        finally:
            self._mute = False

    # ------------------------------------------------------------ 選択中の注釈に合わせる・設定の保存
    @staticmethod
    def _nearest(combo: QComboBox, value: float) -> int:
        return min(range(combo.count()), key=lambda i: abs(float(combo.itemData(i)) - value))

    def reflect(self, annot: dict):
        """選択した注釈の見た目を、リボンの表示に反映する（設定変更の通知は出さない）。"""
        self._mute = True
        try:
            if annot.get("stroke"):
                self.set_color(annot["stroke"])
            if annot.get("fontsize"):
                self.sp_size.setValue(float(annot["fontsize"]))
                if annot.get("font"):
                    i = self.cb_font.findData(annot["font"])
                    if i >= 0:
                        self.cb_font.setCurrentIndex(i)
                    self.btn_bg.set_color(annot.get("fill"))
                    self.btn_border.set_color(annot.get("border_color"))
                    i = self.cb_border_style.findData(annot.get("border_style") or "solid")
                    self.cb_border_style.setCurrentIndex(i if i >= 0 else 0)
                    self.cb_border_width.setCurrentIndex(self._nearest(self.cb_border_width, float(annot.get("border_width") or 1.0)))
                    self._sync_border_enabled()
            else:
                self.cb_width.setCurrentIndex(self._nearest(self.cb_width, float(annot.get("width") or 1.0)))
                self.cb_opacity.setCurrentIndex(self._nearest(self.cb_opacity, float(annot.get("opacity") or 1.0)))
                if annot.get("kind") in ("四角", "円"):
                    self.chk_fill.setChecked(annot.get("fill") is not None)
        finally:
            self._mute = False

    def load_settings(self, s: QSettings):
        self._mute = True
        try:
            col = s.value("write/color", "")
            try:
                parts = [float(x) for x in str(col).split(",")]
                if len(parts) == 3:
                    self.set_color(parts)
            except ValueError:
                pass
            self.cb_width.setCurrentIndex(self._nearest(self.cb_width, s.value("write/width", 2.0, type=float)))
            self.cb_opacity.setCurrentIndex(self._nearest(self.cb_opacity, s.value("write/opacity", 1.0, type=float)))
            self.sp_size.setValue(s.value("write/fontsize", 12.0, type=float))
            self._wanted_font = s.value("write/font", fonts.BUILTIN)
        finally:
            self._mute = False

    def save_settings(self, s: QSettings):
        s.setValue("write/color", ",".join(f"{c:.4f}" for c in self._color))
        s.setValue("write/width", self.width())
        s.setValue("write/opacity", self.opacity())
        s.setValue("write/fontsize", self.fontsize())
        s.setValue("write/font", self.font_key())
