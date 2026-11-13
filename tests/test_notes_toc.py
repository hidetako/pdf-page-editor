"""表示モード・付箋・しおり（目次）のテスト。python tests/test_notes_toc.py"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows の cp1252 コンソール対策
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
    x = m.add_note(1, (300, 100), "確認", "山田")
    assert m.note_at(1, (305, 105))["xref"] == x and m.note_at(1, (10, 10)) is None
    m.update_note(1, x, "済"); assert m.notes_on_page(1)[0]["text"] == "済"
    m.remove_note(1, x); assert m.notes_on_page(1) == []
    m.undo(); assert len(m.notes_on_page(1)) == 1
    out = os.path.join(_TMP, "o.pdf"); m.save(out)
    d = fitz.open(out); assert [a.type[1] for a in d[1].annots()] == ["Text"] and len(d.get_toc()) == 3; d.close()
    m.close()
    print("  model: notes / toc OK")

    # ---- モデル: 注釈（マーカー・テキスト注釈・既存注釈の編集）。回転ページでも表示座標で扱える
    from pdfmodel import PasswordRequired
    pdf_txt = os.path.join(_TMP, "txt.pdf")
    d = fitz.open(); pg = d.new_page(width=595, height=842)
    pg.insert_text((72, 100), "Hello World from PDF", fontsize=24)
    pg.insert_text((72, 160), "稟議書 承認済み", fontsize=24, fontname="japan")
    d.save(pdf_txt); d.close()
    m = PdfModel(); m.open(pdf_txt)
    m.doc[0].set_rotation(90)
    words = m.page_words(0)
    hl = m.add_markup(0, "highlight", [w["rect"] for w in words[:2]])
    ul = m.add_markup(0, "underline", [words[4]["rect"]], color=(0, 0, 1))
    so = m.add_markup(0, "strikeout", [words[5]["rect"]])
    ann = m.annots_on_page(0)
    assert [a["kind"] for a in ann] == ["蛍光ペン", "下線", "取り消し線"] and ann[1]["stroke"] == (0.0, 0.0, 1.0), ann
    assert ann[0]["rect"].contains(fitz.Rect(words[0]["rect"])) and ann[0]["rect"].contains(fitz.Rect(words[1]["rect"]))
    assert not ann[0]["movable"] and m.annot_at(0, (words[1]["rect"].x0 + 1, words[1]["rect"].y0 + 1))["xref"] == hl
    try:
        m.move_annot(0, hl, 10, 10); raise AssertionError("マーカーは移動できない")
    except ValueError:
        pass
    ft = m.add_free_text(0, (300, 100), "日本語の\nメモ", fontsize=14)
    a = m.annot_at(0, (305, 105))
    assert a["xref"] == ft and a["kind"] == "文字" and a["fontsize"] == 14.0 and a["movable"] and a["text"] == "日本語の\nメモ", a
    assert a["rect"].x0 == 300 and a["rect"].y0 == 100 and a["rect"].height > 14 * 2, a["rect"]
    # 文字の書き込みは、動かしたり直したりすると作り直すので、注釈の番号 (xref) は戻り値で受け取る
    ft = m.move_annot(0, ft, 50, 20)
    assert m.annot_at(0, (355, 125))["xref"] == ft and m.annot_at(0, (305, 105)) is None
    ft = m.set_annot_text(0, ft, "変更後"); ft = m.set_annot_color(0, ft, (0, 0, 1)); ft = m.set_annot_fontsize(0, ft, 20)
    a = [z for z in m.annots_on_page(0) if z["xref"] == ft][0]
    assert a["text"] == "変更後" and a["stroke"] == (0.0, 0.0, 1.0) and a["fontsize"] == 20.0 and a["rect"].x0 == 350 and a["rect"].y0 == 120, a
    m.set_annot_color(0, hl, (0, 1, 0)); assert m.annots_on_page(0)[0]["stroke"] == (0.0, 1.0, 0.0)
    pix = m.render(0, 1.0); r = a["rect"]
    blues = sum(1 for yy in range(int(r.y0), int(r.y1)) for xx in range(int(r.x0), int(r.x1)) if pix.pixel(xx, yy)[2] > 150 and pix.pixel(xx, yy)[0] < 100)
    assert blues > 20, "書き込んだ文字が描画される"
    m.remove_annot(0, so); assert [z["kind"] for z in m.annots_on_page(0)] == ["蛍光ペン", "下線", "文字"]
    m.undo(); assert len(m.annots_on_page(0)) == 4
    m.undo(); m.undo(); assert m.annots_on_page(0)[-1]["fontsize"] == 14.0, "文字サイズ変更を Undo"
    out = os.path.join(_TMP, "annots.pdf"); m.save(out); m.close()
    d = fitz.open(out); assert [x.type[1] for x in d[0].annots()] == ["Highlight", "Underline", "StrikeOut", "FreeText"]; d.close()
    # 他のソフトが作った注釈として開き直しても編集できる
    m = PdfModel(); m.open(out)
    a = m.annots_on_page(0)
    assert a[3]["text"] == "変更後" and a[3]["stroke"] == (0.0, 0.0, 1.0) and a[0]["stroke"] == (1.0, 1.0, 0.0), a
    x = m.set_annot_text(0, a[3]["xref"], "再編集"); m.move_annot(0, x, -10, -10)
    assert m.annots_on_page(0)[3]["text"] == "再編集"
    # 回転を戻しても注釈の表示位置は追従する（ページ座標は不変）
    r_before = m.annots_on_page(0)[3]["rect"]
    m.rotate([0], -90)
    r_after = m.annots_on_page(0)[3]["rect"]
    assert r_before != r_after and abs(r_after.width - r_before.height) < 0.01, (r_before, r_after)
    m.close()
    print("  model: annots OK")

    # ---- モデル: パスワード付き PDF（開く・保存・解除・制限）
    enc = os.path.join(_TMP, "enc.pdf")
    d = fitz.open(); d.new_page(); d.new_page(); d.save(enc, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="own"); d.close()
    m = PdfModel()
    for pw, wrong in ((None, False), ("bad", True)):
        try:
            m.open(enc, pw); raise AssertionError("開けてはいけない")
        except PasswordRequired as e:
            assert e.wrong is wrong and e.path == enc
    assert m.doc is None
    m.open(enc, "pw"); assert m.page_count == 2 and m.password == "pw" and m.restrictions() == []
    m.rotate([1], 90); m.undo(); m.rotate([1], 90)
    assert m.doc[1].rotation == 90 and m.password == "pw", "Undo 後もパスワードは保持"
    out = os.path.join(_TMP, "enc_out.pdf"); m.save(out)
    d = fitz.open(out); assert d.needs_pass and d.authenticate("pw") and d[1].rotation == 90; d.close()
    m.set_password(None); assert m.modified; m.save()
    d = fitz.open(out); assert not d.needs_pass; d.close()
    m.set_password("new"); m.save()
    m2 = PdfModel(); m2.open(out, "new"); assert m2.page_count == 2; m2.close()
    # 閲覧パスワード無しの制限付き PDF は開けるが、制限が分かる
    lim = os.path.join(_TMP, "limited.pdf")
    d = fitz.open(); d.new_page(); d.save(lim, encryption=fitz.PDF_ENCRYPT_AES_128, owner_pw="own", permissions=fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_COPY); d.close()
    m3 = PdfModel(); m3.open(lim)
    assert m3.password is None and m3.restrictions() == ["印刷", "編集", "注釈"], m3.restrictions()
    # パスワード付き PDF の挿入
    m4 = PdfModel(); m4.open(pdf_plain)
    try:
        m4.insert_file(out); raise AssertionError("パスワード無しで挿入できてはいけない")
    except PasswordRequired as e:
        assert not e.wrong
    assert m4.insert_file(out, password="new") == 2 and m4.page_count == 4
    m.close(); m3.close(); m4.close()
    print("  model: password OK")

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
