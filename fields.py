"""差し替え枠のルール（Qt 非依存）。

「差し替え枠」は、FAX の送付状のような雛形 PDF の上に置く「中身を入れ替える欄」。枠ごとにルールを 1 つ選ぶ。
- 日付（date）   … 開いたとき・印刷するときに、その日の日付に自動で更新する。書式と「何日後」を選べる
- 決まった文字（fixed）… 送付元・住所・電話番号など、毎回同じ内容。一度入れたら覚えておく
- 毎回入力（ask）… 宛先・件名・枚数など、印刷のたびに入力する。前回の内容を覚えておくかどうかを選べる

ルールは PC ごとの設定ファイル（rules.json）に保存し、どの PDF からも使える。利用者が自分のルールを足せる。
PDF の中の枠には、ルールの番号・名前・種類・書式を写して保存する。ルールが無い PC で開いても、
日付は更新でき、それ以外は名前を見て入力できる。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from dataclasses import asdict, dataclass, field

KINDS = ("date", "fixed", "ask")
KIND_LABELS = {"date": "日付（自動で今日の日付）", "fixed": "決まった文字（毎回同じ）", "ask": "毎回入力する"}
WEEKDAYS = "月火水木金土日"

# (キー, 表示例の作り方)。表示例は format_date で作る
HISTORY_MAX = 12             # 決まった文字の履歴を、ルールごとに何件まで覚えるか

DATE_FORMATS = ("ymd", "ymd_w", "wareki", "wareki_w", "slash", "md", "wareki_short")
DEFAULT_DATE_FORMAT = "ymd"


def _wareki(d: _dt.date) -> tuple[str, str, int]:
    """(元号, 略号, 年)。令和・平成だけ扱う（それより前の日付を書類に入れることは無い想定）。"""
    if d >= _dt.date(2019, 5, 1):
        return "令和", "R", d.year - 2018
    return "平成", "H", d.year - 1988


def format_date(d: _dt.date, fmt: str = DEFAULT_DATE_FORMAT) -> str:
    wd = WEEKDAYS[d.weekday()]
    era, short, y = _wareki(d)
    ys = "元" if y == 1 else str(y)
    if fmt == "ymd_w":
        return f"{d.year}年{d.month}月{d.day}日（{wd}）"
    if fmt == "wareki":
        return f"{era}{ys}年{d.month}月{d.day}日"
    if fmt == "wareki_w":
        return f"{era}{ys}年{d.month}月{d.day}日（{wd}）"
    if fmt == "slash":
        return f"{d.year}/{d.month:02d}/{d.day:02d}"
    if fmt == "md":
        return f"{d.month}月{d.day}日"
    if fmt == "wareki_short":
        return f"{short}{y}.{d.month}.{d.day}"
    return f"{d.year}年{d.month}月{d.day}日"


@dataclass
class Rule:
    id: str
    name: str
    kind: str = "ask"            # date / fixed / ask
    value: str = ""              # fixed: 入れる文字。ask: 最初から入れておく文字（覚えておく場合は前回の内容）
    fmt: str = DEFAULT_DATE_FORMAT   # date: 書式
    offset: int = 0              # date: 何日後（マイナスは何日前）
    remember: bool = False       # ask: 前回入力した内容を覚えておく
    history: list = field(default_factory=list)   # fixed: これまでに使った内容（新しい順）。差し替え印刷の画面で選び直せる

    def push_history(self, value: str) -> None:
        """使った内容を履歴の先頭に入れる（同じ内容は 1 つにまとめ、古いものから捨てる）。空は入れない。"""
        value = str(value or "").strip()
        if not value:
            return
        self.history = ([value] + [h for h in self.history if h != value])[:HISTORY_MAX]

    def normalized(self) -> "Rule":
        kind = self.kind if self.kind in KINDS else "ask"
        fmt = self.fmt if self.fmt in DATE_FORMATS else DEFAULT_DATE_FORMAT
        try:
            offset = int(self.offset)
        except (TypeError, ValueError):
            offset = 0
        history: list[str] = []
        for h in (self.history if isinstance(self.history, (list, tuple)) else []):
            h = h.strip() if isinstance(h, str) else ""
            if h and h not in history:
                history.append(h)
        return Rule(str(self.id), str(self.name).strip() or "名前なし", kind, str(self.value or ""), fmt,
                    max(-3660, min(3660, offset)), bool(self.remember), history[:HISTORY_MAX] if kind == "fixed" else [])


def default_rules() -> list[Rule]:
    """最初から入っているルール（FAX の送付状でよく使うもの）。どれも直したり消したりできる。"""
    return [
        Rule("date", "日付", "date"),
        Rule("to", "宛先", "ask"),
        Rule("from", "送付元", "fixed"),
        Rule("address", "住所", "fixed"),
        Rule("tel", "電話番号", "fixed"),
        Rule("fax", "FAX番号", "fixed"),
        Rule("subject", "件名", "ask"),
        Rule("pages", "枚数", "ask"),
        Rule("message", "連絡事項", "ask"),
    ]


def new_rule_id() -> str:
    return "u" + uuid.uuid4().hex[:10]


def placeholder(name: str) -> str:
    """中身がまだ無い枠に、画面上で出しておく目印。差し替え印刷では、空のままなら何も印刷しない。"""
    return f"〔{name}〕"


def is_placeholder(text: str, name: str) -> bool:
    return text == placeholder(name)


def resolve(rule: Rule, today: _dt.date | None = None) -> str:
    """ルールから、枠に入れる文字を決める。決まっていない（入力が要る）ときは空文字。"""
    if rule.kind == "date":
        d = (today or _dt.date.today()) + _dt.timedelta(days=rule.offset)
        return format_date(d, rule.fmt)
    if rule.kind == "fixed":
        return rule.value
    return rule.value if rule.remember else ""


def field_spec(rule: Rule) -> dict:
    """PDF の枠に保存する、ルールの写し。"""
    r = rule.normalized()
    return {"id": r.id, "name": r.name, "kind": r.kind, "fmt": r.fmt, "offset": r.offset}


def rule_from_spec(spec: dict) -> Rule:
    """PDF の枠に保存してある写しから、ルールを作る（この PC にそのルールが無いとき用）。"""
    return Rule(str(spec.get("id") or new_rule_id()), str(spec.get("name") or "枠"), str(spec.get("kind") or "ask"),
                "", str(spec.get("fmt") or DEFAULT_DATE_FORMAT), spec.get("offset") or 0, False).normalized()


class RuleBook:
    """ルールの一覧。設定ファイルに保存する。"""

    def __init__(self, path: str | None = None):
        self.path = path
        self.rules: list[Rule] = default_rules()
        if path:
            self.load()

    def load(self) -> None:
        """設定ファイルを読む。無い・壊れている・中身の形が違うときは、最初のルールのままにする
        （起動のたびに読むので、ここで例外を外へ出すとアプリが起動できなくなる）。"""
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("rules") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return
            rules = [Rule(**{k: v for k, v in item.items() if k in Rule.__dataclass_fields__}).normalized()
                     for item in items if isinstance(item, dict) and item.get("id")]
            if rules:
                self.rules = rules
        except Exception:
            pass

    def save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "rules": [asdict(r) for r in self.rules]}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def get(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rule_id), None)

    def for_spec(self, spec: dict) -> Rule:
        """PDF の枠のルール。この PC にあればそれ、無ければ枠に保存してある写しから作る。"""
        return self.get(str(spec.get("id"))) or rule_from_spec(spec)

    def add(self, name: str, kind: str = "ask", **kw) -> Rule:
        rule = Rule(new_rule_id(), name, kind, **kw).normalized()
        self.rules.append(rule)
        return rule

    def update(self, rule: Rule) -> None:
        rule = rule.normalized()
        for i, r in enumerate(self.rules):
            if r.id == rule.id:
                self.rules[i] = rule
                return
        self.rules.append(rule)

    def remove(self, rule_id: str) -> None:
        self.set_rules([r for r in self.rules if r.id != rule_id])

    def set_rules(self, rules: list[Rule]) -> None:
        """ルールを入れ替える。1 つも無いと枠を置けないので、空にされたときは最初のルールに戻す。"""
        self.rules = [r.normalized() for r in rules] or default_rules()
        for r in self.rules:
            if r.kind == "fixed":
                r.push_history(r.value)             # 今の内容も履歴に入れておく（あとで別の内容に変えても戻せるように）

    def remember_values(self, values: dict[str, str]) -> bool:
        """差し替え印刷で入力した内容を覚える。決まった文字は必ず、毎回入力は「覚えておく」のものだけ。変わったら True。"""
        changed = False
        for rule in self.rules:
            if rule.id not in values:
                continue
            v = values[rule.id]
            if (rule.kind == "fixed" or (rule.kind == "ask" and rule.remember)) and rule.value != v:
                if rule.kind == "fixed":
                    rule.push_history(rule.value)   # 前の内容も履歴に残す
                    rule.push_history(v)
                rule.value = v
                changed = True
        return changed
