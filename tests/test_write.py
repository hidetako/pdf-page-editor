"""書き込み機能（手書き・直線・矢印・四角・丸・埋め込みフォントの文字）のテスト。python tests/test_write.py

Qt は使わない。フォントは、内蔵フォントを「メイリオ」の名前で置いた偽のフォルダーで試す。
Windows では、実際に入っているフォント（メイリオなど）でも同じことを試す。
"""
import os
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows の cp1252 コンソール対策
import tempfile

_TMP = tempfile.mkdtemp(prefix="pdfpe_write_")
_FAKE = os.path.join(_TMP, "fakefonts")
os.makedirs(_FAKE)
_REAL_DIRS_ENV = os.environ.get("PDFPE_FONT_DIRS", "")
os.environ["PDFPE_FONT_DIRS"] = _FAKE
os.environ["PDFPE_FONT_DIRS_ONLY"] = "1"      # 実機のフォントに左右されないようにする（Windows の実フォントは最後に別に試す）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pymupdf as fitz

import fonts
from pdfmodel import PdfModel

SAMPLE = "確認済み 2026/11/13\nメイリオ ABC"


def make_pdf(path, pages=1):
    d = fitz.open()
    for i in range(pages):
        p = d.new_page(width=595, height=842)
        p.insert_text((72, 100), f"Hello World {i + 1}", fontsize=24)
    d.save(path)
    d.close()


def close(a, b, tol=0.6):
    return abs(a - b) < tol


def dark_pixels(model, index, rect):
    pix = model.render(index, 1.0)
    n = 0
    for y in range(max(0, int(rect.y0)), min(pix.height, int(rect.y1) + 1)):
        for x in range(max(0, int(rect.x0)), min(pix.width, int(rect.x1) + 1)):
            r, g, b = pix.pixel(x, y)
            if r + g + b < 600:
                n += 1
    return n


def test_fonts():
    # 偽の「メイリオ」（中身は内蔵の TrueType フォント）と、フォントではない壊れたファイル
    with open(os.path.join(_FAKE, "meiryo.ttc"), "wb") as f:
        f.write(fitz.Font("japan").buffer)
    with open(os.path.join(_FAKE, "msgothic.ttc"), "wb") as f:
        f.write(b"this is not a font file" * 1000)
    av = fonts.available_fonts(refresh=True)
    assert av[0] == (fonts.BUILTIN, "標準ゴシック"), av
    assert ("meiryo", "メイリオ") in av, av
    assert all(k != "msgothic" for k, _ in av), "埋め込めないフォントは一覧に出さない"
    assert fonts.resolve("yugothic") == fonts.BUILTIN and fonts.resolve(None) == fonts.BUILTIN and fonts.resolve("meiryo") == "meiryo"

    w1, h1 = fonts.measure("あいう", "meiryo", 12)
    w2, h2 = fonts.measure("あいうえおかきくけこ", "meiryo", 12)
    w3, h3 = fonts.measure("あいうえおかきくけこ", "meiryo", 12, wrap_width=60)
    assert w2 > w1 and close(h1, h2) and w3 == 60 and h3 > h2 * 1.8, (w1, h1, w2, h2, w3, h3)
    _w, h4 = fonts.measure("1 行目\n\n3 行目", "builtin", 12)
    assert h4 > h1 * 2.2, "空行も 1 行ぶんの高さを取る"

    for key in ("builtin", "meiryo"):
        src, w, h = fonts.render_text_pdf(SAMPLE, key, 14, (0.8, 0, 0), fill=(1, 1, 0.8), border=(0.8, 0, 0))
        data = src.tobytes(garbage=3, deflate=True)
        text = src[0].get_text()
        assert len(data) < 100_000, f"{key}: フォントは使った文字だけに絞って埋め込む（{len(data)} バイト）"
        assert "確認済み" in text and "メイリオ ABC" in text, text
        assert close(src[0].rect.width, w) and close(src[0].rect.height, h)
        src.close()
    # 回転ページ用: 90° では縦横が入れ替わった PDF になる
    src, w, h = fonts.render_text_pdf(SAMPLE, "builtin", 14, (0, 0, 0), rotation=90)
    assert close(src[0].rect.width, h) and close(src[0].rect.height, w)
    src.close()
    # HTML の記号や連続した空白をそのまま書ける
    src, _w, _h = fonts.render_text_pdf("a < b & c > d   x", "builtin", 12, (0, 0, 0))
    assert "a < b & c > d" in src[0].get_text()
    src.close()
    # 確認結果はファイルに覚えておき、フォントファイルが変わらない限り確かめ直さない
    cache = os.path.join(_TMP, "cache", "fonts.json")
    fonts.set_cache_file(cache)
    calls = []
    real_check = fonts._check
    fonts._check = lambda key: calls.append(key) or real_check(key)
    try:
        assert fonts.available_fonts(refresh=True) == av and sorted(calls) == ["meiryo", "msgothic"] and os.path.isfile(cache)
        calls.clear()
        assert fonts.available_fonts(refresh=True) == av and calls == [], "2 回目は確かめ直さない"
        with open(os.path.join(_FAKE, "msgothic.ttc"), "wb") as f:      # 壊れていたファイルが、正しいフォントに入れ替わった
            f.write(fitz.Font("japan").buffer)
        assert ("msgothic", "MS ゴシック") in fonts.available_fonts(refresh=True) and calls == ["msgothic"], "変わったファイルだけ確かめ直す"
        with open(cache, "w", encoding="utf-8") as f:
            f.write("broken json")
        calls.clear()
        assert len(fonts.available_fonts(refresh=True)) == 3 and len(calls) == 2, "保存ファイルが壊れていても動く"
    finally:
        fonts._check = real_check
        fonts.set_cache_file(None)
        with open(os.path.join(_FAKE, "msgothic.ttc"), "wb") as f:
            f.write(b"this is not a font file" * 1000)
        fonts.available_fonts(refresh=True)
    print("  fonts: registry / measure / render / cache OK")


def test_model(pdf):
    for rot in (0, 90, 180, 270):
        m = PdfModel(); m.open(pdf); m.doc[0].set_rotation(rot)
        x = m.add_text_box(0, (100, 200), SAMPLE, font="meiryo", fontsize=14, fill=(1, 1, 0.8), border=True, title="山田")
        a = m.annot_info(0, x)
        assert a["kind"] == "文字" and a["font"] == "meiryo" and a["fontsize"] == 14 and a["border"] and a["fill"] == (1.0, 1.0, 0.8), a
        assert close(a["rect"].x0, 100, 0.01) and close(a["rect"].y0, 200, 0.01) and a["title"] == "山田" and a["text"] == SAMPLE, a
        assert dark_pixels(m, 0, a["rect"]) > 100, (rot, "書いた文字が見える")
        assert m.page_count == 1, "作業用のページは残さない"
        ink = m.add_ink(0, [[(300, 100), (330, 140), (380, 120)]], (0, 0, 1), 3, 0.5)
        ln = m.add_line(0, (300, 200), (400, 260), (1, 0, 0), 2, 1.0, arrow=True)
        sq = m.add_shape(0, "rect", (300, 300, 400, 360), (0, 0.5, 0), 2, 1.0, fill=(1, 1, 0.6))
        el = m.add_shape(0, "ellipse", (300, 400, 400, 460))
        assert [z["kind"] for z in m.annots_on_page(0)] == ["文字", "手書き", "線", "四角", "円"]
        a = m.annot_info(0, ink); assert a["opacity"] == 0.5 and a["width"] == 3 and a["resizable"] and a["movable"], a
        a = m.annot_info(0, ln); assert a["arrow"] and a["stroke"] == (1.0, 0.0, 0.0), a
        a = m.annot_info(0, sq); assert a["fill"] is not None and close(a["rect"].width, 102) and close(a["rect"].height, 62), a
        ids = {"ink": ink, "line": ln, "rect": sq, "ellipse": el, "text": x}
        for name in list(ids):
            b = m.annot_info(0, ids[name])["rect"]
            # 2 回動かしても大きさは変わらない（四角・丸は線の太さぶん外枠が広いので、ずれやすい）
            xr = m.move_annot(0, m.move_annot(0, ids[name], 20, 10), -5, 5)
            c = m.annot_info(0, xr)["rect"]
            assert close(c.width, b.width) and close(c.height, b.height) and close(c.x0, b.x0 + 15) and close(c.y0, b.y0 + 15), (rot, name, b, c)
            tgt = fitz.Rect(c.x0, c.y0, c.x0 + c.width * 1.5, c.y0 + c.height * 2)
            xr = m.resize_annot(0, xr, tgt)
            e = m.annot_info(0, xr)["rect"]
            if name == "text":      # 文字は幅だけ変わり、高さは文字の量で決まる
                assert close(e.width, tgt.width) and close(e.x0, tgt.x0) and close(e.y0, tgt.y0), (rot, name, tgt, e)
            else:
                assert close(e.width, tgt.width, 1.5) and close(e.height, tgt.height, 1.5) and close(e.x0, tgt.x0, 1) and close(e.y0, tgt.y0, 1), (rot, name, tgt, e)
            ids[name] = xr
        assert m.annot_info(0, ids["line"])["arrow"], "動かしても矢じりは残る"
        assert m.annot_info(0, ids["ink"])["opacity"] == 0.5 and m.annot_info(0, ids["ink"])["width"] == 3
        try:
            m.resize_annot(0, m.add_note(0, (450, 500), "note"), (450, 500, 500, 550)); raise AssertionError("付箋は大きさを変えられない")
        except ValueError:
            pass
        # 見た目の変更
        x = m.set_annot_style(0, ids["text"], color=(0, 0, 1), font="builtin", fontsize=18, fill=None, border=False)
        a = m.annot_info(0, x)
        assert a["stroke"] == (0.0, 0.0, 1.0) and a["font"] == "builtin" and a["fontsize"] == 18 and a["fill"] is None and not a["border"], a
        x = m.set_annot_text(0, x, "変更後の文字\n2 行目")
        assert m.annot_info(0, x)["text"] == "変更後の文字\n2 行目" and m.annot_info(0, x)["title"] == "山田"
        x = m.set_annot_style(0, x, font="meiryo")
        i2 = m.set_annot_style(0, ids["ink"], color=(0, 0, 1), width=6, opacity=1.0)
        a = m.annot_info(0, i2); assert i2 == ids["ink"] and a["stroke"] == (0.0, 0.0, 1.0) and a["width"] == 6 and a["opacity"] == 1.0, a
        m.set_annot_style(0, ids["rect"], fill=None); assert m.annot_info(0, ids["rect"])["fill"] is None
        m.set_annot_style(0, ids["rect"], fill=(1, 0, 0)); assert m.annot_info(0, ids["rect"])["fill"] == (1.0, 0.0, 0.0)
        m.set_annot_style(0, ids["line"], color=(0, 0, 1)); assert m.annot_info(0, ids["line"])["stroke"] == (0.0, 0.0, 1.0) and m.annot_info(0, ids["line"])["arrow"]
        # 保存して開き直しても、文字の設定（フォントなど）を覚えている
        out = os.path.join(_TMP, f"rot{rot}.pdf"); m.save(out)
        assert os.path.getsize(out) < 150_000, os.path.getsize(out)
        m2 = PdfModel(); m2.open(out)
        a = [z for z in m2.annots_on_page(0) if z["kind"] == "文字"][0]
        assert a["font"] == "meiryo" and a["text"].startswith("変更後") and a["fontsize"] == 18
        assert [z["kind"] for z in m2.annots_on_page(0)].count("手書き") == 1 and m2.page_count == 1
        m2.close()
        # Undo / やり直し: 文字の作り直し（注釈の番号が変わる操作）も 1 回で元に戻る
        def state():
            return [(z["kind"], z["text"], z["font"], [round(v, 2) for v in z["rect"]]) for z in m.annots_on_page(0)]
        s0 = state()
        x = m.set_annot_text(0, x, "Undo の確認"); x = m.move_annot(0, x, 7, 7); m.remove_annot(0, ids["ink"])
        assert state() != s0
        m.undo(); m.undo(); m.undo()
        assert state() == s0 and m.page_count == 1, (rot, "Undo で元どおり")
        m.redo(); m.redo(); m.redo()
        assert [z["text"] for z in m.annots_on_page(0) if z["kind"] == "文字"] == ["Undo の確認"] and "手書き" not in [z["kind"] for z in m.annots_on_page(0)]
        m.close()
    print("  model: ink / line / shapes / text box on 0-90-180-270° OK")


def test_text_box_details(pdf):
    m = PdfModel(); m.open(pdf)
    # ページの右端に近い場所に長い文を書くと、ページの中で折り返す
    x = m.add_text_box(0, (450, 300), "これはページの右端で折り返される長い文章です。", fontsize=12)
    r = m.annot_info(0, x)["rect"]
    assert r.x1 <= 595.01 and r.height > 12 * 2.5, r
    # 幅を狭めると行が増えて高くなる。文字を直しても、決めた幅は保つ
    x2 = m.resize_annot(0, x, (r.x0, r.y0, r.x0 + 80, r.y1))
    r2 = m.annot_info(0, x2)["rect"]
    assert close(r2.width, 80) and r2.height > r.height, (r, r2)
    x3 = m.set_annot_text(0, x2, "短い")
    assert close(m.annot_info(0, x3)["rect"].width, 80)
    # 幅を決めていない文字は、直すと文字に合わせた幅になる
    y = m.add_text_box(0, (100, 500), "短い", fontsize=12)
    w0 = m.annot_info(0, y)["rect"].width
    y = m.set_annot_text(0, y, "ずっと長い文字に書き換える")
    assert m.annot_info(0, y)["rect"].width > w0 * 2
    # ページの下端では、はみ出さないよう上にずらす
    z = m.add_text_box(0, (100, 838), "下端\n2 行目\n3 行目", fontsize=12)
    assert m.annot_info(0, z)["rect"].y1 <= 842.01
    # 他の PC で作った（この PC に無い）フォントの文字は、標準ゴシックで作り直す
    w = m.add_text_box(0, (100, 600), "別の PC", font="meiryo", fontsize=12)
    spec = '{"v":1,"font":"no-such-font","size":12.0,"color":[0,0,0],"fill":null,"border":false,"bw":1.0,"wrap":false,"rot":0}'
    m.doc.xref_set_key(w, "PPE", fitz.get_pdf_str(spec))
    assert m.annot_info(0, w)["font"] == "no-such-font"
    w = m.set_annot_text(0, w, "直した")
    assert m.annot_info(0, w)["font"] == fonts.BUILTIN

    # 書き込んだ文字の見た目は、同じページで他の操作をしても変わらない（標準フォントで作り直されない）
    t = m.add_text_box(0, (100, 700), SAMPLE, font="meiryo", fontsize=14)
    rect = m.annot_info(0, t)["rect"]
    before = m.render(0, 2.0).samples

    def crop_equal():
        return m.render(0, 2.0).samples == before

    n = m.add_note(0, (20, 20), "メモ"); m.update_note(0, n, "直したメモ"); m.remove_note(0, n)
    i = m.add_ink(0, [[(20, 30), (40, 50)]]); i = m.move_annot(0, i, 5, 5); m.remove_annot(0, i)
    s = m.add_shape(0, "rect", (20, 60, 40, 80)); m.set_annot_style(0, s, color=(0, 0, 1)); m.remove_annot(0, s)
    assert crop_equal(), "他の注釈の操作で、書き込んだ文字の見た目が変わってはいけない"
    m.add_bookmark(0, "しおり")
    out = os.path.join(_TMP, "stable.pdf"); m.save(out)
    assert crop_equal()
    m2 = PdfModel(); m2.open(out)
    assert m2.render(0, 2.0).samples == before, "保存して開き直しても同じ見た目"
    ap = m2.doc.xref_get_key([a["xref"] for a in m2.annots_on_page(0) if a["text"] == SAMPLE][0], "AP")
    assert ap[0] == "dict" and "/N" in ap[1], ap
    assert dark_pixels(m2, 0, rect) > 100
    m2.close()
    # 消すと、埋め込んだフォントも保存時に取り除かれる
    size_with = os.path.getsize(out)
    for a in m.annots_on_page(0):
        m.remove_annot(0, a["xref"])
    out2 = os.path.join(_TMP, "removed.pdf"); m.save(out2)
    assert os.path.getsize(out2) < size_with - 3000, (os.path.getsize(out2), size_with)
    m.close()
    print("  model: wrapping / widths / unknown font / appearance stability OK")


def test_save_keeps_ids(pdf):
    """保存しても、開いている文書の中の番号は変わらない（保存後も同じ書き込み・押印を指せる）。"""
    m = PdfModel(); m.open(pdf)
    junk = m.add_note(0, (10, 10), "消す"); m.remove_note(0, junk)       # 使われない番号を作っておく
    png = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), 1)
    png.clear_with(200)
    m.stamp(0, png.tobytes("png"), (300, 300), 40, 40)
    ink = m.add_ink(0, [[(100, 400), (150, 450)]])
    txt = m.add_text_box(0, (100, 500), SAMPLE, font="meiryo")
    stamps = m.stamps_on_page(0)
    before = [(a["xref"], a["kind"]) for a in m.annots_on_page(0)]
    out = os.path.join(_TMP, "ids.pdf"); m.save(out)
    assert [(a["xref"], a["kind"]) for a in m.annots_on_page(0)] == before, "保存で注釈の番号が変わってはいけない"
    assert m.stamps_on_page(0) == stamps and m.stamp_at(0, (300, 300)) == stamps[0][0]
    assert m.annot_info(0, ink)["kind"] == "手書き" and m.annot_info(0, txt)["kind"] == "文字"
    m.remove_stamp(0, stamps[0][0]); ink = m.move_annot(0, ink, 5, 5); m.set_annot_text(0, txt, "保存後も直せる")
    m.save()
    d = fitz.open(out); pg = d[0]
    assert sorted(a.type[1] for a in pg.annots()) == ["FreeText", "Ink"] and "Hello World" in pg.get_text()
    assert len(pg.get_contents()) == 1, "押印の取り消しは、保存後も正しい内容を消す"
    del pg; d.close()
    # パスワード付きでも同じ
    m.set_password("pw"); m.save()
    assert [(a["kind"]) for a in m.annots_on_page(0)] == ["手書き", "文字"] and m.annot_info(0, ink) is not None
    d = fitz.open(out); assert d.needs_pass and d.authenticate("pw"); d.close()
    m.close()
    print("  model: ids stay valid across save OK")


def test_undo_memory(pdf):
    """元に戻す用の控えは、合計の大きさでも制限する（大きな PDF に手書きを重ねてもメモリを使い切らない）。"""
    import pdfmodel
    keep = pdfmodel.MAX_UNDO_BYTES
    m = PdfModel(); m.open(pdf)
    try:
        size = len(m._snapshot())
        pdfmodel.MAX_UNDO_BYTES = size * 5
        for i in range(12):
            m.add_ink(0, [[(100, 100 + i * 10), (200, 110 + i * 10)]])
        assert pdfmodel.MIN_UNDO <= len(m._undo) <= 6, len(m._undo)
        n = len(m._undo)
        for _ in range(20):
            m.undo()
        assert len(m.annots_on_page(0)) == 12 - n and not m.can_undo(), "残っている回数ぶんは戻せる"
        pdfmodel.MAX_UNDO_BYTES = 1                      # どんなに小さくしても最低回数は残す
        for i in range(6):
            m.add_ink(0, [[(300, 100 + i * 10), (400, 110 + i * 10)]])
        assert len(m._undo) == pdfmodel.MIN_UNDO
    finally:
        pdfmodel.MAX_UNDO_BYTES = keep
        m.close()
    print("  model: undo memory limit OK")


def test_foreign_annots(pdf):
    """他のソフトで付けた（このアプリの設定を持たない）注釈も、動かす・大きさを変える・色を変えることができる。"""
    src = os.path.join(_TMP, "foreign.pdf")
    d = fitz.open(pdf); p = d[0]
    a = p.add_ink_annot([[(100, 200), (150, 240), (200, 210)], [(120, 260), (180, 270)]]); a.set_border(width=2); a.update()
    a = p.add_line_annot((300, 200), (400, 250)); a.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW); a.update()
    a = p.add_rect_annot(fitz.Rect(100, 400, 200, 460)); a.set_border(width=6); a.update()
    a = p.add_freetext_annot(fitz.Rect(300, 400, 420, 440), "old style", fontsize=12, fontname="helv", text_color=(1, 0, 0)); a.update()
    d.save(src); d.close()
    m = PdfModel(); m.open(src)
    ann = {z["kind"]: z for z in m.annots_on_page(0)}
    assert set(ann) == {"手書き", "線", "四角", "文字"} and ann["文字"]["font"] is None, ann.keys()
    # 手書き（2 画）: 画数を保ったまま移動・拡大
    b = ann["手書き"]["rect"]
    x = m.move_annot(0, ann["手書き"]["xref"], 30, 40)
    c = m.annot_info(0, x)["rect"]
    assert close(c.x0, b.x0 + 30) and close(c.y0, b.y0 + 40) and close(c.width, b.width) and close(c.height, b.height), (b, c)
    x = m.resize_annot(0, x, (c.x0, c.y0, c.x0 + c.width * 2, c.y0 + c.height * 2))
    page = m.doc[0]
    strokes = [z for z in page.annots() if z.xref == x][0].vertices
    assert len(strokes) == 2 and len(strokes[0]) == 3 and len(strokes[1]) == 2, strokes
    del page
    # 直線: 矢じりの種類（開いた矢）を保つ
    x = m.move_annot(0, ann["線"]["xref"], 10, 10)
    page = m.doc[0]
    assert [z for z in page.annots() if z.xref == x][0].line_ends == (fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
    del page
    # 太い線の四角: 何度動かしても大きくならない
    b = ann["四角"]["rect"]; x = ann["四角"]["xref"]
    for _ in range(4):
        x = m.move_annot(0, x, 5, 5)
    c = m.annot_info(0, x)["rect"]
    assert close(c.width, b.width, 0.1) and close(c.height, b.height, 0.1) and close(c.x0, b.x0 + 20, 0.1), (b, c)
    # 以前の版・他のソフトの文字注釈: 内容・色・大きさ・位置を変えられる（フォントは選べない）
    x = ann["文字"]["xref"]
    assert m.set_annot_text(0, x, "直した") == x and m.annot_info(0, x)["text"] == "直した"
    m.set_annot_color(0, x, (0, 0, 1)); m.set_annot_fontsize(0, x, 18)
    a = m.annot_info(0, x); assert a["stroke"] == (0.0, 0.0, 1.0) and a["fontsize"] == 18.0 and a["font"] is None, a
    m.move_annot(0, x, -20, 30)
    assert close(m.annot_info(0, x)["rect"].x0, 280)
    m.close()
    print("  model: annotations made by other software OK")


def test_real_windows_fonts(pdf):
    """Windows に実際に入っているフォントで、埋め込みと書き込みを試す（Windows 以外では何もしない）。"""
    os.environ["PDFPE_FONT_DIRS"] = _REAL_DIRS_ENV
    os.environ["PDFPE_FONT_DIRS_ONLY"] = "0"
    try:
        dirs = fonts.font_dirs()
        if os.name != "nt" or not dirs:
            print("  real Windows fonts: skipped (not Windows)")
            return
        av = fonts.available_fonts(refresh=True)
        keys = [k for k, _ in av]
        present = [(k, label, [n for n in names if any(os.path.isfile(os.path.join(d, n)) for d in dirs)])
                   for k, label, names in fonts._CANDIDATES]
        for k, label, found in present:
            state = "使える" if k in keys else ("ファイルはあるが埋め込めない" if found else "この PC に無い")
            print(f"    {label}: {state} {found}")
        for k, label, found in present:
            if found and k.startswith("meiryo"):
                assert k in keys, f"{label} のファイルがあるのに使えない"
        m = PdfModel(); m.open(pdf)
        y = 150
        for k in keys:
            x = m.add_text_box(0, (72, y), f"{fonts.LABELS[k]}: {SAMPLE}", font=k, fontsize=14)
            assert dark_pixels(m, 0, m.annot_info(0, x)["rect"]) > 100, k
            y += 60
        out = os.path.join(_TMP, "real_fonts.pdf"); m.save(out)
        size = os.path.getsize(out)
        print(f"    {len(keys)} フォントで書き込んだ PDF: {size} バイト")
        assert size < 120_000 * max(1, len(keys)), size
        m.close()
        print("  real Windows fonts OK")
    finally:
        os.environ["PDFPE_FONT_DIRS"] = _FAKE
        os.environ["PDFPE_FONT_DIRS_ONLY"] = "1"
        fonts.available_fonts(refresh=True)


def main():
    pdf = os.path.join(_TMP, "base.pdf")
    make_pdf(pdf)
    test_fonts()
    test_model(pdf)
    test_text_box_details(pdf)
    test_save_keeps_ids(pdf)
    test_undo_memory(pdf)
    test_foreign_annots(pdf)
    test_real_windows_fonts(pdf)
    # 配布用 exe の自己診断（CI で exe に対して実行するもの）が、ソースからでも通る
    import selftest
    log = os.path.join(_TMP, "selftest.log")
    assert selftest.run(log) == 0, open(log, encoding="utf-8").read()
    assert open(log, encoding="utf-8").read().strip().endswith("OK")
    print("  selftest OK")
    shutil.rmtree(_TMP, ignore_errors=True)
    print("write tests passed")


if __name__ == "__main__":
    main()
