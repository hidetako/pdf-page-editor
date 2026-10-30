"""PDF 編集モデル（Qt 非依存）。

PyMuPDF (fitz) をラップし、ページ並べ替え・回転・削除・挿入・抽出・保存と
簡易アンドゥ（バイト列スナップショット）を提供する。
"""
from __future__ import annotations

import os
import re
import tempfile
from typing import Iterable, Sequence

import pymupdf as fitz  # PyMuPDF

_CM_RE = re.compile(r"([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+) cm")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
MAX_UNDO = 20


class PdfModel:
    def __init__(self) -> None:
        self.doc: fitz.Document | None = None
        self.path: str | None = None
        self.modified = False
        self._undo: list[bytes] = []
        self._redo: list[bytes] = []
        self._stamp_streams: set[int] = set()   # このセッションで押印した内容ストリームの xref（押印 1 つ = 1 ストリーム）

    # ------------------------------------------------------------ 基本
    @property
    def page_count(self) -> int:
        return self.doc.page_count if self.doc else 0

    def new(self) -> None:
        self.close()
        self.doc = fitz.open()
        self.modified = False

    def open(self, path: str) -> None:
        """ファイルを丸ごとメモリに読み込んで開く（元ファイルへの上書き保存を可能にする）。"""
        with open(path, "rb") as f:
            data = f.read()
        doc = fitz.open(stream=data, filetype="pdf")
        if doc.needs_pass:
            raise PermissionError("パスワード付き PDF は現在未対応です。")
        self.close()
        self.doc = doc
        self.path = path
        self.modified = False

    def close(self) -> None:
        if self.doc is not None:
            self.doc.close()
        self.doc = None
        self.path = None
        self.modified = False
        self._undo.clear()
        self._redo.clear()
        self._stamp_streams.clear()

    def _require(self) -> fitz.Document:
        if self.doc is None:
            raise RuntimeError("PDF が開かれていません。")
        return self.doc

    # ------------------------------------------------------------ アンドゥ
    def _snapshot(self) -> bytes:
        return self._require().tobytes(garbage=0, deflate=False)

    def _push_undo(self) -> None:
        self._undo.append(self._snapshot())
        if len(self._undo) > MAX_UNDO:
            self._undo.pop(0)
        self._redo.clear()

    def _restore(self, data: bytes) -> None:
        self._require().close()
        self.doc = fitz.open(stream=data, filetype="pdf")
        self.modified = True

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())

    # ------------------------------------------------------------ 編集
    def reorder(self, new_order: Sequence[int]) -> None:
        """new_order: 現在のページ番号(0始まり)を新しい順に並べたリスト。"""
        doc = self._require()
        if list(new_order) == list(range(doc.page_count)):
            return
        if sorted(new_order) != list(range(doc.page_count)):
            raise ValueError("並べ替え順が不正です。")
        self._push_undo()
        doc.select(list(new_order))
        self.modified = True

    def move_pages(self, indices: Sequence[int], delta: int) -> list[int]:
        """選択ページを delta (-1/+1) だけ移動。移動後のインデックス一覧を返す。"""
        doc = self._require()
        n = doc.page_count
        sel = sorted(set(indices))
        if not sel or delta == 0:
            return list(sel)
        if delta < 0 and sel[0] + delta < 0:
            return list(sel)
        if delta > 0 and sel[-1] + delta >= n:
            return list(sel)
        order = list(range(n))
        rng = sel if delta < 0 else list(reversed(sel))
        for i in rng:
            j = i + delta
            order[i], order[j] = order[j], order[i]
        self.reorder(order)
        return [i + delta for i in sel]

    def rotate(self, indices: Iterable[int], degrees: int) -> None:
        doc = self._require()
        idx = [i for i in set(indices) if 0 <= i < doc.page_count]
        if not idx:
            return
        self._push_undo()
        for i in idx:
            page = doc[i]
            page.set_rotation((page.rotation + degrees) % 360)
        self.modified = True

    def delete(self, indices: Iterable[int]) -> None:
        doc = self._require()
        idx = sorted({i for i in indices if 0 <= i < doc.page_count})
        if not idx:
            return
        if len(idx) >= doc.page_count:
            raise ValueError("全ページを削除することはできません。")
        self._push_undo()
        doc.delete_pages(idx)
        self.modified = True

    def insert_file(self, src_path: str, at: int | None = None) -> int:
        """PDF または画像を at の位置（None なら末尾）に挿入。挿入ページ数を返す。"""
        doc = self._require()
        ext = os.path.splitext(src_path)[1].lower()
        if ext in IMAGE_EXTS:
            img = fitz.open(src_path)
            src = fitz.open("pdf", img.convert_to_pdf())
            img.close()
        else:
            src = fitz.open(src_path)
            if src.needs_pass:
                src.close()
                raise PermissionError("パスワード付き PDF は挿入できません。")
        count = src.page_count
        self._push_undo()
        if at is None or at >= doc.page_count:
            doc.insert_pdf(src)
        else:
            doc.insert_pdf(src, start_at=at)
        src.close()
        self.modified = True
        return count

    def extract(self, indices: Sequence[int], out_path: str) -> None:
        """選択ページだけを新しい PDF として書き出す（元文書は変更しない）。"""
        doc = self._require()
        idx = sorted({i for i in indices if 0 <= i < doc.page_count})
        if not idx:
            raise ValueError("抽出するページが選択されていません。")
        out = fitz.open()
        for i in idx:
            out.insert_pdf(doc, from_page=i, to_page=i)
        _atomic_write(out_path, out.tobytes(garbage=3, deflate=True))
        out.close()

    def stamp(self, index: int, png: bytes, center: tuple[float, float],
              width_pt: float, height_pt: float) -> None:
        """表示座標 (回転後) の center を中心に、印影 PNG をページへ直接押印する。"""
        doc = self._require()
        page = doc[index]
        cx, cy = center
        rect = fitz.Rect(cx - width_pt / 2, cy - height_pt / 2, cx + width_pt / 2, cy + height_pt / 2)
        self._push_undo()
        before = set(page.get_contents())
        # insert_image は回転前の座標系を使うので derotation で変換し、画像側を逆回転して正立させる。
        # 押印 1 つにつき内容ストリームが 1 つ追加されるので、その xref を覚えて個別に取り消せるようにする
        page.insert_image(rect * page.derotation_matrix, stream=png, rotate=page.rotation,
                          keep_proportion=True, overlay=True)
        self._stamp_streams.update(set(page.get_contents()) - before)
        self.modified = True

    def stamps_on_page(self, index: int) -> list[tuple[int, fitz.Rect]]:
        """ページ上にある「このセッションで押した」印影の (ストリーム xref, 表示座標の矩形) 一覧。押した順。"""
        doc = self._require()
        page = doc[index]
        out = []
        for cx in page.get_contents():
            if cx not in self._stamp_streams:
                continue
            m = _CM_RE.search(doc.xref_stream(cx).decode("latin-1"))
            if not m:
                continue
            mat = fitz.Matrix(*[float(v) for v in m.groups()])
            # cm は PDF 座標系（左下原点）。transformation_matrix でページ座標へ、rotation_matrix で表示座標へ
            r = (fitz.Rect(0, 0, 1, 1) * mat * page.transformation_matrix * page.rotation_matrix).normalize()
            out.append((cx, r))
        return out

    def stamp_at(self, index: int, point: tuple[float, float]) -> int | None:
        """表示座標 point の位置にある押印の xref（重なっていれば後から押したもの）。無ければ None。"""
        hits = [x for x, r in self.stamps_on_page(index) if r.contains(fitz.Point(*point))]
        return hits[-1] if hits else None

    def remove_stamp(self, index: int, xref: int) -> None:
        """押印 1 つを取り消す（Undo 可）。"""
        doc = self._require()
        page = doc[index]
        contents = page.get_contents()
        if xref not in self._stamp_streams or xref not in contents:
            raise ValueError("この位置に取り消せる押印はありません。")
        self._push_undo()
        contents.remove(xref)
        doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in contents) + "]")
        # 記録は残す（Undo で戻ったときに再び押印として扱えるように。実在はページの内容と突き合わせて判定する）
        self.modified = True

    # ------------------------------------------------------------ 保存
    def save(self, path: str | None = None) -> str:
        doc = self._require()
        target = path or self.path
        if not target:
            raise ValueError("保存先が指定されていません。")
        _atomic_write(target, doc.tobytes(garbage=3, deflate=True))
        self.path = target
        self.modified = False
        return target

    # ------------------------------------------------------------ 描画
    def render(self, index: int, scale: float = 1.0, max_width: int | None = None):
        """ページを fitz.Pixmap として描画。max_width 指定時はその幅に収める。"""
        doc = self._require()
        page = doc[index]
        if max_width:
            w = page.rect.width or 1
            scale = max_width / w
        mat = fitz.Matrix(scale, scale)
        return page.get_pixmap(matrix=mat, alpha=False)

    def page_info(self, index: int) -> str:
        page = self._require()[index]
        r = page.rect
        return f"{r.width / 72 * 25.4:.0f} x {r.height / 72 * 25.4:.0f} mm  回転 {page.rotation}°"


def _atomic_write(path: str, data: bytes) -> None:
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".pdfpe_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
