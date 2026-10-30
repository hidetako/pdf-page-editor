"""アプリアイコン (assets/icon.ico) を生成する。PyMuPDF だけで描くので追加ライブラリ不要。
python assets/make_icon.py で再生成できる。"""
import os
import struct

import pymupdf as fitz

SIZES = [16, 24, 32, 48, 64, 128, 256]
SHU = (0.86, 0.13, 0.13)


def draw(size: int) -> bytes:
    """書類 + 朱印のアイコンを size px の透過 PNG として返す。"""
    doc = fitz.open()
    page = doc.new_page(width=256, height=256)
    sh = page.new_shape()
    # 書類（右上を折った紙）
    m = 28
    pts = [(m, m), (256 - m - 56, m), (256 - m, m + 56), (256 - m, 256 - m), (m, 256 - m)]
    sh.draw_polyline(pts + [pts[0]])
    sh.finish(color=(0.45, 0.45, 0.45), fill=(1, 1, 1), width=10, closePath=True)
    sh.draw_polyline([(256 - m - 56, m), (256 - m - 56, m + 56), (256 - m, m + 56)])
    sh.finish(color=(0.45, 0.45, 0.45), fill=(0.88, 0.88, 0.88), width=8, closePath=True)
    # 本文の線
    for y in (96, 124, 152):
        sh.draw_line((m + 28, y), (256 - m - 70, y))
    sh.finish(color=(0.7, 0.7, 0.7), width=9)
    # 朱印（右下）
    cx, cy, r = 168, 172, 58
    sh.draw_circle((cx, cy), r)
    sh.finish(color=SHU, width=11)
    sh.commit()
    fs = 74
    w = fitz.get_text_length("印", fontname="japan", fontsize=fs)
    page.insert_text((cx - w / 2, cy + fs * 0.36), "印", fontsize=fs, fontname="japan", color=SHU)
    pix = page.get_pixmap(matrix=fitz.Matrix(size / 256, size / 256), alpha=True)
    doc.close()
    return pix.tobytes("png")


def write_ico(path: str) -> None:
    pngs = [(s, draw(s)) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(pngs))
    entries = b""
    data = b""
    offset = 6 + 16 * len(pngs)
    for s, png in pngs:
        entries += struct.pack("<BBBBHHII", s if s < 256 else 0, s if s < 256 else 0, 0, 0, 1, 32, len(png), offset)
        data += png
        offset += len(png)
    with open(path, "wb") as f:
        f.write(header + entries + data)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    write_ico(out)
    print("wrote", out)
