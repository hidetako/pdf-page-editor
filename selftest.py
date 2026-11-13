"""配布用 exe の自己診断（CI 用、Qt 非依存）。

PyInstaller でまとめた exe の中でも、書き込み機能（手書き・図形・埋め込みフォントの文字）と保存が動くかを確かめる。
`PdfPageEditor.exe --selftest 記録ファイル` で実行する。画面は出さず、結果を記録ファイルに書いて終了コードで知らせる
（0 = 成功、1 = 失敗）。配布版は画面付きのアプリで標準出力が無いので、ファイルに書く。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import traceback


def run(log_path: str | None = None) -> int:
    lines: list[str] = []
    code = 1
    tmp = tempfile.mkdtemp(prefix="pdfpe_selftest_")
    try:
        import pymupdf as fitz

        import fonts
        from pdfmodel import PdfModel

        lines.append(f"PyMuPDF {fitz.VersionBind}")
        src = os.path.join(tmp, "in.pdf")
        d = fitz.open()
        page = d.new_page(width=595, height=842)
        page.insert_text((72, 100), "selftest", fontsize=20)
        del page
        d.save(src)
        d.close()

        m = PdfModel()
        m.open(src)
        m.add_ink(0, [[(100, 150), (150, 190), (200, 160)]])
        m.add_line(0, (100, 220), (200, 260), arrow=True)
        m.add_shape(0, "rect", (100, 280, 200, 320))
        m.add_shape(0, "ellipse", (220, 280, 320, 320), fill=(1, 1, 0.6))
        available = fonts.available_fonts(refresh=True)
        lines.append("fonts: " + ", ".join(label for _key, label in available))
        y = 360.0
        for key, label in available:
            x = m.add_text_box(0, (72, y), f"{label} 自己診断 ABC 123", font=key, fontsize=14)
            x = m.move_annot(0, x, 10, 0)
            info = m.annot_info(0, x)
            if info is None or info["font"] != key:
                raise RuntimeError(f"text box with font {key} was not created")
            y += 40
        out = os.path.join(tmp, "out.pdf")
        m.save(out)
        m.close()
        size = os.path.getsize(out)
        d = fitz.open(out)
        page = d[0]
        kinds = sorted(a.type[1] for a in page.annots())
        del page
        d.close()
        lines.append(f"saved {size} bytes, annots: {', '.join(kinds)}")
        if kinds.count("FreeText") != len(available) or "Ink" not in kinds or "Line" not in kinds:
            raise RuntimeError("annotations missing after save")
        if size > 400_000 * max(1, len(available)):
            raise RuntimeError(f"output too large ({size} bytes): fonts were not subset")
        lines.append("OK")
        code = 0
    except Exception:
        lines.append("FAILED")
        lines.append(traceback.format_exc())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    text = "\n".join(lines) + "\n"
    if log_path:
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
    else:
        print(text, end="")
    return code
