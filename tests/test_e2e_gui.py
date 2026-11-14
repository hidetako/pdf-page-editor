"""GUI の E2E テスト（オフスクリーン）。python tests/test_e2e_gui.py で実行可。

実際の MainWindow を起動し、QAction・マウス・キー操作で一連の業務フロー
（開く → 閲覧 → ページ編集 → 押印 → 保存/抽出 → 終了確認）を通す。
ファイル選択・確認ダイアログだけ差し替え、それ以外は本番コードを通す。
環境変数 E2E_SHOTS にディレクトリを指定すると各段階のスクリーンショットを保存する。
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows の cp1252 コンソール対策
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="pdfpe_e2e_")
os.environ["APPDATA"] = os.path.join(_TMP, "appdata")  # 印鑑ライブラリを隔離
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "xdg")  # QSettings (Linux) を隔離
_FONTS = os.path.join(_TMP, "fonts")                     # 書き込み用フォントを隔離（偽の「メイリオ」を置く）
os.makedirs(_FONTS)
os.environ["PDFPE_FONT_DIRS"] = _FONTS
os.environ["PDFPE_FONT_DIRS_ONLY"] = "1"                 # 実機のフォントは使わない
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pymupdf as fitz
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QUrl, Qt
from PySide6.QtGui import QColor, QDropEvent, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

import app as appmod
import stamp_ui
from app import MainWindow

SHOTS = os.environ.get("E2E_SHOTS")
_step = [0]


def step(name):
    _step[0] += 1
    print(f"  [{_step[0]:02d}] {name}")


def shot(win, name):
    if SHOTS:
        QApplication.processEvents()
        win.grab().save(os.path.join(SHOTS, f"{_step[0]:02d}_{name}.png"))


def make_pdf(path, n, label):
    d = fitz.open()
    for i in range(n):
        p = d.new_page(width=595, height=842)
        p.insert_text((72, 100), f"{label}-{i + 1}", fontsize=40)
    d.save(path)
    d.close()


def texts(path):
    d = fitz.open(path)
    out = [p.get_text().strip() for p in d]
    rots = [p.rotation for p in d]
    d.close()
    return out, rots


def red_center(path, page=0, dpi=288):
    """ページ上の朱色領域の中心と幅を pt で返す（細い枠線を拾うため高解像度で描画）。"""
    pix = fitz.open(path)[page].get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    m = (arr[:, :, 0] > 150) & (arr[:, :, 1] < 110) & (arr[:, :, 2] < 110)
    ys, xs = np.where(m)
    assert len(xs), "朱色が見つからない"
    k = 72 / dpi
    return (xs.min() + xs.max()) / 2 * k, (ys.min() + ys.max()) / 2 * k, (xs.max() - xs.min()) * k


def wait(ms=50):
    QTest.qWait(ms)


def mouse_move(widget, pos, button=Qt.NoButton):
    """マウス移動イベントをウィジェットへ直接送る。
    QTest.mouseMove はプラットフォーム経由（カーソル位置→下のウィジェット）で、Windows のオフスクリーンでは届かないことがある。"""
    from PySide6.QtGui import QMouseEvent
    ev = QMouseEvent(QEvent.MouseMove, QPointF(pos), QPointF(widget.mapToGlobal(pos)), Qt.NoButton, button, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)


def thumb_center(win, row):
    r = win.thumbs.visualItemRect(win.thumbs.item(row))
    return r.center()


def near(a, b, tol=0.02):
    """色などの数値の組が、ほぼ等しいか。"""
    return a is not None and b is not None and len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    with open(os.path.join(_FONTS, "meiryo.ttc"), "wb") as f:      # 中身は内蔵の TrueType フォント
        f.write(fitz.Font("japan").buffer)
    a = os.path.join(_TMP, "a.pdf"); make_pdf(a, 5, "A")
    b = os.path.join(_TMP, "b.pdf"); make_pdf(b, 2, "B")
    img = os.path.join(_TMP, "img.png")
    fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100), 0).save(img)
    broken = os.path.join(_TMP, "broken.pdf")
    with open(broken, "wb") as f:
        f.write(b"not a pdf at all")
    locked = os.path.join(_TMP, "locked.pdf")
    d = fitz.open(); d.new_page(); d.save(locked, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="x"); d.close()

    errors = []
    QMessageBox.critical = staticmethod(lambda *args, **kw: errors.append(args[2]) or QMessageBox.Ok)
    infos = []
    QMessageBox.information = staticmethod(lambda *args, **kw: infos.append(args[2]) or QMessageBox.Ok)

    win = MainWindow()
    win.show()
    wait(100)

    # ---------------------------------------------------------------- 起動直後
    step("起動: 文書なしでは編集系アクションが無効")
    assert win.windowTitle() == appmod.APP_NAME
    assert win.thumbs.count() == 0
    for act in (win.a_save, win.a_saveas, win.a_insert, win.a_print, win.a_delete, win.a_rotl, win.a_rotr,
                win.a_up, win.a_down, win.a_extract, win.a_undo, win.a_redo):
        assert not act.isEnabled(), act.text()
    assert win.a_open.isEnabled()
    assert win.view is None and win.empty_label.isVisible() and "ドラッグ" in win.empty_label.text()
    assert win.stamp_dock_widget.list.count() == 5, "既定の印鑑 5 種"
    assert not win.stamp_dock.isVisible(), "起動時は印鑑パネルは非表示"
    win.a_stamp.trigger()
    assert not win.stamp_mode and not win.a_stamp.isChecked(), "文書なしでは押印モードに入れない"
    shot(win, "empty")

    # ---------------------------------------------------------------- 開く（エラー系）
    step("開く: 壊れた PDF はエラー表示で継続。パスワード付き PDF は入力を求め、キャンセル / 間違い→再入力 / 正解で開く")
    QFileDialog.getOpenFileName = staticmethod(lambda *a_, **k: (broken, ""))
    win.a_open.trigger(); wait()
    assert len(errors) == 1 and win.model.doc is None
    QFileDialog.getOpenFileName = staticmethod(lambda *a_, **k: (locked, ""))
    asked = []
    win._ask_password = lambda path, wrong=False: asked.append(wrong) or None     # キャンセル
    win.a_open.trigger(); wait()
    assert asked == [False] and win.model.doc is None and len(errors) == 1, "キャンセルは何も開かずエラーも出ない"
    answers = iter(["bad", "x"])
    win._ask_password = lambda path, wrong=False: asked.append(wrong) or next(answers)
    win.a_open.trigger(); wait(100)
    assert asked == [False, False, True], asked
    assert win.model.page_count == 1 and win.model.password == "x" and "パスワード保護" in win.page_label.text()
    assert "パスワード付き" in win.statusBar().currentMessage()
    # 編集して保存してもパスワード付きのまま。解除すると平文になる
    enc_out = os.path.join(_TMP, "locked_out.pdf")
    QFileDialog.getSaveFileName = staticmethod(lambda *a_, **k: (enc_out, ""))
    win.a_rotr.trigger(); wait()
    win.a_saveas.trigger(); wait(100)
    _d = fitz.open(enc_out); assert _d.needs_pass and _d.authenticate("x") and _d[0].rotation == 90; _d.close()
    win._ask_new_password = lambda current: ""     # 空 = 解除
    win.a_password.trigger(); wait()
    assert win.model.password is None and win.model.modified and "解除" in win.statusBar().currentMessage()
    win.a_save.trigger(); wait(100)
    _d = fitz.open(enc_out); assert not _d.needs_pass; _d.close()
    win._ask_new_password = lambda current: "newpw"
    win.a_password.trigger(); win.a_save.trigger(); wait(100)
    _d = fitz.open(enc_out); assert _d.needs_pass and _d.authenticate("newpw") == 6; _d.close()
    assert "パスワード保護" in win.page_label.text()
    win.model.close(); win.refresh_thumbs(); wait()
    QFileDialog.getOpenFileName = staticmethod(lambda *a_, **k: ("", ""))
    win.a_open.trigger(); wait()
    assert len(errors) == 1 and win.model.doc is None, "キャンセルは何も起きない"

    # ---------------------------------------------------------------- 開く（正常）
    step("開く: Ctrl+O 相当で 5 ページを開き、サムネイル・タイトル・ステータスが更新")
    QFileDialog.getOpenFileName = staticmethod(lambda *a_, **k: (a, ""))
    win.a_open.trigger(); wait(100)
    assert win.model.page_count == 5 and win.thumbs.count() == 5
    assert win.windowTitle() == f"a.pdf - {appmod.APP_NAME}"
    assert win.current == 0 and win.thumbs.selected_indices() == [0]
    assert win.page_label.text().startswith("ページ 1 / 5") and "幅合わせ" in win.page_label.text()
    assert "210 x 297 mm" in win.page_label.text()
    assert len(win.views) == 5 and not win.view.pixmap().isNull() and not win.empty_label.isVisible()
    for act in (win.a_save, win.a_saveas, win.a_insert, win.a_delete, win.a_rotl, win.a_rotr, win.a_up, win.a_down, win.a_extract):
        assert act.isEnabled(), act.text()
    assert not win.a_undo.isEnabled()
    shot(win, "opened")

    # ---------------------------------------------------------------- 閲覧
    step("閲覧: PgDown/PgUp、サムネイルクリック、Ctrl+クリック複数選択")
    QTest.keyClick(win, Qt.Key_PageDown); wait()
    assert win.current == 1 and win.page_label.text().startswith("ページ 2 / 5")
    QTest.keyClick(win, Qt.Key_PageUp); wait()
    assert win.current == 0
    win.goto(99); assert win.current == 4, "範囲外はクランプ"
    win.goto(-5); assert win.current == 0
    QTest.mouseClick(win.thumbs.viewport(), Qt.LeftButton, Qt.NoModifier, thumb_center(win, 2)); wait()
    assert win.current == 2 and win.thumbs.selected_indices() == [2]
    QTest.mouseClick(win.thumbs.viewport(), Qt.LeftButton, Qt.ControlModifier, thumb_center(win, 4)); wait()
    assert win.thumbs.selected_indices() == [2, 4]
    assert win.a_delete.isEnabled()

    step("連続スクロール: 全ページが縦に並び、スクロールで現在ページとサムネイル選択が追従、遠いページは未描画")
    win.set_zoom(1.0); wait()   # ページの高さを表示幅やフォントに依存させない（A4 = 842px）
    win.goto(0); wait()
    vbar = win.scroll.verticalScrollBar()
    assert vbar.maximum() > 0 and win.pages_widget.height() > win.scroll.viewport().height() * 3, (
        win.pages_widget.height(), win.scroll.viewport().height(), [v.height() for v in win.views])
    assert win._page_top(1) == appmod.PAGE_GAP + win.views[0].height() + appmod.PAGE_GAP
    assert win.views[0].rendered_key is not None and win.views[4].rendered_key is None, "表示範囲外は遅延描画"
    vbar.setValue(vbar.maximum()); wait(100)
    assert win.current == 4 and win.thumbs.currentRow() == 4 and win.thumbs.selected_indices() == [4]
    assert win.views[4].rendered_key is not None and win.views[0].rendered_key is None, "離れたページの画像は捨てる"
    assert win.page_label.text().startswith("ページ 5 / 5")
    vbar.setValue(0); wait(100)
    assert win.current == 0 and win.thumbs.selected_indices() == [0]
    QTest.keyClick(win, Qt.Key_PageDown); wait(100)
    assert win.current == 1 and vbar.value() == win._page_top(1) - appmod.PAGE_GAP, "PgDn は次ページの上端へ"
    # ページの途中では、大きく見えている方が現在ページ（ページ 3 がほぼ全面なら 3、ページ 3 が 10px だけなら 2）
    vbar.setValue(win._page_top(3) - appmod.PAGE_GAP - 10); wait(100)
    assert win.current == 3, (win.current, vbar.value(), win._page_top(3), win.scroll.viewport().height())
    vh = win.scroll.viewport().height()
    vbar.setValue(win._page_top(3) - vh + 10); wait(100)
    assert win.current == 2, (win.current, vbar.value(), win._page_top(3), vh)
    win.zoom_fit(); win.goto(0); wait()

    step("ズーム: 拡大/縮小/幅合わせ/100%/Ctrl+ホイール")
    win.goto(0); wait()
    fit_w = win.view.pixmap().width()
    QTest.keyClick(win, Qt.Key_Equal, Qt.ControlModifier); wait()
    assert not win.fit_width and "ズーム" in win.page_label.text() and "幅合わせ" not in win.page_label.text()
    assert win.zoom in appmod.ZOOM_STEPS, "幅合わせから拡大すると段階のどれかになる"
    # 幅合わせの倍率は表示幅次第（狭い環境では最小段階を下回る）ので、段階の比較は 100% を起点に行う
    QTest.keyClick(win, Qt.Key_1, Qt.ControlModifier); wait()
    assert win.zoom == 1.0 and "100%" in win.page_label.text()
    QTest.keyClick(win, Qt.Key_Equal, Qt.ControlModifier); wait()
    assert win.zoom == 1.25, win.zoom
    QTest.keyClick(win, Qt.Key_Minus, Qt.ControlModifier); wait()
    assert win.zoom == 1.0, win.zoom
    assert abs(win.view.pixmap().width() / win.view.pixmap().devicePixelRatio() - 595) <= 1
    ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120), Qt.NoButton,
                     Qt.ControlModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(win, ev); wait()
    assert win.zoom == 1.25, win.zoom
    ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, -120), Qt.NoButton,
                     Qt.ControlModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(win, ev); wait()
    assert win.zoom == 1.0
    QTest.keyClick(win, Qt.Key_0, Qt.ControlModifier); wait()
    assert win.fit_width and win.view.pixmap().width() == fit_w
    shot(win, "zoom_fit")

    # ---------------------------------------------------------------- ページ編集
    step("回転: 右回転 → 90°、タイトルに * 、ステータスに回転表示、サムネ横長")
    win.goto(0); wait()
    win.a_rotr.trigger(); wait()
    assert win.model.doc[0].rotation == 90 and win.model.modified
    assert win.windowTitle() == f"a.pdf * - {appmod.APP_NAME}"
    assert "回転 90°" in win.page_label.text()
    ic = win.thumbs.item(0).icon().pixmap(appmod.THUMB_W, appmod.THUMB_W * 2)
    assert ic.width() > ic.height(), "回転後サムネイルは横長"
    assert win.a_undo.isEnabled() and not win.a_redo.isEnabled()
    win.a_rotl.trigger(); wait()
    assert win.model.doc[0].rotation == 0
    shot(win, "rotated")

    step("削除: Delete キーで選択ページ削除、選択が次ページへ")
    win.goto(1); wait()                     # A-2
    QTest.keyClick(win, Qt.Key_Delete); wait()
    assert win.model.page_count == 4 and win.thumbs.count() == 4
    assert [p.get_text().strip() for p in win.model.doc] == ["A-1", "A-3", "A-4", "A-5"]
    assert win.current == 1 and win.page_label.text().startswith("ページ 2 / 4")

    step("削除: 全選択して削除はエラーで拒否")
    win.thumbs.selectAll(); wait()
    assert win.thumbs.selected_indices() == [0, 1, 2, 3]
    win.a_delete.trigger(); wait()
    assert "全ページ" in errors[-1] and win.model.page_count == 4

    step("移動: Ctrl+Down / Ctrl+Up で前後移動、選択が追従")
    win.goto(0); wait()                     # A-1
    QTest.keyClick(win, Qt.Key_Down, Qt.ControlModifier); wait()
    assert [p.get_text().strip() for p in win.model.doc] == ["A-3", "A-1", "A-4", "A-5"]
    assert win.thumbs.selected_indices() == [1] and win.current == 1
    QTest.keyClick(win, Qt.Key_Up, Qt.ControlModifier); wait()
    assert [p.get_text().strip() for p in win.model.doc] == ["A-1", "A-3", "A-4", "A-5"]
    assert win.current == 0
    QTest.keyClick(win, Qt.Key_Up, Qt.ControlModifier); wait()
    assert win.current == 0 and win.model.page_count == 4, "先頭ページの前移動は無視"

    step("並べ替え: サムネイル内移動（ドロップ完了シグナル）→ モデルに反映")
    # 内部 D&D のドロップ後状態（行の移動）を再現し、dropEvent が発火する order_changed を通す
    m = win.thumbs.model()
    win.thumbs.blockSignals(True)
    assert m.moveRow(m.index(3, 0).parent(), 3, m.index(0, 0).parent(), 0)  # 末尾 A-5 を先頭へ
    win.thumbs.blockSignals(False)
    assert win.thumbs.current_order() == [3, 0, 1, 2]
    win.thumbs.order_changed.emit(); wait()
    assert [p.get_text().strip() for p in win.model.doc] == ["A-5", "A-1", "A-3", "A-4"]
    assert win.thumbs.current_order() == [0, 1, 2, 3], "リフレッシュ後は連番に戻る"
    shot(win, "reordered")

    step("元に戻す / やり直し: Ctrl+Z / Ctrl+Y、ボタン活性が追従")
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier); wait()
    assert [p.get_text().strip() for p in win.model.doc] == ["A-1", "A-3", "A-4", "A-5"]
    assert win.a_redo.isEnabled()
    QTest.keyClick(win, Qt.Key_Y, Qt.ControlModifier); wait()
    assert [p.get_text().strip() for p in win.model.doc] == ["A-5", "A-1", "A-3", "A-4"]
    assert not win.a_redo.isEnabled()
    for _ in range(10):
        win.a_undo.trigger(); wait(10)
    assert [p.get_text().strip() for p in win.model.doc] == ["A-1", "A-2", "A-3", "A-4", "A-5"]
    assert not win.a_undo.isEnabled() and win.a_redo.isEnabled()
    for _ in range(10):
        win.a_redo.trigger(); wait(10)
    assert [p.get_text().strip() for p in win.model.doc] == ["A-5", "A-1", "A-3", "A-4"]
    assert not win.a_redo.isEnabled()

    step("挿入: Ctrl+I で PDF と画像を選択ページの後ろへ、挿入ページが選択される")
    win.goto(0); wait()
    QFileDialog.getOpenFileNames = staticmethod(lambda *a_, **k: ([b, img], ""))
    QTest.keyClick(win, Qt.Key_I, Qt.ControlModifier); wait(100)
    assert win.model.page_count == 7
    assert [p.get_text().strip() for p in win.model.doc] == ["A-5", "B-1", "B-2", "", "A-1", "A-3", "A-4"]
    assert win.thumbs.selected_indices() == [1, 2, 3] and win.current == 1
    assert win.model.doc[3].rect.width > win.model.doc[3].rect.height, "画像ページは画像の縦横比"
    QFileDialog.getOpenFileNames = staticmethod(lambda *a_, **k: ([locked], ""))
    asked_ins = []
    win._ask_password = lambda path, wrong=False: asked_ins.append(wrong) or None    # パスワード付きはキャンセル → 挿入しない
    win.a_insert.trigger(); wait()
    assert asked_ins == [False] and win.model.page_count == 7
    win._ask_password = lambda path, wrong=False: "x"
    win.a_insert.trigger(); wait(100)
    assert win.model.page_count == 8 and win.thumbs.selected_indices() == [4], "正しいパスワードで挿入される"
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier); wait()
    assert win.model.page_count == 7
    QFileDialog.getOpenFileNames = staticmethod(lambda *a_, **k: ([], ""))
    win.a_insert.trigger(); wait()
    assert win.model.page_count == 7
    shot(win, "inserted")

    # ---------------------------------------------------------------- 電子印鑑
    step("押印: F2 でモード ON、ゴースト表示、クリック位置に朱印、Esc で OFF")
    win.goto(4); wait()                     # A-1
    dock = win.stamp_dock_widget
    dock.list.setCurrentRow(3)              # 「済」
    assert dock.current_stamp().name == "済"
    QTest.keyClick(win, Qt.Key_F2); wait(250)   # パネルが自動で開いて表示領域が変わり、幅合わせの再計算が落ち着くまで待つ
    assert win.stamp_mode and win.a_stamp.isChecked() and dock.btn_mode.isChecked()
    assert win.stamp_dock.isVisible(), "押印モードに入ると印鑑パネルが開く"
    assert win.view.stamp_mode and win.view.ghost is not None
    assert "押印モード" in win.statusBar().currentMessage()
    g = win.view.pix_geometry(); ox, oy, pw, ph = g
    target = QPoint(int(ox + pw * 0.75), int(oy + ph * 0.25))
    mouse_move(win.view, target); wait()
    assert win.view.ghost_pos is not None, "マウス追従ゴースト"
    shot(win, "stamp_ghost")
    before_undo = len(win.model._undo)
    QTest.mouseClick(win.view, Qt.LeftButton, Qt.NoModifier, target); wait(100)
    assert len(win.model._undo) == before_undo + 1 and win.model.modified
    assert "押印しました（ページ 5）" in win.statusBar().currentMessage(), (win.statusBar().currentMessage(), win.current)
    assert win.stamp_mode, "連続押印のためモードは継続"
    assert win.current == 4 and win.model.page_count == 7
    # ページ外（ページ間の余白）のクリックは押印しない
    QTest.mouseClick(win.pages_widget, Qt.LeftButton, Qt.NoModifier, QPoint(1, 1)); wait()
    assert len(win.model._undo) == before_undo + 1
    shot(win, "stamped")
    QTest.keyClick(win, Qt.Key_Escape); wait()
    assert not win.stamp_mode and not win.a_stamp.isChecked() and not dock.btn_mode.isChecked()
    assert win.view.ghost is None and win.view.cursor().shape() == Qt.ArrowCursor

    step("押印の取り消し: 印影を右クリック → 「この押印を取り消す」、余白では無効項目、Ctrl+Z で戻る")
    from PySide6.QtGui import QContextMenuEvent
    picked = []
    def fake_popup(menu, gpos):  # QMenu.exec はオフスクリーンでも実際にブロックするので差し替える
        acts = [x for x in menu.actions() if not x.isSeparator()]
        picked.append([(x.text(), x.isEnabled()) for x in acts])
        for x in acts:  # 押印に関する項目だけを対象にする（付箋・しおりの項目はダイアログを開くため）
            if x.isEnabled() and "押印" in x.text():
                x.trigger(); break
    win._popup_menu = fake_popup
    QApplication.sendEvent(win.pages_widget, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(1, 1), win.pages_widget.mapToGlobal(QPoint(1, 1)))); wait()
    assert not picked, "ページ外の右クリックは押印メニューを出さない"
    # ページ外の右クリックは Qt 標準のツールバー/パネル表示メニューになる（開いたままだとショートカットが効かないので閉じる）
    pop = QApplication.activePopupWidget()
    assert pop is not None and any(x.text() == "電子印鑑" for x in pop.actions())
    pop.close(); wait()
    g = win.view.pix_geometry(); ox, oy, pw, ph = g
    blank = QPoint(int(ox + pw * 0.2), int(oy + ph * 0.8))
    QApplication.sendEvent(win.view, QContextMenuEvent(QContextMenuEvent.Mouse, blank, win.view.mapToGlobal(blank))); wait()
    assert picked[-1][0] == ("ここに取り消せる押印はありません", False), picked[-1]
    assert len(win.model.stamps_on_page(4)) == 1
    QApplication.sendEvent(win.view, QContextMenuEvent(QContextMenuEvent.Mouse, target, win.view.mapToGlobal(target))); wait(100)
    assert picked[-1][0] == ("この押印を取り消す", True), picked[-1]
    assert win.model.stamps_on_page(4) == [] and "取り消しました" in win.statusBar().currentMessage()
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier); wait()
    assert len(win.model.stamps_on_page(4)) == 1, "Undo で押印が戻る"
    QTest.keyClick(win, Qt.Key_Y, Qt.ControlModifier); wait()
    assert win.model.stamps_on_page(4) == [], "Redo で再び消える"
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier); wait()
    shot(win, "stamp_removed_undone")

    step("押印: 印鑑パネルのボタンから ON/OFF、回転ページでも表示位置どおり")
    win.goto(0); wait()
    win.a_rotr.trigger(); wait()            # A-5 を 90° 回転
    dock.btn_mode.click(); wait()
    assert win.stamp_mode and win.a_stamp.isChecked()
    g = win.view.pix_geometry(); ox, oy, pw, ph = g
    QTest.mouseClick(win.view, Qt.LeftButton, Qt.NoModifier, QPoint(int(ox + pw * 0.25), int(oy + ph * 0.5))); wait(100)
    dock.btn_mode.click(); wait()
    assert not win.stamp_mode

    step("押印設定: サイズ変更でゴーストが変わる、日付・不透明度・かすれ・傾きで描画可")
    QTest.keyClick(win, Qt.Key_F2); wait()
    g0 = win.view.ghost.width()
    dock.sp_size.setValue(30); wait()
    assert win.view.ghost.width() > g0 * 2
    dock.sp_size.setValue(0); wait()
    assert win.view.ghost.width() == g0
    dock.sl_opacity.setValue(50); dock.sl_kasure.setValue(60); dock.sp_tilt.setValue(10); wait()
    dock.list.setCurrentRow(1); wait()      # データネーム印（日付入り）
    assert win.view.ghost is not None
    win.goto(5); wait()                     # A-3
    g = win.view.pix_geometry(); ox, oy, pw, ph = g
    n0 = len(win.model._undo)
    QTest.mouseClick(win.view, Qt.LeftButton, Qt.NoModifier, QPoint(int(ox + pw * 0.5), int(oy + ph * 0.5))); wait(100)
    assert len(win.model._undo) == n0 + 1
    dock.sl_opacity.setValue(100); dock.sl_kasure.setValue(0); dock.sp_tilt.setValue(0)
    QTest.keyClick(win, Qt.Key_Escape); wait()

    step("印鑑パネル: 新規 / 編集 / 複製 / 削除 / エクスポート / インポート / PNG 保存")
    lib_path = dock.lib.path
    assert lib_path.startswith(os.environ["APPDATA"]) and os.path.exists(lib_path)

    def accept_dialog(self):  # 編集ダイアログ: 文字を変えて OK
        self.ed_name.setText("E2E印"); self.ed_lines.setPlainText("検査"); self.sp_size.setValue(14)
        return QDialog.Accepted
    stamp_ui.StampEditDialog.exec = accept_dialog
    n = dock.list.count()
    dock.new_stamp(); wait()
    assert dock.list.count() == n + 1 and dock.current_stamp().name == "E2E印" and dock.current_stamp().lines == ["検査"]
    dock.edit_stamp(); wait()
    assert dock.list.count() == n + 1 and dock.current_stamp().size_mm == 14
    dock.dup_stamp(); wait()
    assert dock.list.count() == n + 2 and dock.current_stamp().name == "E2E印 のコピー"
    QMessageBox.question = staticmethod(lambda *a_, **k: QMessageBox.Yes)
    dock.del_stamp(); wait()
    assert dock.list.count() == n + 1
    import json
    with open(lib_path, encoding="utf-8") as f:
        assert any(s["name"] == "E2E印" for s in json.load(f)["stamps"]), "ライブラリが保存されている"
    exp = os.path.join(_TMP, "exp.json")
    QFileDialog.getSaveFileName = staticmethod(lambda *a_, **k: (exp, ""))
    dock.export_json(); wait()
    assert os.path.exists(exp)
    QFileDialog.getOpenFileNames = staticmethod(lambda *a_, **k: ([exp], ""))
    dock.import_json(); wait()
    assert dock.list.count() == 2 * (n + 1) and infos and "件" in infos[-1]
    # おまかせ電子印鑑 (.entstmp) と壊れたファイルをまとめてインポート → 1 件取り込み + 失敗一覧
    ent = os.path.join(_TMP, "印影A.entstmp")
    with open(ent, "w", encoding="utf-8-sig") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?><ENTSTMP product="EntrustStampForBusiness" version="1.0.2"><stampEntities>'
                f'<stampEntity class="ScannerStamp" image="{__import__("base64").b64encode(open(img, "rb").read()).decode()}" />'
                '</stampEntities></ENTSTMP>')
    QFileDialog.getOpenFileNames = staticmethod(lambda *a_, **k: ([ent, broken], ""))
    n_before = dock.list.count()
    dock.import_json(); wait()
    assert dock.list.count() == n_before + 1 and dock.lib.stamps[-1].name == "印影A" and dock.lib.stamps[-1].kind == "image"
    assert "1 件" in infos[-1] and "15 mm" in infos[-1] and "broken.pdf" in infos[-1], infos[-1]
    png_out = os.path.join(_TMP, "stamp.png")
    QFileDialog.getSaveFileName = staticmethod(lambda *a_, **k: (png_out, ""))
    dock.list.setCurrentRow(0)
    dock.export_png(); wait()
    pm = fitz.Pixmap(png_out)
    assert pm.alpha and pm.width > 100, "透過 PNG が書き出される"
    # 画像から印鑑（白地透過）
    QFileDialog.getOpenFileName = staticmethod(lambda *a_, **k: (img, ""))
    QMessageBox.question = staticmethod(lambda *a_, **k: QMessageBox.Yes)
    stamp_ui.StampEditDialog.exec = lambda self: QDialog.Accepted
    n2 = dock.list.count()
    dock.from_image(); wait()
    assert dock.list.count() == n2 + 1 and dock.current_stamp().kind == "image"
    QTest.keyClick(win, Qt.Key_F2); wait()
    assert win.stamp_mode and win.view.ghost is not None, "画像印でもゴーストが出る"
    QTest.keyClick(win, Qt.Key_Escape); wait()
    # 画像印の編集ダイアログ: 色を選ぶと「印色で塗り直す」が自動 ON になり、保存される
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog
    QColorDialog.getColor = staticmethod(lambda *a_, **k: QColor(20, 40, 220))
    def recolor_dialog(self):
        assert not self.cb_recolor.isChecked()
        self.pick_color()
        assert self.cb_recolor.isChecked()
        return QDialog.Accepted
    stamp_ui.StampEditDialog.exec = recolor_dialog
    dock.edit_stamp(); wait()
    st_img = dock.current_stamp()
    assert st_img.kind == "image" and st_img.recolor and st_img.color[2] > 0.8
    pm_img = fitz.Pixmap(dock.render_current())
    arr_img = np.frombuffer(pm_img.samples, dtype=np.uint8).reshape(pm_img.height, pm_img.width, 4)
    ink_px = arr_img[arr_img[:, :, 3] > 120]
    assert len(ink_px) and (ink_px[:, 2] / ink_px[:, 3].astype(float) > 0.8).all(), "塗り直した画像印は青で描かれる"
    shot(win, "stamp_panel")

    # ---------------------------------------------------------------- 保存・抽出
    step("抽出: 選択 2 ページだけ別 PDF へ、元文書は不変")
    win.goto(0); wait()
    QTest.mouseClick(win.thumbs.viewport(), Qt.LeftButton, Qt.ControlModifier, thumb_center(win, 4)); wait()
    assert win.thumbs.selected_indices() == [0, 4]
    ex = os.path.join(_TMP, "ex")       # 拡張子なし → .pdf が補われる
    QFileDialog.getSaveFileName = staticmethod(lambda *a_, **k: (ex, ""))
    win.a_extract.trigger(); wait()
    assert os.path.exists(ex + ".pdf")
    t, r = texts(ex + ".pdf")
    assert t == ["A-5", "A-1"] and r == [90, 0], (t, r)
    assert win.model.page_count == 7 and "2 ページを抽出" in win.statusBar().currentMessage()
    assert red_center(ex + ".pdf", 1)[2] > 10, "抽出ページに押印が残る"

    step("印刷: Ctrl+P → ダイアログ（差し替え）→ 範囲指定 / 現在のページ / 選択ページを PDF プリンターへ、キャンセル")
    from PySide6.QtPrintSupport import QPrintDialog, QPrinter
    assert win.a_print.isEnabled()
    def accept_with(path, setup):
        def _accept(dlg):
            p = dlg.printer()
            p.setOutputFormat(QPrinter.PdfFormat); p.setOutputFileName(path)
            setup(p)
            return QPrintDialog.Accepted
        return _accept
    def printed(path):
        pd = fitz.open(path)
        n = pd.page_count
        px = pd[0].get_pixmap(dpi=36)
        dark = int(np.frombuffer(px.samples, dtype=np.uint8).min())
        pd.close()
        return n, dark
    # 範囲指定 1〜2 ページ（1 ページ目は 90° 回転した横長ページ → 用紙に合わせて回転して印刷）
    p1 = os.path.join(_TMP, "print_range.pdf")
    win._exec_print_dialog = accept_with(p1, lambda p: (p.setPrintRange(QPrinter.PageRange), p.setFromTo(1, 2)))
    QTest.keyClick(win, Qt.Key_P, Qt.ControlModifier); wait(300)
    assert os.path.exists(p1) and "2 ページを印刷しました" in win.statusBar().currentMessage(), win.statusBar().currentMessage()
    n, dark = printed(p1); assert n == 2 and dark < 200, (n, dark)
    pd = fitz.open(p1); r0 = pd[0].rect; pd.close()
    assert r0.width < r0.height, "出力用紙は縦（横長ページは回転して収める）"
    # 現在のページ
    p2 = os.path.join(_TMP, "print_current.pdf")
    win._exec_print_dialog = accept_with(p2, lambda p: p.setPrintRange(QPrinter.CurrentPage))
    win.a_print.trigger(); wait(300)
    assert printed(p2)[0] == 1
    # 選択ページ（2 ページ選択中）
    assert len(win.thumbs.selected_indices()) == 2
    p3 = os.path.join(_TMP, "print_sel.pdf")
    win._exec_print_dialog = accept_with(p3, lambda p: p.setPrintRange(QPrinter.Selection))
    win.a_print.trigger(); wait(300)
    assert printed(p3)[0] == 2
    # すべて
    p4 = os.path.join(_TMP, "print_all.pdf")
    win._exec_print_dialog = accept_with(p4, lambda p: p.setPrintRange(QPrinter.AllPages))
    win.a_print.trigger(); wait(400)
    assert printed(p4)[0] == win.model.page_count
    # キャンセルは何もしない
    win._exec_print_dialog = lambda dlg: QPrintDialog.Rejected
    win.statusBar().clearMessage(); win.a_print.trigger(); wait()
    assert "印刷しました" not in win.statusBar().currentMessage()

    step("名前を付けて保存: Ctrl+Shift+S、タイトル更新、押印位置・順序・回転が保存される")
    out = os.path.join(_TMP, "out.pdf")
    QFileDialog.getSaveFileName = staticmethod(lambda *a_, **k: (out, ""))
    QTest.keyClick(win, Qt.Key_S, Qt.ControlModifier | Qt.ShiftModifier); wait(100)
    assert os.path.exists(out) and not win.model.modified
    assert win.windowTitle() == f"out.pdf - {appmod.APP_NAME}"
    assert "保存しました" in win.statusBar().currentMessage()
    t, r = texts(out)
    assert t == ["A-5", "B-1", "B-2", "", "A-1", "A-3", "A-4"], t
    assert r == [90, 0, 0, 0, 0, 0, 0], r
    cx, cy, w = red_center(out, 4)        # A-1 の押印: 幅 75% / 高さ 25% の位置
    assert abs(cx - 595 * 0.75) < 6 and abs(cy - 842 * 0.25) < 6, (cx, cy)
    assert 20 < w < 40, w                 # 済 10mm ≒ 28pt
    cx, cy, w = red_center(out, 0)        # 回転ページ A-5: 表示上 (25%, 50%)
    assert abs(cx - 842 * 0.25) < 6 and abs(cy - 595 * 0.5) < 6, (cx, cy)
    t0, _ = texts(a)
    assert t0 == ["A-1", "A-2", "A-3", "A-4", "A-5"], "元ファイルは未変更"
    shot(win, "saved")

    step("上書き保存: Ctrl+S で現在のパスへ、* が消える。未保存の新規文書は名前を付けて保存へ")
    win.goto(1); win.a_rotr.trigger(); wait()
    assert win.model.modified and " * " in win.windowTitle()
    QTest.keyClick(win, Qt.Key_S, Qt.ControlModifier); wait(100)
    assert not win.model.modified and win.windowTitle() == f"out.pdf - {appmod.APP_NAME}"
    assert texts(out)[1][1] == 90
    assert fitz.open(out).page_count == 7

    # ---------------------------------------------------------------- ファイル D&D
    step("ファイルの D&D: 文書ありなら挿入、文書なしなら 1 つ目を開き残りを挿入")
    def drop(paths):
        md = QMimeData(); md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        ev = QDropEvent(QPointF(100, 100), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier, QEvent.Drop)
        win.dropEvent(ev)
    win.goto(6); wait()
    drop([img]); wait(100)
    assert win.model.page_count == 8 and win.thumbs.selected_indices() == [7]
    # 文書を閉じた状態での D&D（破棄確認は Discard）
    QMessageBox.question = staticmethod(lambda *a_, **k: QMessageBox.Discard)
    win.model.close(); win.refresh_thumbs(); wait()
    assert win.thumbs.count() == 0 and win.view is None and win.empty_label.isVisible()
    drop([a, b, img]); wait(150)
    # Windows では QUrl 経由のパスが C:/... 表記になるので、文字列ではなく同一ファイルかで比較する
    assert os.path.samefile(win.model.path, a) and win.model.page_count == 8
    assert [p.get_text().strip() for p in win.model.doc] == ["A-1", "A-2", "A-3", "A-4", "A-5", "B-1", "B-2", ""]
    assert win.model.modified, "挿入したので未保存状態"
    shot(win, "dropped")

    # ---------------------------------------------------------------- 終了確認
    step("終了確認: 変更ありで Cancel → 開いたまま、Save → 保存して終了、Discard → 終了")
    QMessageBox.question = staticmethod(lambda *a_, **k: QMessageBox.Cancel)
    assert not win.close() and win.isVisible()
    QMessageBox.question = staticmethod(lambda *a_, **k: QMessageBox.Save)
    assert win.close() and not win.isVisible()
    assert texts(a)[0] == ["A-1", "A-2", "A-3", "A-4", "A-5", "B-1", "B-2", ""], "Save で元ファイルに保存"
    win2 = MainWindow(); win2.show(); wait(50)
    win2.open_file(b); win2.a_rotr.trigger(); wait()
    QMessageBox.question = staticmethod(lambda *a_, **k: QMessageBox.Discard)
    assert win2.close()
    assert texts(b)[1] == [0, 0], "Discard では保存されない"

    step("ツールバー: 左辺へ移動して閉じると、次回起動時に位置が復元される")
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, os.path.join(_TMP, "qsettings"))
    w4 = MainWindow(); w4.show(); wait(50)
    assert w4.toolbar.isMovable() and w4.toolBarArea(w4.toolbar) == Qt.TopToolBarArea
    # 右クリックメニュー（createPopupMenu）に上下左右の項目があり、選ぶと移動する。現在位置にチェック
    def popup_items(w):
        return {x.text(): x for x in w.createPopupMenu().actions() if x.text().startswith("ツールバーを")}
    items = popup_items(w4)
    assert set(items) == {"ツールバーを上へ", "ツールバーを下へ", "ツールバーを左へ", "ツールバーを右へ"}
    assert items["ツールバーを上へ"].isChecked() and not items["ツールバーを左へ"].isChecked()
    items["ツールバーを左へ"].trigger(); wait()
    assert w4.toolBarArea(w4.toolbar) == Qt.LeftToolBarArea and w4.toolbar.isVisible()
    assert popup_items(w4)["ツールバーを左へ"].isChecked()
    popup_items(w4)["ツールバーを右へ"].trigger(); wait()
    assert w4.toolBarArea(w4.toolbar) == Qt.RightToolBarArea
    popup_items(w4)["ツールバーを左へ"].trigger(); wait()
    w4.stamp_dock.hide()
    assert w4.close()
    w5 = MainWindow(); w5.show(); wait(50)
    assert w5.toolBarArea(w5.toolbar) == Qt.LeftToolBarArea, "ツールバー位置が復元される"
    assert not w5.stamp_dock.isVisible(), "印鑑パネルは起動時は常に非表示"
    w5.addToolBar(Qt.TopToolBarArea, w5.toolbar); w5.stamp_dock.show(); assert w5.close()
    w5b = MainWindow(); w5b.show(); wait(50)
    assert not w5b.stamp_dock.isVisible(), "前回表示したまま終了しても、起動時は非表示"
    assert w5b.close()

    step("目次付き PDF: 開くと目次パネルが自動で開き、項目クリックでそのページへ移動")
    toc_pdf = os.path.join(_TMP, "toc.pdf")
    make_pdf(toc_pdf, 4, "T")
    _d = fitz.open(toc_pdf); _d.set_toc([[1, "第1章", 1], [2, "1-1", 2], [1, "第2章", 4]]); _d.saveIncr(); _d.close()
    w6 = MainWindow(); w6.resize(1400, 900); w6.show(); wait(50)
    assert not w6.toc_dock.isVisible()
    w6.open_file(toc_pdf); wait(100)
    assert w6.toc_dock.isVisible() and w6.a_toc_dock.isChecked()
    assert w6.toc_tree.topLevelItemCount() == 2 and w6.toc_tree.topLevelItem(0).childCount() == 1
    it2 = w6.toc_tree.topLevelItem(1)
    rect = w6.toc_tree.visualItemRect(it2)
    QTest.mouseClick(w6.toc_tree.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center()); wait(150)
    assert w6.current == 3, f"目次クリックで 4 ページ目へ: {w6.current}"
    shot(w6, "toc_panel")

    step("しおり: Ctrl+D で現在ページをしおりに追加 → 目次に並ぶ、名前変更、削除、Undo で戻る")
    w6.goto(1); wait(100)
    w6._ask_line = lambda title, label, default="": "重要ページ"
    QTest.keyClick(w6, Qt.Key_D, Qt.ControlModifier); wait(100)
    assert [t[1] for t in w6.model.get_toc()] == ["第1章", "1-1", "重要ページ", "第2章"], w6.model.get_toc()
    assert w6.toc_tree.topLevelItemCount() == 3 and w6.model.modified
    tgt = next(w6.toc_tree.topLevelItem(i) for i in range(3) if w6.toc_tree.topLevelItem(i).text(0) == "重要ページ")
    w6.toc_tree.setCurrentItem(tgt); wait()
    assert w6.btn_bm_rename.isEnabled() and w6.btn_bm_del.isEnabled()
    w6._ask_line = lambda title, label, default="": default + "（改）"
    QTest.mouseClick(w6.btn_bm_rename, Qt.LeftButton); wait()
    assert "重要ページ（改）" in [t[1] for t in w6.model.get_toc()]
    tgt = next(w6.toc_tree.topLevelItem(i) for i in range(3) if w6.toc_tree.topLevelItem(i).text(0) == "重要ページ（改）")
    w6.toc_tree.setCurrentItem(tgt); wait()
    QTest.mouseClick(w6.btn_bm_del, Qt.LeftButton); wait()
    assert len(w6.model.get_toc()) == 3 and w6.toc_tree.topLevelItemCount() == 2
    QTest.keyClick(w6, Qt.Key_Z, Qt.ControlModifier); wait()
    assert len(w6.model.get_toc()) == 4, "Undo でしおりが戻る"

    step("付箋: F3 でモード ON → クリックでメモ、ホバーで内容、右クリックで編集/削除、Undo、押印モードと排他")
    w6.goto(0); wait(100)
    w6._ask_text = lambda title, label, default="": "要確認です"
    QTest.keyClick(w6, Qt.Key_F3); wait()
    assert w6.note_mode and w6.a_note.isChecked() and w6.view.note_mode and w6.view.cursor().shape() == Qt.CrossCursor
    g = w6.view.pix_geometry(); ox, oy, pw, ph = g
    at = QPoint(int(ox + pw * 0.6), int(oy + ph * 0.3))
    QTest.mouseClick(w6.view, Qt.LeftButton, Qt.NoModifier, at); wait(150)
    notes = w6.model.notes_on_page(0)
    assert len(notes) == 1 and notes[0]["text"] == "要確認です", notes
    r = notes[0]["rect"]; pr = w6.model.doc[0].rect
    assert abs(r.x0 - pr.width * 0.6) < 3 and abs(r.y0 - pr.height * 0.3) < 3, "クリック位置に付箋"
    assert w6.note_mode, "連続して貼れるようモードは継続"
    # ホバー: 付箋の上でツールチップ、余白では出ない
    from PySide6.QtWidgets import QToolTip
    on_note = QPoint(int(ox + (r.x0 + 6) * pw / pr.width), int(oy + (r.y0 + 6) * ph / pr.height))
    mouse_move(w6.view, on_note); wait(100)
    assert "要確認です" in QToolTip.text(), QToolTip.text()
    mouse_move(w6.view, QPoint(int(ox + pw * 0.1), int(oy + ph * 0.9))); wait(700)   # hideText はフェード後に消える
    assert not QToolTip.isVisible() and QToolTip.text() == "", QToolTip.text()
    # 右クリック → 編集 / 削除（メニューを差し替えて項目を選ぶ）
    from PySide6.QtGui import QContextMenuEvent as _CME
    chosen = []
    def pick_note(menu, gpos):
        acts = {x.text(): x for x in menu.actions() if not x.isSeparator()}
        chosen.append(sorted(acts))
        for key in ("付箋を編集...", ):
            if key in acts:
                acts[key].trigger(); return
    w6._popup_menu = pick_note
    w6._ask_text = lambda title, label, default="": default + "（済）"
    QApplication.sendEvent(w6.view, _CME(_CME.Mouse, on_note, w6.view.mapToGlobal(on_note))); wait(100)
    assert "付箋を編集..." in chosen[-1] and "付箋を削除" in chosen[-1], chosen[-1]
    assert w6.model.notes_on_page(0)[0]["text"] == "要確認です（済）"
    def pick_delete(menu, gpos):
        for x in menu.actions():
            if x.text() == "付箋を削除":
                x.trigger(); return
    w6._popup_menu = pick_delete
    QApplication.sendEvent(w6.view, _CME(_CME.Mouse, on_note, w6.view.mapToGlobal(on_note))); wait(100)
    assert w6.model.notes_on_page(0) == [] and "削除" in w6.statusBar().currentMessage()
    QTest.keyClick(w6, Qt.Key_Z, Qt.ControlModifier); wait()
    assert len(w6.model.notes_on_page(0)) == 1, "Undo で付箋が戻る"
    # 余白の右クリックには「ここに付箋を追加」「このページをしおりに追加」
    w6._popup_menu = lambda menu, gpos: chosen.append(sorted(x.text() for x in menu.actions() if not x.isSeparator()))
    blank = QPoint(int(ox + pw * 0.1), int(oy + ph * 0.9))
    QApplication.sendEvent(w6.view, _CME(_CME.Mouse, blank, w6.view.mapToGlobal(blank))); wait()
    assert "ここに付箋を追加..." in chosen[-1] and "このページをしおりに追加..." in chosen[-1] and "付箋を編集..." not in chosen[-1]
    # Esc で終了、押印モードに入ると付箋モードは解除され、印鑑パネルが自動で開く
    QTest.keyClick(w6, Qt.Key_Escape); wait()
    assert not w6.note_mode and not w6.a_note.isChecked() and w6.view.cursor().shape() == Qt.ArrowCursor
    QTest.keyClick(w6, Qt.Key_F3); wait(); assert w6.note_mode
    w6.stamp_dock.hide(); wait()
    QTest.keyClick(w6, Qt.Key_F2); wait()
    assert w6.stamp_mode and not w6.note_mode, "押印モードに入ると付箋モードは解除"
    assert w6.stamp_dock.isVisible(), "押印モードに入ると印鑑パネルが自動で開く"
    QTest.keyClick(w6, Qt.Key_Escape); wait(); assert not w6.stamp_mode
    shot(w6, "note_added")

    step("表示モード: メニューで暖色/白黒反転に切替、ページ色が変わる、保存・再起動で復元、ノーマルに戻る")
    w6.mode_actions["warm"].trigger(); wait(150)
    assert w6.display_mode == "warm" and "#faf0dc" in w6.view.styleSheet()
    c = w6.view.pixmap().toImage().pixelColor(5, 5)
    assert (c.red(), c.green(), c.blue()) == (250, 240, 220), (c.red(), c.green(), c.blue())
    assert "暖色" in w6.page_label.text()
    shot(w6, "display_warm")
    w6.mode_actions["invert"].trigger(); wait(150)
    c = w6.view.pixmap().toImage().pixelColor(5, 5); assert (c.red(), c.green(), c.blue()) == (0, 0, 0)
    assert "#000000" in w6.view.styleSheet()
    shot(w6, "display_invert")
    # 保存 → 別名で保存した PDF に付箋としおりが残っている（表示モードは PDF には影響しない）
    saved = os.path.join(_TMP, "toc_saved.pdf")
    QFileDialog.getSaveFileName = staticmethod(lambda *a_, **k: (saved, ""))
    QTest.keyClick(w6, Qt.Key_S, Qt.ControlModifier | Qt.ShiftModifier); wait(100)
    _d = fitz.open(saved)
    assert [t[1] for t in _d.get_toc()] == ["第1章", "1-1", "重要ページ（改）", "第2章"] or len(_d.get_toc()) == 4
    assert [x.type[1] for x in _d[0].annots()] == ["Text"] and _d[0].annots().__next__().info["content"] == "要確認です（済）"
    px = _d[0].get_pixmap(dpi=36); assert px.pixel(1, 1) == (255, 255, 255), "保存した PDF の地色は白のまま"
    _d.close()
    assert w6.close()
    w7 = MainWindow(); w7.show(); wait(50)
    assert w7.display_mode == "invert" and w7.mode_actions["invert"].isChecked(), "表示モードが復元される"
    w7.mode_actions["normal"].trigger(); wait()
    assert w7.display_mode == "normal"
    assert w7.close()

    step("テキスト選択: ドラッグで文字を選択 → 強調表示、Ctrl+C でコピー、右クリックに「コピー」、Esc で解除")
    sel_pdf = os.path.join(_TMP, "sel.pdf")
    _d = fitz.open(); _pg = _d.new_page(width=595, height=842)
    _pg.insert_text((72, 100), "Hello World from PDF", fontsize=24)
    _pg.insert_text((72, 160), "稟議書 承認済み", fontsize=24, fontname="japan")
    _pg.insert_text((72, 220), "second line here", fontsize=24)
    _d.save(sel_pdf); _d.close()
    w8 = MainWindow(); w8.resize(1400, 900); w8.show(); wait(50)
    w8.open_file(sel_pdf); wait(150)
    words = w8.model.page_words(0)
    assert [w["text"] for w in words][:4] == ["Hello", "World", "from", "PDF"], [w["text"] for w in words]
    v = w8.view; ox, oy, pw, ph = v.pix_geometry(); pr = w8.model.doc[0].rect
    def to_widget(r, fx=0.5, fy=0.5):
        return QPoint(int(ox + (r.x0 + (r.x1 - r.x0) * fx) * pw / pr.width), int(oy + (r.y0 + (r.y1 - r.y0) * fy) * ph / pr.height))
    # "World" から "second" までドラッグ（2 行またぎ、日本語行を含む）
    start = to_widget(words[1]["rect"]); end = to_widget(words[-3]["rect"])
    QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, start); wait()
    mouse_move(v, QPoint(start.x() + 40, start.y() + 30), Qt.LeftButton); wait()
    mouse_move(v, end, Qt.LeftButton); wait()
    QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, end); wait(100)
    assert w8.sel_range is not None and len(v.selection_rects) >= 5, (w8.sel_range, len(v.selection_rects))
    assert w8.selected_text == "World from PDF\n稟議書 承認済み\nsecond", repr(w8.selected_text)
    assert w8.a_copy.isEnabled() and "選択中" in w8.statusBar().currentMessage()
    shot(w8, "text_selected")
    QApplication.clipboard().setText("")
    QTest.keyClick(w8, Qt.Key_C, Qt.ControlModifier); wait(100)
    assert QApplication.clipboard().text() == w8.selected_text, repr(QApplication.clipboard().text())
    assert "コピーしました" in w8.statusBar().currentMessage()
    # 右クリックメニューの先頭に「コピー」
    menu_items = []
    w8._popup_menu = lambda menu, gpos: menu_items.append([x.text() for x in menu.actions() if not x.isSeparator()])
    from PySide6.QtGui import QContextMenuEvent as _CME2
    QApplication.sendEvent(v, _CME2(_CME2.Mouse, end, v.mapToGlobal(end))); wait()
    assert menu_items[-1][0] == "コピー", menu_items[-1]
    # 余白をクリックすると選択解除、文字の上では I ビーム
    blank = QPoint(int(ox + pw * 0.5), int(oy + ph * 0.8))
    QTest.mouseClick(v, Qt.LeftButton, Qt.NoModifier, blank); wait(100)
    assert w8.selected_text == "" and not v.selection_rects and not w8.a_copy.isEnabled()
    mouse_move(v, to_widget(words[0]["rect"])); wait(100)
    assert v.cursor().shape() == Qt.IBeamCursor
    mouse_move(v, blank); wait(100)
    assert v.cursor().shape() == Qt.ArrowCursor
    # 1 単語だけの選択 → Esc で解除。押印モード中はドラッグしても選択されない
    QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, to_widget(words[0]["rect"], 0.2)); wait()
    mouse_move(v, to_widget(words[0]["rect"], 0.9), Qt.LeftButton); wait()
    QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, to_widget(words[0]["rect"], 0.9)); wait(100)
    assert w8.selected_text == "Hello", repr(w8.selected_text)
    QTest.keyClick(w8, Qt.Key_Escape); wait()
    assert w8.selected_text == "" and not v.selection_rects
    w8.stamp_dock_widget.list.setCurrentRow(0); w8.a_stamp.trigger(); wait()
    QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, start); mouse_move(v, end, Qt.LeftButton); QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, end); wait(150)
    assert w8.selected_text == "" and len(w8.model.stamps_on_page(0)) == 1, "押印モードのドラッグは押印になる"
    QTest.keyClick(w8, Qt.Key_Escape); wait()
    w8.model.modified = False; assert w8.close()

    step("注釈: 選択文字に蛍光ペン / 下線 / 取り消し線、F4 → 文字の書き込み、ドラッグ移動、右クリックで編集・色・サイズ・削除、Undo、保存")
    w9 = MainWindow(); w9.resize(1400, 900); w9.show(); wait(50)
    w9.open_file(sel_pdf); wait(150)
    words = w9.model.page_words(0)
    # 注釈を付けるたびにページビューは作り直されるので、毎回 w9.view を引き直す
    ox, oy, pw, ph = w9.view.pix_geometry(); pr = w9.model.doc[0].rect
    def to_widget9(r, fx=0.5, fy=0.5):
        ox, oy, pw, ph = w9.view.pix_geometry()
        return QPoint(int(ox + (r.x0 + (r.x1 - r.x0) * fx) * pw / pr.width), int(oy + (r.y0 + (r.y1 - r.y0) * fy) * ph / pr.height))
    assert not w9.a_highlight.isEnabled() and not w9.a_underline.isEnabled() and not w9.a_strikeout.isEnabled(), "選択なしではマーカー不可"
    w9.apply_markup("highlight"); wait()
    assert "選択" in w9.statusBar().currentMessage() and w9.model.annots_on_page(0) == []
    # "Hello World" を選んで Ctrl+Shift+H
    start = to_widget9(words[0]["rect"]); end = to_widget9(words[1]["rect"])
    QTest.mousePress(w9.view, Qt.LeftButton, Qt.NoModifier, start); mouse_move(w9.view, end, Qt.LeftButton); QTest.mouseRelease(w9.view, Qt.LeftButton, Qt.NoModifier, end); wait(100)
    assert w9.selected_text == "Hello World" and w9.a_highlight.isEnabled()
    QTest.keyClick(w9, Qt.Key_H, Qt.ControlModifier | Qt.ShiftModifier); wait(150)
    ann = w9.model.annots_on_page(0)
    assert [a["kind"] for a in ann] == ["蛍光ペン"] and ann[0]["stroke"] == (1.0, 1.0, 0.0), ann
    assert ann[0]["rect"].contains(fitz.Rect(words[0]["rect"])) and not ann[0]["movable"]
    assert w9.selected_text == "" and not w9.a_highlight.isEnabled(), "マーカー後は選択解除"
    assert "蛍光ペン" in w9.statusBar().currentMessage()
    # 右クリックメニューから下線・取り消し線（2 行目の日本語）
    picks = []
    def pick9(label):
        def f(menu, gpos):
            acts = {x.text(): x for x in menu.actions() if not x.isSeparator()}
            picks.append(sorted(acts))
            if label in acts:
                acts[label].trigger()
        return f
    jp = words[4]["rect"]
    QTest.mousePress(w9.view, Qt.LeftButton, Qt.NoModifier, to_widget9(jp, 0.1)); mouse_move(w9.view, to_widget9(jp, 0.9), Qt.LeftButton); QTest.mouseRelease(w9.view, Qt.LeftButton, Qt.NoModifier, to_widget9(jp, 0.9)); wait(100)
    assert w9.selected_text == "稟議書"
    w9._popup_menu = pick9("下線")
    QApplication.sendEvent(w9.view, _CME2(_CME2.Mouse, to_widget9(jp), w9.view.mapToGlobal(to_widget9(jp)))); wait(100)
    assert picks[-1][:4] == ["コピー", "下線", "取り消し線", "蛍光ペン"] or set(["コピー", "蛍光ペン", "下線", "取り消し線"]) <= set(picks[-1]), picks[-1]
    assert [a["kind"] for a in w9.model.annots_on_page(0)] == ["蛍光ペン", "下線"]
    QTest.mousePress(w9.view, Qt.LeftButton, Qt.NoModifier, to_widget9(words[5]["rect"], 0.1)); mouse_move(w9.view, to_widget9(words[5]["rect"], 0.9), Qt.LeftButton); QTest.mouseRelease(w9.view, Qt.LeftButton, Qt.NoModifier, to_widget9(words[5]["rect"], 0.9)); wait(100)
    QTest.keyClick(w9, Qt.Key_K, Qt.ControlModifier | Qt.ShiftModifier); wait(150)
    assert [a["kind"] for a in w9.model.annots_on_page(0)] == ["蛍光ペン", "下線", "取り消し線"]
    # 蛍光ペンの上で右クリック: 色変更・削除。色を緑に
    hl = w9.model.annots_on_page(0)[0]
    on_hl = to_widget9(hl["rect"])
    w9._ask_color = lambda initial, title: QColor(0, 255, 0)
    w9._popup_menu = pick9("蛍光ペンの色を変更...")
    QApplication.sendEvent(w9.view, _CME2(_CME2.Mouse, on_hl, w9.view.mapToGlobal(on_hl))); wait(100)
    assert "この蛍光ペンを削除" in picks[-1] and "文字を編集..." not in picks[-1], picks[-1]
    assert w9.model.annots_on_page(0)[0]["stroke"] == (0.0, 1.0, 0.0)
    mouse_move(w9.view, on_hl); wait(100)
    assert QToolTip.text().startswith("蛍光ペン") and w9.view.cursor().shape() != Qt.SizeAllCursor, QToolTip.text()
    # 蛍光ペンはドラッグしても動かない（文字選択にもならない）
    QTest.mousePress(w9.view, Qt.LeftButton, Qt.NoModifier, on_hl); mouse_move(w9.view, QPoint(on_hl.x() + 60, on_hl.y() + 60), Qt.LeftButton); QTest.mouseRelease(w9.view, Qt.LeftButton, Qt.NoModifier, QPoint(on_hl.x() + 60, on_hl.y() + 60)); wait(100)
    assert w9.model.annots_on_page(0)[0]["rect"] == hl["rect"]
    QTest.keyClick(w9, Qt.Key_Escape); wait()
    # F4 → 書き込みリボン → 「文字」の道具 → クリックで文字を書き込む（日本語）
    w9._ask_text = lambda title, label, default="": "確認済み\n田中"
    QTest.keyClick(w9, Qt.Key_F4); wait(200)
    assert w9.ribbon.isVisible() and w9.a_write.isChecked() and w9.write_tool == "select" and not w9.text_mode
    w9.ribbon.tool_actions["text"].trigger(); wait()
    assert w9.text_mode and w9.view.text_mode and w9.view.cursor().shape() == Qt.CrossCursor
    at = QPoint(int(ox + pw * 0.55), int(oy + ph * 0.5))
    QTest.mouseClick(w9.view, Qt.LeftButton, Qt.NoModifier, at); wait(150)
    ft = [a for a in w9.model.annots_on_page(0) if a["kind"] == "文字"]
    assert len(ft) == 1 and ft[0]["text"] == "確認済み\n田中" and ft[0]["movable"] and ft[0]["fontsize"] == 12.0, ft
    assert abs(ft[0]["rect"].x0 - pr.width * 0.55) < 3 and abs(ft[0]["rect"].y0 - pr.height * 0.5) < 3
    assert w9.text_mode and w9.view.text_mode, "連続して置けるよう道具は継続"
    QTest.keyClick(w9, Qt.Key_Escape); wait()
    assert not w9.text_mode and w9.ribbon.isVisible() and w9.view.cursor().shape() == Qt.ArrowCursor, "1 回目の Esc は道具を選択に戻す"
    QTest.keyClick(w9, Qt.Key_Escape); wait()
    assert not w9.ribbon.isVisible() and not w9.a_write.isChecked(), "2 回目の Esc でリボンを閉じる"
    # 描画されている（注釈の位置に赤っぽい画素）
    pix = w9.model.render(0, 1.0); r = ft[0]["rect"]
    reds = 0
    for yy in range(int(r.y0), int(r.y1)):
        for xx in range(int(r.x0), int(r.x1)):
            cr, cg, cb = pix.pixel(xx, yy)
            if cr > 150 and cg < 100 and cb < 100:
                reds += 1
    assert reds > 20, reds
    # ホバー: ツールチップと移動カーソル。ドラッグで移動
    on_ft = to_widget9(ft[0]["rect"], 0.3, 0.5)
    mouse_move(w9.view, on_ft); wait(100)
    assert "文字: 確認済み" in QToolTip.text() and "移動" in QToolTip.text() and w9.view.cursor().shape() == Qt.SizeAllCursor, (QToolTip.text(), w9.view.cursor().shape(), w9.sel_annot, w9.view.click_mode, w9.view.draw_mode)
    dest = QPoint(on_ft.x() - 150, on_ft.y() + 80)
    QTest.mousePress(w9.view, Qt.LeftButton, Qt.NoModifier, on_ft); wait()
    mouse_move(w9.view, QPoint(on_ft.x() - 50, on_ft.y() + 20), Qt.LeftButton); wait()
    assert w9.view.move_rect is not None and w9.drag_annot is not None, "移動中は枠を表示"
    mouse_move(w9.view, dest, Qt.LeftButton); wait()
    QTest.mouseRelease(w9.view, Qt.LeftButton, Qt.NoModifier, dest); wait(150)
    ft2 = [a for a in w9.model.annots_on_page(0) if a["kind"] == "文字"][0]
    assert w9.view.move_rect is None and w9.drag_annot is None and w9.selected_text == ""
    assert abs((ft2["rect"].x0 - ft[0]["rect"].x0) - (-150) * pr.width / pw) < 3 and abs((ft2["rect"].y0 - ft[0]["rect"].y0) - 80 * pr.height / ph) < 3, (ft[0]["rect"], ft2["rect"])
    assert "移動しました" in w9.statusBar().currentMessage()
    shot(w9, "annots")
    # 右クリック: 編集 / 文字サイズ / 色 / 削除
    on_ft2 = to_widget9(ft2["rect"], 0.3, 0.5)
    w9._ask_text = lambda title, label, default="": default.replace("田中", "佐藤")
    w9._popup_menu = pick9("文字を編集...")
    QApplication.sendEvent(w9.view, _CME2(_CME2.Mouse, on_ft2, w9.view.mapToGlobal(on_ft2))); wait(100)
    assert {"文字を編集...", "文字サイズを変更...", "文字の色を変更...", "この文字を削除"} <= set(picks[-1]), picks[-1]
    assert [a for a in w9.model.annots_on_page(0) if a["kind"] == "文字"][0]["text"] == "確認済み\n佐藤"
    w9._ask_line = lambda title, label, default="": "18"
    w9._popup_menu = pick9("文字サイズを変更...")
    QApplication.sendEvent(w9.view, _CME2(_CME2.Mouse, on_ft2, w9.view.mapToGlobal(on_ft2))); wait(100)
    ft3 = [a for a in w9.model.annots_on_page(0) if a["kind"] == "文字"][0]
    assert ft3["fontsize"] == 18.0 and ft3["rect"].width > ft2["rect"].width and abs(ft3["rect"].x0 - ft2["rect"].x0) < 0.5, (ft2["rect"], ft3["rect"])
    w9._ask_color = lambda initial, title: QColor(0, 0, 255)
    w9._popup_menu = pick9("文字の色を変更...")
    QApplication.sendEvent(w9.view, _CME2(_CME2.Mouse, on_ft2, w9.view.mapToGlobal(on_ft2))); wait(100)
    assert [a for a in w9.model.annots_on_page(0) if a["kind"] == "文字"][0]["stroke"] == (0.0, 0.0, 1.0)
    w9._popup_menu = pick9("この文字を削除")
    QApplication.sendEvent(w9.view, _CME2(_CME2.Mouse, on_ft2, w9.view.mapToGlobal(on_ft2))); wait(100)
    assert [a["kind"] for a in w9.model.annots_on_page(0)] == ["蛍光ペン", "下線", "取り消し線"] and "削除" in w9.statusBar().currentMessage()
    QTest.keyClick(w9, Qt.Key_Z, Qt.ControlModifier); wait()
    assert [a["kind"] for a in w9.model.annots_on_page(0)] == ["蛍光ペン", "下線", "取り消し線", "文字"], "Undo で戻る"
    w9._popup_menu = pick9("この下線を削除")
    ul = w9.model.annots_on_page(0)[1]; on_ul = to_widget9(ul["rect"])
    QApplication.sendEvent(w9.view, _CME2(_CME2.Mouse, on_ul, w9.view.mapToGlobal(on_ul))); wait(100)
    assert [a["kind"] for a in w9.model.annots_on_page(0)] == ["蛍光ペン", "取り消し線", "文字"]
    # 保存して他のビューアで読める形（標準の注釈）になっている
    annot_out = os.path.join(_TMP, "annots.pdf")
    QFileDialog.getSaveFileName = staticmethod(lambda *a_, **k: (annot_out, ""))
    w9.a_saveas.trigger(); wait(100)
    _d = fitz.open(annot_out)
    assert [a.type[1] for a in _d[0].annots()] == ["Highlight", "StrikeOut", "FreeText"], [a.type[1] for a in _d[0].annots()]
    assert [a.info["content"] for a in _d[0].annots()][-1] == "確認済み\n佐藤"
    _d.close()
    # 開き直しても既存の注釈として編集できる（色変更）
    w9.open_file(annot_out); wait(150)
    ann = w9.model.annots_on_page(0)
    assert [a["kind"] for a in ann] == ["蛍光ペン", "取り消し線", "文字"] and ann[0]["stroke"] == (0.0, 1.0, 0.0) and ann[2]["fontsize"] == 18.0
    w9.recolor_annot(0, ann[1]["xref"]); wait()
    assert w9.model.annots_on_page(0)[1]["stroke"] == (0.0, 0.0, 1.0)
    w9.model.modified = False; assert w9.close()

    step("書き込みリボン: ペン・直線・矢印・四角・丸をドラッグで描く、色・太さ・濃さ・塗り、選択して移動・大きさ変更・Delete、文字はフォント・大きさ・背景・枠、Esc")
    w10 = MainWindow(); w10.resize(1400, 900); w10.show(); wait(50)
    assert not w10.ribbon.isVisible() and not w10.a_write.isEnabled(), "文書なしでは書き込みを開けない"
    w10.open_file(sel_pdf); wait(150)
    rb = w10.ribbon
    pr = w10.model.doc[0].rect
    def P10(fx, fy):
        ox, oy, pw, ph = w10.view.pix_geometry()
        return QPoint(int(ox + pw * fx), int(oy + ph * fy))
    def PP10(x, y):
        ox, oy, pw, ph = w10.view.pix_geometry()
        return QPoint(int(ox + x * pw / pr.width), int(oy + y * ph / pr.height))
    def dclick10(pos):
        """ダブルクリック（2 回目の押下 → その場で離す）。編集画面は、動かさずに離したときに開く。"""
        QTest.mouseDClick(w10.view, Qt.LeftButton, Qt.NoModifier, pos); wait(20)
        QTest.mouseRelease(w10.view, Qt.LeftButton, Qt.NoModifier, pos)

    def drag10(an, bn, mid=()):
        v = w10.view
        QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, an); wait(20)
        for q in mid:
            mouse_move(v, q, Qt.LeftButton); wait(10)
        mouse_move(v, bn, Qt.LeftButton); wait(20)
        QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, bn); wait(120)
    def annots10():
        return w10.model.annots_on_page(0)
    def kinds10():
        return [an["kind"] for an in annots10()]
    scale = w10.view.pix_geometry()[2] / pr.width
    w10.a_write.trigger(); wait(200)
    assert rb.isVisible() and w10.a_write.isChecked() and w10.write_tool == "select" and rb.context() == "shape"
    assert [rb.cb_font.itemText(i) for i in range(rb.cb_font.count())] == ["標準ゴシック", "メイリオ"], "使えるフォントだけが並ぶ"
    assert not rb.markup_actions["highlight"].isEnabled() and "書き込み" in w10.statusBar().currentMessage()
    # ペン: ドラッグ中は線の下書きが出て、離すと手書きの注釈になる。道具はそのまま
    rb.tool_actions["pen"].trigger(); wait()
    assert w10.write_tool == "pen" and w10.view.draw_mode and w10.view.cursor().shape() == Qt.CrossCursor and "ペン" in w10.statusBar().currentMessage()
    QTest.mousePress(w10.view, Qt.LeftButton, Qt.NoModifier, P10(0.2, 0.35)); wait(20)
    for q in (P10(0.25, 0.42), P10(0.3, 0.33), P10(0.35, 0.43)):
        mouse_move(w10.view, q, Qt.LeftButton); wait(10)
    assert w10.view.preview is not None and w10.view.preview["kind"] == "pen" and len(w10.view.preview["pts"]) == 4
    QTest.mouseRelease(w10.view, Qt.LeftButton, Qt.NoModifier, P10(0.4, 0.36)); wait(120)
    assert kinds10() == ["手書き"] and w10.view.preview is None and w10.write_tool == "pen" and w10.view.draw_mode
    an = annots10()[0]
    assert near(an["stroke"], (0.85, 0.10, 0.10)) and an["width"] == 2.0 and an["opacity"] == 1.0, an
    # 外枠は線の太さぶんの余白を含むので、線そのものより少し広い
    assert 0 <= pr.width * 0.2 - an["rect"].x0 < 12 and 0 <= an["rect"].x1 - pr.width * 0.4 < 12, an["rect"]
    assert w10.model.modified and "線を書きました" in w10.statusBar().currentMessage()
    QTest.mouseClick(w10.view, Qt.LeftButton, Qt.NoModifier, P10(0.8, 0.8)); wait(80)
    assert kinds10() == ["手書き"], "クリックだけでは何も書かない"
    # 色=青・太さ=太い → 矢印、直線
    rb.color_buttons[1].click(); rb.cb_width.setCurrentIndex(2); wait()
    rb.tool_actions["arrow"].trigger(); wait(); drag10(P10(0.5, 0.35), P10(0.7, 0.42))
    an = annots10()[-1]
    assert an["kind"] == "線" and an["arrow"] and near(an["stroke"], (0.10, 0.30, 0.85)) and an["width"] == 4.0, an
    rb.tool_actions["line"].trigger(); wait(); drag10(P10(0.2, 0.47), P10(0.7, 0.47))
    assert annots10()[-1]["kind"] == "線" and not annots10()[-1]["arrow"]
    # 四角（塗りなし）、丸（塗りつぶし・濃さ 50%）
    rb.tool_actions["rect"].trigger(); wait()
    QTest.mousePress(w10.view, Qt.LeftButton, Qt.NoModifier, P10(0.2, 0.52)); mouse_move(w10.view, P10(0.3, 0.56), Qt.LeftButton); wait(20)
    assert w10.view.preview["kind"] == "rect" and not w10.view.preview["fill"]
    mouse_move(w10.view, P10(0.4, 0.6), Qt.LeftButton); QTest.mouseRelease(w10.view, Qt.LeftButton, Qt.NoModifier, P10(0.4, 0.6)); wait(120)
    sq = annots10()[-1]
    assert sq["kind"] == "四角" and sq["fill"] is None and sq["resizable"], sq
    assert abs(sq["rect"].x0 - pr.width * 0.2) < 4 and abs(sq["rect"].y0 - pr.height * 0.52) < 4 and abs(sq["rect"].x1 - pr.width * 0.4) < 4 and abs(sq["rect"].y1 - pr.height * 0.6) < 4, sq["rect"]
    rb.chk_fill.setChecked(True); rb.cb_opacity.setCurrentIndex(2); wait()
    rb.tool_actions["ellipse"].trigger(); wait(); drag10(P10(0.5, 0.52), P10(0.7, 0.6))
    el = annots10()[-1]
    assert el["kind"] == "円" and near(el["fill"], (0.10, 0.30, 0.85)) and el["opacity"] == 0.5, el
    assert kinds10() == ["手書き", "線", "線", "四角", "円"]
    shot(w10, "write_ribbon")
    # Esc: 道具を「選択」に戻す（リボンは開いたまま）
    QTest.keyClick(w10, Qt.Key_Escape); wait()
    assert rb.isVisible() and w10.write_tool == "select" and not w10.view.draw_mode and w10.view.cursor().shape() != Qt.CrossCursor
    # 選択: 四角をクリック → 枠とつまみが出て、リボンは四角の見た目（塗りなし・濃さ 100%）を映す
    QTest.mouseClick(w10.view, Qt.LeftButton, Qt.NoModifier, PP10(sq["rect"].x0 + 2, sq["rect"].y0 + 10)); wait(100)
    assert w10.sel_annot == (0, sq["xref"]) and w10.view.annot_box is not None and w10.view.annot_handle and w10.a_del_annot.isEnabled()
    assert not rb.chk_fill.isChecked() and rb.cb_opacity.currentData() == 1.0 and rb.cb_width.currentData() == 4.0 and rb.context() == "shape"
    assert kinds10() == ["手書き", "線", "線", "四角", "円"] and annots10()[3]["rect"] == sq["rect"], "クリックだけでは動かない"
    # つまみ（右下の四角）をドラッグして大きさ変更
    handle = PP10(sq["rect"].x1, sq["rect"].y1)
    mouse_move(w10.view, handle); wait(60)
    assert w10.view.cursor().shape() == Qt.SizeFDiagCursor
    QTest.mousePress(w10.view, Qt.LeftButton, Qt.NoModifier, handle); mouse_move(w10.view, QPoint(handle.x() + 30, handle.y() + 20), Qt.LeftButton); wait(20)
    assert w10.view.move_rect is not None and w10._drag["kind"] == "resize"
    mouse_move(w10.view, QPoint(handle.x() + 60, handle.y() + 40), Qt.LeftButton)
    QTest.mouseRelease(w10.view, Qt.LeftButton, Qt.NoModifier, QPoint(handle.x() + 60, handle.y() + 40)); wait(120)
    sq2 = w10.model.annot_info(*w10.sel_annot)
    assert abs(sq2["rect"].width - (sq["rect"].width + 60 / scale)) < 2 and abs(sq2["rect"].height - (sq["rect"].height + 40 / scale)) < 2, (sq["rect"], sq2["rect"])
    assert abs(sq2["rect"].x0 - sq["rect"].x0) < 0.5 and "大きさ" in w10.statusBar().currentMessage() and w10.view.move_rect is None
    # 本体をドラッグして移動（選択は続く）
    grab = PP10(sq2["rect"].x0 + 3, sq2["rect"].y0 + 12)
    drag10(grab, QPoint(grab.x() - 40, grab.y() + 50))
    sq3 = w10.model.annot_info(*w10.sel_annot)
    assert abs(sq3["rect"].x0 - (sq2["rect"].x0 - 40 / scale)) < 2 and abs(sq3["rect"].y0 - (sq2["rect"].y0 + 50 / scale)) < 2 and abs(sq3["rect"].width - sq2["rect"].width) < 0.6
    assert w10.view.annot_box is not None and abs(w10.view.annot_box[0] - sq3["rect"].x0 / pr.width) < 0.001, "選択の枠は移動先に付く"
    # 選択中にリボンを変えると、その書き込みに反映される
    rb.color_buttons[3].click(); wait(100)
    assert near(w10.model.annot_info(*w10.sel_annot)["stroke"], (0.0, 0.55, 0.20))
    rb.chk_fill.setChecked(True); wait(100)
    assert near(w10.model.annot_info(*w10.sel_annot)["fill"], (0.0, 0.55, 0.20))
    rb.cb_width.setCurrentIndex(3); rb.cb_opacity.setCurrentIndex(1); wait(100)
    an = w10.model.annot_info(*w10.sel_annot); assert an["width"] == 8.0 and an["opacity"] == 0.75, an
    # 手書きも選んで移動・拡大できる
    ink = annots10()[0]
    QTest.mouseClick(w10.view, Qt.LeftButton, Qt.NoModifier, PP10(ink["rect"].x0 + ink["rect"].width / 2, ink["rect"].y0 + ink["rect"].height / 2)); wait(100)
    assert w10.sel_annot is not None and w10.model.annot_info(*w10.sel_annot)["kind"] == "手書き" and w10.view.annot_handle
    h2 = PP10(ink["rect"].x1, ink["rect"].y1)
    drag10(h2, QPoint(h2.x() + 50, h2.y() + 50))
    ink2 = w10.model.annot_info(*w10.sel_annot)
    assert ink2["kind"] == "手書き" and abs(ink2["rect"].width - (ink["rect"].width + 50 / scale)) < 2.5 and ink2["width"] == 2.0, (ink["rect"], ink2["rect"])
    # Delete キー: 書き込みを選んでいるときは、ページではなく書き込みだけを消す
    n_pages = w10.model.page_count
    QTest.keyClick(w10, Qt.Key_Delete); wait(100)
    assert "手書き" not in kinds10() and w10.model.page_count == n_pages and w10.sel_annot is None and w10.view.annot_box is None and not w10.a_del_annot.isEnabled()
    QTest.keyClick(w10, Qt.Key_Z, Qt.ControlModifier); wait(100)
    assert "手書き" in kinds10(), "Undo で戻る"
    # 文字: メイリオ・16pt・背景=黄・枠あり。文字用の設定に切り替わる
    rb.tool_actions["text"].trigger(); wait()
    assert rb.context() == "text" and w10.view.text_mode and not w10.view.draw_mode
    rb.cb_font.setCurrentIndex(rb.cb_font.findData("meiryo")); rb.sp_size.setValue(16); rb.cb_bg.setCurrentIndex(2); rb.chk_border.setChecked(True)
    rb.color_buttons[0].click(); wait()
    w10._ask_text = lambda title, label, default="": "確認済み 山田\n2026/11/13"
    QTest.mouseClick(w10.view, Qt.LeftButton, Qt.NoModifier, P10(0.2, 0.72)); wait(150)
    ft = [an for an in annots10() if an["kind"] == "文字"]
    assert len(ft) == 1 and ft[0]["font"] == "meiryo" and ft[0]["fontsize"] == 16.0 and near(ft[0]["fill"], (1.0, 1.0, 0.8)) and ft[0]["border"], ft
    assert near(ft[0]["stroke"], (0.85, 0.10, 0.10)) and ft[0]["text"] == "確認済み 山田\n2026/11/13" and w10.sel_annot == (0, ft[0]["xref"])
    assert abs(ft[0]["rect"].x0 - pr.width * 0.2) < 3 and abs(ft[0]["rect"].y0 - pr.height * 0.72) < 3
    pix = w10.model.render(0, 1.0); r = ft[0]["rect"]
    reds = sum(1 for yy in range(int(r.y0), int(r.y1)) for xx in range(int(r.x0), int(r.x1)) if pix.pixel(xx, yy)[0] > 150 and pix.pixel(xx, yy)[1] < 100)
    assert reds > 30, reds
    # 文字の道具のまま、書いてある文字をクリックすると、新しく増やさずに直す
    w10._ask_text = lambda title, label, default="": default + " 済"
    on_ft = PP10(r.x0 + r.width / 2, r.y0 + r.height / 2)
    QTest.mouseClick(w10.view, Qt.LeftButton, Qt.NoModifier, on_ft); wait(150)
    ft = [an for an in annots10() if an["kind"] == "文字"]
    assert len(ft) == 1 and ft[0]["text"].endswith(" 済") and ft[0]["font"] == "meiryo"
    # 文字の道具のまま、書いてある文字を押してそのままドラッグすると動かせる（押した瞬間に編集画面を開かない）
    opened = []
    w10._ask_text = lambda title, label, default="": opened.append(default) or default
    before = ft[0]["rect"]
    drag10(on_ft, QPoint(on_ft.x() + 60, on_ft.y() - 40)); wait(100)
    ft = [an for an in annots10() if an["kind"] == "文字"]
    assert not opened, "ドラッグでは編集画面を開かない"
    assert len(ft) == 1 and abs((ft[0]["rect"].x0 - before.x0) - 60 / scale) < 2 and abs((ft[0]["rect"].y0 - before.y0) + 40 / scale) < 2, (before, ft[0]["rect"])
    assert ft[0]["text"].endswith(" 済") and w10.view.text_mode, "中身はそのまま。道具も文字のまま"
    # 余白をドラッグしただけでは、文字を書く画面は開かない（クリックのときだけ開く）
    drag10(P10(0.75, 0.9), P10(0.85, 0.93)); wait(100)
    assert not opened and len([an for an in annots10() if an["kind"] == "文字"]) == 1
    QTest.keyClick(w10, Qt.Key_Z, Qt.ControlModifier); wait(100)      # 移動を戻す
    ft = [an for an in annots10() if an["kind"] == "文字"]
    assert abs(ft[0]["rect"].x0 - before.x0) < 0.5 and abs(ft[0]["rect"].y0 - before.y0) < 0.5
    # 選択の道具: 素早く 2 回押して、そのままドラッグしても動かせる（2 回目の押下で編集画面を開かない）
    rb.tool_actions["select"].trigger(); wait()
    QTest.mouseClick(w10.view, Qt.LeftButton, Qt.NoModifier, on_ft); wait(20)
    QApplication.sendEvent(w10.view, QMouseEvent(QEvent.MouseButtonDblClick, QPointF(on_ft), QPointF(w10.view.mapToGlobal(on_ft)),
                                                 Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    to = QPoint(on_ft.x() + 50, on_ft.y() + 30)
    QApplication.sendEvent(w10.view, QMouseEvent(QEvent.MouseMove, QPointF(to), QPointF(w10.view.mapToGlobal(to)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    QApplication.sendEvent(w10.view, QMouseEvent(QEvent.MouseButtonRelease, QPointF(to), QPointF(w10.view.mapToGlobal(to)), Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    wait(100)
    ft = [an for an in annots10() if an["kind"] == "文字"]
    assert not opened, "2 回目の押下からのドラッグでは編集画面を開かない"
    assert abs((ft[0]["rect"].x0 - before.x0) - 50 / scale) < 2 and abs((ft[0]["rect"].y0 - before.y0) - 30 / scale) < 2, (before, ft[0]["rect"])
    QTest.keyClick(w10, Qt.Key_Z, Qt.ControlModifier); wait(100)
    # 選択の道具ではダブルクリックで直せる
    w10._ask_text = lambda title, label, default="": default.replace(" 済", " 確認")
    dclick10(on_ft); wait(150)
    ft = [an for an in annots10() if an["kind"] == "文字"]
    assert len(ft) == 1 and ft[0]["text"].endswith(" 確認"), ft
    assert w10.sel_annot == (0, ft[0]["xref"]) and rb.context() == "text", "文字を選ぶと文字用の設定が出る"
    assert rb.font_key() == "meiryo" and rb.fontsize() == 16.0 and rb.text_border()
    # 選択中の文字に、フォント・大きさ・背景・枠を適用
    rb.cb_font.setCurrentIndex(0); wait(100)
    assert w10.model.annot_info(*w10.sel_annot)["font"] == "builtin"
    rb.sp_size.setValue(20); wait(100); rb.cb_bg.setCurrentIndex(0); wait(100); rb.chk_border.setChecked(False); wait(100)
    an = w10.model.annot_info(*w10.sel_annot)
    assert an["fontsize"] == 20.0 and an["fill"] is None and not an["border"] and an["text"].endswith(" 確認"), an
    # つまみで幅を狭めると折り返して高くなる
    h3 = PP10(an["rect"].x1, an["rect"].y1)
    drag10(h3, QPoint(h3.x() - int(an["rect"].width * scale * 0.5), h3.y()))
    bn = w10.model.annot_info(*w10.sel_annot)
    assert bn["rect"].width < an["rect"].width * 0.7 and bn["rect"].height > an["rect"].height, (an["rect"], bn["rect"])
    # 文字を空にすると消える
    w10._ask_text = lambda title, label, default="": ""
    dclick10(PP10(bn["rect"].x0 + 8, bn["rect"].y0 + 8)); wait(150)
    assert "文字" not in kinds10() and w10.sel_annot is None
    QTest.keyClick(w10, Qt.Key_Z, Qt.ControlModifier); wait(100)
    assert "文字" in kinds10()
    # マーカー: 文字を選ぶとリボンの蛍光ペンが押せる
    words = w10.model.page_words(0)
    wr = words[0]["rect"]
    drag10(PP10(wr.x0 + 2, wr.y0 + wr.height / 2), PP10(wr.x1 - 2, wr.y0 + wr.height / 2))
    assert w10.selected_text == "Hello" and rb.markup_actions["highlight"].isEnabled() and rb.markup_actions["strikeout"].isEnabled()
    rb.markup_actions["highlight"].trigger(); wait(120)
    assert "蛍光ペン" in kinds10() and w10.selected_text == "" and not rb.markup_actions["highlight"].isEnabled()
    # 押印・付箋モードとは排他
    w10.stamp_dock_widget.list.setCurrentRow(0)
    QTest.keyClick(w10, Qt.Key_F2); wait(250)
    assert w10.stamp_mode and not rb.isVisible() and not w10.a_write.isChecked(), "押印モードに入るとリボンを閉じる"
    QTest.keyClick(w10, Qt.Key_F4); wait(250)
    assert rb.isVisible() and not w10.stamp_mode and not w10.a_stamp.isChecked(), "リボンを開くと押印モードを終える"
    QTest.keyClick(w10, Qt.Key_F3); wait(100)
    assert w10.note_mode and not rb.isVisible()
    QTest.keyClick(w10, Qt.Key_F4); wait(100)
    assert rb.isVisible() and not w10.note_mode and w10.write_tool == "select"
    # 「閉じる」ボタン、Esc 2 回（道具 → リボン）
    rb.a_close.trigger(); wait()
    assert not rb.isVisible() and not w10.a_write.isChecked()
    w10.a_write.trigger(); wait(); rb.tool_actions["pen"].trigger(); wait()
    QTest.keyClick(w10, Qt.Key_Escape); wait()
    assert rb.isVisible() and w10.write_tool == "select"
    QTest.keyClick(w10, Qt.Key_Escape); wait()
    assert not rb.isVisible() and not w10.view.draw_mode and not w10.view.text_mode
    # リボンを閉じていても、選んで動かす・Delete で消すことはできる
    el = [an for an in annots10() if an["kind"] == "円"][0]
    c = PP10(el["rect"].x0 + el["rect"].width / 2, el["rect"].y0 + el["rect"].height / 2)
    drag10(c, QPoint(c.x() + 30, c.y()))
    scale = w10.view.pix_geometry()[2] / pr.width       # 押印モードで印鑑パネルが開いたので、表示の倍率が変わっている
    assert abs(w10.model.annot_info(*w10.sel_annot)["rect"].x0 - (el["rect"].x0 + 30 / scale)) < 2
    # 保存しても選択中の書き込みを指し続ける。開き直すと種類・フォントが残っている
    write_out = os.path.join(_TMP, "write.pdf")
    QFileDialog.getSaveFileName = staticmethod(lambda *a_, **k: (write_out, ""))
    w10.a_saveas.trigger(); wait(150)
    assert w10.model.annot_info(*w10.sel_annot)["kind"] == "円", "保存で選択が別のものに変わらない"
    _d = fitz.open(write_out); _pg = _d[0]
    assert sorted(an.type[1] for an in _pg.annots()) == ["Circle", "FreeText", "Highlight", "Ink", "Line", "Line", "Square"], sorted(an.type[1] for an in _pg.annots())
    del _pg; _d.close()
    assert os.path.getsize(write_out) < 200_000, os.path.getsize(write_out)
    w10.model.modified = False; assert w10.close()
    # 色・太さなどの設定は次回に引き継ぐ。リボンは起動時には出ない
    w11 = MainWindow(); w11.show(); wait(50)
    # （選択した書き込みの見た目をリボンに映すので、最後に映した値が引き継がれる）
    assert not w11.ribbon.isVisible() and near(w11.ribbon.color(), (0.85, 0.10, 0.10)) and w11.ribbon.fontsize() == 20.0, (w11.ribbon.color(), w11.ribbon.fontsize())
    assert os.path.isfile(os.path.join(os.environ["APPDATA"], "PdfPageEditor", "fonts.json")), "フォントの確認結果を保存している"
    w11.open_file(write_out); wait(150)
    ft = [an for an in w11.model.annots_on_page(0) if an["kind"] == "文字"][0]
    assert ft["font"] == "builtin" and ft["fontsize"] == 20.0 and ft["text"].endswith(" 確認")
    w11.a_write.trigger(); wait(200)
    assert w11.ribbon.isVisible() and w11.ribbon.font_key() == "builtin"
    w11.model.modified = False; assert w11.close()

    step("書き込みリボンの位置: 以前の版で保存したレイアウトからでも、ツールバーの下の段に横幅いっぱいで出る。ツールバーを動かしても同じ")
    from PySide6.QtWidgets import QMainWindow, QToolBar

    def ribbon_row(w):
        """リボンが上側の独立した段に、横幅いっぱいで出ているか。ツールバーが上にあるときは、その下の段か。"""
        tb, rb = w.toolbar.geometry(), w.ribbon.geometry()
        return (w.ribbon.isVisible() and w.toolBarArea(w.ribbon) == Qt.TopToolBarArea and rb.width() >= w.width() - 4
                and (w.toolBarArea(w.toolbar) != Qt.TopToolBarArea or rb.top() >= tb.bottom()))

    # リボンが無かった頃（1.4.0 以前）に保存されたレイアウト。そのまま復元すると、リボンがツールバーと同じ段の右端に押し込まれていた
    _old = QMainWindow(); _t = QToolBar("メイン"); _t.setObjectName("mainToolBar"); _old.addToolBar(_t)
    QSettings("PdfPageEditor", "PdfPageEditor").setValue("state", _old.saveState())
    _old.deleteLater()
    for _round in range(2):     # 2 回目は、1 回目の終了時に保存されたレイアウトから
        w12 = MainWindow(); w12.resize(1400, 900); w12.show(); wait(50)
        w12.open_file(write_out); wait(150)
        assert w12.toolBarArea(w12.toolbar) == Qt.TopToolBarArea and not w12.ribbon.isVisible()
        top0 = w12.centralWidget().geometry().top()
        w12.a_write.trigger(); wait(200)
        assert ribbon_row(w12), (_round, w12.toolbar.geometry(), w12.ribbon.geometry())
        assert w12.centralWidget().geometry().top() >= top0 + w12.ribbon.height() - 2, "リボンのぶん、ページの表示が下がる"
        w12.move_toolbar(Qt.LeftToolBarArea); wait(100)
        assert ribbon_row(w12), (w12.toolbar.geometry(), w12.ribbon.geometry())
        w12.move_toolbar(Qt.TopToolBarArea); wait(100)
        assert ribbon_row(w12), (w12.toolbar.geometry(), w12.ribbon.geometry())
        w12.a_write.trigger(); wait(100)
        assert not w12.ribbon.isVisible() and w12.centralWidget().geometry().top() == top0, "閉じると段ごと消える（空の段を残さない）"
        w12.model.modified = False; assert w12.close()


    step("自動アップデート: 最新なら「最新です」、新版なら更新ダイアログ、スキップは記憶、更新するとダウンロード→インストーラー起動→終了")
    import io, json
    from updater import DOWNLOAD_PREFIX
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, os.path.join(_TMP, "qsettings_upd"))
    w7 = MainWindow(); w7.show(); wait(50)
    assert w7.a_auto_update.isChecked(), "既定で起動時チェックは ON"
    assert not w7.auto_update_enabled(), "ソース実行（exe 化していない）では起動時チェックしない"
    def rel(tag):
        return lambda url: json.dumps({"tag_name": tag, "html_url": "https://github.com/hidetako/pdf-page-editor/releases/tag/" + tag, "body": "notes",
                                       "draft": False, "prerelease": False,
                                       "assets": [{"name": f"PdfPageEditor-Setup-{tag[1:]}.exe", "size": 2048,
                                                   "browser_download_url": DOWNLOAD_PREFIX + f"{tag}/PdfPageEditor-Setup-{tag[1:]}.exe"}]}).encode()
    def run_check(w, manual, fetch):
        w.check_for_update(manual=manual, fetch=fetch); w._update_thread.wait(); wait(100)
    # 最新 → 手動なら「最新です」
    run_check(w7, True, rel("v" + appmod.__version__))
    assert infos and "最新です" in infos[-1], infos[-1:]
    # 通信エラー → 自動では黙る、手動では警告
    warns = []
    QMessageBox.warning = staticmethod(lambda *a_, **k: warns.append(a_[2]) or QMessageBox.Ok)
    def offline(url): raise OSError("offline")
    run_check(w7, False, offline); assert not warns
    run_check(w7, True, offline); assert warns and "確認できませんでした" in warns[-1]
    # 新版 → ダイアログ（差し替え）で「スキップ」→ 記憶され、次回の自動チェックでは出ない
    shown = []
    def choose(label):
        def _exec(dlg):
            shown.append(dlg.windowTitle())
            for b in dlg.buttons():
                if b.text() == label:
                    b.click(); return 0
            raise AssertionError(f"ボタンが無い: {label} / {[b.text() for b in dlg.buttons()]}")
        return _exec
    w7._exec_dialog = choose("このバージョンをスキップ")
    run_check(w7, False, rel("v9.9.9"))
    assert shown == ["新しいバージョン"] and w7._settings().value("update/skip_version") == "9.9.9"
    run_check(w7, False, rel("v9.9.9")); assert len(shown) == 1, "スキップ済みの版は自動では出ない"
    run_check(w7, True, rel("v9.9.9")); assert len(shown) == 2, "手動確認ならスキップ済みでも出る"
    # 「更新する」→（exe 版相当の環境変数）ダウンロード → インストーラー起動 → 終了
    os.environ["PDFPE_UPDATE_CHECK"] = "1"
    launched = []
    w7._launch_installer = lambda path: launched.append(path)
    data = os.urandom(2048)
    class FakeResp(io.BytesIO):
        headers = {}
    w7_download = w7._download_with_progress
    w7._download_with_progress = lambda info, opener=None: w7_download(info, lambda url: FakeResp(data))
    def exec_update(dlg):
        if dlg.windowTitle() == "新しいバージョン":
            return choose("更新する")(dlg)
        # 進捗ダイアログ: ダウンロード完了（accept）まで待つ
        for _ in range(300):
            QApplication.processEvents(); QTest.qWait(10)
            if dlg.result() == 1:
                break
        return dlg.result()
    w7._exec_dialog = exec_update
    run_check(w7, False, rel("v9.9.8")); wait(200)
    assert launched and launched[0].endswith("PdfPageEditor-Setup-9.9.8.exe") and open(launched[0], "rb").read() == data
    assert not w7.isVisible(), "インストーラー起動後にアプリは終了する"
    os.remove(launched[0])
    del os.environ["PDFPE_UPDATE_CHECK"]
    # 起動時チェックの ON/OFF は設定に保存される
    w8 = MainWindow(); w8.show(); wait(50)
    w8.a_auto_update.trigger(); wait()
    assert not w8.a_auto_update.isChecked() and w8._settings().value("update/check_on_start", type=bool) is False
    assert w8.close()

    step("変更なしで開き直し: 確認ダイアログは出ない")
    asked = []
    QMessageBox.question = staticmethod(lambda *a_, **k: asked.append(1) or QMessageBox.Cancel)
    win3 = MainWindow(); win3.show(); wait(50)
    win3.open_file(a); win3.open_file(b)
    assert not asked and os.path.samefile(win3.model.path, b)
    assert win3.close()

    print(f"GUI E2E: {_step[0]} steps passed  (tmp={_TMP})")


if __name__ == "__main__":
    main()
