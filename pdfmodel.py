"""PDF 編集モデル（Qt 非依存）。

PyMuPDF (fitz) をラップし、ページ並べ替え・回転・削除・挿入・抽出・保存と
簡易アンドゥ（バイト列スナップショット）を提供する。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Iterable, Sequence

import pymupdf as fitz  # PyMuPDF

import fonts

_CM_RE = re.compile(r"([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+) cm")
_DA_COLOR_RE = re.compile(r"([-\d.]+) ([-\d.]+) ([-\d.]+) rg")
_DA_FONT_RE = re.compile(r"/\S+ ([-\d.]+) Tf")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
MAX_UNDO = 20
MAX_UNDO_BYTES = 400 * 1024 * 1024   # 元に戻す用の控えの合計がこれを超えたら、古いものから捨てる（最低 MIN_UNDO 回ぶんは残す）
MIN_UNDO = 3

# 注釈の種類 → 表示名
ANNOT_KINDS = {
    fitz.PDF_ANNOT_TEXT: "付箋",
    fitz.PDF_ANNOT_FREE_TEXT: "文字",
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
# 外枠をドラッグして大きさを変えられる注釈
RESIZABLE_TYPES = {fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE, fitz.PDF_ANNOT_INK, fitz.PDF_ANNOT_LINE, fitz.PDF_ANNOT_FREE_TEXT}
MIN_ANNOT_SIZE = 6.0     # 大きさ変更の下限 (pt)
_UNSET = object()        # 「指定なし」（None は「塗りなし」などの意味で使うため）
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
        # 控えは文書まるごとの写しなので、大きな PDF に手書きを重ねるとメモリを使い切る。合計の大きさでも制限する
        while len(self._undo) > MIN_UNDO and sum(len(b) for b in self._undo) > MAX_UNDO_BYTES:
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

        type: PyMuPDF の種類番号, kind: 表示名, text: 内容, movable / resizable: 動かせるか・大きさを変えられるか,
        stroke: 線・文字の色 (r, g, b) または None, fill: 塗りの色または None, width: 線の太さ, opacity: 濃さ (0〜1),
        fontsize / font: 文字の書き込みの文字サイズとフォント（font はこのアプリで書いたものだけ）, border: 枠線の有無
        """
        doc = self._require()
        page = doc[index]
        out = []
        for annot in page.annots():
            t = annot.type[0]
            if t in _SKIP_TYPES:
                continue
            info = annot.info
            colors = annot.colors
            c = colors.get("stroke") or []
            fl = colors.get("fill") or []
            opacity = annot.opacity
            d = {
                "xref": annot.xref,
                "type": t,
                "kind": ANNOT_KINDS.get(t, annot.type[1]),
                "rect": (annot.rect * page.rotation_matrix).normalize(),
                "text": info.get("content", ""),
                "title": info.get("title", ""),
                "movable": t not in MARKUP_TYPES,
                "resizable": t in RESIZABLE_TYPES,
                "stroke": tuple(c[:3]) if len(c) >= 3 else (tuple(fl[:3]) if len(fl) >= 3 else None),
                "fill": tuple(fl[:3]) if len(fl) >= 3 else None,
                "width": max(0.0, float(annot.border.get("width", 1.0) or 0.0)),
                "opacity": 1.0 if opacity is None or opacity < 0 else float(opacity),
                "fontsize": None,
                "font": None,
                "border": False,
                "border_color": None,       # 文字の書き込みの枠の色（枠なしは None）
                "border_style": "solid",    # solid（1 本線）/ dotted（点線）/ double（2 重線）
                "border_width": 1.0,
                "arrow": False,
            }
            if t == fitz.PDF_ANNOT_LINE:
                d["arrow"] = any(e not in (fitz.PDF_ANNOT_LE_NONE,) for e in (annot.line_ends or ()))
            if t == fitz.PDF_ANNOT_FREE_TEXT:
                spec = _ppe(doc, annot.xref)
                if spec is not None:
                    d["stroke"] = tuple(spec.get("color") or (0, 0, 0))
                    d["fontsize"] = float(spec.get("size") or 12)
                    d["font"] = spec.get("font") or fonts.BUILTIN
                    d["fill"] = tuple(spec["fill"]) if spec.get("fill") else None
                    d["border"] = bool(spec.get("border"))
                    d["width"] = float(spec.get("bw") or 1.0)
                    d["border_color"] = _spec_border_color(spec)
                    d["border_style"] = spec.get("bs") if spec.get("bs") in fonts.BORDER_STYLES else "solid"
                    d["border_width"] = d["width"]
                else:
                    col, size = _parse_da(doc.xref_get_key(annot.xref, "DA")[1])
                    d["stroke"], d["fontsize"], d["fill"] = col, size, None
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

    def annot_info(self, index: int, xref: int) -> dict | None:
        """xref の注釈の情報（annots_on_page の 1 件）。無ければ None。"""
        if self.doc is None or not (0 <= index < self.page_count):
            return None
        return next((a for a in self.annots_on_page(index) if a["xref"] == xref), None)

    def _find_annot(self, page, xref: int):
        for annot in page.annots():
            if annot.xref == xref:
                return annot
        raise ValueError("注釈が見つかりません。")

    # ---- 追加
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

    def add_ink(self, index: int, strokes: Sequence[Sequence[tuple[float, float]]],
                color: tuple[float, float, float] = (0.85, 0.1, 0.1), width: float = 2.0, opacity: float = 1.0) -> int:
        """手書きの線を追加する。strokes は表示座標の点列のリスト（1 画 = 1 リスト）。注釈の xref を返す。"""
        doc = self._require()
        page = doc[index]
        der = page.derotation_matrix
        pts = [[tuple(fitz.Point(*p) * der) for p in s] for s in strokes if len(s) >= 2]
        if not pts:
            raise ValueError("線が短すぎます。")
        self._push_undo()
        annot = self._make_ink(page, pts, color, width, opacity)
        self.modified = True
        return annot.xref

    def add_line(self, index: int, p1: tuple[float, float], p2: tuple[float, float],
                 color: tuple[float, float, float] = (0.85, 0.1, 0.1), width: float = 2.0, opacity: float = 1.0,
                 arrow: bool = False) -> int:
        """直線（arrow=True なら終点に矢じり）を追加する。座標は表示座標。注釈の xref を返す。"""
        doc = self._require()
        page = doc[index]
        der = page.derotation_matrix
        self._push_undo()
        ends = (fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW if arrow else fitz.PDF_ANNOT_LE_NONE)
        annot = self._make_line(page, [tuple(fitz.Point(*p1) * der), tuple(fitz.Point(*p2) * der)], ends, color, width, opacity)
        self.modified = True
        return annot.xref

    def add_shape(self, index: int, kind: str, rect, color: tuple[float, float, float] = (0.85, 0.1, 0.1),
                  width: float = 2.0, opacity: float = 1.0, fill: tuple[float, float, float] | None = None) -> int:
        """四角 (kind="rect") または丸 (kind="ellipse") を追加する。rect は表示座標。注釈の xref を返す。"""
        doc = self._require()
        page = doc[index]
        r = (fitz.Rect(rect).normalize() * page.derotation_matrix).normalize()
        if r.width < 1 or r.height < 1:
            raise ValueError("図形が小さすぎます。")
        self._push_undo()
        annot = page.add_rect_annot(r) if kind == "rect" else page.add_circle_annot(r)
        annot.set_border(width=width)
        annot.set_colors(stroke=color, fill=fill if fill is not None else [])
        if opacity < 1:
            annot.set_opacity(opacity)
        annot.update()
        self.modified = True
        return annot.xref

    @staticmethod
    def _make_ink(page, strokes, color, width, opacity, title: str = "", content: str = ""):
        annot = page.add_ink_annot([[tuple(p) for p in s] for s in strokes])
        annot.set_border(width=width)
        annot.set_colors(stroke=color)
        if opacity < 1:
            annot.set_opacity(opacity)
        if title or content:
            annot.set_info(title=title, content=content)
        annot.update()
        return annot

    @staticmethod
    def _make_line(page, pts, ends, color, width, opacity, title: str = "", content: str = ""):
        annot = page.add_line_annot(tuple(pts[0]), tuple(pts[1]))
        annot.set_line_ends(*ends)
        annot.set_border(width=width)
        annot.set_colors(stroke=color, fill=color)
        if opacity < 1:
            annot.set_opacity(opacity)
        if title or content:
            annot.set_info(title=title, content=content)
        annot.update()
        return annot

    # ---- 文字の書き込み（埋め込みフォントで描くテキストボックス）
    def add_text_box(self, index: int, point: tuple[float, float], text: str, font: str = fonts.BUILTIN,
                     fontsize: float = 12, color: tuple[float, float, float] = (0.85, 0.1, 0.1),
                     fill: tuple[float, float, float] | None = None, border=False,
                     wrap_width: float | None = None, title: str = "",
                     border_style: str = "solid", border_width: float = 1.0) -> int:
        """表示座標 point を左上として文字を書き込む。フォントは PDF に埋め込むので、どのソフトでも同じ見た目になる。

        注釈（FreeText）として保存し、見た目だけを埋め込みフォントで描いたものに差し替える。注釈の xref を返す。
        wrap_width を指定するとその幅で折り返す。指定しなければ文字に合わせた幅（ページの右端まで）にする。
        fill は背景の色（None は無色）。border は枠の色（None / False は枠なし。True は文字と同じ色）。
        border_style は "solid"（1 本線）/ "dotted"（点線）/ "double"（2 重線）、border_width は枠の太さ (pt)。
        """
        self._require()
        spec = {"v": 1, "font": fonts.resolve(font), "size": float(fontsize), "color": [float(c) for c in color[:3]],
                "fill": [float(c) for c in fill[:3]] if fill else None, "wrap": wrap_width is not None}
        spec.update(_border_spec(border))
        spec["bs"] = border_style if border_style in fonts.BORDER_STYLES else "solid"
        spec["bw"] = max(0.25, float(border_width))
        self._push_undo()
        xref = self._build_text_box(index, spec, text, fitz.Point(*point), wrap_width, title)
        self.modified = True
        return xref

    def add_free_text(self, index: int, point: tuple[float, float], text: str, fontsize: float = 12,
                      color: tuple[float, float, float] = (0.85, 0.1, 0.1)) -> int:
        """旧 API（1.4.0）。標準ゴシックの add_text_box と同じ。"""
        return self.add_text_box(index, point, text, fonts.BUILTIN, fontsize, color)

    def _build_text_box(self, index: int, spec: dict, text: str, top_left: fitz.Point,
                        wrap_width: float | None, title: str = "") -> int:
        """spec の見た目でテキストボックスを作る（Undo の記録と modified は呼び出し側）。"""
        doc = self._require()
        page = doc[index]
        rotation = page.rotation
        pw, ph = page.rect.width, page.rect.height       # 表示上の大きさ（回転後）
        x = max(0.0, min(top_left.x, pw - 20))
        y = max(0.0, min(top_left.y, ph - 10))
        if wrap_width is not None:
            wrap_width = max(20.0, min(wrap_width, pw - x))
        color = tuple(spec["color"])
        fill = tuple(spec["fill"]) if spec.get("fill") else None
        border = _spec_border_color(spec)
        bw = float(spec.get("bw") or 1.0)
        bs = spec.get("bs") if spec.get("bs") in fonts.BORDER_STYLES else "solid"
        w, h = fonts.measure(text, spec["font"], spec["size"], wrap_width, max_width=pw - x, pad=fonts.box_pad(border, bw, bs))
        if y + h > ph:
            y = max(0.0, ph - h)
        src, w, h = fonts.render_text_pdf(text, spec["font"], spec["size"], color, wrap_width=w, fill=fill,
                                          border=border, border_width=bw, rotation=rotation, border_style=bs)
        del page
        # 見た目（フォーム XObject）を文書に取り込む。作業用のページを末尾に足して貼り、すぐ消す
        sp = doc.new_page(-1, width=src[0].rect.width, height=src[0].rect.height)
        xobj = sp.show_pdf_page(sp.rect, src, 0)
        del sp
        doc.delete_page(doc.page_count - 1)
        src.close()
        page = doc[index]
        prect = (fitz.Rect(x, y, x + w, y + h) * page.derotation_matrix).normalize()
        annot = page.add_freetext_annot(prect, text, fontsize=spec["size"], fontname="helv", text_color=color)
        xref = annot.xref
        del annot
        # ここから先は annot.update() を呼ばない（呼ぶと MuPDF が標準フォントで見た目を作り直してしまう）
        spec = dict(spec, rot=rotation)
        doc.xref_set_key(xref, "AP", f"<</N {xobj} 0 R>>")
        doc.xref_set_key(xref, "PPE", fitz.get_pdf_str(json.dumps(spec, separators=(",", ":"))))
        if title:
            doc.xref_set_key(xref, "T", fitz.get_pdf_str(title))
        return xref

    def _rebuild_text_box(self, index: int, xref: int, *, spec: dict | None = None, text: str | None = None,
                          top_left: fitz.Point | None = None, wrap_width: float | None = None,
                          natural: bool = False) -> int:
        """このアプリで書いたテキストボックスを、内容や見た目を変えて作り直す。新しい xref を返す。"""
        doc = self._require()
        page = doc[index]
        annot = self._find_annot(page, xref)
        old = _ppe(doc, xref) or {}
        disp = (annot.rect * page.rotation_matrix).normalize()
        info = annot.info
        cur_text = info.get("content", "")
        title = info.get("title", "")
        page.delete_annot(annot)
        del annot, page
        new_spec = dict(old)
        if spec:
            new_spec.update(spec)
        new_spec["font"] = fonts.resolve(new_spec.get("font"))
        if wrap_width is not None:
            new_spec["wrap"] = True
        elif natural and not new_spec.get("wrap"):
            wrap_width = None                       # 文字に合わせた幅に測り直す
        else:
            wrap_width = disp.width                 # 今の幅のまま
        return self._build_text_box(index, new_spec, cur_text if text is None else text,
                                    top_left or disp.tl, wrap_width, title)

    # ---- 変更
    def set_annot_text(self, index: int, xref: int, text: str) -> int:
        """付箋・文字の書き込みの内容を変更する。注釈の xref を返す（作り直した場合は新しい番号）。"""
        doc = self._require()
        page = doc[index]
        annot = self._find_annot(page, xref)
        self._push_undo()
        self.modified = True
        if annot.type[0] == fitz.PDF_ANNOT_FREE_TEXT and _ppe(doc, xref) is not None:
            del annot, page
            return self._rebuild_text_box(index, xref, text=text, natural=True)
        annot.set_info(content=text)
        if annot.type[0] == fitz.PDF_ANNOT_FREE_TEXT:
            col, size = _parse_da(doc.xref_get_key(xref, "DA")[1])
            annot.update(fontname="japan", fontsize=size, text_color=col)
        else:
            annot.update()
        return xref

    def set_annot_style(self, index: int, xref: int, *, color: tuple[float, float, float] | None = None,
                        width: float | None = None, opacity: float | None = None, fill=_UNSET,
                        font: str | None = None, fontsize: float | None = None, border=_UNSET,
                        border_style: str | None = None, border_width: float | None = None) -> int:
        """注釈の見た目を変える。指定した項目だけ変える。fill=None は塗りなし。注釈の xref を返す。

        文字の書き込みでは color は文字色、fill は背景の色、border は枠の色（None / False は枠なし、True は文字と同じ色）、
        border_style は枠の線（solid / dotted / double）、border_width は枠の太さ。width は使わない。
        それ以外の注釈では font / fontsize / border… は使わない。
        """
        doc = self._require()
        page = doc[index]
        annot = self._find_annot(page, xref)
        t = annot.type[0]
        self._push_undo()
        self.modified = True
        if t == fitz.PDF_ANNOT_FREE_TEXT:
            if _ppe(doc, xref) is not None:
                spec: dict = {}
                if color is not None:
                    spec["color"] = [float(c) for c in color[:3]]
                if font is not None:
                    spec["font"] = font
                if fontsize is not None:
                    spec["size"] = float(fontsize)
                if fill is not _UNSET:
                    spec["fill"] = [float(c) for c in fill[:3]] if fill else None
                if border is not _UNSET:
                    spec.update(_border_spec(border))
                if border_style is not None and border_style in fonts.BORDER_STYLES:
                    spec["bs"] = border_style
                if border_width is not None:
                    spec["bw"] = max(0.25, float(border_width))
                del annot, page
                return self._rebuild_text_box(index, xref, spec=spec, natural=font is not None or fontsize is not None)
            # 他のソフト（または 1.4.0 以前）の文字注釈: 標準の作り直しに任せる
            col, size = _parse_da(doc.xref_get_key(xref, "DA")[1])
            if fontsize is not None and fontsize != size:
                k = fontsize / (size or fontsize)
                r = (annot.rect * page.rotation_matrix).normalize()   # 表示上の左上を固定して拡縮
                disp = fitz.Rect(r.x0, r.y0, r.x0 + r.width * k, r.y0 + r.height * k)
                annot.set_rect((disp * page.derotation_matrix).normalize())
            annot.update(fontname="japan", fontsize=fontsize or size, text_color=color or col)
            return xref
        if color is not None or fill is not _UNSET:
            kw = {}
            if color is not None:
                kw["stroke"] = color
                if t == fitz.PDF_ANNOT_LINE:
                    kw["fill"] = color              # 矢じりの塗り
            if fill is not _UNSET and t in (fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE):
                kw["fill"] = fill if fill else []
            annot.set_colors(**kw)
        if width is not None and t not in MARKUP_TYPES and t != fitz.PDF_ANNOT_TEXT:
            annot.set_border(width=width)
        if opacity is not None:
            annot.set_opacity(opacity)
        annot.update()
        return xref

    def set_annot_color(self, index: int, xref: int, color: tuple[float, float, float]) -> int:
        """注釈の色（文字の書き込みは文字色、それ以外は線・マーカーの色）を変更する。"""
        return self.set_annot_style(index, xref, color=color)

    def set_annot_fontsize(self, index: int, xref: int, fontsize: float) -> int:
        """文字の書き込みの文字サイズを変更する（枠の大きさも合わせて変わる）。"""
        doc = self._require()
        page = doc[index]       # ページを変数で持っておく（手放すと注釈が無効になる）
        is_text = self._find_annot(page, xref).type[0] == fitz.PDF_ANNOT_FREE_TEXT
        del page
        if not is_text:
            raise ValueError("文字サイズを変えられるのは文字の書き込みだけです。")
        return self.set_annot_style(index, xref, fontsize=fontsize)

    def move_annot(self, index: int, xref: int, dx: float, dy: float) -> int:
        """注釈を表示座標で (dx, dy) だけ動かす。蛍光ペンなど文字に付く注釈は動かせない。注釈の xref を返す。"""
        doc = self._require()
        page = doc[index]
        annot = self._find_annot(page, xref)
        if annot.type[0] in MARKUP_TYPES:
            raise ValueError(f"{ANNOT_KINDS.get(annot.type[0], '')}は文字の位置に付くので移動できません。")
        if abs(dx) < 0.01 and abs(dy) < 0.01:
            return xref
        disp = (annot.rect * page.rotation_matrix).normalize()
        del annot, page
        return self._transform_annot(index, xref, disp + (dx, dy, dx, dy))

    def resize_annot(self, index: int, xref: int, rect) -> int:
        """注釈の外枠を表示座標の rect に合わせて大きさを変える。注釈の xref を返す。

        手書き・直線は中の線ごと拡大縮小する。文字の書き込みは幅だけ変わり、文字を折り返して高さを決める。
        """
        doc = self._require()
        page = doc[index]
        annot = self._find_annot(page, xref)
        if annot.type[0] not in RESIZABLE_TYPES:
            raise ValueError(f"{ANNOT_KINDS.get(annot.type[0], 'この注釈')}は大きさを変えられません。")
        new = fitz.Rect(rect).normalize()
        if new.width < MIN_ANNOT_SIZE or new.height < MIN_ANNOT_SIZE:
            new = fitz.Rect(new.x0, new.y0, max(new.x1, new.x0 + MIN_ANNOT_SIZE), max(new.y1, new.y0 + MIN_ANNOT_SIZE))
        del annot, page
        return self._transform_annot(index, xref, new, resize=True)

    def _transform_annot(self, index: int, xref: int, new_disp: fitz.Rect, resize: bool = False) -> int:
        """注釈の外枠（表示座標）を new_disp に移す。種類ごとに、枠の設定・座標の作り直しを使い分ける。"""
        doc = self._require()
        page = doc[index]
        annot = self._find_annot(page, xref)
        t = annot.type[0]
        rot, der = page.rotation_matrix, page.derotation_matrix
        old_disp = (annot.rect * rot).normalize()
        self._push_undo()
        self.modified = True
        if t == fitz.PDF_ANNOT_FREE_TEXT and _ppe(doc, xref) is not None:
            del annot, page
            return self._rebuild_text_box(index, xref, top_left=new_disp.tl, wrap_width=new_disp.width if resize else None)
        if t in (fitz.PDF_ANNOT_INK, fitz.PDF_ANNOT_LINE):
            verts = annot.vertices or []
            flat = [fitz.Point(*p) * rot for p in (verts if t == fitz.PDF_ANNOT_LINE else [q for s_ in verts for q in s_])]
            # 外枠は線の太さぶん広い。線そのものの範囲 (gb) を、新しい外枠から同じ余白を引いた範囲 (tg) に写す
            gb = fitz.Rect(min(q.x for q in flat), min(q.y for q in flat), max(q.x for q in flat), max(q.y for q in flat))
            tg = fitz.Rect(new_disp.x0 + (gb.x0 - old_disp.x0), new_disp.y0 + (gb.y0 - old_disp.y0),
                           new_disp.x1 - (old_disp.x1 - gb.x1), new_disp.y1 - (old_disp.y1 - gb.y1))
            sx = tg.width / gb.width if gb.width > 0.01 and tg.width > 0 else 1.0
            sy = tg.height / gb.height if gb.height > 0.01 and tg.height > 0 else 1.0

            def tr(p):
                q = fitz.Point(*p) * rot
                q = fitz.Point(tg.x0 + (q.x - gb.x0) * sx, tg.y0 + (q.y - gb.y0) * sy)
                return tuple(q * der)

            colors = annot.colors
            stroke = tuple((colors.get("stroke") or (0, 0, 0))[:3])
            width = float(annot.border.get("width", 1.0) or 1.0)
            opacity = annot.opacity if annot.opacity is not None and annot.opacity >= 0 else 1.0
            info = annot.info
            title, content = info.get("title", ""), info.get("content", "")
            ends = annot.line_ends if t == fitz.PDF_ANNOT_LINE else None
            page.delete_annot(annot)
            del annot
            if t == fitz.PDF_ANNOT_INK:
                new = self._make_ink(page, [[tr(p) for p in s] for s in verts], stroke, width, opacity, title, content)
            else:
                new = self._make_line(page, [tr(verts[0]), tr(verts[-1])], ends or (0, 0), stroke, width, opacity, title, content)
            return new.xref
        # 四角・丸は線の太さぶん外枠が広がっている（/RD）。その分を引いた枠を設定する
        target = (new_disp * der).normalize()
        if t in (fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE):
            rd = _rect_diff(doc, xref)
            target = fitz.Rect(target.x0 + rd[0], target.y0 + rd[1], target.x1 - rd[2], target.y1 - rd[3])
            if target.width < 1 or target.height < 1:
                target = (new_disp * der).normalize()
        annot.set_rect(target)
        if t == fitz.PDF_ANNOT_FREE_TEXT:
            col, size = _parse_da(doc.xref_get_key(xref, "DA")[1])
            annot.update(fontname="japan", fontsize=size, text_color=col)
        else:
            annot.update()
        return xref

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
        self._require()
        target = path or self.path
        if not target:
            raise ValueError("保存先が指定されていません。")
        _atomic_write(target, self._output_bytes())
        self.path = target
        self.modified = False
        return target

    def _output_bytes(self) -> bytes:
        """保存用のバイト列。パスワード付きで開いた（または設定した）文書は同じパスワードで暗号化し直す。

        不要なオブジェクトを取り除く保存（garbage）は、開いている文書の中のオブジェクト番号を振り直してしまう。
        押印や書き込みを番号で覚えているので、開いている文書には触らず、複製を作ってそこから書き出す。
        """
        doc = self._require()
        copy = fitz.open(stream=doc.tobytes(garbage=0, deflate=False, encryption=fitz.PDF_ENCRYPT_NONE), filetype="pdf")
        try:
            if self.password:
                return copy.tobytes(garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_AES_256,
                                    user_pw=self.password, owner_pw=self.password, permissions=self.permissions)
            return copy.tobytes(garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)
        finally:
            copy.close()

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


def _ppe(doc: fitz.Document, xref: int) -> dict | None:
    """このアプリで書いたテキストボックスの設定（注釈の /PPE キーに JSON で保存）。無ければ None。"""
    try:
        kind, value = doc.xref_get_key(xref, "PPE")
        if kind != "string":
            return None
        spec = json.loads(value)
        return spec if isinstance(spec, dict) else None
    except Exception:
        return None


def _rect_diff(doc: fitz.Document, xref: int) -> tuple[float, float, float, float]:
    """四角・丸の /RD（外枠と図形の差。左 上 右 下 ではなく PDF の並びだが 4 辺同じ値で書かれる）。"""
    try:
        kind, value = doc.xref_get_key(xref, "RD")
        if kind == "array":
            v = [float(x) for x in value.strip("[]").split()]
            if len(v) == 4:
                return v[0], v[1], v[2], v[3]
    except Exception:
        pass
    return 0.0, 0.0, 0.0, 0.0


def _border_spec(border) -> dict:
    """枠の指定（色 / True / False / None）を、注釈に保存する形にする。
    "border" は枠の有無、"bc" は枠の色（None は「文字と同じ色」。1.6.0 までの「枠」はこの形）。"""
    if border is None or border is False:
        return {"border": False, "bc": None}
    if border is True:
        return {"border": True, "bc": None}
    return {"border": True, "bc": [float(c) for c in border[:3]]}


def _spec_border_color(spec: dict):
    """保存してある見た目から、枠の色を求める。枠なしは None。"""
    if not spec.get("border"):
        return None
    return tuple(spec["bc"]) if spec.get("bc") else tuple(spec.get("color") or (0, 0, 0))


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
