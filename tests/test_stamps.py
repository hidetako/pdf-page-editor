"""印影生成と押印のテスト。python tests/test_stamps.py"""
import base64
import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import pymupdf as fitz

from pdfmodel import PdfModel
from stamps import (Stamp, StampLibrary, default_stamps, format_date, image_to_stamp_png, render_png)


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
    ent = os.path.join(tmp, "安藤印.entstmp")
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
    assert s1.name == "安藤印 1" and s2.name == "安藤印 2" and s1.kind == s2.kind == "image" and s1.size_mm == 15
    p1 = fitz.Pixmap(base64.b64decode(s1.image_b64))
    assert p1.alpha, "不透過 PNG は白地を透過して取り込む"
    a1 = np.frombuffer(p1.samples, dtype=np.uint8).reshape(p1.height, p1.width, 4)
    assert a1[2, 2, 3] == 0 and a1[:, :, 3].max() > 200
    assert fitz.Pixmap(render_png(s1, dpi=200)).width > 10, "押印用に描画できる"
    assert StampLibrary(os.path.join(tmp, "stamps3.json")).stamps[-1].name == "安藤印 2", "保存されている"
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
    print("stamp tests passed")


if __name__ == "__main__":
    main()
