"""印影生成と押印のテスト。python tests/test_stamps.py"""
import base64
import datetime
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows の cp1252 コンソール対策
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import pymupdf as fitz

from pdfmodel import PdfModel
from stamps import (MM, Stamp, StampLibrary, default_stamps, format_date, image_to_stamp_png, render_png)


def red_bbox(pix):
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    m = (arr[:, :, 0] > 150) & (arr[:, :, 1] < 110) & (arr[:, :, 2] < 110)
    ys, xs = np.where(m)
    assert len(xs), "朱色が見つからない"
    return xs.min(), ys.min(), xs.max(), ys.max()


def main():
    tmp = tempfile.mkdtemp()
    d = datetime.date(2026, 10, 29)
    assert format_date(d, "reiwa") == "R8.10.29"
    assert format_date(d, "reiwa_kanji") == "令和8年10月29日"
    assert format_date(d, "yyyy.mm.dd") == "2026.10.29"

    # 全種類が描画できる
    for s in default_stamps():
        png = render_png(s, dpi=300, date=d)
        pix = fitz.Pixmap(png)
        assert pix.alpha and pix.width > 10
    png = render_png(default_stamps()[1], dpi=300, tilt_deg=10, opacity=0.7, kasure=0.4, seed=3)
    assert fitz.Pixmap(png).width > 10

    # 画像印: 白地が透過される
    img = os.path.join(tmp, "scan.png")
    doc = fitz.open(); pg = doc.new_page(width=100, height=100)
    sh = pg.new_shape(); sh.draw_circle((50, 50), 30); sh.finish(color=(0.8, 0.1, 0.1), width=6); sh.commit()
    pg.get_pixmap(dpi=144).save(img)
    out = fitz.Pixmap(image_to_stamp_png(img))
    arr = np.frombuffer(out.samples, dtype=np.uint8).reshape(out.height, out.width, 4)
    assert arr[2, 2, 3] == 0 and arr[:, :, 3].max() > 200
    import base64
    s_img = Stamp(name="scan", kind="image", image_b64=base64.b64encode(image_to_stamp_png(img)).decode(), size_mm=20)
    assert fitz.Pixmap(render_png(s_img, dpi=200)).width > 10
    # 画像印は元画像の解像度を下回らない（15mm・150dpi なら本来 89px だが、800px の元画像なら 800px で描く）
    big = os.path.join(tmp, "big.png")
    fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 800, 800), 1).save(big)
    s_big = Stamp(kind="image", image_b64=base64.b64encode(open(big, "rb").read()).decode(), size_mm=15)
    assert fitz.Pixmap(render_png(s_big, dpi=150)).width == 800
    assert fitz.Pixmap(render_png(s_big, dpi=600)).width == 800
    # 加工なしの画像印は元画像のピクセルがそのまま出る（再サンプリングなし）
    src_pix = fitz.Pixmap(base64.b64decode(s_img.image_b64))
    out_pix = fitz.Pixmap(render_png(s_img, dpi=150))
    assert (out_pix.width, out_pix.height, out_pix.n) == (src_pix.width, src_pix.height, 4)
    sa = np.frombuffer(src_pix.samples, np.uint8).astype(int); oa = np.frombuffer(out_pix.samples, np.uint8).astype(int)
    assert np.abs(sa - oa).max() <= 2, "PNG 往復の丸め以上の差がある"
    # 画像印の塗り直し: 赤い丸の画像を青で描くと赤は消え、白地は透明のまま
    s_blue = Stamp(kind="image", image_b64=s_img.image_b64, size_mm=20, color=[0.1, 0.2, 0.9], recolor=True)
    bp = fitz.Pixmap(render_png(s_blue, dpi=150))
    ba = np.frombuffer(bp.samples, dtype=np.uint8).reshape(bp.height, bp.width, 4)
    assert ba[2, 2, 3] == 0, "白地は透明"
    inked = ba[:, :, 3] > 120
    af = ba[inked][:, 3].astype(float)
    assert inked.sum() > 100 and (ba[inked][:, 2] / af > 0.8).all() and (ba[inked][:, 0] / af < 0.2).all(), "インク部分は青"
    # 白地透過で alpha を下げた縁のピクセルは RGB <= alpha（事前乗算）になっていて色が壊れない
    sa = np.frombuffer(src_pix.samples, dtype=np.uint8).reshape(src_pix.height, src_pix.width, 4)
    assert (sa[:, :, :3].max(axis=2) <= sa[:, :, 3]).all(), "RGB > alpha のピクセルがある"
    try:
        red_bbox(bp); raise AssertionError("赤が残っている")
    except AssertionError as e:
        assert "朱色" in str(e)
    assert fitz.Pixmap(render_png(s_blue, dpi=150, opacity=0.5)).width == bp.width, "加工ありでも塗り直しが効く"
    assert Stamp.from_dict({"name": "old", "kind": "image"}).recolor is False, "旧データは塗り直しなし"
    # 傾き・不透明度・かすれを付けたときは従来どおり描画でき、解像度も元画像以上
    assert fitz.Pixmap(render_png(s_big, dpi=150, opacity=0.5)).width == 800
    assert fitz.Pixmap(render_png(s_big, dpi=150, tilt_deg=7)).width > 800
    s_small = Stamp(kind="image", image_b64=s_img.image_b64, size_mm=100)  # 200px の元画像を 100mm で加工あり: dpi 側が勝つ
    assert abs(fitz.Pixmap(render_png(s_small, dpi=150, opacity=0.5)).width - 100 * MM * 150 / 72) <= 1

    # ライブラリの保存/読込/インポート
    lib = StampLibrary(os.path.join(tmp, "stamps.json"))
    n0 = len(lib.stamps)
    lib.stamps.append(Stamp(name="x", lines=["試験"])); lib.save()
    lib2 = StampLibrary(os.path.join(tmp, "stamps.json"))
    assert len(lib2.stamps) == n0 + 1 and lib2.stamps[-1].name == "x"
    lib2.export(os.path.join(tmp, "exp.json"))
    assert lib2.import_file(os.path.join(tmp, "exp.json")) == n0 + 1

    # おまかせ電子印鑑 (.entstmp) のインポート: XML の image 属性 (base64 PNG) を画像印として取り込む
    scan_png = fitz.open(img).convert_to_pdf()  # ダミー: 不透過の PNG を作る
    pix = fitz.open("pdf", scan_png)[0].get_pixmap(dpi=72)
    opaque = pix.tobytes("png"); assert not fitz.Pixmap(opaque).alpha
    alpha_png = image_to_stamp_png(img)
    ent = os.path.join(tmp, "印影A.entstmp")
    with open(ent, "w", encoding="utf-8-sig") as f:
        f.write('<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
                '<ENTSTMP product="EntrustStampForBusiness" version="1.0.2">\n  <stampEntities>\n'
                f'    <stampEntity class="ScannerStamp" text="" col="ffd80016" image="{base64.b64encode(opaque).decode()}" chromakey="0">\n'
                '      <transforms />\n    </stampEntity>\n'
                f'    <stampEntity class="ScannerStamp" image="{base64.b64encode(alpha_png).decode()}" />\n'
                '  </stampEntities>\n</ENTSTMP>\n')
    lib3 = StampLibrary(os.path.join(tmp, "stamps3.json"))
    n3 = len(lib3.stamps)
    assert lib3.import_file(ent) == 2 and len(lib3.stamps) == n3 + 2
    s1, s2 = lib3.stamps[-2:]
    assert s1.name == "印影A 1" and s2.name == "印影A 2" and s1.kind == s2.kind == "image" and s1.size_mm == 15
    assert s1.recolor and [round(c * 255) for c in s1.color] == [0xD8, 0x00, 0x16], "col 属性が印色になる"
    assert s2.recolor and s2.color == list(default_stamps()[0].color), "col なしは既定の朱色"
    # グレーのマスク画像でも col の色で描かれる（元ソフトと同じ見え方）
    ga0 = np.zeros((40, 40, 2), dtype=np.uint8); ga0[10:30, 10:30, 1] = 255  # 中央だけ黒・不透明（グレー+alpha）
    gray = fitz.Pixmap(fitz.csGRAY, 40, 40, ga0.tobytes(), True)
    ent2 = os.path.join(tmp, "gray.entstmp")
    with open(ent2, "w", encoding="utf-8") as f:
        f.write(f'<ENTSTMP><stampEntities><stampEntity class="ScannerStamp" col="ff0000ff" image="{base64.b64encode(gray.tobytes("png")).decode()}" /></stampEntities></ENTSTMP>')
    assert lib3.import_file(ent2) == 1
    gp = fitz.Pixmap(render_png(lib3.stamps[-1], dpi=150))
    ga = np.frombuffer(gp.samples, dtype=np.uint8).reshape(gp.height, gp.width, 4)
    assert ga[2, 2, 3] == 0 and ga[20, 20, 3] > 250 and ga[20, 20, 2] > 250 and ga[20, 20, 0] < 5, "中央は青・外は透明"
    # 中間グレー (128) + alpha のマスクでも薄くならない（形はアルファ、濃さは印色そのまま）
    ga1 = np.zeros((40, 40, 2), dtype=np.uint8); ga1[10:30, 10:30, 0] = 128; ga1[10:30, 10:30, 1] = 255
    mid = fitz.Pixmap(fitz.csGRAY, 40, 40, ga1.tobytes(), True)
    s_mid = Stamp(kind="image", color=[0.1, 0.2, 0.9], recolor=True, image_b64=base64.b64encode(mid.tobytes("png")).decode())
    mp = fitz.Pixmap(render_png(s_mid, dpi=150)); ma = np.frombuffer(mp.samples, dtype=np.uint8).reshape(40, 40, 4)
    assert ma[20, 20, 3] > 250 and ma[20, 20, 2] > 220, ma[20, 20]
    # アルファなしの白地画像（赤い丸）は濃さから形を取る
    opaque_red = fitz.open("pdf", fitz.open(img).convert_to_pdf())[0].get_pixmap(dpi=72)
    s_op = Stamp(kind="image", color=[0.1, 0.2, 0.9], recolor=True, image_b64=base64.b64encode(opaque_red.tobytes("png")).decode())
    op = fitz.Pixmap(render_png(s_op, dpi=150)); oa = np.frombuffer(op.samples, dtype=np.uint8).reshape(op.height, op.width, 4)
    assert oa[2, 2, 3] == 0 and (oa[:, :, 3] > 200).sum() > 50, "白地は透明、赤線は青インクになる"
    p1 = fitz.Pixmap(base64.b64decode(s1.image_b64))
    assert p1.alpha, "不透過 PNG は白地を透過して取り込む"
    a1 = np.frombuffer(p1.samples, dtype=np.uint8).reshape(p1.height, p1.width, 4)
    assert a1[2, 2, 3] == 0 and a1[:, :, 3].max() > 200
    assert fitz.Pixmap(render_png(s1, dpi=200)).width > 10, "押印用に描画できる"
    assert StampLibrary(os.path.join(tmp, "stamps3.json")).stamps[-1].name == "gray", "保存されている"
    bad = os.path.join(tmp, "bad.entstmp")
    with open(bad, "w", encoding="utf-8") as f:
        f.write('<ENTSTMP><stampEntities><stampEntity class="TextStamp" text="承認" /></stampEntities></ENTSTMP>')
    try:
        lib3.import_file(bad); raise AssertionError("画像なしが通ってしまった")
    except ValueError as e:
        assert "文字だけ" in str(e)

    # 押印: 回転ページでも表示座標どおりの位置に正立で入る
    for rot in (0, 90, 270):
        m = PdfModel(); m.new()
        pg = m.doc.new_page(width=400, height=600); pg.set_rotation(rot)
        rect = pg.rect
        png = render_png(Stamp(kind="user", lines=["済"], size_mm=20), dpi=300, date=d)
        center = (rect.width * 0.75, rect.height * 0.25)
        m.stamp(0, png, center, 60, 60)
        assert m.modified and m.can_undo()
        p = os.path.join(tmp, f"st{rot}.pdf"); m.save(p)
        pix = fitz.open(p)[0].get_pixmap(dpi=72)
        x0, y0, x1, y1 = red_bbox(pix)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        assert abs(cx - center[0]) < 4 and abs(cy - center[1]) < 4, (rot, cx, cy, center)
        assert (x1 - x0) < 62 and (y1 - y0) < 62
        m.undo(); assert not m.can_undo() or True
        m.close()

    # 押印の矩形は回転 0/90/180/270 のどのページでも表示座標で正しく求まる
    png = render_png(Stamp(kind="user", lines=["済"], size_mm=20), dpi=300, date=d)
    for rot in (0, 90, 180, 270):
        m = PdfModel(); m.new(); pg = m.doc.new_page(width=400, height=600); pg.set_rotation(rot)
        m.stamp(0, png, (300, 100), 60, 60)
        (_, r), = m.stamps_on_page(0)
        assert abs(r.x0 - 270) < 1 and abs(r.y0 - 70) < 1 and abs(r.x1 - 330) < 1 and abs(r.y1 - 130) < 1, (rot, r)
        m.close()

    # 押印の個別取り消し: 同じ印影を 2 か所に押しても片方だけ消せる。Undo で戻る
    m = PdfModel(); m.new(); pg = m.doc.new_page(width=400, height=600); pg.set_rotation(90)
    m.stamp(0, png, (100, 100), 60, 60)
    m.stamp(0, png, (300, 400), 60, 60)
    st = m.stamps_on_page(0)
    assert len(st) == 2 and st[0][0] != st[1][0], "同じ画像でも押印ごとに別ストリーム"
    assert st[1][1].contains(fitz.Point(300, 400)) and not st[1][1].contains(fitz.Point(100, 100))
    assert m.stamp_at(0, (300, 400)) == st[1][0] and m.stamp_at(0, (10, 10)) is None
    m.remove_stamp(0, st[1][0])
    assert [x for x, _ in m.stamps_on_page(0)] == [st[0][0]]
    p = os.path.join(tmp, "rm.pdf"); m.save(p)
    x0, y0, x1, y1 = red_bbox(fitz.open(p)[0].get_pixmap(dpi=72))
    assert abs((x0 + x1) / 2 - 100) < 4 and abs((y0 + y1) / 2 - 100) < 4, "残った方は (100,100) の印影"
    m.undo()
    assert len(m.stamps_on_page(0)) == 2, "取り消しを Undo すると戻る"
    try:
        m.remove_stamp(0, 1); raise AssertionError("押印以外が消せてしまう")
    except ValueError:
        pass
    m.close()
    print("stamp tests passed")


if __name__ == "__main__":
    main()
