"""差し替え枠の画面（ルールの管理、差し替え印刷の入力）。ルールそのものは fields.py。"""
from __future__ import annotations

import copy
import datetime as _dt

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

import fields


def _date_format_items(today: _dt.date | None = None) -> list[tuple[str, str]]:
    """日付の書式の一覧（今日の日付での見え方を名前にする）。"""
    d = today or _dt.date.today()
    return [(fields.format_date(d, key), key) for key in fields.DATE_FORMATS]


class RulesDialog(QDialog):
    """ルールの管理: 追加・名前や種類の変更・削除。OK を押すまで、もとのルールは変えない。"""

    def __init__(self, book: fields.RuleBook, parent: QWidget | None = None, select_id: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("差し替え枠のルール")
        self.resize(640, 420)
        self.book = book
        self.rules: list[fields.Rule] = copy.deepcopy(book.rules)
        self._loading = False

        self.list = QListWidget(self)
        self.list.currentRowChanged.connect(self._on_row)
        self.btn_add = QPushButton("新しいルール", self)
        self.btn_add.setToolTip("自分のルールを足す（例: 担当者名、診療科、定型の挨拶文）")
        self.btn_add.clicked.connect(self.add_rule)
        self.btn_del = QPushButton("削除", self)
        self.btn_del.clicked.connect(self.delete_rule)
        left = QVBoxLayout()
        left.addWidget(self.list, 1)
        row = QHBoxLayout(); row.addWidget(self.btn_add); row.addWidget(self.btn_del)
        left.addLayout(row)

        self.ed_name = QLineEdit(self)
        self.ed_name.setPlaceholderText("例: 送付元、担当者、診療科")
        self.cb_kind = QComboBox(self)
        for key in fields.KINDS:
            self.cb_kind.addItem(fields.KIND_LABELS[key], key)
        self.cb_fmt = QComboBox(self)
        for label, key in _date_format_items():
            self.cb_fmt.addItem(label, key)
        self.sp_offset = QSpinBox(self)
        self.sp_offset.setRange(-3660, 3660)
        self.sp_offset.setSuffix(" 日後")
        self.sp_offset.setToolTip("0 は今日。1 は明日、7 は 1 週間後、-1 は昨日")
        self.ed_value = QPlainTextEdit(self)
        self.ed_value.setTabChangesFocus(True)
        self.chk_remember = QCheckBox("前回入力した内容を覚えておく", self)
        self.lbl_value = QLabel("入れる文字", self)
        self.lbl_hint = QLabel("", self)
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#616161;")
        self.form = QFormLayout()
        self.form.addRow("名前", self.ed_name)
        self.form.addRow("種類", self.cb_kind)
        self.form.addRow("日付の書式", self.cb_fmt)
        self.form.addRow("いつの日付", self.sp_offset)
        self.form.addRow(self.lbl_value, self.ed_value)
        self.form.addRow("", self.chk_remember)
        right = QVBoxLayout()
        right.addLayout(self.form)
        right.addWidget(self.lbl_hint)
        right.addStretch(1)
        for w in (self.ed_name, self.ed_value):
            w.textChanged.connect(self._store)
        self.cb_kind.currentIndexChanged.connect(self._on_kind)
        self.cb_fmt.currentIndexChanged.connect(self._store)
        self.sp_offset.valueChanged.connect(self._store)
        self.chk_remember.toggled.connect(self._store)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText("OK")
        buttons.button(QDialogButtonBox.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        body = QHBoxLayout()
        body.addLayout(left, 2)
        body.addLayout(right, 3)
        outer = QVBoxLayout(self)
        outer.addLayout(body, 1)
        outer.addWidget(buttons)

        self._fill_list()
        start = next((i for i, r in enumerate(self.rules) if r.id == select_id), 0)
        self.list.setCurrentRow(start if self.rules else -1)
        self._on_row(self.list.currentRow())

    # ------------------------------------------------------------ 一覧
    def _fill_list(self):
        self._loading = True
        self.list.clear()
        for r in self.rules:
            self.list.addItem(r.name)
        self._loading = False

    def current_rule(self) -> fields.Rule | None:
        i = self.list.currentRow()
        return self.rules[i] if 0 <= i < len(self.rules) else None

    def add_rule(self, name: str = "新しいルール", kind: str = "ask") -> fields.Rule:
        rule = fields.Rule(fields.new_rule_id(), name if isinstance(name, str) and name else "新しいルール", kind).normalized()
        self.rules.append(rule)
        self._fill_list()
        self.list.setCurrentRow(len(self.rules) - 1)
        self.ed_name.setFocus(); self.ed_name.selectAll()
        return rule

    def delete_rule(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.rules):
            del self.rules[i]
            self._fill_list()
            self.list.setCurrentRow(min(i, len(self.rules) - 1))
            self._on_row(self.list.currentRow())

    # ------------------------------------------------------------ 右側の編集欄
    def _on_row(self, _row: int):
        if self._loading:
            return
        rule = self.current_rule()
        self._loading = True
        for w in (self.ed_name, self.cb_kind, self.cb_fmt, self.sp_offset, self.ed_value, self.chk_remember, self.btn_del):
            w.setEnabled(rule is not None)
        if rule is not None:
            self.ed_name.setText(rule.name)
            self.cb_kind.setCurrentIndex(max(0, self.cb_kind.findData(rule.kind)))
            self.cb_fmt.setCurrentIndex(max(0, self.cb_fmt.findData(rule.fmt)))
            self.sp_offset.setValue(rule.offset)
            self.ed_value.setPlainText(rule.value)
            self.chk_remember.setChecked(rule.remember)
        self._loading = False
        self._show_for_kind()

    def _on_kind(self, _i: int):
        self._store()
        self._show_for_kind()

    def _show_for_kind(self):
        kind = self.cb_kind.currentData()
        self.form.setRowVisible(self.cb_fmt, kind == "date")
        self.form.setRowVisible(self.sp_offset, kind == "date")
        self.form.setRowVisible(self.ed_value, kind != "date")
        self.form.setRowVisible(self.chk_remember, kind == "ask")
        self.lbl_value.setText("入れる文字" if kind == "fixed" else "最初に入れておく文字")
        self.lbl_hint.setText({
            "date": "PDF を開いたとき・印刷するときに、その日の日付に自動で変わります。",
            "fixed": "送付元・住所・電話番号のように、毎回同じ内容を入れます。差し替え印刷の画面で直すと、次回からその内容になります。",
            "ask": "宛先・件名・枚数のように、印刷のたびに入力します。",
        }.get(kind, ""))

    def _store(self, *_):
        if self._loading:
            return
        rule = self.current_rule()
        if rule is None:
            return
        rule.name = self.ed_name.text().strip() or "名前なし"
        rule.kind = self.cb_kind.currentData() or "ask"
        rule.fmt = self.cb_fmt.currentData() or fields.DEFAULT_DATE_FORMAT
        rule.offset = self.sp_offset.value()
        rule.value = self.ed_value.toPlainText()
        rule.remember = self.chk_remember.isChecked()
        item = self.list.currentItem()
        if item is not None and item.text() != rule.name:
            item.setText(rule.name)

    def accept(self):
        self._store()
        self.book.rules = [r.normalized() for r in self.rules]
        self.book.save()
        super().accept()


class FillDialog(QDialog):
    """差し替え印刷: 枠に入れる内容を確かめて（入力して）、印刷するか PDF に保存する。

    rows は (ルール, 今入れる内容) の一覧（同じルールの枠が複数あっても 1 行）。
    閉じたあと、action が "print" / "save" / None（やめた）。
    """

    def __init__(self, rows: list[tuple[fields.Rule, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("差し替え印刷")
        self.resize(520, min(640, 190 + 74 * len(rows)))
        self.action: str | None = None
        self.editors: dict[str, QWidget] = {}
        head = QLabel("枠に入れる内容を確かめてください。空のままの枠には、何も印刷しません。\n"
                      "ここで入れた内容は印刷にだけ使い、開いている PDF は書き換えません。", self)
        head.setWordWrap(True)
        inner = QWidget(self)
        form = QFormLayout(inner)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignTop)
        for rule, value in rows:
            if rule.kind == "date":
                ed = QLineEdit(value, inner)
                ed.setToolTip("今日の日付が入っています。別の日付にしたいときは、ここで直せます（今回の印刷だけ）")
            else:
                ed = QPlainTextEdit(inner)
                ed.setPlainText(value)
                ed.setTabChangesFocus(True)
                ed.setFixedHeight(ed.fontMetrics().lineSpacing() * 3 + 14)
                ed.setPlaceholderText("（空のままなら印刷しない）")
                if rule.kind == "fixed":
                    ed.setToolTip("ここで直した内容は、次回からも使います")
            note = {"date": "自動", "fixed": "毎回同じ", "ask": "毎回入力"}.get(rule.kind, "")
            label = QLabel(f"{rule.name}\n<span style='color:#808080'>{note}</span>".replace("\n", "<br>"), inner)
            label.setTextFormat(Qt.RichText)
            form.addRow(label, ed)
            self.editors[rule.id] = ed
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        self.btn_print = QPushButton("印刷...", self)
        self.btn_print.setDefault(True)
        self.btn_save = QPushButton("PDF に保存...", self)
        self.btn_save.setToolTip("この内容を入れた PDF を、別のファイルとして保存する（送った控えを残すときに）")
        self.btn_cancel = QPushButton("キャンセル", self)
        self.btn_print.clicked.connect(lambda: self._finish("print"))
        self.btn_save.clicked.connect(lambda: self._finish("save"))
        self.btn_cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for b in (self.btn_print, self.btn_save, self.btn_cancel):
            buttons.addWidget(b)
        outer = QVBoxLayout(self)
        outer.addWidget(head)
        outer.addWidget(scroll, 1)
        outer.addLayout(buttons)
        first = next((ed for (rule, value), ed in zip(rows, self.editors.values()) if rule.kind != "date" and not value), None)
        (first or self.btn_print).setFocus()

    def _finish(self, action: str):
        self.action = action
        self.accept()

    def set_value(self, rule_id: str, text: str):
        ed = self.editors[rule_id]
        ed.setText(text) if isinstance(ed, QLineEdit) else ed.setPlainText(text)

    def values(self) -> dict[str, str]:
        """ルールの番号 → 入力された内容（前後の空白・改行は取り除く）。"""
        out = {}
        for rule_id, ed in self.editors.items():
            text = ed.text() if isinstance(ed, QLineEdit) else ed.toPlainText()
            out[rule_id] = text.strip()
        return out
