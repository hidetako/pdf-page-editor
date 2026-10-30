"""GUI の E2E テスト（オフスクリーン）。python tests/test_e2e_gui.py で実行可。

実際の MainWindow を起動し、QAction・マウス・キー操作で一連の業務フロー
（開く → 閲覧 → ページ編集 → 押印 → 保存/抽出 → 終了確認）を通す。
ファイル選択・確認ダイアログだけ差し替え、それ以外は本番コードを通す。
環境変数 E2E_SHOTS にディレクトリを指定すると各段階のスクリーンショットを保存する。
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="pdfpe_e2e_")
os.environ["APPDATA"] = os.path.join(_TMP, "appdata")  # 印鑑ライブラリを隔離
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "xdg")  # QSettings (Linux) を隔離
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pymupdf as fitz
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QUrl, Qt
from PySide6.QtGui import QDropEvent, QWheelEvent
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


def thumb_center(win, row):
    r = win.thumbs.visualItemRect(win.thumbs.item(row))
    return r.center()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
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
    for act in (win.a_save, win.a_saveas, win.a_insert, win.a_delete, win.a_rotl, win.a_rotr,
                win.a_up, win.a_down, win.a_extract, win.a_undo, win.a_redo):
        assert not act.isEnabled(), act.text()
    assert win.a_open.isEnabled()
    assert win.view is None and win.empty_label.isVisible() and "ドラッグ" in win.empty_label.text()
    assert win.stamp_dock_widget.list.count() == 5, "既定の印鑑 5 種"
    win.a_stamp.trigger()
    assert not win.stamp_mode and not win.a_stamp.isChecked(), "文書なしでは押印モードに入れない"
    shot(win, "empty")

    # ---------------------------------------------------------------- 開く（エラー系）
    step("開く: 壊れた PDF / パスワード付き PDF はエラー表示で継続")
    QFileDialog.getOpenFileName = staticmethod(lambda *a_, **k: (broken, ""))
    win.a_open.trigger(); wait()
    assert len(errors) == 1 and win.model.doc is None
    QFileDialog.getOpenFileName = staticmethod(lambda *a_, **k: (locked, ""))
    win.a_open.trigger(); wait()
    assert len(errors) == 2 and "パスワード" in errors[-1]
    QFileDialog.getOpenFileName = staticmethod(lambda *a_, **k: ("", ""))
    win.a_open.trigger(); wait()
    assert len(errors) == 2, "キャンセルは何も起きない"

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
    win.goto(0); wait()
    vbar = win.scroll.verticalScrollBar()
    assert vbar.maximum() > 0 and win.pages_widget.height() > win.scroll.viewport().height() * 3
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
    win.goto(0); wait()

    step("ズーム: 拡大/縮小/幅合わせ/100%/Ctrl+ホイール")
    win.goto(0); wait()
    fit_w = win.view.pixmap().width()
    QTest.keyClick(win, Qt.Key_Equal, Qt.ControlModifier); wait()
    assert not win.fit_width and "ズーム" in win.page_label.text() and "幅合わせ" not in win.page_label.text()
    z1 = win.zoom
    QTest.keyClick(win, Qt.Key_Minus, Qt.ControlModifier); wait()
    assert win.zoom < z1
    QTest.keyClick(win, Qt.Key_1, Qt.ControlModifier); wait()
    assert win.zoom == 1.0 and "100%" in win.page_label.text()
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
    win.a_insert.trigger(); wait()
    assert "パスワード" in errors[-1] and win.model.page_count == 7
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
    QTest.keyClick(win, Qt.Key_F2); wait()
    assert win.stamp_mode and win.a_stamp.isChecked() and dock.btn_mode.isChecked()
    assert win.view.stamp_mode and win.view.ghost is not None
    assert "押印モード" in win.statusBar().currentMessage()
    g = win.view.pix_geometry(); ox, oy, pw, ph = g
    target = QPoint(int(ox + pw * 0.75), int(oy + ph * 0.25))
    QTest.mouseMove(win.view, target); wait()
    assert win.view.ghost_pos is not None, "マウス追従ゴースト"
    shot(win, "stamp_ghost")
    before_undo = len(win.model._undo)
    QTest.mouseClick(win.view, Qt.LeftButton, Qt.NoModifier, target); wait(100)
    assert len(win.model._undo) == before_undo + 1 and win.model.modified
    assert "押印しました（ページ 5）" in win.statusBar().currentMessage()
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
    ent = os.path.join(_TMP, "安藤印.entstmp")
    with open(ent, "w", encoding="utf-8-sig") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?><ENTSTMP product="EntrustStampForBusiness" version="1.0.2"><stampEntities>'
                f'<stampEntity class="ScannerStamp" image="{__import__("base64").b64encode(open(img, "rb").read()).decode()}" />'
                '</stampEntities></ENTSTMP>')
    QFileDialog.getOpenFileNames = staticmethod(lambda *a_, **k: ([ent, broken], ""))
    n_before = dock.list.count()
    dock.import_json(); wait()
    assert dock.list.count() == n_before + 1 and dock.lib.stamps[-1].name == "安藤印" and dock.lib.stamps[-1].kind == "image"
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
    assert not w5.stamp_dock.isVisible(), "印鑑パネルの表示状態も復元される"
    w5.addToolBar(Qt.TopToolBarArea, w5.toolbar); w5.stamp_dock.show(); assert w5.close()

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
    QTest.mouseMove(w6.view, on_note); wait(100)
    assert "要確認です" in QToolTip.text(), QToolTip.text()
    QTest.mouseMove(w6.view, QPoint(int(ox + pw * 0.1), int(oy + ph * 0.9))); wait(700)   # hideText はフェード後に消える
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
