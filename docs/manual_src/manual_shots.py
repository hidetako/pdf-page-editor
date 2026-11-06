"""説明書用の画面写真を、実アプリをオフスクリーンで動かして撮る。番号付きの丸印（コールアウト）も描く。"""
import os, sys, tempfile, json
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = tempfile.mkdtemp(prefix="pdfpe_manual_")
os.environ["APPDATA"] = os.path.join(TMP, "appdata")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "xdg")
os.environ["USERNAME"] = "山田"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import pymupdf as fitz
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt, QRect
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QFont, QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMessageBox, QMenu, QWidget
import app as appmod
from app import MainWindow
from stamps import Stamp

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
app = QApplication([])
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, os.path.join(TMP, "qs"))
app.setFont(QFont("Noto Sans CJK JP", 10))

# ---- サンプル文書（ダミーの業務委託契約書 4 ページ、目次付き）
F = "japan"
def page_text(p, items):
    for (x, y, text, fs) in items:
        p.insert_text((x, y), text, fontsize=fs, fontname=F)
doc = fitz.open()
p = doc.new_page(width=595, height=842)
page_text(p, [(200, 110, "業務委託契約書", 24),
    (72, 180, "株式会社サンプル商事（以下「甲」という。）と合同会社ダミー技研（以下「乙」という。）は、", 10.5),
    (72, 198, "次のとおり業務委託契約（以下「本契約」という。）を締結する。", 10.5),
    (72, 240, "第1条（目的）", 12),
    (72, 262, "甲は乙に対し、第2条に定める業務を委託し、乙はこれを受託する。", 10.5),
    (72, 300, "第2条（業務内容）", 12),
    (72, 322, "乙が甲に提供する業務は次のとおりとする。", 10.5),
    (90, 342, "（1）文書管理システムの保守および運用支援", 10.5),
    (90, 360, "（2）電子帳票の作成および押印フローの設計", 10.5),
    (90, 378, "（3）前各号に付帯する業務", 10.5),
    (72, 416, "第3条（契約期間）", 12),
    (72, 438, "本契約の有効期間は、2026年11月1日から2027年10月31日までとする。", 10.5),
    (72, 476, "第4条（委託料）", 12),
    (72, 498, "甲は乙に対し、業務の対価として月額金300,000円（消費税別）を支払う。", 10.5),
    (72, 536, "第5条（秘密保持）", 12),
    (72, 558, "甲および乙は、本契約の履行に関して知り得た相手方の秘密情報を、相手方の事前の", 10.5),
    (72, 576, "書面による承諾なく第三者に開示または漏洩してはならない。", 10.5),
    (470, 790, "1 / 4", 9)])
p = doc.new_page(width=595, height=842)
page_text(p, [(72, 110, "第6条（検収）", 12),
    (72, 132, "乙は業務の成果物を甲に納入し、甲は納入後10営業日以内に検収を行う。", 10.5),
    (72, 170, "第7条（知的財産権）", 12),
    (72, 192, "成果物に関する著作権その他の知的財産権は、委託料の支払完了をもって甲に移転する。", 10.5),
    (72, 230, "第8条（契約の解除）", 12),
    (72, 252, "甲または乙は、相手方が本契約に違反し、催告後14日以内に是正されないときは、", 10.5),
    (72, 270, "本契約を解除することができる。", 10.5),
    (470, 790, "2 / 4", 9)])
p = doc.new_page(width=595, height=842)
page_text(p, [(72, 110, "本契約の成立を証するため、本書2通を作成し、甲乙記名押印のうえ各1通を保有する。", 10.5),
    (72, 160, "2026年10月30日", 11),
    (72, 230, "甲　東京都千代田区丸の内一丁目1番1号", 11),
    (100, 252, "株式会社サンプル商事", 11),
    (100, 274, "代表取締役　山田 太郎", 11),
    (72, 360, "乙　長野県上田市中央一丁目2番3号", 11),
    (100, 382, "合同会社ダミー技研", 11),
    (100, 404, "代表社員　佐藤 花子", 11),
    (470, 790, "3 / 4", 9)])
p = doc.new_page(width=595, height=842)
page_text(p, [(220, 110, "別紙　業務仕様書", 16),
    (72, 160, "1. 作業場所　甲の指定する事業所または乙の事業所", 10.5),
    (72, 182, "2. 報告　乙は毎月末に当月の作業報告書を甲へ提出する。", 10.5),
    (470, 790, "4 / 4", 9)])
doc.set_toc([[1, "業務委託契約書", 1], [2, "第1条〜第5条", 1], [2, "第6条〜第8条", 2], [1, "記名押印", 3], [1, "別紙 業務仕様書", 4]])
pdf = os.path.join(TMP, "業務委託契約書.pdf"); doc.save(pdf); doc.close()

META = {}

def draw_callouts(pm: QPixmap, callouts, rects=()):
    """番号付きの赤丸と、強調の赤枠を描く。callouts: [(x, y, n)], rects: [QRect]"""
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    for r in rects:
        p.setPen(QPen(QColor(220, 30, 30), 4)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 6, 6)
    f = QFont("Noto Sans CJK JP", 15); f.setBold(True)
    p.setFont(f)
    for (x, y, n) in callouts:
        p.setPen(QPen(QColor(255, 255, 255), 3)); p.setBrush(QColor(220, 30, 30))
        p.drawEllipse(QPoint(int(x), int(y)), 17, 17)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRect(int(x) - 17, int(y) - 17, 34, 34), Qt.AlignCenter, str(n))
    p.end()

def shot(win, name, callouts=(), rects=(), crop=None):
    QApplication.processEvents(); QTest.qWait(200); QApplication.processEvents()
    pm = win.grab()
    draw_callouts(pm, callouts, rects)
    if crop:
        pm = pm.copy(crop)
    pm.save(os.path.join(OUT, name))
    print("shot", name)

def center_in(win, w: QWidget):
    g = w.geometry(); c = w.mapTo(win, QPoint(g.width() // 2, g.height() // 2))
    return c.x(), c.y()

def tb_center(win, action):
    """ツールバーのボタンの少し下（ラベルを隠さない位置）"""
    w = win.toolbar.widgetForAction(action)
    x, y = center_in(win, w)
    return x, y + 26

def tb_rect(win, action):
    w = win.toolbar.widgetForAction(action)
    tl = w.mapTo(win, QPoint(0, 0)); return QRect(tl.x() - 2, tl.y() - 2, w.width() + 4, w.height() + 4)

win = MainWindow(); win.resize(1400, 900); win.show(); QTest.qWait(300)
lib = win.stamp_dock_widget.lib
lib.stamps = [
    Stamp(name="認印（山田）", kind="mitome", lines=["山田"], size_mm=11),
    Stamp(name="データネーム印", kind="datename", lines=["承認", "山田"], size_mm=16),
    Stamp(name="角印", kind="kaku", lines=["株式会社", "サンプル商事", "之印"], size_mm=18, border=0.5),
    Stamp(name="済", kind="user", lines=["済"], size_mm=10),
]
win.stamp_dock_widget.refresh()

# 1. 起動直後
shot(win, "01_start.png", callouts=[(*tb_center(win, win.a_open), 1), (700, 470, 2)])

# 2. 開いた直後の全体像
win.open_file(pdf); QTest.qWait(400); win.zoom_fit(); QTest.qWait(300)
win.goto(0); QTest.qWait(200)
thumbs_c = center_in(win, win.thumbs)
scroll_c = center_in(win, win.scroll)
toc_c = center_in(win, win.toc_tree)
sb = win.statusBar(); sb_c = center_in(win, win.page_label)
mb = win.menuBar(); mb_c = (mb.mapTo(win, QPoint(60, mb.height() // 2)).x(), mb.mapTo(win, QPoint(0, mb.height() // 2)).y())
shot(win, "02_overview.png", callouts=[(mb_c[0], mb_c[1], 1), (tb_center(win, win.a_save)[0], tb_center(win, win.a_save)[1], 2),
                                       (toc_c[0], toc_c[1] - 150, 3), (thumbs_c[0], thumbs_c[1] - 200, 4), (scroll_c[0] + 250, scroll_c[1] - 250, 5), (sb_c[0] - 180, sb_c[1], 6)])

# 3. ツールバー拡大（ページ編集ボタン）
tl = win.toolbar.mapTo(win, QPoint(0, 0))
shot(win, "03_toolbar.png", callouts=[(*tb_center(win, win.a_insert), 1), (*tb_center(win, win.a_delete), 2), (*tb_center(win, win.a_rotl), 3),
                                      (*tb_center(win, win.a_up), 4), (*tb_center(win, win.a_undo), 5)],
     crop=QRect(0, tl.y() - 4, win.width(), win.toolbar.height() + 40))

# 4. サムネイルで 2 ページを選択（Ctrl+クリック）
win.thumbs.item(1).setSelected(True); win.thumbs.item(2).setSelected(True); QTest.qWait(150)
shot(win, "04_thumbs_select.png", callouts=[(thumbs_c[0], center_in(win, win.thumbs)[1] - 60, 1)],
     crop=QRect(win.thumbs.mapTo(win, QPoint(0, 0)).x() - 4, win.thumbs.mapTo(win, QPoint(0, 0)).y() - 4, win.thumbs.width() + 8, min(620, win.thumbs.height() + 8)))
win.thumbs.clearSelection(); win.thumbs.item(0).setSelected(True)

# 5. 押印モード: パネルが開き、ゴーストが出る。3 ページ目に角印と認印
win.goto(2); QTest.qWait(200)
dock = win.stamp_dock_widget
def stamp_with(name, center, size_mm):
    dock.list.setCurrentRow([s.name for s in lib.stamps].index(name))
    dock.sp_size.setValue(size_mm)
    png = dock.render_current()
    st = dock.settings(); w_pt, h_pt = win._stamp_size_pt(st)
    win.model.stamp(2, png, center, w_pt, h_pt)
stamp_with("角印", (296, 258), 18)
dock.sp_size.setValue(0)
win.refresh_thumbs(select=[2]); QTest.qWait(200)
dock.list.setCurrentRow(0)
QTest.keyClick(win, Qt.Key_F2); QTest.qWait(300)
win.zoom_fit(); QTest.qWait(300)
v = win.view; ox, oy, pw, ph = v.pix_geometry()
QTest.mouseMove(v, QPoint(int(ox + pw * 0.60), int(oy + ph * 0.33))); QTest.qWait(150)
from PySide6.QtGui import QMouseEvent
pos = QPointF(ox + pw * 0.60, oy + ph * 0.33)
QApplication.sendEvent(v, QMouseEvent(QMouseEvent.Type.MouseMove, pos, v.mapToGlobal(pos.toPoint()).toPointF(), Qt.NoButton, Qt.NoButton, Qt.NoModifier)); QTest.qWait(150)
dk = win.stamp_dock
dk_tl = dk.mapTo(win, QPoint(0, 0))
list_c = center_in(win, dock.list)
btn_c = center_in(win, dock.btn_mode)
gpos = v.mapTo(win, pos.toPoint())
shot(win, "05_stamp_mode.png", callouts=[(list_c[0], list_c[1], 1), (btn_c[0], btn_c[1], 2), (gpos.x() + 40, gpos.y() - 30, 3)],
     rects=[QRect(dk_tl.x(), dk_tl.y(), dk.width(), dk.height())])
# 実際に押す
QTest.mouseClick(v, Qt.LeftButton, Qt.NoModifier, pos.toPoint()); QTest.qWait(300)
QTest.keyClick(win, Qt.Key_Escape); QTest.qWait(100)

# 6. 押印の取り消しメニュー（印影の上で右クリック）
stamps = win.model.stamps_on_page(2)
r = stamps[-1][1]; pr = win.model.doc[2].rect
v = win.view; ox, oy, pw, ph = v.pix_geometry()
on_stamp = QPoint(int(ox + (r.x0 + r.width / 2) * pw / pr.width), int(oy + (r.y0 + r.height / 2) * ph / pr.height))
captured = {}
def grab_menu(menu, gp):
    menu.move(v.mapToGlobal(on_stamp)); menu.show(); QTest.qWait(200)
    captured["menu"] = menu.grab(); menu.hide()
win._popup_menu = grab_menu
from PySide6.QtGui import QContextMenuEvent
QApplication.sendEvent(v, QContextMenuEvent(QContextMenuEvent.Mouse, on_stamp, v.mapToGlobal(on_stamp))); QTest.qWait(200)
pm = win.grab()
mp = v.mapTo(win, on_stamp)
pt = QPainter(pm); pt.drawPixmap(mp.x() + 6, mp.y() + 6, captured["menu"]); pt.end()
draw_callouts(pm, [(mp.x() + 6 + captured["menu"].width() + 24, mp.y() + 6 + 14, 1)])
crop = QRect(max(0, mp.x() - 320), max(0, mp.y() - 160), 760, 420)
pm.copy(crop).save(os.path.join(OUT, "06_stamp_cancel.png")); print("shot 06")

# 7. 付箋とマーカー、テキスト注釈（1 ページ目）
win.goto(0); QTest.qWait(200)
win.model.add_note(0, (470, 540), "第5条の期間を確認してください。", title="山田")
words = win.model.page_words(0)
sec5 = [w for w in words if "第5条" in w["text"]]
if sec5:
    win.model.add_markup(0, "highlight", [sec5[0]["rect"]])
win.model.add_free_text(0, (380, 660), "確認済み　2026/11/06", fontsize=14)
win.refresh_thumbs(select=[0], keep_view=True); QTest.qWait(300)
v = win.view; ox, oy, pw, ph = v.pix_geometry(); pr = win.model.doc[0].rect
def pt_of(x, y):
    q = v.mapTo(win, QPoint(int(ox + x * pw / pr.width), int(oy + y * ph / pr.height))); return q.x(), q.y()
n = pt_of(470, 540); h = pt_of(100, 530); t = pt_of(380, 660)
shot(win, "07_annots.png", callouts=[(h[0] - 30, h[1], 1), (n[0] + 30, n[1], 2), (t[0] - 30, t[1] + 8, 3), (*tb_center(win, win.a_note), 4), (*tb_center(win, win.a_highlight), 5), (*tb_center(win, win.a_text), 6)])

# 8. 表示モード（暖色）
win.set_display_mode("warm"); QTest.qWait(300)
shot(win, "08_warm.png")
win.set_display_mode("normal"); QTest.qWait(200)

# 9. パスワード入力ダイアログ
dlg = QInputDialog(win); dlg.setWindowTitle("パスワード付き PDF"); dlg.setLabelText("業務委託契約書.pdf を開くパスワード:")
dlg.setTextEchoMode(QLineEdit.Password); dlg.setTextValue("secret123"); dlg.resize(420, 140); dlg.show(); QTest.qWait(300)
dlg.grab().save(os.path.join(OUT, "09_password.png")); dlg.hide(); print("shot 09")

# 10. 更新の案内
box = QMessageBox(win); box.setWindowTitle("更新があります"); box.setIcon(QMessageBox.Information)
box.setText(f"<b>{appmod.APP_NAME} 1.4.1</b> が公開されています（お使いのバージョンは {appmod.__version__}）。<br>ダウンロードしてインストールしますか？")
b_yes = box.addButton("更新する", QMessageBox.AcceptRole); box.addButton("後で", QMessageBox.RejectRole); box.addButton("このバージョンをスキップ", QMessageBox.DestructiveRole)
box.show(); QTest.qWait(300)
pm = box.grab(); tl = b_yes.mapTo(box, QPoint(0, 0))
draw_callouts(pm, [], rects=[QRect(tl.x() - 3, tl.y() - 3, b_yes.width() + 6, b_yes.height() + 6)])
pm.save(os.path.join(OUT, "10_update.png")); box.hide(); print("shot 10")

# 11. 終了時の確認
box2 = QMessageBox(win); box2.setWindowTitle(appmod.APP_NAME); box2.setIcon(QMessageBox.Question)
box2.setText("変更が保存されていません。保存しますか？")
box2.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
box2.show(); QTest.qWait(300); box2.grab().save(os.path.join(OUT, "11_confirm.png")); box2.hide(); print("shot 11")

# 12. 印鑑の編集ダイアログ
try:
    from stamp_ui import StampEditDialog
    ed = StampEditDialog(lib.stamps[1], win); ed.show(); QTest.qWait(400)
    ed.grab().save(os.path.join(OUT, "12_stamp_edit.png")); ed.hide(); print("shot 12")
except Exception as e:
    print("stamp edit dialog skipped:", e)

print("done", OUT)
