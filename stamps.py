"""電子印鑑: 印影の定義・描画・保存（Qt 非依存）。

PyMuPDF 内蔵の日本語フォント ("japan") で描画するため、OS にフォントがなくても動く。
印影は透過 PNG (bytes) として生成し、PDF への押印・画像保存の両方に使う。
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import math
import os
import random
import uuid
from dataclasses import asdict, dataclass, field, fields

import numpy as np
import pymupdf as fitz

MM = 72 / 25.4
FONT = "japan"
SHU = [0.86, 0.13, 0.13]  # 朱色
MAX_IMAGE_PX = 4000       # 画像印を描画する最大ピクセル幅

KINDS = {
    "mitome": "認印・三文判（丸・縦書き）",
    "datename": "データネーム印（丸・3段・日付）",
    "kaku": "ビジネス印／角印（四角・縦書き）",
    "user": "ユーザー印（丸・横書き）",
    "address": "住所印（長方形・横書き）",
    "image": "画像印（スキャナ印・イラスト印・手書きサイン）",
}
DATE_FORMATS = {
    "yyyy.mm.dd": "2026.10.29",
    "yyyy/mm/dd": "2026/10/29",
    "reiwa": "R8.10.29",
    "reiwa_kanji": "令和8年10月29日",
    "ymd_kanji": "2026年10月29日",
}


def format_date(d: _dt.date, fmt: str) -> str:
    if fmt == "yyyy/mm/dd":
        return f"{d.year}/{d.month:02d}/{d.day:02d}"
    if fmt == "reiwa":
        return f"R{d.year - 2018}.{d.month}.{d.day}"
    if fmt == "reiwa_kanji":
        return f"令和{d.year - 2018}年{d.month}月{d.day}日"
    if fmt == "ymd_kanji":
        return f"{d.year}年{d.month}月{d.day}日"
    return f"{d.year}.{d.month:02d}.{d.day:02d}"


@dataclass
class Stamp:
    name: str = "新しい印鑑"
    kind: str = "mitome"
    lines: list[str] = field(default_factory=lambda: ["山田"])
    size_mm: float = 12.0          # 直径 / 一辺 / 長方形の幅
    height_mm: float = 0.0         # 長方形の高さ（0 なら自動）
    color: list[float] = field(default_factory=lambda: list(SHU))
    border: float = 0.08           # 枠線の太さ (mm)
    date_format: str = "yyyy.mm.dd"
    font_scale: float = 1.0        # 文字の大きさ倍率
    image_b64: str = ""            # 画像印の PNG (base64)
    recolor: bool = False          # 画像印を color で塗り直す（白地・濃淡はインク量として扱う）
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Stamp":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})

    def size_pt(self) -> tuple[float, float]:
        w = self.size_mm * MM
        if self.kind == "address":
            h = (self.height_mm or self.size_mm * 0.4) * MM
        elif self.kind == "image":
            if self.image_b64:
                pix = fitz.Pixmap(base64.b64decode(self.image_b64))
                h = w * pix.height / max(pix.width, 1)
            else:
                h = w
        else:
            h = w
        return w, h


# ---------------------------------------------------------------- 描画
def _text_len(s: str, fs: float) -> float:
    return fitz.get_text_length(s, fontname=FONT, fontsize=fs)


def _fit_h(text: str, max_w: float, max_h: float) -> float:
    """横書き 1 行が幅 max_w・高さ max_h に収まるフォントサイズ。"""
    if not text:
        return max_h
    w1 = _text_len(text, 1.0) or 1.0
    return min(max_w / w1, max_h)


def _draw_h_center(page, text: str, cx: float, cy: float, fs: float, color):
    """横書きテキストを中心 (cx, cy) に描く。"""
    w = _text_len(text, fs)
    page.insert_text((cx - w / 2, cy + fs * 0.36), text, fontsize=fs, fontname=FONT, color=color)


def _draw_vertical_columns(page, lines: list[str], box: fitz.Rect, color, scale: float):
    """縦書き。行は右から左へ列として並べる（角印の慣例）。"""
    lines = [l for l in lines if l] or [" "]
    ncol = len(lines)
    nmax = max(len(l) for l in lines)
    fs = min(box.height / nmax, box.width / ncol) * 0.92 * scale
    col_w = box.width / ncol
    for ci, text in enumerate(lines):
        x_center = box.x1 - col_w * (ci + 0.5)
        total_h = fs * len(text)
        y0 = box.y0 + (box.height - total_h) / 2
        for k, ch in enumerate(text):
            w = _text_len(ch, fs)
            page.insert_text((x_center - w / 2, y0 + fs * k + fs * 0.86), ch,
                             fontsize=fs, fontname=FONT, color=color)


def _draw_shape(stamp: Stamp, page: fitz.Page, date: _dt.date):
    color = tuple(stamp.color)
    w, h = page.rect.width, page.rect.height
    bw = max(stamp.border * MM, 0.2)
    sh = page.new_shape()
    k = stamp.kind
    if k in ("mitome", "datename", "user"):
        c = fitz.Point(w / 2, h / 2)
        r = w / 2 - bw / 2
        sh.draw_circle(c, r)
        sh.finish(color=color, width=bw)
        inner = r - bw
        if k == "mitome":
            side = inner * math.sqrt(2) * 0.92
            box = fitz.Rect(c.x - side / 2, c.y - side / 2, c.x + side / 2, c.y + side / 2)
            _draw_vertical_columns(page, stamp.lines, box, color, stamp.font_scale)
        elif k == "user":
            lines = [l for l in stamp.lines if l] or [" "]
            n = len(lines)
            side = inner * math.sqrt(2) * 0.9
            row_h = side / n
            for i, text in enumerate(lines):
                fs = _fit_h(text, side, row_h * 0.85) * stamp.font_scale
                _draw_h_center(page, text, c.x, c.y - side / 2 + row_h * (i + 0.5), fs, color)
        else:  # datename
            off = inner * 0.33
            for y in (c.y - off, c.y + off):
                half = math.sqrt(max(inner ** 2 - (y - c.y) ** 2, 0))
                sh.draw_line(fitz.Point(c.x - half, y), fitz.Point(c.x + half, y))
            sh.finish(color=color, width=bw)
            top = stamp.lines[0] if stamp.lines else ""
            bottom = stamp.lines[1] if len(stamp.lines) > 1 else ""
            date_s = format_date(date, stamp.date_format)
            # 上段: 中心 y = c.y - inner*0.63, 利用可能幅は弦の 85%
            for text, cy in ((top, c.y - inner * 0.64), (bottom, c.y + inner * 0.64)):
                if not text:
                    continue
                half = math.sqrt(max(inner ** 2 - (cy - c.y) ** 2, 0))
                fs = _fit_h(text, half * 2 * 0.85, (inner - off) * 0.62) * stamp.font_scale
                _draw_h_center(page, text, c.x, cy, fs, color)
            fs = _fit_h(date_s, inner * 2 * 0.88, off * 2 * 0.62) * stamp.font_scale
            _draw_h_center(page, date_s, c.x, c.y, fs, color)
    elif k == "kaku":
        rect = fitz.Rect(bw / 2, bw / 2, w - bw / 2, h - bw / 2)
        sh.draw_rect(rect)
        sh.finish(color=color, width=bw)
        pad = w * 0.10 + bw
        box = fitz.Rect(pad, pad, w - pad, h - pad)
        _draw_vertical_columns(page, stamp.lines, box, color, stamp.font_scale)
    elif k == "address":
        rect = fitz.Rect(bw / 2, bw / 2, w - bw / 2, h - bw / 2)
        sh.draw_rect(rect)
        sh.finish(color=color, width=bw)
        lines = [l for l in stamp.lines if l] or [" "]
        pad_x = w * 0.05
        row_h = (h - bw * 2) / len(lines)
        for i, text in enumerate(lines):
            fs = _fit_h(text, w - pad_x * 2, row_h * 0.75) * stamp.font_scale
            cy = bw + row_h * (i + 0.5)
            page.insert_text((pad_x, cy + fs * 0.36), text, fontsize=fs, fontname=FONT, color=color)
    sh.commit()


def render_png(stamp: Stamp, dpi: int = 600, date: _dt.date | None = None,
               tilt_deg: float = 0.0, opacity: float = 1.0, kasure: float = 0.0,
               seed: int | None = None) -> bytes:
    """印影を透過 PNG として描画する。tilt: 傾き(度), kasure: かすれ 0〜1。"""
    date = date or _dt.date.today()
    w, h = stamp.size_pt()
    scale = dpi / 72
    if stamp.kind == "image":
        if not stamp.image_b64:
            raise ValueError("画像が設定されていません。")
        src = fitz.Pixmap(base64.b64decode(stamp.image_b64))
        if stamp.recolor:
            src = recolor_pixmap(src, stamp.color)
        if tilt_deg == 0 and opacity >= 1.0 and kasure <= 0:
            # 加工なしなら元画像のピクセルをそのまま使う（再サンプリングによるにじみを避ける）
            return _as_rgba(src).tobytes("png")
        # 元画像の解像度を下回らないようにする（小さい押印サイズでもぼやけない）。上限 4000px
        scale = min(max(scale, src.width / (w or 1)), MAX_IMAGE_PX / (w or 1))
    mat = fitz.Matrix(scale, scale).prerotate(tilt_deg)
    if stamp.kind == "image":
        doc = fitz.open()
        page = doc.new_page(width=w, height=h)
        page.insert_image(page.rect, pixmap=src)
    else:
        doc = fitz.open()
        page = doc.new_page(width=w, height=h)
        _draw_shape(stamp, page, date)
    pix = page.get_pixmap(matrix=mat, alpha=True)
    doc.close()
    if opacity < 1.0 or kasure > 0:
        pix = _post_process(pix, opacity, kasure, seed)
    return pix.tobytes("png")


def _as_rgba(pix: fitz.Pixmap) -> fitz.Pixmap:
    """RGB+alpha の Pixmap にそろえる（グレー・CMYK・アルファなしに対応）。"""
    if pix.n - pix.alpha != 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if not pix.alpha:
        pix = fitz.Pixmap(pix, 1)
    return pix


def recolor_pixmap(pix: fitz.Pixmap, color) -> fitz.Pixmap:
    """画像印を単色に塗り直す。各ピクセルの「インク量」= alpha × (1 - min(R,G,B))。
    白は 0、朱・黒などは 1 に近い。アンチエイリアス部分は中間になる。"""
    pix = _as_rgba(pix)
    # MuPDF の alpha 付き samples は事前乗算済み (RGB <= alpha)。出力も同じ形式で作る
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4).astype(np.float32) / 255
    ink = np.clip(arr[:, :, 3] - arr[:, :, :3].min(axis=2), 0, 1)
    out = np.empty_like(arr)
    for c in range(3):
        out[:, :, c] = color[c] * ink
    out[:, :, 3] = ink
    return fitz.Pixmap(fitz.csRGB, pix.width, pix.height, (out * 255 + 0.5).astype(np.uint8).tobytes(), True)


def _post_process(pix: fitz.Pixmap, opacity: float, kasure: float, seed: int | None) -> fitz.Pixmap:
    # MuPDF の alpha 付き Pixmap は事前乗算済みなので RGB も同じ係数で減らす
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).astype(np.float32)
    factor = np.ones((pix.height, pix.width), dtype=np.float32)
    if kasure > 0:
        rng = np.random.default_rng(seed)
        cell = max(2, int(min(pix.width, pix.height) / 60))
        gh, gw = pix.height // cell + 1, pix.width // cell + 1
        noise = rng.random((gh, gw)).astype(np.float32)
        noise = np.kron(noise, np.ones((cell, cell), dtype=np.float32))[: pix.height, : pix.width]
        # 閾値以下のセルを薄くする（完全に消さない）
        factor *= np.where(noise < kasure * 0.6, 0.25 + noise, 1.0)
    factor *= opacity
    arr *= factor[:, :, None]
    out = np.clip(arr, 0, 255).astype(np.uint8)
    return fitz.Pixmap(fitz.csRGB, pix.width, pix.height, out.tobytes(), True)


def image_to_stamp_png(path: str | bytes, knockout_white: bool = True, threshold: int = 200) -> bytes:
    """スキャン画像などから印影 PNG を作る。白地を透過し、朱色成分を残す。path は画像データ (bytes) でも可。"""
    pix = fitz.Pixmap(path)
    if pix.n - pix.alpha != 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if not pix.alpha:
        pix = fitz.Pixmap(pix, 1)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4).copy()
    if knockout_white:
        rgb = arr[:, :, :3].astype(np.int16)
        lum = rgb.mean(axis=2)
        # 明るさに応じて滑らかに透過（threshold 以上は完全透過）
        a = np.clip((threshold - lum) / max(threshold - 60, 1), 0, 1) * 255
        new_a = np.minimum(arr[:, :, 3], a.astype(np.uint8))
        # samples は事前乗算済みなので、alpha を下げた分だけ RGB も下げる（でないと縁の色が壊れる）
        old_a = np.maximum(arr[:, :, 3].astype(np.float32), 1)
        arr[:, :, :3] = (arr[:, :, :3].astype(np.float32) * (new_a / old_a)[:, :, None] + 0.5).astype(np.uint8)
        arr[:, :, 3] = new_a
    out = fitz.Pixmap(fitz.csRGB, pix.width, pix.height, arr.tobytes(), True)
    return out.tobytes("png")


# ---------------------------------------------------------------- 保存
def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "PdfPageEditor")
    os.makedirs(d, exist_ok=True)
    return d


def default_stamps() -> list[Stamp]:
    return [
        Stamp(name="認印（山田）", kind="mitome", lines=["山田"], size_mm=11),
        Stamp(name="データネーム印", kind="datename", lines=["承認", "山田"], size_mm=16),
        Stamp(name="角印", kind="kaku", lines=["株式会社", "〇〇病院", "之印"], size_mm=18, border=0.5),
        Stamp(name="済", kind="user", lines=["済"], size_mm=10),
        Stamp(name="住所印", kind="address", lines=["医療法人〇〇会 〇〇病院", "長野県上田市〇〇1-2-3", "TEL 0268-00-0000"], size_mm=50, height_mm=14, border=0.3),
    ]


class StampLibrary:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(config_dir(), "stamps.json")
        self.stamps: list[Stamp] = []
        self.load()

    def load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.stamps = [Stamp.from_dict(d) for d in data.get("stamps", [])]
        else:
            self.stamps = default_stamps()
            self.save()

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "stamps": [s.to_dict() for s in self.stamps]}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def export(self, path: str, stamps: list[Stamp] | None = None) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "stamps": [s.to_dict() for s in (stamps or self.stamps)]}, f, ensure_ascii=False, indent=2)

    def import_file(self, path: str) -> int:
        """本アプリの JSON、または「おまかせ電子印鑑 for Business」の .entstmp を取り込む。"""
        if os.path.splitext(path)[1].lower() == ".entstmp":
            items = [s.to_dict() for s in import_entstmp(path)]
        else:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("stamps", data if isinstance(data, list) else [])
        ids = {s.id for s in self.stamps}
        n = 0
        for d in items:
            s = Stamp.from_dict(d)
            if s.id in ids:
                s.id = uuid.uuid4().hex
            self.stamps.append(s)
            n += 1
        self.save()
        return n


def import_entstmp(path: str, size_mm: float = 15.0) -> list[Stamp]:
    """「おまかせ電子印鑑 for Business」(EntrustStampForBusiness) の .entstmp を読む。

    実体は UTF-8 の XML で、<stampEntity image="base64 PNG" ...> が印影。
    物理サイズは記録されていないため size_mm は既定値を入れ、後から編集してもらう。
    画像にアルファが無ければ白地を透過する。文字だけの印鑑（image 属性なし）は対象外。
    """
    import xml.etree.ElementTree as ET
    tree = ET.parse(path)
    root = tree.getroot()
    if root.tag != "ENTSTMP":
        raise ValueError("おまかせ電子印鑑の印影ファイル (.entstmp) ではありません。")
    base = os.path.splitext(os.path.basename(path))[0]
    entities = [e for e in root.iter("stampEntity") if e.get("image")]
    if not entities:
        raise ValueError("画像の印影が含まれていません（文字だけの印鑑は未対応）。")
    out = []
    for i, e in enumerate(entities):
        png = base64.b64decode(e.get("image"))
        pix = fitz.Pixmap(png)
        if not pix.alpha:
            png = image_to_stamp_png(png)
        name = base if len(entities) == 1 else f"{base} {i + 1}"
        # 画像は色なしのマスク（グレー）で入っていることがあり、元ソフトは col 属性 (AARRGGBB) の色で塗って表示する
        color = _parse_argb(e.get("col")) or list(SHU)
        out.append(Stamp(name=name, kind="image", lines=[], size_mm=size_mm, color=color, recolor=True,
                         image_b64=base64.b64encode(png).decode("ascii")))
    return out


def _parse_argb(text: str | None) -> list[float] | None:
    """"ffd80016" (AARRGGBB) または "d80016" (RRGGBB) を 0〜1 の RGB にする。"""
    if not text:
        return None
    h = text.strip().lstrip("#")
    if len(h) == 8:
        h = h[2:]
    if len(h) != 6:
        return None
    try:
        return [int(h[k:k + 2], 16) / 255 for k in (0, 2, 4)]
    except ValueError:
        return None


def random_tilt(max_deg: float) -> float:
    return random.uniform(-max_deg, max_deg) if max_deg > 0 else 0.0
