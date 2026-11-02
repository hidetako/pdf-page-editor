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
_DA_COLOR_RE = re.compile(r"([-\d.]+) ([-\d.]+) ([-\d.]+) rg")
_DA_FONT_RE = re.compile(r"/\S+ ([-\d.]+) Tf")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
MAX_UNDO = 20

# 注釈の種類 → 表示名
ANNOT_KINDS = {
    fitz.PDF_ANNOT_TEXT: "付箋",
    fitz.PDF_ANNOT_FREE_TEXT: "テキスト注釈",
    fitz.PDF_ANNOT_HIGHLIGHT: "蛍光ペン",
    fitz.PDF_ANNOT_UNDERLINE: "下線",
    fitz.PDF_ANNOT_STRIKE_OUT: "取り消し線",
    fitz.PDF_ANNOT_SQUIGGLY: "波線",
    fitz.PDF_ANNOT_SQUARE: "四角",
    fitz.PDF_ANNOT_CIRCLE: "円",
    fitz.PDF_ANNOT_LINE: "線",
    fitz.PDF_ANNOT_POLYGON: "多角形",
    fitz.PDF_ANNOT_POLY_LINE: "折れ線",
    fitz.PDF_ANNOT_INK: "手書き",
    fitz.PDF_ANNOT_STAMP: "スタンプ",
    fitz.PDF_ANNOT_FILE_ATTACHMENT: "添付ファイル",
    fitz.PDF_ANNOT_CARET: "挿入記号",
    fitz.PDF_ANNOT_REDACT: "墨消し",
}
# 文字の範囲に付く注釈（位置は文字で決まるので移動できない）
MARKUP_TYPES = {fitz.PDF_ANNOT_HIGHLIGHT, fitz.PDF_ANNOT_UNDERLINE, fitz.PDF_ANNOT_STRIKE_OUT, fitz.PDF_ANNOT_SQUIGGLY}
MARKUP_KINDS = {"highlight": fitz.PDF_ANNOT_HIGHLIGHT, "underline": fitz.PDF_ANNOT_UNDERLINE, "strikeout": fitz.PDF_ANNOT_STRIKE_OUT}
_SKIP_TYPES = {fitz.PDF_ANNOT_POPUP, fitz.PDF_ANNOT_LINK, fitz.PDF_ANNOT_WIDGET}
# 制限（permissions ビットが立っていない = 禁止）
_PERMISSION_LABELS = [(fitz.PDF_PERM_PRINT, "印刷"), (fitz.PDF_PERM_MODIFY, "編集"),
                      (fitz.PDF_PERM_COPY, "コピー"), (fitz.PDF_PERM_ANNOTATE, "注釈")]


class PasswordRequired(PermissionError):
    """パスワード付き PDF。password を付けて開き直す。wrong=True なら渡したパスワードが違った。"""

    def __init__(self, path: str, wrong: bool = False):
        self.path = path
        self.wrong = wrong
        super().__init__("パスワードが違います。" if wrong else "この PDF はパスワードで保護されています。")


class PdfModel:
    def __init__(self) -> None:
        self.doc: fitz.Document | None = None
        self.path: str | None = None
        self.modified = False
        self._undo: list[bytes] = []
        self._redo: list[bytes] = []
        self._stamp_streams: set[int] = set()   # このセッションで押印した内容ストリームの xref（押印 1 つ = 1 ストリーム）
        self.password: str | None = None        # 開くのに使ったパスワード（保存時に同じパスワードで暗号化し直す）
        self.permissions: int = -1              # PDF の許可ビット（-1 = すべて許可）

    # ------------------------------------------------------------ 基本
    @property
    def page_count(self) -> int:
        return self.doc.page_count if self.doc else 0

    def new(self) -> None:
        self.close()
        self.doc = fitz.open()
        self.modified = False

    def open(self, path: str, password: str | None = None) -> None:
        """ファイルを丸ごとメモリに読み込んで開く（元ファイルへの上書き保存を可能にする）。

        パスワード付き PDF は PasswordRequired を投げるので、password を付けて開き直す。
        """
        with open(path, "rb") as f:
            data = f.read()
        doc = _open_bytes(data, path, password)
        self.close()
        self.doc = doc
        self.path = path
        self.password = password if password else None
        self.permissions = doc.permissions
        self.modified = False

    def restrictions(self) -> list[str]:
        """この PDF に設定されている制限（印刷・編集・コピー・注釈）の表示名。無ければ空。"""
        if self.doc is None or self.permissions == -1:
            return []
        return [label for bit, label in _PERMISSION_LABELS if not (self.permissions & bit)]

    def set_password(self, password: str | None) -> None:
        """保存時のパスワードを設定（None / 空で解除）。"""
        self._require()
        password = password or None
        if password == self.password:
            return
        self.password = password
        if password is None:
            self.permissions = -1
        self.modified = True

    def close(self) -> None:
        if self.doc is not None:
            self.doc.close()
        self.doc = None
        self.path = None
        self.modified = False
        self._undo.clear()
        self._redo.clear()
        self._stamp_streams.clear()
        self.password = None
        self.permissions = -1

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

    def insert_file(self, src_path: str, at: int | None = None, password: str | None = None) -> int:
        """PDF または画像を at の位置（None なら末尾）に挿入。挿入ページ数を返す。
        パスワード付き PDF は PasswordRequired を投げるので、password を付けて呼び直す。"""
        doc = self._require()
        ext = os.path.splitext(src_path)[1].lower()
        if ext in IMAGE_EXTS:
            img = fitz.open(src_path)
            src = fitz.open("pdf", img.convert_to_pdf())
            img.close()
        else:
            with open(src_path, "rb") as f:
                src = _open_bytes(f.read(), src_path, password)
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

    # ------------------------------------------------------------ 付箋（テキスト注釈）
    def notes_on_page(self, index: int) -> list[dict]:
        """ページ上の付箋（Text 注釈）一覧。rect は表示座標。"""
        doc = self._require()
        page = doc[index]
        out = []
        for annot in page.annots(types=[fitz.PDF_ANNOT_TEXT]):
            info = annot.info
            out.append({
                "xref": annot.xref,
                "rect": (annot.rect * page.rotation_matrix).normalize(),
                "text": info.get("content", ""),
                "title": info.get("title", ""),
            })
        return out

    def note_at(self, index: int, point: tuple[float, float]) -> dict | None:
        """表示座標 point にある付箋（重なっていれば後のもの）。無ければ None。"""
        pt = fitz.Point(*point)
        hits = [n for n in self.notes_on_page(index) if n["rect"].contains(pt)]
        return hits[-1] if hits else None

    def add_note(self, index: int, point: tuple[float, float], text: str, title: str = "") -> int:
        """表示座標 point を左上として付箋を置く。注釈の xref を返す。"""
        doc = self._require()
        page = doc[index]
        self._push_undo()
        annot = page.add_text_annot(fitz.Point(*point) * page.derotation_matrix, text, icon="Note")
        annot.set_info(title=title or "付箋", content=text)
        annot.set_colors(stroke=(1.0, 0.85, 0.2))
        annot.update()
        self.modified = True
        return annot.xref

    def update_note(self, index: int, xref: int, text: str) -> None:
        doc = self._require()
        page = doc[index]
        for annot in page.annots(types=[fitz.PDF_ANNOT_TEXT]):
            if annot.xref == xref:
                self._push_undo()
                # Undo スナップショット後に取り直す（同じ page オブジェクトは使い続けられる）
                annot.set_info(content=text)
                annot.update()
                self.modified = True
                return
        raise ValueError("付箋が見つかりません。")

    def remove_note(self, index: int, xref: int) -> None:
        doc = self._require()
        page = doc[index]
        for annot in page.annots(types=[fitz.PDF_ANNOT_TEXT]):
            if annot.xref == xref:
                self._push_undo()
                page.delete_annot(annot)
                self.modified = True
                return
        raise ValueError("付箋が見つかりません。")

    # ------------------------------------------------------------ 注釈（全種類）
    def annots_on_page(self, index: int) -> list[dict]:
        """ページ上の注釈一覧（ポップアップ・リンク・フォーム部品を除く）。rect は表示座標。

        type: PyMuPDF の種類番号, kind: 表示名, text: 内容, movable: ドラッグで動かせるか,
        stroke: 線・文字の色 (r, g, b) または None, fontsize: テキスト注釈の文字サイズ
        """
        doc = self._require()
        page = doc[index]
        out = []
        for annot in page.annots():
            t = annot.type[0]
            if t in _SKIP_TYPES:
                continue
            info = annot.info
            d = {
                "xref": annot.xref,
                "type": t,
                "kind": ANNOT_KINDS.get(t, annot.type[1]),
                "rect": (annot.rect * page.rotation_matrix).normalize(),
                "text": info.get("content", ""),
                "title": info.get("title", ""),
                "movable": t not in MARKUP_TYPES,
                "stroke": None,
                "fontsize": None,
            }
            if t == fitz.PDF_ANNOT_FREE_TEXT:
                col, size = _parse_da(doc.xref_get_key(annot.xref, "DA")[1])
                d["stroke"], d["fontsize"] = col, size
            else:
                c = annot.colors.get("stroke") or annot.colors.get("fill") or []
                d["stroke"] = tuple(c[:3]) if len(c) >= 3 else None
            out.append(d)
        return out

    def annot_at(self, index: int, point: tuple[float, float], pad: float = 0.0) -> dict | None:
        """表示座標 point にある注釈（重なっていれば後のもの）。無ければ None。"""
        pt = fitz.Point(*point)
        hits = []
        for a in self.annots_on_page(index):
            r = fitz.Rect(a["rect"])
            r.x0 -= pad; r.y0 -= pad; r.x1 += pad; r.y1 += pad
            if r.contains(pt):
                hits.append(a)
        return hits[-1] if hits else None

    def _find_annot(self, page, xref: int):
        for annot in page.annots():
            if annot.xref == xref:
                return annot
        raise ValueError("注釈が見つかりません。")

    def add_markup(self, index: int, kind: str, rects: Sequence, color: tuple[float, float, float] | None = None) -> int:
        """文字の範囲（表示座標の矩形列）に蛍光ペン / 下線 / 取り消し線を付ける。注釈の xref を返す。"""
        doc = self._require()
        page = doc[index]
        t = MARKUP_KINDS[kind]
        quads = [(fitz.Rect(r) * page.derotation_matrix).normalize() for r in rects]
        if not quads:
            raise ValueError("対象の文字が選択されていません。")
        self._push_undo()
        if t == fitz.PDF_ANNOT_HIGHLIGHT:
            annot = page.add_highlight_annot(quads)
        elif t == fitz.PDF_ANNOT_UNDERLINE:
            annot = page.add_underline_annot(quads)
        else:
            annot = page.add_strikeout_annot(quads)
        if color is not None:
            annot.set_colors(stroke=color)
        annot.update()
        self.modified = True
        return annot.xref

    def add_free_text(self, index: int, point: tuple[float, float], text: str, fontsize: float = 12,
                      color: tuple[float, float, float] = (0.85, 0.1, 0.1)) -> int:
        """表示座標 point を左上として、文字を書き込むテキスト注釈を置く。注釈の xref を返す。
        大きさは文字数と行数から決める（ページ右端・下端で切る）。"""
        doc = self._require()
        page = doc[index]
        prect = page.rect * page.rotation_matrix
        pw, ph = prect.width, prect.height
        lines = text.split("\n") or [""]
        width = max(fitz.get_text_length(ln, fontname="japan", fontsize=fontsize) for ln in lines) + fontsize + 6
        height = len(lines) * fontsize * 1.35 + 8
        x, y = point
        x = max(0.0, min(x, pw - 20))
        y = max(0.0, min(y, ph - 20))
        width = min(width, pw - x)
        height = min(height, ph - y)
        rect = (fitz.Rect(x, y, x + width, y + height) * page.derotation_matrix).normalize()
        self._push_undo()
        annot = page.add_freetext_annot(rect, text, fontsize=fontsize, fontname="japan", text_color=color,
                                        rotate=page.rotation)
        annot.update()
        self.modified = True
        return annot.xref

    def set_annot_text(self, index: int, xref: int, text: str) -> None:
        """付箋・テキスト注釈の内容を変更する。"""
        doc = self._require()
        page = doc[index]
        annot = self._find_annot(page, xref)
        self._push_undo()
        annot.set_info(content=text)
        if annot.type[0] == fitz.PDF_ANNOT_FREE_TEXT:
            col, size = _parse_da(doc.xref_get_key(xref, "DA")[1])
            annot.update(fontname="japan", fontsize=size, text_color=col)
        else:
            annot.update()
        self.modified = True

    def set_annot_color(self, index: int, xref: int, color: tuple[float, float, float]) -> None:
        """注釈の色（テキスト注釈は文字色、それ以外は線・マーカーの色）を変更する。"""
        doc = self._require()
        page = doc[index]
        annot = self._find_annot(page, xref)
        self._push_undo()
        if annot.type[0] == fitz.PDF_ANNOT_FREE_TEXT:
            _col, size = _parse_da(doc.xref_get_key(xref, "DA")[1])
            annot.update(fontname="japan", fontsize=size, text_color=color)
        else:
            annot.set_colors(stroke=color)
            annot.update()
        self.modified = True

    def set_annot_fontsize(self, index: int, xref: int, fontsize: float) -> None:
        """テキスト注釈の文字サイズを変更する（枠の大きさも合わせて変える）。"""
        doc = self._require()
        page = doc[index]
        annot = self._find_annot(page, xref)
        if annot.type[0] != fitz.PDF_ANNOT_FREE_TEXT:
            raise ValueError("文字サイズを変えられるのはテキスト注釈だけです。")
        col, old = _parse_da(doc.xref_get_key(xref, "DA")[1])
        self._push_undo()
        k = fontsize / (old or fontsize)
        r = (annot.rect * page.rotation_matrix).normalize()   # 表示上の左上を固定して拡縮
        disp = fitz.Rect(r.x0, r.y0, r.x0 + r.width * k, r.y0 + r.height * k)
        annot.set_rect((disp * page.derotation_matrix).normalize())
        annot.update(fontname="japan", fontsize=fontsize, text_color=col)
        self.modified = True

    def move_annot(self, index: int, xref: int, dx: float, dy: float) -> None:
        """注釈を表示座標で (dx, dy) だけ動かす。蛍光ペンなど文字に付く注釈は動かせない。"""
        doc = self._require()
        page = doc[index]
        annot = self._find_annot(page, xref)
        if annot.type[0] in MARKUP_TYPES:
            raise ValueError(f"{ANNOT_KINDS.get(annot.type[0], '')}は文字の位置に付くので移動できません。")
        if abs(dx) < 0.01 and abs(dy) < 0.01:
            return
        self._push_undo()
        disp = (annot.rect * page.rotation_matrix).normalize() + (dx, dy, dx, dy)
        annot.set_rect((disp * page.derotation_matrix).normalize())
        if annot.type[0] == fitz.PDF_ANNOT_FREE_TEXT:
            col, size = _parse_da(doc.xref_get_key(xref, "DA")[1])
            annot.update(fontname="japan", fontsize=size, text_color=col)
        else:
            annot.update()
        self.modified = True

    def remove_annot(self, index: int, xref: int) -> None:
        doc = self._require()
        page = doc[index]
        annot = self._find_annot(page, xref)
        self._push_undo()
        page.delete_annot(annot)
        self.modified = True

    # ------------------------------------------------------------ しおり / 目次（アウトライン）
    def get_toc(self) -> list[list]:
        """[[レベル, タイトル, ページ番号(1 始まり)], ...]。目次の無い PDF は空リスト。"""
        return self._require().get_toc(simple=True)

    def add_bookmark(self, index: int, title: str) -> None:
        """ページ index(0 始まり) へのしおりを、ページ順の位置にレベル 1 で追加する。"""
        doc = self._require()
        toc = doc.get_toc(simple=True)
        self._push_undo()
        entry = [1, title, index + 1]
        pos = len(toc)
        for i, (lvl, _t, pg) in enumerate(toc):
            if lvl == 1 and pg > index + 1:
                pos = i
                break
        toc.insert(pos, entry)
        doc.set_toc(toc)
        self.modified = True

    def remove_toc_entry(self, pos: int) -> None:
        """目次の pos 番目の項目を削除（子項目はひとつ上のレベルへ繰り上げる）。"""
        doc = self._require()
        toc = doc.get_toc(simple=True)
        if not (0 <= pos < len(toc)):
            raise ValueError("目次の項目が見つかりません。")
        self._push_undo()
        lvl = toc[pos][0]
        del toc[pos]
        for i in range(pos, len(toc)):
            if toc[i][0] > lvl:
                toc[i][0] -= 1
            else:
                break
        doc.set_toc(toc)
        self.modified = True

    def rename_toc_entry(self, pos: int, title: str) -> None:
        doc = self._require()
        toc = doc.get_toc(simple=True)
        if not (0 <= pos < len(toc)):
            raise ValueError("目次の項目が見つかりません。")
        self._push_undo()
        toc[pos][1] = title
        doc.set_toc(toc)
        self.modified = True

    # ------------------------------------------------------------ 保存
    def save(self, path: str | None = None) -> str:
        doc = self._require()
        target = path or self.path
        if not target:
            raise ValueError("保存先が指定されていません。")
        _atomic_write(target, self._output_bytes())
        self.path = target
        self.modified = False
        return target

    def _output_bytes(self) -> bytes:
        """保存用のバイト列。パスワード付きで開いた（または設定した）文書は同じパスワードで暗号化し直す。"""
        doc = self._require()
        if self.password:
            return doc.tobytes(garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_AES_256,
                               user_pw=self.password, owner_pw=self.password, permissions=self.permissions)
        return doc.tobytes(garbage=3, deflate=True)

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

    def page_words(self, index: int) -> list[dict]:
        """ページ上の単語一覧（読み順）。rect は表示座標。"""
        doc = self._require()
        page = doc[index]
        rot = page.rotation_matrix
        words = []
        for x0, y0, x1, y1, text, block, line, wno in page.get_text("words"):
            words.append({"rect": (fitz.Rect(x0, y0, x1, y1) * rot).normalize(), "text": text,
                          "block": block, "line": line, "word": wno})
        words.sort(key=lambda w: (w["block"], w["line"], w["word"]))
        return words

    def page_info(self, index: int) -> str:
        page = self._require()[index]
        r = page.rect
        return f"{r.width / 72 * 25.4:.0f} x {r.height / 72 * 25.4:.0f} mm  回転 {page.rotation}°"


def _open_bytes(data: bytes, path: str, password: str | None) -> fitz.Document:
    """バイト列から PDF を開く。パスワードが必要なら（または違えば）PasswordRequired。"""
    doc = fitz.open(stream=data, filetype="pdf")
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise PasswordRequired(path, wrong=bool(password))
    return doc


def _parse_da(da: str) -> tuple[tuple[float, float, float], float]:
    """テキスト注釈の既定外観 (/DA) から (文字色, 文字サイズ) を取り出す。無ければ黒・12pt。"""
    col = (0.0, 0.0, 0.0)
    size = 12.0
    m = _DA_COLOR_RE.search(da or "")
    if m:
        col = tuple(float(v) for v in m.groups())
    m = _DA_FONT_RE.search(da or "")
    if m and float(m.group(1)) > 0:
        size = float(m.group(1))
    return col, size


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
