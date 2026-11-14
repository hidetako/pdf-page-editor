"""差し替え枠のテスト（Qt 非依存）。ルール（日付・決まった文字・毎回入力・独自ルール）と、PDF への保存・入れ替え。

実行: python tests/test_fields.py
"""
import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf as fitz

import fields
from fields import Rule, RuleBook
from pdfmodel import PdfModel

D = datetime.date


def test_dates():
    d = D(2026, 11, 14)        # 土曜日
    assert fields.format_date(d) == "2026年11月14日" and fields.format_date(d, "ymd_w") == "2026年11月14日（土）"
    assert fields.format_date(d, "wareki") == "令和8年11月14日" and fields.format_date(d, "wareki_w") == "令和8年11月14日（土）"
    assert fields.format_date(d, "slash") == "2026/11/14" and fields.format_date(d, "md") == "11月14日"
    assert fields.format_date(d, "wareki_short") == "R8.11.14" and fields.format_date(d, "no-such") == "2026年11月14日"
    assert fields.format_date(D(2026, 1, 5), "slash") == "2026/01/05"
    assert fields.format_date(D(2019, 5, 1), "wareki") == "令和元年5月1日", "令和の最初の年は元年"
    assert fields.format_date(D(2019, 4, 30), "wareki") == "平成31年4月30日" and fields.format_date(D(2019, 4, 30), "wareki_short") == "H31.4.30"
    assert [fields.format_date(D(2026, 11, 9) + datetime.timedelta(days=i), "ymd_w")[-2] for i in range(7)] == list("月火水木金土日")
    print("  dates OK")


def test_rules(tmp):
    today = D(2026, 11, 14)
    assert fields.resolve(Rule("d", "日付", "date"), today) == "2026年11月14日"
    assert fields.resolve(Rule("d", "期限", "date", fmt="wareki", offset=7), today) == "令和8年11月21日", "何日後"
    assert fields.resolve(Rule("d", "前日", "date", fmt="slash", offset=-14), today) == "2026/10/31", "月をまたぐ"
    assert fields.resolve(Rule("f", "送付元", "fixed", "△△病院\n外科"), today) == "△△病院\n外科"
    assert fields.resolve(Rule("a", "宛先", "ask", "前回の宛先"), today) == "", "毎回入力は、覚えない設定なら空"
    assert fields.resolve(Rule("a", "担当", "ask", "佐藤", remember=True), today) == "佐藤"
    assert fields.placeholder("宛先") == "〔宛先〕" and fields.is_placeholder("〔宛先〕", "宛先") and not fields.is_placeholder("宛先", "宛先")
    # おかしな値は直す
    r = Rule("x", "  ", "no-such-kind", None, "no-such-fmt", "abc", 1).normalized()
    assert (r.name, r.kind, r.value, r.fmt, r.offset, r.remember) == ("名前なし", "ask", "", "ymd", 0, True)
    assert Rule("x", "n", "date", offset=99999).normalized().offset == 3660

    # 最初のルール（FAX の送付状でよく使うもの）
    book = RuleBook()
    names = [r.name for r in book.rules]
    assert names[:4] == ["日付", "宛先", "送付元", "住所"] and book.get("date").kind == "date" and book.get("from").kind == "fixed"
    assert book.get("to").kind == "ask" and not book.get("to").remember and book.get("no-such") is None

    # 独自ルールの追加・変更・削除と、保存して読み直し
    path = os.path.join(tmp, "cfg", "rules.json")
    book = RuleBook(path)
    assert len(book.rules) == len(names), "ファイルが無ければ最初のルール"
    mine = book.add("担当者", "fixed", value="外科 山田")
    limit = book.add("回答期限", "date", fmt="wareki_w", offset=7)
    assert mine.id != limit.id and mine.id.startswith("u")
    book.update(Rule("from", "送付元", "fixed", "△△病院 外科"))
    book.remove("tel")
    book.save()
    again = RuleBook(path)
    assert [r.name for r in again.rules] == [n for n in names if n != "電話番号"] + ["担当者", "回答期限"]
    assert again.get(mine.id).value == "外科 山田" and again.get("from").value == "△△病院 外科"
    assert fields.resolve(again.get(limit.id), today) == "令和8年11月21日（土）"
    # 壊れたファイルは無視して、最初のルールで続ける
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ 壊れている")
    assert [r.name for r in RuleBook(path).rules] == names

    # 差し替え印刷で入力した内容: 決まった文字は覚える。毎回入力は「覚えておく」のものだけ。日付は覚えない
    book = RuleBook()
    keep = book.add("担当", "ask", remember=True)
    assert book.remember_values({"from": "△△病院", "to": "〇〇クリニック", keep.id: "佐藤", "date": "2027年1月1日", "zzz": "x"})
    assert book.get("from").value == "△△病院" and book.get("to").value == "" and book.get(keep.id).value == "佐藤"
    assert fields.resolve(book.get("date"), today) == "2026年11月14日"
    assert not book.remember_values({"from": "△△病院"}), "同じ内容なら変更なし"

    # PDF に保存する写しと、ルールが無い PC での扱い
    spec = fields.field_spec(limit)
    assert spec == {"id": limit.id, "name": "回答期限", "kind": "date", "fmt": "wareki_w", "offset": 7}
    other = RuleBook()                              # 別の PC（このルールを持っていない）
    r = other.for_spec(spec)
    assert r.kind == "date" and fields.resolve(r, today) == "令和8年11月21日（土）", "日付は、ルールが無くても更新できる"
    r = other.for_spec(fields.field_spec(mine))
    assert r.name == "担当者" and fields.resolve(r, today) == "", "決まった文字は、その PC に内容が無ければ入力してもらう"
    assert other.for_spec(fields.field_spec(other.get("from"))) is other.get("from")
    print("  rules OK")


def test_model(tmp):
    today = D(2026, 11, 14)
    book = RuleBook()
    src = os.path.join(tmp, "fax.pdf")
    d = fitz.open(); d.new_page(); d.new_page(); d.save(src); d.close()
    for rot in (0, 90, 180, 270):
        m = PdfModel(); m.open(src); m.doc[0].set_rotation(rot)
        rd, rt, rf = book.get("date"), book.get("to"), book.get("from")
        xd = m.add_text_box(0, (300, 80), fields.resolve(rd, today), fontsize=12, color=(0, 0, 0), field=fields.field_spec(rd))
        xt = m.add_text_box(0, (80, 150), fields.placeholder(rt.name), fontsize=14, color=(0, 0, 0), wrap_width=220,
                            border=(0, 0, 1), border_style="dotted", field=fields.field_spec(rt))
        m.add_text_box(1, (80, 150), fields.placeholder(rf.name), fontsize=12, field=fields.field_spec(rf))
        m.add_text_box(0, (80, 300), fields.resolve(rd, today), fontsize=10, field=fields.field_spec(rd))      # 同じルールの枠をもう 1 つ
        plain = m.add_text_box(0, (80, 400), "ふつうの文字", fontsize=12)
        fs = m.fields()
        assert [(f["index"], f["field"]["name"]) for f in fs] == [(0, "日付"), (0, "宛先"), (0, "日付"), (1, "送付元")], (rot, fs)
        assert m.annot_info(0, xd)["kind"] == "差し替え枠" and m.annot_info(0, plain)["kind"] == "文字" and m.annot_info(0, plain)["field"] is None
        to = m.annot_info(0, xt)
        assert round(to["rect"].width) == 220 and to["border_style"] == "dotted" and to["field"]["id"] == "to"

        # 日付の自動更新: 元に戻す対象にも「変更あり」にもしない。同じルールの枠はまとめて変わる
        m.modified = False
        undo_before = len(m._undo)
        n = m.set_field_texts({"date": fields.resolve(rd, D(2026, 11, 15))}, undo=False, mark_modified=False)
        assert n == 2 and not m.modified and len(m._undo) == undo_before
        assert [f["text"] for f in m.fields() if f["field"]["id"] == "date"] == ["2026年11月15日"] * 2
        assert m.set_field_texts({"date": "2026年11月15日"}, undo=False, mark_modified=False) == 0, "同じ内容なら何もしない"
        assert len([a for a in m.annots_on_page(0)]) == 4 and m.annot_info(0, plain)["text"] == "ふつうの文字", "ほかの書き込みはそのまま"

        # 差し替え印刷: 複製に中身を入れる。開いている文書は変わらない
        c = m.snapshot()
        n = c.set_field_texts({"to": "〇〇クリニック 御中\n院長 山田 太郎 先生", "from": "", "date": "2026年12月1日", "zzz": "無い枠"},
                              undo=False, mark_modified=False)
        assert n == 4, n
        got = {(f["index"], f["field"]["name"], f["text"]) for f in c.fields()}
        assert (0, "宛先", "〇〇クリニック 御中\n院長 山田 太郎 先生") in got and (1, "送付元", "") in got and (0, "日付", "2026年12月1日") in got, got
        cto = [a for a in c.annots_on_page(0) if a["field"] and a["field"]["id"] == "to"][0]
        assert round(cto["rect"].width) == 220 and cto["rect"].height > to["rect"].height, "ドラッグで決めた幅のまま、行が増えたぶん下へ伸びる"
        assert abs(cto["rect"].x0 - to["rect"].x0) < 0.01 and abs(cto["rect"].y0 - to["rect"].y0) < 0.01, "左上は動かない"
        assert cto["border_style"] == "dotted" and cto["fontsize"] == 14, "見た目（枠・大きさ）は引き継ぐ"
        assert [f["text"] for f in m.fields()] == ["2026年11月15日", "〔宛先〕", "2026年11月15日", "〔送付元〕"], "元の文書はそのまま"
        out = os.path.join(tmp, f"filled_{rot}.pdf")
        c.save(out); c.close()
        o = PdfModel(); o.open(out)
        assert {f["text"] for f in o.fields()} == {"2026年12月1日", "〇〇クリニック 御中\n院長 山田 太郎 先生", ""}
        text0 = o.doc[0].get_text()
        o.close()
        assert "〔宛先〕" not in text0, "入れ替えたあとに目印は残らない"

        # ルールを別のものに変える（元に戻せる）
        x = m.set_field_rule(0, [f for f in m.fields() if f["field"]["id"] == "to"][0]["xref"], fields.field_spec(book.get("subject")), "〔件名〕")
        a = m.annot_info(0, x)
        assert a["field"]["name"] == "件名" and a["text"] == "〔件名〕" and round(a["rect"].width) == 220 and m.modified
        m.undo()
        assert [f["field"]["name"] for f in m.fields()] == ["日付", "宛先", "日付", "送付元"]

        # 雛形として保存して開き直しても、枠とルールの写しが残る
        tpl = os.path.join(tmp, f"tpl_{rot}.pdf")
        m.save(tpl); m.close()
        t = PdfModel(); t.open(tpl)
        assert [(f["index"], f["field"]["id"], f["field"]["kind"]) for f in t.fields()] == [(0, "date", "date"), (0, "to", "ask"), (0, "date", "date"), (1, "from", "fixed")]
        t.close()
    assert PdfModel().fields() == []
    print("  model OK")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_dates()
        test_rules(tmp)
        test_model(tmp)
    print("fields tests passed")


if __name__ == "__main__":
    main()
