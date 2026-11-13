"""書き込み用フォント（Qt 非依存）。

文字の書き込み（テキスト注釈）の見た目を、PDF に埋め込んだフォントで描くための部品。
- 「標準ゴシック」は PyMuPDF 内蔵の日本語フォント（どの PC でも使える）
- それ以外は PC に入っている TrueType フォント（メイリオなど）。ファイルを読んで、使った文字だけを PDF に埋め込む
- 埋め込めないフォント（必要な文字だけに絞れず PDF が極端に大きくなるもの）は一覧に出さない
"""
from __future__ import annotations

import html
import json
import os

import pymupdf as fitz

BUILTIN = "builtin"
PAD = 3.0            # テキストボックスの内側の余白 (pt)
MAX_SUBSET_BYTES = 600_000   # 1 つのテキストボックスがこれより大きくなるフォントは使わない

# (キー, 表示名, 候補ファイル名)。Windows の標準フォントのうち TrueType 形式のゴシック系
_CANDIDATES: list[tuple[str, str, tuple[str, ...]]] = [
    ("meiryo", "メイリオ", ("meiryo.ttc",)),
    ("meiryo-bold", "メイリオ 太字", ("meiryob.ttc",)),
    ("yugothic", "游ゴシック", ("YuGothR.ttc", "YuGothM.ttc")),
    ("yugothic-bold", "游ゴシック 太字", ("YuGothB.ttc",)),
    ("bizudgothic", "BIZ UDゴシック", ("BIZ-UDGothicR.ttc",)),
    ("msgothic", "MS ゴシック", ("msgothic.ttc",)),
]
LABELS = {BUILTIN: "標準ゴシック", **{k: label for k, label, _ in _CANDIDATES}}

_paths: dict[str, str] = {}                 # キー → フォントファイルのパス（PC のフォント）
_data: dict[str, bytes] = {}                # キー → フォントの中身
_archives: dict[str, fitz.Archive] = {}     # キー → HTML 描画エンジンに渡す入れ物（中身はメモリ上）
_font_objs: dict[str, fitz.Font] = {}
_available: list[tuple[str, str]] | None = None
_cache_file: str | None = None              # 「埋め込めるか」の確認結果を覚えておくファイル（無ければ毎回確かめる）


def set_cache_file(path: str | None) -> None:
    """フォントの確認結果を保存するファイルを決める。確認は 1 フォントあたり 1 秒近くかかることがあるので、
    アプリでは保存しておき、フォントファイルが変わらない限り 2 回目以降は確かめ直さない。"""
    global _cache_file
    _cache_file = path


def font_dirs() -> list[str]:
    """フォントを探すフォルダー。PDFPE_FONT_DIRS（os.pathsep 区切り）を最優先にする。
    PDFPE_FONT_DIRS_ONLY=1 なら PC のフォントフォルダーは見ない（テストで実機のフォントに左右されないように）。"""
    dirs = [d for d in os.environ.get("PDFPE_FONT_DIRS", "").split(os.pathsep) if d]
    if os.environ.get("PDFPE_FONT_DIRS_ONLY") == "1":
        return [d for d in dirs if os.path.isdir(d)]
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        dirs.append(os.path.join(windir, "Fonts"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    return [d for d in dirs if os.path.isdir(d)]


def font_path(key: str) -> str | None:
    """PC のフォントのファイルのパス。内蔵フォント・見つからないフォントは None。"""
    if key in _paths:
        return _paths[key]
    for k, _label, names in _CANDIDATES:
        if k != key:
            continue
        for d in font_dirs():
            for name in names:
                path = os.path.join(d, name)
                if os.path.isfile(path):
                    _paths[key] = path
                    return path
    return None


def _font_data(key: str) -> bytes:
    """フォントの中身。ファイルは Python で読む（フォルダー名に日本語が入っていても確実に読めるように）。"""
    if key not in _data:
        path = font_path(key) if key != BUILTIN else None
        if path is None:
            _data[key] = fitz.Font("japan").buffer
        else:
            with open(path, "rb") as f:
                _data[key] = f.read()
    return _data[key]


def _archive(key: str) -> fitz.Archive:
    if key not in _archives:
        arch = fitz.Archive()
        arch.add(_font_data(key), "font.bin")
        _archives[key] = arch
    return _archives[key]


def _load_cache() -> dict:
    if not _cache_file:
        return {}
    try:
        with open(_cache_file, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    if not _cache_file:
        return
    try:
        os.makedirs(os.path.dirname(_cache_file), exist_ok=True)
        tmp = _cache_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _cache_file)
    except OSError:
        pass


def _check(key: str) -> bool:
    """実際に 1 回描いてみて、必要な文字だけに絞って埋め込めるフォントか確かめる。"""
    try:
        src, _w, _h = _render("確認 ABC 123", key, 12, (0, 0, 0))
        size = len(src.tobytes(garbage=3, deflate=True))
        text = src[0].get_text()
        src.close()
        return size <= MAX_SUBSET_BYTES and "確認" in text
    except Exception:
        return False


def _usable(key: str, cache: dict) -> bool:
    path = font_path(key)
    if path is None:
        return False
    try:
        st = os.stat(path)
        stamp = [path, st.st_size, int(st.st_mtime), fitz.VersionBind]
    except OSError:
        return False
    hit = cache.get(key)
    if isinstance(hit, dict) and hit.get("file") == stamp and isinstance(hit.get("ok"), bool):
        return hit["ok"]
    ok = _check(key)
    if not ok:                                  # 使わないフォントの中身は持ち続けない
        _data.pop(key, None); _archives.pop(key, None); _font_objs.pop(key, None)
    cache[key] = {"file": stamp, "ok": ok}
    return ok


def available_fonts(refresh: bool = False) -> list[tuple[str, str]]:
    """使えるフォントの (キー, 表示名) 一覧。先頭は必ず標準ゴシック。"""
    global _available
    if _available is None or refresh:
        if refresh:
            _paths.clear(); _data.clear(); _archives.clear(); _font_objs.clear()
        cache = _load_cache()
        before = json.dumps(cache, sort_keys=True)
        out = [(BUILTIN, LABELS[BUILTIN])]
        for key, label, _names in _CANDIDATES:
            if _usable(key, cache):
                out.append((key, label))
        if json.dumps(cache, sort_keys=True) != before:
            _save_cache(cache)
        _available = out
    return list(_available)


def resolve(key: str | None) -> str:
    """使えないキー（他の PC で作った注釈のフォントなど）は標準ゴシックに置き換える。"""
    keys = [k for k, _ in available_fonts()]
    return key if key in keys else BUILTIN


def _font(key: str) -> fitz.Font:
    if key not in _font_objs:
        _font_objs[key] = fitz.Font(fontbuffer=_font_data(key))
    return _font_objs[key]


def _css(key: str, fontsize: float, color: tuple[float, float, float]) -> tuple[str, fitz.Archive]:
    rgb = "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in color[:3])
    css = ("@font-face {font-family: ppe; src: url(font.bin);} "
           f"body {{font-family: ppe; font-size: {fontsize:g}pt; color: {rgb}; margin: 0; padding: 0;}} "
           "p {margin: 0; padding: 0; white-space: pre-wrap;}")
    return css, _archive(key)


def _html(text: str) -> str:
    lines = [html.escape(ln) if ln else "&#160;" for ln in text.split("\n")]
    return "<p>" + "<br/>".join(lines) + "</p>"


def measure(text: str, key: str, fontsize: float, wrap_width: float | None = None,
            max_width: float = 2000.0) -> tuple[float, float]:
    """テキストボックスの (幅, 高さ) pt。wrap_width 指定時はその幅で折り返したときの高さを返す。"""
    return _measure(text, resolve(key), fontsize, wrap_width, max_width)


def _measure(text: str, key: str, fontsize: float, wrap_width: float | None, max_width: float) -> tuple[float, float]:
    if wrap_width is None:
        f = _font(key)
        natural = max((f.text_length(ln, fontsize) for ln in text.split("\n")), default=0.0)
        width = min(max_width, natural + 2 * PAD + 2.0)
    else:
        width = wrap_width
    width = max(width, fontsize + 2 * PAD)
    css, arch = _css(key, fontsize, (0, 0, 0))
    story = fitz.Story(html=_html(text), user_css=css, archive=arch)
    _more, filled = story.place(fitz.Rect(0, 0, width - 2 * PAD, 100000))
    height = max(fitz.Rect(filled).height, fontsize * 1.2) + 2 * PAD
    return width, height


def render_text_pdf(text: str, key: str, fontsize: float, color: tuple[float, float, float],
                    wrap_width: float | None = None, fill: tuple[float, float, float] | None = None,
                    border: tuple[float, float, float] | None = None, border_width: float = 1.0,
                    rotation: int = 0, max_width: float = 2000.0) -> tuple[fitz.Document, float, float]:
    """テキストボックス 1 つぶんの小さな PDF を作る。戻り値は (PDF, 幅, 高さ)。幅・高さは回転前の表示上の大きさ。

    フォントは使った文字だけに絞って埋め込む。rotation（ページの回転）が 0 以外のときは、
    回転したページの上で正立して見えるよう、あらかじめ逆向きに回した PDF を返す。
    """
    return _render(text, resolve(key), fontsize, color, wrap_width, fill, border, border_width, rotation, max_width)


def _render(text: str, key: str, fontsize: float, color, wrap_width=None, fill=None, border=None,
            border_width: float = 1.0, rotation: int = 0, max_width: float = 2000.0) -> tuple[fitz.Document, float, float]:
    w, h = _measure(text, key, fontsize, wrap_width, max_width)
    css, arch = _css(key, fontsize, color)
    tmp = fitz.open()
    page = tmp.new_page(width=w, height=h)
    if fill is not None or border is not None:
        half = border_width / 2 if border is not None else 0
        page.draw_rect(page.rect + (half, half, -half, -half), color=border, fill=fill,
                       width=border_width if border is not None else 0)
    page.insert_htmlbox(page.rect + (PAD, PAD, -PAD, -PAD), _html(text), css=css, archive=arch, scale_low=1)
    tmp.subset_fonts()
    if rotation % 360:
        rot = rotation % 360
        turned = fitz.open()
        swap = rot in (90, 270)
        tp = turned.new_page(width=h if swap else w, height=w if swap else h)
        tp.show_pdf_page(tp.rect, tmp, 0, rotate=rot)
        tmp.close()
        return turned, w, h
    return tmp, w, h
