"""pdfmodel の自動テスト。python tests/test_model.py で実行可。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pymupdf as fitz
from pdfmodel import PdfModel


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


def main():
    tmp = tempfile.mkdtemp()
    a = os.path.join(tmp, "a.pdf")
    b = os.path.join(tmp, "b.pdf")
    img = os.path.join(tmp, "img.png")
    make_pdf(a, 5, "A")
    make_pdf(b, 2, "B")
    fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100), 0).save(img)

    m = PdfModel()
    m.open(a)
    assert m.page_count == 5 and not m.modified

    m.reorder([4, 3, 2, 1, 0])
    m.rotate([0], 90)
    m.delete([1])                      # A-4 を削除
    assert m.page_count == 4
    assert m.move_pages([3], -1) == [2]  # A-1 を 1 つ前へ
    n = m.insert_file(b, at=1)
    assert n == 2 and m.page_count == 6
    n = m.insert_file(img)             # 画像を末尾へ
    assert n == 1 and m.page_count == 7

    m.save()                           # 元ファイルへ上書き
    t, r = texts(a)
    assert t == ["A-5", "B-1", "B-2", "A-3", "A-1", "A-2", ""], t
    assert r == [90, 0, 0, 0, 0, 0, 0], r

    # アンドゥ: 直前の画像挿入を取り消す
    m.undo()
    assert m.page_count == 6 and m.can_redo()
    m.redo()
    assert m.page_count == 7

    out = os.path.join(tmp, "ex.pdf")
    m.extract([0, 4], out)
    assert texts(out)[0] == ["A-5", "A-1"]
    assert m.page_count == 7           # 抽出は元文書を変えない

    pix = m.render(0, max_width=140)
    assert abs(pix.width - 140) <= 1 and pix.height < pix.width  # 90°回転後は横長で表示される

    try:
        m.delete(range(7))
        raise AssertionError("全削除が通ってしまった")
    except ValueError:
        pass

    m.close()
    print("all tests passed")


if __name__ == "__main__":
    main()
