"""書き込みリボン（ツールバー）。「書き込み」ボタンを押している間だけ表示する。

道具（選択・ペン・直線・矢印・四角・丸・文字）、マーカー、色・太さ・濃さ・塗り、フォント・文字サイズ・背景・枠をまとめる。
文字用の設定は、文字の道具を選んでいるとき（または文字を選択しているとき）だけ表示し、それ以外では線用の設定を表示する。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QLabel, QSizePolicy, QToolBar,
                               QToolButton, QWidget)

import fonts

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
TEXT_BGS = [("なし", None), ("白", (1.0, 1.0, 1.0)), ("黄", (1.0, 1.0, 0.8))]


def _qcolor(c) -> QColor:
    return QColor.fromRgbF(c[0], c[1], c[2])


def tool_icon(kind: str, size: int = 26) -> QIcon:
    """道具の絵を描く（テーマアイコンが無い Windows でも同じ見た目にするため自前で描く）。"""
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


class WriteRibbon(QToolBar):
    tool_changed = Signal(str)        # 選んだ道具
    style_changed = Signal(str)       # 変わった設定: color / width / opacity / fill / font / fontsize / textbg / border
    markup_requested = Signal(str)    # highlight / underline / strikeout
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("書き込み", parent)
        self.setObjectName("writeToolBar")
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(26, 26))
        self.setMovable(False)
        self.ask_color = None             # 「他の色」のダイアログ（テストで差し替える）。None なら QColorDialog
        self._color = COLORS[0][1]
        self._fonts_loaded = False
        self._mute = False

        # ---- 道具
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions: dict[str, QAction] = {}
        for key, label, tip in TOOLS:
            a = QAction(tool_icon(key), label, self)
            a.setCheckable(True)
            a.setToolTip(tip); a.setStatusTip(tip)
            a.triggered.connect(lambda _=False, k=key: self._on_tool(k))
            self.tool_group.addAction(a)
            self.addAction(a)
            self.tool_actions[key] = a
        self.tool_actions["select"].setChecked(True)
        self.addSeparator()

        # ---- マーカー（選択した文字に付ける）
        self.markup_actions: dict[str, QAction] = {}
        for key, label, tip in (("highlight", "蛍光ペン", "選択した文字に蛍光ペンを引く（先に文字の上をドラッグして選ぶ）"),
                                ("underline", "下線", "選択した文字に下線を引く"),
                                ("strikeout", "取り消し線", "選択した文字に取り消し線を引く")):
            a = QAction(tool_icon(key), label, self)
            a.setToolTip(tip); a.setStatusTip(tip)
            a.setEnabled(False)
            a.triggered.connect(lambda _=False, k=key: self.markup_requested.emit(k))
            self.addAction(a)
            self.markup_actions[key] = a
        self.addSeparator()

        # ---- 色
        self.addWidget(QLabel(" 色 "))
        self.color_buttons: list[QToolButton] = []
        for label, col in COLORS:
            b = QToolButton(self)
            b.setIcon(_swatch_icon(col)); b.setIconSize(QSize(18, 18))
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setCheckable(True); b.setAutoRaise(True)
            b.setToolTip(f"{label}（線・文字の色）")
            b.clicked.connect(lambda _=False, c=col: self.set_color(c, emit=True))
            self.addWidget(b)
            self.color_buttons.append(b)
        self.btn_other = QToolButton(self)
        self.btn_other.setText("他の色")
        self.btn_other.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_other.setToolTip("一覧に無い色を選ぶ")
        self.btn_other.clicked.connect(self._pick_other)
        self.addWidget(self.btn_other)
        self.addSeparator()

        # ---- 線の設定（ペン・直線・矢印・四角・丸）
        self._shape_actions: list[QAction] = []
        self._shape_actions.append(self.addWidget(QLabel(" 太さ ")))
        self.cb_width = QComboBox(self)
        for label, w in WIDTHS:
            self.cb_width.addItem(label, w)
        self.cb_width.setCurrentIndex(1)
        self.cb_width.setToolTip("線の太さ。「マーカー」は濃さ 50% と合わせると蛍光ペンのように使える")
        self.cb_width.currentIndexChanged.connect(lambda _: self._emit("width"))
        self._shape_actions.append(self.addWidget(self.cb_width))
        self._shape_actions.append(self.addWidget(QLabel(" 濃さ ")))
        self.cb_opacity = QComboBox(self)
        for label, o in OPACITIES:
            self.cb_opacity.addItem(label, o)
        self.cb_opacity.setToolTip("線の濃さ（下の文字が透けて見える度合い）")
        self.cb_opacity.currentIndexChanged.connect(lambda _: self._emit("opacity"))
        self._shape_actions.append(self.addWidget(self.cb_opacity))
        self.chk_fill = QCheckBox("塗りつぶす", self)
        self.chk_fill.setToolTip("四角・丸の中を、選んでいる色で塗りつぶす")
        self.chk_fill.toggled.connect(lambda _: self._emit("fill"))
        self._shape_actions.append(self.addWidget(self.chk_fill))

        # ---- 文字の設定
        self._text_actions: list[QAction] = []
        self._text_actions.append(self.addWidget(QLabel(" フォント ")))
        self.cb_font = QComboBox(self)
        self.cb_font.addItem(fonts.LABELS[fonts.BUILTIN], fonts.BUILTIN)
        self.cb_font.setMinimumContentsLength(9)
        self.cb_font.setToolTip("文字の書体。フォントは PDF に埋め込むので、どのパソコンで開いても同じ見た目になる")
        self.cb_font.currentIndexChanged.connect(lambda _: self._emit("font"))
        self._text_actions.append(self.addWidget(self.cb_font))
        self._text_actions.append(self.addWidget(QLabel(" 大きさ ")))
        self.sp_size = QDoubleSpinBox(self)
        self.sp_size.setRange(6, 96); self.sp_size.setDecimals(0); self.sp_size.setSingleStep(1); self.sp_size.setValue(12)
        self.sp_size.setSuffix(" pt")
        self.sp_size.setKeyboardTracking(False)
        self.sp_size.setToolTip("文字の大きさ")
        self.sp_size.valueChanged.connect(lambda _: self._emit("fontsize"))
        self._text_actions.append(self.addWidget(self.sp_size))
        self._text_actions.append(self.addWidget(QLabel(" 背景 ")))
        self.cb_bg = QComboBox(self)
        for label, c in TEXT_BGS:
            self.cb_bg.addItem(label, c)
        self.cb_bg.setToolTip("文字の後ろを塗る色。「白」にすると下の文字を隠して上書きしたように見せられる")
        self.cb_bg.currentIndexChanged.connect(lambda _: self._emit("textbg"))
        self._text_actions.append(self.addWidget(self.cb_bg))
        self.chk_border = QCheckBox("枠", self)
        self.chk_border.setToolTip("文字のまわりを線で囲む")
        self.chk_border.toggled.connect(lambda _: self._emit("border"))
        self._text_actions.append(self.addWidget(self.chk_border))

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.addWidget(spacer)
        self.a_close = QAction(tool_icon("close"), "閉じる", self)
        self.a_close.setToolTip("書き込みを終える（Esc キーでも閉じる）")
        self.a_close.triggered.connect(self.close_requested.emit)
        self.addAction(self.a_close)

        self.set_color(self._color)
        self.set_context("shape")

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
        text = kind == "text"
        for a in self._text_actions:
            a.setVisible(text)
        for a in self._shape_actions:
            a.setVisible(not text)

    def context(self) -> str:
        return "text" if self._text_actions[0].isVisible() else "shape"

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
        return self.cb_bg.currentData()

    def text_border(self) -> bool:
        return self.chk_border.isChecked()

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
                    fill = annot.get("fill")
                    j = next((k for k, (_l, c) in enumerate(TEXT_BGS) if (c is None) == (fill is None)
                              and (c is None or all(abs(a - b) < 0.02 for a, b in zip(c, fill)))), 0)
                    self.cb_bg.setCurrentIndex(j)
                    self.chk_border.setChecked(bool(annot.get("border")))
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
