"""PC が苦手な人向けの「PDF Page Editor かんたん説明書」を reportlab で組む。"""
import os, sys, datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image, Table, TableStyle,
                                KeepTogether, CondPageBreak)
from PIL import Image as PILImage

IMG = sys.argv[1]      # 画面写真のフォルダ
OUT = sys.argv[2]      # 出力 PDF
VERSION = sys.argv[3] if len(sys.argv) > 3 else "1.5.0"
pdfmetrics.registerFont(TTFont("JP", os.path.join(IMG, "DroidSansFallback.ttf")))

W, H = A4
M = 18 * mm
RED = colors.HexColor("#c8102e")
NAVY = colors.HexColor("#1f3a5f")
GRAY = colors.HexColor("#555555")
LIGHT = colors.HexColor("#f3f4f6")
YEL = colors.HexColor("#fff7d6")

st_title = ParagraphStyle("title", wordWrap="CJK", fontName="JP", fontSize=30, leading=40, textColor=NAVY, alignment=TA_CENTER)
st_sub = ParagraphStyle("sub", wordWrap="CJK", fontName="JP", fontSize=16, leading=24, textColor=GRAY, alignment=TA_CENTER)
st_h1 = ParagraphStyle("h1", wordWrap="CJK", fontName="JP", fontSize=19, leading=27, textColor=colors.white, backColor=NAVY,
                       borderPadding=(6, 8, 6, 8), spaceBefore=6, spaceAfter=12, keepWithNext=1)
st_h2 = ParagraphStyle("h2", wordWrap="CJK", fontName="JP", fontSize=14.5, leading=21, textColor=NAVY, spaceBefore=12, spaceAfter=6, keepWithNext=1)
st_body = ParagraphStyle("body", wordWrap="CJK", fontName="JP", fontSize=12, leading=20, spaceAfter=6)
st_small = ParagraphStyle("small", wordWrap="CJK", fontName="JP", fontSize=9.5, leading=14, textColor=GRAY)
st_cap = ParagraphStyle("cap", wordWrap="CJK", fontName="JP", fontSize=10, leading=15, textColor=GRAY, alignment=TA_CENTER, spaceAfter=10)
st_step_no = ParagraphStyle("stepno", wordWrap="CJK", fontName="JP", fontSize=15, leading=20, textColor=colors.white, alignment=TA_CENTER)
st_step = ParagraphStyle("step", wordWrap="CJK", fontName="JP", fontSize=12, leading=19)
st_tip = ParagraphStyle("tip", wordWrap="CJK", fontName="JP", fontSize=11, leading=17)
st_word = ParagraphStyle("word", wordWrap="CJK", fontName="JP", fontSize=11, leading=16.5)
st_toc = ParagraphStyle("toc", wordWrap="CJK", fontName="JP", fontSize=13, leading=24)

story = []
def P(text, style=st_body):
    story.append(Paragraph(text, style))
def H1(text):
    story.append(CondPageBreak(95 * mm))   # 見出しの下に十分な余白が無ければ改ページ
    story.append(Paragraph(text, st_h1))
def H2(text):
    story.append(CondPageBreak(60 * mm))
    story.append(Paragraph(text, st_h2))
def SP(h=6):
    story.append(Spacer(1, h))

def IMGF(name, width_mm=170, caption=None):
    path = os.path.join(IMG, name)
    w, h = PILImage.open(path).size
    width = width_mm * mm
    if width > W - 2 * M:
        width = W - 2 * M
    img = Image(path, width=width, height=width * h / w)
    img.hAlign = "CENTER"
    items = [img]
    if caption:
        items.append(Spacer(1, 3))
        items.append(Paragraph(caption, st_cap))
    story.append(KeepTogether(items))

def STEPS(items):
    """番号付き手順。items: [text, ...]"""
    rows = []
    for i, t in enumerate(items, 1):
        rows.append([Paragraph(str(i), st_step_no), Paragraph(t, st_step)])
    tbl = Table(rows, colWidths=[11 * mm, W - 2 * M - 11 * mm])
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (1, 0), (1, -1), 8),
             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
             ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#dddddd"))]
    for i in range(len(rows)):
        style.append(("BACKGROUND", (0, i), (0, i), RED))
        style.append(("ROUNDEDCORNERS", [4, 4, 4, 4]))
    tbl.setStyle(TableStyle(style))
    story.append(KeepTogether(tbl))
    SP(8)

def TIP(text, label="ポイント", bg=YEL):
    tbl = Table([[Paragraph(f"<font color='#c8102e'>{label}</font>　{text}", st_tip)]], colWidths=[W - 2 * M])
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#e6d9a0")),
                             ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                             ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story.append(tbl)
    SP(10)

def WORDS(pairs):
    rows = [[Paragraph(f"<b>{a}</b>", st_word), Paragraph(b, st_word)] for a, b in pairs]
    tbl = Table(rows, colWidths=[42 * mm, W - 2 * M - 42 * mm])
    tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (0, -1), LIGHT),
                             ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story.append(KeepTogether(tbl))
    SP(8)

# ============================================================ 表紙
SP(70)
P("PDF Page Editor", st_title)
P("かんたん説明書", st_title)
SP(18)
P("パソコンが苦手な方のための、やさしい使い方ガイド", st_sub)
SP(40)
story.append(Image(os.path.join(IMG, "02_overview.png"), width=150 * mm, height=150 * mm * 900 / 1400))
SP(40)
today = datetime.date.today()
P(f"バージョン {VERSION} 対応　　{today.year} 年 {today.month} 月", st_sub)
P("Yorozu Solutions　https://www.yorozusolutions.com/", st_sub)
story.append(PageBreak())

# ============================================================ もくじ
H1("もくじ")
toc = ["1. このアプリでできること", "2. 説明書で使うことば", "3. 最初の一回だけ: インストールする", "4. 起動する・画面の見方",
       "5. PDF を開く", "6. ページを見る（スクロール・拡大・見やすい色）", "7. ページを並べ替える・回転する・消す・足す",
       "8. 印鑑を押す", "9. 書き込む（ペン・図形・文字・蛍光ペン・付箋）", "10. 保存する・印刷する", "11. パスワード付きの PDF",
       "12. 「新しいバージョンがあります」と出たら", "13. 困ったときは（よくある質問）"]
for t in toc:
    P(t, st_toc)
SP(16)
TIP("この説明書は、むずかしい言葉をできるだけ使わずに書いています。赤い丸の数字は、画面写真の中の場所を示しています。", "読み方")
story.append(PageBreak())

# ============================================================ 1
H1("1. このアプリでできること")
P("PDF Page Editor（ピーディーエフ ページ エディター）は、PDF という書類ファイルを「見る」「並べ替える」「ハンコを押す」ためのソフトです。契約書や申請書など、紙の書類と同じように扱えます。")
WORDS([("見る", "書類を画面で読む。文字を大きくしたり、目にやさしい色にしたりできる。"),
       ("並べ替える", "ページの順番を入れ替える。いらないページを消す。別の書類のページを足す。"),
       ("ハンコを押す", "自分の印鑑（認印・角印・日付入り印など）を作って、クリックした場所に押す。"),
       ("書き込む", "ペンで線を引く、矢印や四角・丸で示す、文字を書き込む、蛍光ペンを引く、付箋（メモ）を貼る。"),
       ("保存・印刷", "変えた内容を保存する。プリンターで印刷する。")])
TIP("できないこと: PDF の本文の文字そのものを書き換えることはできません。文字を直したい場合は、元の Word などで直してから PDF にし直してください。", "注意")

# ============================================================ 2
H1("2. 説明書で使うことば")
WORDS([("クリック", "マウスの左ボタンを 1 回カチッと押す。"),
       ("ダブルクリック", "マウスの左ボタンを 2 回すばやく押す。"),
       ("右クリック", "マウスの右ボタンを 1 回押す。小さなメニューが出る。"),
       ("ドラッグ", "左ボタンを押したまま、マウスを動かして、目的の場所で指を離す。"),
       ("ドラッグ＆ドロップ", "ファイルをドラッグして、アプリの画面の上で指を離す。"),
       ("ホイール", "マウスの真ん中にある回る部品。回すと画面が上下に動く。"),
       ("Ctrl（コントロール）キー", "キーボードの左下にあるキー。「Ctrl を押しながら S」のように、他のキーと一緒に使う。"),
       ("Esc（エスケープ）キー", "キーボードの左上にあるキー。「やめる」「元の状態に戻る」ときに押す。")])
story.append(PageBreak())

# ============================================================ 3
H1("3. 最初の一回だけ: インストールする")
P("インストールとは、パソコンにソフトを入れる作業です。一度やれば、次からは必要ありません。インターネットにつながった Windows 11 または Windows 10 のパソコンで行います。")
STEPS(["ブラウザー（Edge や Chrome）で、次のページを開きます。<br/><font color='#1f3a5f'>https://github.com/hidetako/pdf-page-editor/releases/latest</font><br/>（Yorozu Solutions のホームページにある「ダウンロード」ボタンからも開けます）",
       "ページの下のほうにある <b>PdfPageEditor-Setup-○.○.○.exe</b> という名前をクリックします。ダウンロードが始まります（1 〜 2 分ほど）。",
       "ダウンロードが終わったら、そのファイルを開きます。ブラウザーの右上に出る「ファイルを開く」をクリックするか、「ダウンロード」フォルダーの中のファイルをダブルクリックします。",
       "青い画面で「<b>Windows によって PC が保護されました</b>」と出たら、「<b>詳細情報</b>」の文字をクリックし、次に出る「<b>実行</b>」ボタンをクリックします。これはこのソフトがまだ Microsoft に登録されていないために出る表示で、ウイルスではありません。",
       "インストールの画面が出ます。「<b>次へ</b>」を何回か押し、最後に「<b>完了</b>」を押します。管理者のパスワードは必要ありません。",
       "スタートメニュー（画面左下の Windows のマーク）に「PDF Page Editor」が追加されます。デスクトップにもアイコンが置かれます。"])
TIP("「詳細情報」の文字が見つからないときは、青い画面の中の文章をよく見てください。小さな文字で書かれています。")
TIP("以前に古い版を入れたことがある場合も、同じ手順で新しい版に置き換わります。先に古い版を消す必要はありません。", "補足")
story.append(PageBreak())

# ============================================================ 4
H1("4. 起動する・画面の見方")
STEPS(["デスクトップの赤いアイコン「PDF Page Editor」をダブルクリックします。または、スタートメニューから「PDF Page Editor」をクリックします。",
       "次のような画面が開きます。まだ書類は何も表示されていません。"])
IMGF("01_start.png", 165, "起動直後の画面。<font color='#c8102e'>1</font> 「開く」ボタン　<font color='#c8102e'>2</font> ここに PDF をドラッグ＆ドロップしてもよい")
P("書類を開くと、画面は次のようになります。")
IMGF("02_overview.png", 170, "<font color='#c8102e'>1</font> メニュー　<font color='#c8102e'>2</font> ボタンの列（よく使う操作）　<font color='#c8102e'>3</font> 目次・しおり　<font color='#c8102e'>4</font> ページの一覧（小さな絵）　<font color='#c8102e'>5</font> 書類の本体　<font color='#c8102e'>6</font> 今のページ番号")
TIP("画面の大きさは、ウィンドウの端をドラッグして変えられます。ウィンドウの右上の四角いボタンを押すと画面いっぱいになります。")
story.append(PageBreak())

# ============================================================ 5
H1("5. PDF を開く")
P("開き方は 2 通りあります。どちらでも同じです。")
H2("方法 A: 「開く」ボタンを使う")
STEPS(["画面左上の「<b>開く</b>」ボタンをクリックします。",
       "ファイルを選ぶ画面が出ます。目的の PDF をクリックして選び、右下の「<b>開く</b>」を押します。"])
H2("方法 B: ファイルをドラッグ＆ドロップする")
STEPS(["デスクトップやフォルダーにある PDF のファイルを、アプリの画面の上までドラッグします。",
       "画面の上で指を離します。すぐに書類が表示されます。"])
TIP("エクスプローラーで PDF を右クリックし、「プログラムから開く」→「PDF Page Editor」を選んでも開けます。")
TIP("別の書類を開くと、今の書類は閉じます。変更を保存していないときは「保存しますか？」と聞かれるので、「Save（保存）」を選べば安心です。", "注意")

# ============================================================ 6
H1("6. ページを見る")
H2("ページを進める・戻る")
P("マウスのホイールを回すと、書類が上下に動きます。すべてのページが縦につながって並んでいるので、紙の束をめくるように続けて読めます。左側の小さな絵（ページの一覧）をクリックすると、そのページへ一気に移動します。")
H2("文字を大きくする・小さくする")
P("ボタンの列の右端にある「<b>拡大</b>」「<b>縮小</b>」を押します。Ctrl キーを押しながらホイールを回しても同じです。「表示」メニューの「幅に合わせる」を選ぶと、ページ全体がちょうど画面の幅に収まります。")
H2("目にやさしい色にする")
P("「表示」メニュー →「表示モード」で、紙の色を変えられます。白い紙がまぶしいときは「暖色」（クリーム色）、夜に読むときは「白黒反転」が楽です。次に起動したときも同じ色のままになります。")
IMGF("08_warm.png", 150, "暖色モード。紙の部分がクリーム色になる")
story.append(PageBreak())

# ============================================================ 7
H1("7. ページを並べ替える・回転する・消す・足す")
P("ページの操作は、左側の「ページの一覧」で行います。まず操作したいページの小さな絵をクリックして選びます（水色になります）。複数のページを選びたいときは、Ctrl キーを押しながらクリックします。")
IMGF("04_thumbs_select.png", 48, "ページ 1〜3 を選んだところ（水色が選択中）")
IMGF("03_toolbar.png", 175, "<font color='#c8102e'>1</font> ページ挿入（足す）　<font color='#c8102e'>2</font> 削除（消す）　<font color='#c8102e'>3</font> 左回転・右回転　<font color='#c8102e'>4</font> 前へ・後へ移動　<font color='#c8102e'>5</font> 元に戻す")
WORDS([("順番を変える", "ページの一覧で、小さな絵をドラッグして、入れたい場所で離します。または「前へ移動」「後へ移動」ボタンを押します。"),
       ("向きを直す", "横向きにスキャンされたページなどは「左回転」「右回転」で直します。"),
       ("消す", "ページを選んで「削除」ボタン。まちがえて消しても「元に戻す」で戻ります。"),
       ("足す", "「ページ挿入」ボタンを押し、別の PDF や写真（JPEG・PNG）を選びます。選んでいるページの後ろに入ります。"),
       ("一部だけ取り出す", "「ファイル」メニュー →「選択ページを抽出」で、選んだページだけを別の PDF として保存できます。")])
TIP("どの操作も、「<b>元に戻す</b>」ボタン（または Ctrl を押しながら Z）で 1 つ前の状態に戻せます。20 回までさかのぼれます。安心して試してください。")
story.append(PageBreak())

# ============================================================ 8
H1("8. 印鑑を押す")
P("画面右側の「電子印鑑」パネルに、自分の印鑑を登録しておきます。最初から見本の印鑑がいくつか入っています。")
H2("自分の印鑑を作る")
STEPS(["ボタンの列の「<b>押印モード</b>」を押すと、右側に「電子印鑑」パネルが開きます。",
       "パネルの下にある「<b>新規</b>」ボタンを押します。",
       "「表示名」に印鑑の名前、「種類」で認印・角印・日付入り印などを選び、「文字」に名前（例: 山田）を入れます。右側に出来上がりが表示されます。",
       "「<b>OK</b>」を押すと、一覧に追加されます。"])
IMGF("12_stamp_edit.png", 130, "印鑑を作る画面。右側に出来上がりが表示される")
TIP("すでに持っている印影の画像（PNG や JPEG）や、「おまかせ電子印鑑」の .entstmp ファイルは、「<b>インポート</b>」ボタンから取り込めます。")
H2("押す")
STEPS(["右側の一覧で、押したい印鑑をクリックして選びます。",
       "「<b>押印モード</b>」ボタンを押します（すでに押してあればそのまま）。マウスを書類の上に動かすと、印鑑の薄い影が一緒に動きます。",
       "押したい場所でクリックします。カチッと押せます。続けて何か所でも押せます。",
       "終わったら、キーボードの <b>Esc</b> キーを押します（または「押印モード」ボタンをもう一度押します）。"])
IMGF("05_stamp_mode.png", 170, "<font color='#c8102e'>1</font> 印鑑の一覧　<font color='#c8102e'>2</font> 押印モードのボタン　<font color='#c8102e'>3</font> マウスについて動く印鑑の影（ここでクリックすると押せる）")
H2("まちがえて押したとき")
STEPS(["押した印鑑の上で<b>右クリック</b>します。",
       "「<b>この押印を取り消す</b>」をクリックします。"])
IMGF("06_stamp_cancel.png", 120, "印鑑の上で右クリックすると出るメニュー")
TIP("印鑑の大きさや日付は、パネルの下の「押印設定」で変えられます。「不透明度」を少し下げると、朱肉が薄くなり本物らしくなります。")
story.append(PageBreak())

# ============================================================ 9
H1("9. 書き込む（ペン・図形・文字・蛍光ペン・付箋）")
P("書類の上に、線や図形、文字を書き込めます。「<b>書き込み</b>」ボタンを押すと、その下に書き込み用の道具の列（リボン）が出ます。書いたものは「注釈」という PDF の標準の仕組みで保存されるので、Adobe Acrobat など他のソフトでも見えます。")
IMGF("07_write.png", 170, "赤枠が「書き込み」ボタン。<font color='#c8102e'>1</font> 道具（選択・ペン・直線・矢印・四角・丸・文字）　<font color='#c8102e'>2</font> 蛍光ペン・下線・取り消し線　<font color='#c8102e'>3</font> 色　<font color='#c8102e'>4</font> 太さ・濃さ・塗りつぶし　<font color='#c8102e'>5</font> 閉じる")
H2("線や図形を描く")
STEPS(["「<b>書き込み</b>」ボタンを押します。道具の列が出ます。",
       "使いたい道具（<b>ペン・直線・矢印・四角・丸</b>）を押します。",
       "色を変えたいときは、色の四角を押します。太さ・濃さも選べます。",
       "書類の上で、マウスの左ボタンを押したまま動かし、指を離します。ペンは動かしたとおりの線、矢印は離した側が矢の先になります。",
       "続けて何回でも描けます。終わったら <b>Esc</b> キーを押すと「選択」の道具に戻り、もう一度 <b>Esc</b> を押すと道具の列が閉じます。"])
IMGF("07b_ribbon_shape.png", 175, "書き込み用の道具の列（線を描くとき）")
TIP("太さを「マーカー」、濃さを「50%」にしてペンで描くと、手書きの蛍光ペンとして使えます。")
H2("文字を書き込む")
STEPS(["道具の列の「<b>文字</b>」を押します。道具の列の右側が、文字用の設定に変わります。",
       "フォント（書体）、大きさ、色を選びます。「背景」を白にすると、下の文字を隠して上書きしたように見せられます。",
       "書き込みたい場所をクリックし、文字を入力して「OK」を押します。",
       "書いた文字を直したいときは、その文字をクリックします（「選択」の道具のときはダブルクリック）。文字を全部消して OK を押すと、書き込みごと消えます。"])
IMGF("07c_ribbon_text.png", 175, "文字を書くときの設定。<font color='#c8102e'>1</font> フォント　<font color='#c8102e'>2</font> 大きさ　<font color='#c8102e'>3</font> 背景")
TIP("フォントは「標準ゴシック」のほか、パソコンに入っている「メイリオ」などが選べます。選んだフォントは PDF の中に入るので、他のパソコンで開いても同じ見た目になります。")
H2("動かす・大きさを変える・消す")
STEPS(["道具の列の「<b>選択</b>」を押します（道具の列を閉じていても同じことができます）。",
       "書いたものをクリックします。青い点線の枠が付きます。",
       "そのままドラッグすると<b>移動</b>します。右下の小さな四角をドラッグすると<b>大きさ</b>が変わります。",
       "<b>Delete</b> キーを押すと消えます。選んだまま色や太さを変えると、その書き込みの色や太さが変わります。"])
IMGF("07d_selected.png", 120, "選んだところ。<font color='#c8102e'>1</font> の小さな四角をドラッグすると大きさが変わる")
TIP("まちがえても「<b>元に戻す</b>」ボタン（Ctrl を押しながら Z）で 1 つずつ戻せます。")
H2("蛍光ペンを引く")
STEPS(["書類の文字の上を、ドラッグしてなぞります。文字が青くなります。",
       "道具の列の「<b>蛍光ペン</b>」を押します。黄色いマーカーが付きます。「下線」「取り消し線」も同じです。"])
H2("付箋（メモ）を貼る")
STEPS(["上の段の「<b>付箋</b>」ボタンを押します。",
       "書類の、メモを貼りたい場所をクリックします。",
       "メモの文章を入力して「OK」を押します。黄色い小さなマークが付きます。マウスをマークに乗せると内容が読めます。",
       "終わったら <b>Esc</b> キーを押します。"])
IMGF("07_annots.png", 165, "<font color='#c8102e'>1</font> 蛍光ペン　<font color='#c8102e'>2</font> 付箋（黄色いマーク）　<font color='#c8102e'>3</font> 付箋ボタン")
P("付箋や蛍光ペンを<b>右クリック</b>すると、「編集」「色を変更」「削除」が選べます。蛍光ペンは文字に付くので動かせません。")
story.append(PageBreak())

# ============================================================ 10
H1("10. 保存する・印刷する")
H2("保存する")
P("変更すると、画面の一番上の題名に「<b>*</b>」（星印）が付きます。これは「まだ保存していません」という印です。")
WORDS([("上書き保存", "「<b>保存</b>」ボタン（Ctrl を押しながら S）。元のファイルがそのまま新しい内容になります。"),
       ("別の名前で保存", "「<b>名前を付けて保存</b>」ボタン。元のファイルは残り、新しいファイルが作られます。元を残したいときはこちら。")])
P("保存せずに閉じようとすると、次の画面が出ます。「Save」は保存して終了、「Discard」は保存せずに終了、「Cancel」は閉じるのをやめる、です。")
IMGF("11_confirm.png", 95, "閉じるときの確認")
H2("印刷する")
STEPS(["「<b>印刷</b>」ボタン（Ctrl を押しながら P）を押します。",
       "プリンターを選び、必要なら「現在のページ」や「ページ範囲」を指定して「印刷」を押します。横向きのページは自動で向きを合わせて印刷されます。"])

# ============================================================ 11
H1("11. パスワード付きの PDF")
P("パスワードで守られた PDF を開くと、次の画面が出ます。パスワードを入れて「OK」を押してください。まちがえると、もう一度聞かれます。")
IMGF("09_password.png", 95, "パスワードを聞く画面")
P("保存するときも、同じパスワードで守られたままになります。パスワードを付けたり外したりしたいときは、「ファイル」メニュー →「<b>パスワードを設定・解除</b>」を選びます。空のまま OK を押すと、パスワードが外れます。")
TIP("パスワードはどこにも記録されません。忘れると開けなくなるので、大切に管理してください。", "注意")

# ============================================================ 12
H1("12. 「新しいバージョンがあります」と出たら")
P("アプリは起動のときに、新しいバージョンが出ていないか自動で確認します。あれば次の画面が出ます。")
IMGF("10_update.png", 100, "新しいバージョンの案内。赤枠の「更新する」を押す")
STEPS(["「<b>更新する</b>」を押します。",
       "ダウンロードが進み、自動でインストールされて、アプリが開き直します。何も操作は要りません。",
       "途中で「Windows によって PC が保護されました」が出たら、第 3 章と同じく「詳細情報」→「実行」を押します。"])
TIP("忙しいときは「後で」を押せば、次の起動のときにまた聞かれます。「ヘルプ」メニューの「更新を確認」でいつでも自分から確認できます。")

# ============================================================ 13
H1("13. 困ったときは（よくある質問）")
WORDS([("操作をまちがえた", "「元に戻す」ボタン（Ctrl を押しながら Z）。何回でも戻せます。保存した後でも、閉じるまでは戻せます。"),
       ("マウスに印鑑の影が付いてくる／クリックしても普通に選べない／マウスが十字の形になった", "押印・付箋・書き込みの最中です。<b>Esc</b> キーを押すと普通の状態に戻ります。"),
       ("Delete キーを押したらページが消えた", "「元に戻す」で戻せます。書き込みを消したいときは、先にその書き込みをクリックして青い枠を付けてから Delete を押します。"),
       ("右側の印鑑パネルが消えた", "「印鑑」メニュー →「印鑑パネルを表示」をクリックします。「押印モード」ボタンを押しても自動で開きます。"),
       ("左の目次パネルを閉じたい・出したい", "「しおり・付箋」メニュー →「目次・しおりパネルを表示」（F9 キー）。"),
       ("ボタンの列がどこかへ行った", "ボタンの列の上で右クリックすると「上へ」「左へ」などが選べます。"),
       ("文字が小さくて読めない", "「拡大」ボタンを押すか、Ctrl を押しながらホイールを回します。"),
       ("開いたら「制限が設定されています」と出た", "印刷やコピーが禁止された PDF です。このアプリでは編集できますが、保存すると制限は外れます。そのままで問題なければ気にしなくて構いません。"),
       ("アプリを消したい", "Windows の「設定」→「アプリ」→「インストールされているアプリ」で「PDF Page Editor」の「アンインストール」を押します。作った印鑑のデータは残ります。")])
H2("問い合わせ")
P("不具合や要望は、Yorozu Solutions のホームページ（https://www.yorozusolutions.com/）、または GitHub の「Issues」（https://github.com/hidetako/pdf-page-editor/issues）へお寄せください。")
SP(10)
P(f"PDF Page Editor かんたん説明書　バージョン {VERSION} 対応　{today.strftime('%Y 年 %m 月 %d 日')}　Copyright (c) 2026 Yorozu Solutions", st_small)

# ============================================================ ページ番号
def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("JP", 9)
    canvas.setFillColor(GRAY)
    if doc.page > 1:
        canvas.drawCentredString(W / 2, 11 * mm, f"- {doc.page} -")
        canvas.drawRightString(W - M, H - 11 * mm, "PDF Page Editor かんたん説明書")
    canvas.restoreState()

doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=M, rightMargin=M, topMargin=18 * mm, bottomMargin=18 * mm,
                        title="PDF Page Editor かんたん説明書", author="Yorozu Solutions", subject=f"バージョン {VERSION} 対応")
doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
print("wrote", OUT)
