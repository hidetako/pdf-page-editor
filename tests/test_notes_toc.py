"""表示モード・付箋・しおり（目次）のテスト。python tests/test_notes_toc.py"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="pdfpe_notes_")
os.environ["APPDATA"] = os.path.join(_TMP, "appdata")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "xdg")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pymupdf as fitz
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtWidgets import QApplication

import app as appmod
from app import DISPLAY_MODES, MainWindow, apply_display_mode
from pdfmodel import PdfModel


def make_pdf(path, n=4, toc=None):
    d = fitz.open()
    for i in range(n):
        p = d.new_page(width=595, height=842)
        p.insert_text((72, 100), f"P{i + 1}", fontsize=40)
    if toc:
        d.set_toc(toc)
    d.save(path)
    d.close()


def wait(ms=50):
    from PySide6.QtTest import QTest
    QTest.qWait(ms)


def main():
    qapp = QApplication.instance() or QApplication([])
    pdf_toc = os.path.join(_TMP, "toc.pdf")
    make_pdf(pdf_toc, 4, toc=[[1, "第1章", 1], [2, "1-1", 2], [1, "第2章", 4]])
    pdf_plain = os.path.join(_TMP, "plain.pdf")
    make_pdf(pdf_plain, 2)

    # ---- モデル: 付箋としおり
    m = PdfModel(); m.open(pdf_toc)
    assert m.get_toc() == [[1, "第1章", 1], [2, "1-1", 2], [1, "第2章", 4]]
    m.add_bookmark(2, "しおり A")
    assert [t[1] for t in m.get_toc()] == ["第1章", "1-1", "しおり A", "第2章"]
    m.remove_toc_entry(0)
    assert m.get_toc()[0] == [1, "1-1", 2], "子項目は繰り上がる"
    m.undo(); m.undo(); assert len(m.get_toc()) == 3
    m.doc[1].set_rotation(90)
    x = m.add_note(1, (300, 100), "確認", "安藤")
    assert m.note_at(1, (305, 105))["xref"] == x and m.note_at(1, (10, 10)) is None
    m.update_note(1, x, "済"); assert m.notes_on_page(1)[0]["text"] == "済"
    m.remove_note(1, x); assert m.notes_on_page(1) == []
    m.undo(); assert len(m.notes_on_page(1)) == 1
    out = os.path.join(_TMP, "o.pdf"); m.save(out)
    d = fitz.open(out); assert [a.type[1] for a in d[1].annots()] == ["Text"] and len(d.get_toc()) == 3; d.close()
    m.close()
    print("  model: notes / toc OK")

    # ---- 表示モードの色変換
    pix = fitz.open(pdf_plain)[0].get_pixmap(dpi=36)
    img_w = apply_display_mode(pix, "warm"); img_i = apply_display_mode(pix, "invert"); img_n = apply_display_mode(pix, "normal")
    c = img_w.pixelColor(2, 2); assert (c.red(), c.green(), c.blue()) == (250, 240, 220), (c.red(), c.green(), c.blue())
    c = img_i.pixelColor(2, 2); assert (c.red(), c.green(), c.blue()) == (0, 0, 0)
    c = img_n.pixelColor(2, 2); assert (c.red(), c.green(), c.blue()) == (255, 255, 255)
    print("  display mode conversion OK")

    # ---- GUI
    win = MainWindow(); win.resize(1400, 900); win.show(); wait()
    assert not win.toc_dock.isVisible()
    win.open_file(pdf_toc); wait(100)
    assert win.toc_dock.isVisible(), "目次付き PDF では自動でパネルが開く"
    assert win.toc_tree.topLevelItemCount() == 2 and win.toc_tree.topLevelItem(0).childCount() == 1
    # 目次クリックで移動
    item = win.toc_tree.topLevelItem(1)
    win.toc_tree.setCurrentItem(item); win._on_toc_clicked(item, 0); wait(100)
    assert win.current == 3, win.current
    # しおり追加（入力ダイアログを差し替え）
    win._ask_line = lambda title, label, default="": "重要ページ"
    win.goto(1); wait(100)
    win.add_bookmark(); wait()
    titles = [t[1] for t in win.model.get_toc()]
    assert "重要ページ" in titles and win.toc_tree.topLevelItemCount() == 3, titles
    # 名前変更・削除
    for i in range(win.toc_tree.topLevelItemCount()):
        if win.toc_tree.topLevelItem(i).text(0) == "重要ページ":
            win.toc_tree.setCurrentItem(win.toc_tree.topLevelItem(i))
    win._ask_line = lambda title, label, default="": "重要ページ2"
    win.rename_bookmark(); wait()
    assert "重要ページ2" in [t[1] for t in win.model.get_toc()]
    for i in range(win.toc_tree.topLevelItemCount()):
        if win.toc_tree.topLevelItem(i).text(0) == "重要ページ2":
            win.toc_tree.setCurrentItem(win.toc_tree.topLevelItem(i))
    win.remove_bookmark(); wait()
    assert "重要ページ2" not in [t[1] for t in win.model.get_toc()] and win.toc_tree.topLevelItemCount() == 2
    print("  GUI: toc panel / bookmark add-rename-remove OK")

    # 付箋モード: クリックで追加、右クリックメニューに編集/削除、ツールチップ
    win._ask_text = lambda title, label, default="": "要確認です"
    win.a_note.trigger(); wait()
    assert win.note_mode and win.a_note.isChecked() and win.view.note_mode and win.view.cursor().shape() == Qt.CrossCursor
    win._on_page_clicked(1, QPointF(0.5, 0.3)); wait(100)
    notes = win.model.notes_on_page(1)
    assert len(notes) == 1 and notes[0]["text"] == "要確認です", notes
    assert win.note_mode, "連続して貼れるようモードは継続"
    captured = {}
    win._popup_menu = lambda menu, gpos: captured.setdefault("items", [a.text() for a in menu.actions() if not a.isSeparator()])
    r = notes[0]["rect"]; pr = win.model.doc[1].rect
    frac = QPointF((r.x0 + 4) / pr.width, (r.y0 + 4) / pr.height)
    win._on_page_context(1, frac, QPoint(0, 0)); wait()
    assert "付箋を編集..." in captured["items"] and "付箋を削除" in captured["items"], captured
    captured.clear()
    win._on_page_context(1, QPointF(0.9, 0.9), QPoint(0, 0)); wait()
    assert "付箋を編集..." not in captured["items"] and "ここに付箋を追加..." in captured["items"]
    # 編集 → 削除 → Undo
    win._ask_text = lambda title, label, default="": default + "（済）"
    win.edit_note(1, notes[0]); wait()
    assert win.model.notes_on_page(1)[0]["text"] == "要確認です（済）"
    win.remove_note(1, notes[0]["xref"]); wait()
    assert win.model.notes_on_page(1) == []
    win.undo(); wait()
    assert len(win.model.notes_on_page(1)) == 1
    # Esc で付箋モード終了、押印モードと排他
    win._cancel_modes(); assert not win.note_mode and not win.a_note.isChecked()
    win.a_note.trigger(); win.a_stamp.trigger(); wait()
    assert win.stamp_mode and not win.note_mode, "押印モードに入ると付箋モードは解除"
    win._cancel_modes()
    print("  GUI: note mode / context menu / edit / remove / undo OK")

    # 表示モード: 背景色と描画色、設定の保存
    win.set_display_mode("warm"); wait(100)
    v = win.view; assert "#faf0dc" in v.styleSheet()
    pm = v.pixmap(); img = pm.toImage(); c = img.pixelColor(5, 5)
    assert (c.red(), c.green(), c.blue()) == (250, 240, 220), (c.red(), c.green(), c.blue())
    win.set_display_mode("invert"); wait(100)
    c = win.view.pixmap().toImage().pixelColor(5, 5); assert (c.red(), c.green(), c.blue()) == (0, 0, 0)
    assert win.mode_actions["invert"].isChecked() and "白黒反転" in win.page_label.text()
    win._save_layout()
    win2 = MainWindow(); assert win2.display_mode == "invert", "表示モードが復元される"
    win2.close()
    win.set_display_mode("normal"); wait(100)
    c = win.view.pixmap().toImage().pixelColor(5, 5); assert (c.red(), c.green(), c.blue()) == (255, 255, 255)
    print("  GUI: display modes OK")

    # 目次なし PDF: パネルは自動では開かない（手動では開ける）
    win.toc_dock.hide(); win.model.modified = False
    win.open_file(pdf_plain); wait(100)
    assert not win.toc_dock.isVisible() and win.toc_tree.topLevelItemCount() == 0
    win.a_toc_dock.trigger(); wait(); assert win.toc_dock.isVisible()
    win.model.modified = False; win.close()
    print("notes/toc/display tests passed")


if __name__ == "__main__":
    main()
