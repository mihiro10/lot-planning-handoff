"""計画提案: suggest production plans from a 在庫表 workbook + live 米飯課 bookings (AppSheet).

Pure logic, no Streamlit. Used by app_pages/suggest.py.

Workbook: one sheet per planner group (e.g. 野末在庫) in the 10-row block layout
(備考, 計画（倍）, 入庫予定数, 入庫, 使用予測, 店, 通販, 米飯課, 調整, 最終), columns found
by header text. Stock is recomputed exactly like the sheet's 最終 formula.
AppSheet: Requests (TargetDate, Timeslot, EstTime, Requester, Status, LotCount) and
Products (RequiredTime per LotCount). Read-only.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

LOCK_DAYS = 4            # 4日前 11:00 rule
ROW_LABELS = ["備考", "計画（倍）", "入庫予定数", "入庫", "使用予測", "店", "通販", None, "調整", "最終"]
MODES = {
    "level": "米飯課平準化（おすすめ）",
    "lean": "在庫少なめ",
    "batch": "まとめ製造",
}


# ---------------------------------------------------------------- workbook

def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def _hdr(v):
    return re.sub(r"\s+", "", str(v)) if v is not None else ""


@dataclass
class Item:
    name: str
    status: str
    planner: str
    place: str
    lot: float
    unit: str
    min: float | None
    max: float | None
    shelf: float
    lead: int
    start: float
    days: list
    plan: list
    inc: list          # 入庫 overrides
    fcst: list
    actual: list       # 店+通販+row7 when any entered (COUNTA rule), else None
    adj: list
    note: list
    # filled later
    usual: float = 1.0
    rice_product: str | None = None
    rice_slot: str | None = None
    rice_min_per_bai: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def plannable(self):
        return self.status == "使用中" and bool(self.min) and bool(self.max) and bool(self.lot)


def planner_sheets(wb):
    """Sheets that have the 在庫表 header (行種別 + 品目名)."""
    out = []
    for ws in wb.worksheets:
        for r in ws.iter_rows(min_row=1, max_row=5, values_only=True):
            h = [_hdr(v) for v in r]
            if "行種別" in h and "品目名" in h:
                out.append(ws.title)
                break
    return out


def parse_sheet(ws):
    rows = list(ws.iter_rows(values_only=True))
    hi = next(i for i, r in enumerate(rows[:5]) if "行種別" in [_hdr(v) for v in r])
    hdr = [_hdr(v) for v in rows[hi]]

    def col(*prefixes):
        for p in prefixes:
            for i, h in enumerate(hdr):
                if h.startswith(p):
                    return i
        return None

    c = dict(status=col("状態"), planner=col("計画担当"), place=col("作業場所"), name=col("品目名"),
             lot=col("ロット（", "ロット("), unit=col("ロット単位"), min=col("最低在庫"), max=col("最高在庫"),
             shelf=col("出荷期限"), lead=col("入庫リードタイム"), start=col("在庫数"), kind=col("行種別"))
    dcols = [(i, v.date()) for i, v in enumerate(rows[hi]) if isinstance(v, dt.datetime)]
    days = [d for _, d in dcols]
    g = lambda r, k: r[c[k]] if c[k] is not None and c[k] < len(r) else None

    items = []
    for i in range(hi + 1, len(rows) - 9):
        r = rows[i]
        if g(r, "kind") != "備考" or not g(r, "name"):
            continue
        labels = [g(rows[i + k], "kind") for k in range(10)]
        if any(want and got != want for want, got in zip(ROW_LABELS, labels)):
            continue
        blk = [rows[i + k] for k in range(10)]
        val = lambda k: [_num(blk[k][ci]) if ci < len(blk[k]) else None for ci, _ in dcols]
        present = [any((blk[k][ci] if ci < len(blk[k]) else None) not in (None, "") for k in (5, 6, 7))
                   for ci, _ in dcols]
        s5, s6, s7 = val(5), val(6), val(7)
        actual = [(s5[d] or 0) + (s6[d] or 0) + (s7[d] or 0) if present[d] else None for d in range(len(days))]
        items.append(Item(
            name=str(g(r, "name")).strip(), status=str(g(r, "status") or ""), planner=str(g(r, "planner") or ""),
            place=str(g(r, "place") or ""), lot=_num(g(r, "lot")) or 0, unit=str(g(r, "unit") or ""),
            min=_num(g(r, "min")), max=_num(g(r, "max")), shelf=_num(g(r, "shelf")) or 20,
            lead=int(_num(g(r, "lead")) or 0), start=_num(g(r, "start")) or 0, days=days,
            plan=val(1), inc=val(3), fcst=val(4), actual=actual, adj=val(8),
            note=[blk[0][ci] if ci < len(blk[0]) else None for ci, _ in dcols]))
    return items


def load_workbook(data: bytes):
    return openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)


# ---------------------------------------------------------------- stock maths (same as the sheet)

def arrivals(it: Item, plan: list, x: int, use_inc: bool):
    if use_inc and it.inc[x] is not None:
        return it.inc[x]
    return (plan[x - it.lead] or 0) * it.lot if x - it.lead >= 0 else 0.0


def demand(it: Item, x: int, base: int):
    """Actual before the base day; 使用予測 (or trailing average) from the base day on."""
    if x < base and it.actual[x] is not None:
        return it.actual[x]
    if it.fcst[x] is not None:
        return it.fcst[x]
    # sheet rule: blank = 0. Only past the last day she forecast, assume the recent average.
    return it.extra.get("avg", 0.0) if x > it.extra.get("last_fcst", len(it.fcst)) else 0.0


def stock_series(it: Item, plan: list, base: int, upto: int, locked_until: int):
    """End-of-day stock for every day up to `upto`. 入庫 overrides apply before the first free day."""
    out, s = [], it.start
    for x in range(upto + 1):
        s = s + arrivals(it, plan, x, use_inc=x < locked_until) - demand(it, x, base) + (it.adj[x] or 0)
        out.append(s)
    return out


def prepare(items, base: int):
    """Usual 倍 and trailing average demand per item."""
    for it in items:
        lo = max(0, base - 60)
        c = Counter(v for v in it.plan[lo:base] if v)
        it.usual = c.most_common(1)[0][0] if c else 1.0
        act = [a for a in it.actual[max(0, base - 28):base] if a is not None]
        it.extra["avg"] = sum(act) / len(act) if act else (it.extra.get("avg") or 0.0)
        it.extra["step"] = it.usual if it.usual < 1 else 1.0
        it.extra["last_fcst"] = max((x for x, v in enumerate(it.fcst) if v is not None), default=-1)


# ---------------------------------------------------------------- AppSheet

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = s.replace("-", "ー").replace("－", "ー")
    return re.sub(r"\s+", "", s)


ALIASES = {"小袋国産鶏つくね": "小袋鶏つくね"}


def match_products(items, products):
    """Default sheet item -> AppSheet product (name match), editable in the UI."""
    by_norm = {_norm(p["ProductName"]): p for p in products if p.get("TaskName") != "予定ブロック"}
    for it in items:
        n = _norm(it.name)
        n = ALIASES.get(n, n)
        p = by_norm.get(n)
        if not p:
            cands = [p for k, p in by_norm.items() if k and (k in n or n in k)]
            p = cands[0] if len(cands) == 1 else None
        it.rice_product = p["ProductName"] if p else None


def apply_products(items, products, history=None):
    by_name = {p["ProductName"]: p for p in products}
    for it in items:
        p = by_name.get(it.rice_product or "")
        if p:
            it.rice_slot = p.get("DefaultTimeSlot") or "昼間"
            it.rice_min_per_bai = float(p.get("RequiredTime") or 0)
        else:
            it.rice_slot, it.rice_min_per_bai = None, 0.0


def requests_by_slot(requests, planner: str, start: dt.date, end: dt.date):
    """Minutes booked by everyone else (+ 米飯課予定 blocks), and the planner's own requests."""
    others = defaultdict(float)
    mine = []
    for r in requests:
        try:
            d = dt.datetime.strptime(r["TargetDate"], "%m/%d/%Y").date()
        except (KeyError, ValueError):
            continue
        if not (start <= d <= end):
            continue
        own = (r.get("Requester") or "").startswith(planner) and r.get("Status") != "米飯課予定"
        if own:
            mine.append(dict(date=d, slot=r.get("Timeslot"), product=r.get("ProductName"),
                             lots=_num(r.get("LotCount")) or 0, minutes=_num(r.get("EstTime")) or 0,
                             status=r.get("Status")))
        else:
            others[(d, r.get("Timeslot"))] += _num(r.get("EstTime")) or 0
    return others, mine


# ---------------------------------------------------------------- planner

@dataclass
class Caps:
    target: dict  # slot -> minutes (aim)
    hard: dict    # slot -> minutes (allowed)


def fixed_from_requests(items, mine_requests, first_free, statuses=("確定",)):
    """Requests that can no longer be changed in AppSheet -> {item name: {day index: 倍}}."""
    by_prod = {it.rice_product: it for it in items if it.rice_product}
    out = defaultdict(dict)
    for r in mine_requests:
        it = by_prod.get(r["product"])
        if it and r["status"] in statuses and r["date"] in it.days:
            x = it.days.index(r["date"])
            if x >= first_free:
                out[it.name][x] = out[it.name].get(x, 0) + r["lots"]
    return out


def plan_all(items, base: int, first_free: int, end: int, mode: str, others, own_locked, caps: Caps, fixed=None):
    """Decide 倍 for every free day (first_free..end) for every item. Days-outer so capacity is shared.

    own_locked: (date, slot) -> minutes the planner already has on locked days (from AppSheet).
    fixed: {item name: {day index: 倍}} that must stay (e.g. 確定 requests).
    Returns {item name: plan list}.
    """
    fixed = fixed or {}
    plans = {it.name: list(it.plan[:first_free]) + [0.0] * (len(it.days) - first_free) for it in items}
    mine = defaultdict(float, own_locked)
    for it in items:
        for x, k in fixed.get(it.name, {}).items():
            plans[it.name][x] = k
            if it.rice_slot:
                mine[(it.days[x], it.rice_slot)] += k * it.rice_min_per_bai
    horizon = min(len(items[0].days) - 1, end + 10) if items else end

    def load(it, x):
        k = (it.days[x], it.rice_slot) if x < len(it.days) else None
        return others.get(k, 0.0) + mine.get(k, 0.0) if k else 0.0

    for p in range(first_free, end + 1):
        decisions = []
        for it in items:
            a = p + it.lead
            if a + 3 > horizon:
                continue
            ss = stock_series(it, plans[it.name], base, a + 3, first_free)
            urgency = (ss[a] - it.min) / max(it.extra["avg"], 1)
            decisions.append((urgency, it, ss))
        for _, it, ss in sorted(decisions, key=lambda d: d[0]):
            if p in fixed.get(it.name, {}):
                continue
            k = decide(it, p, ss, mode, load, caps)
            if k:
                plans[it.name][p] = k
                if it.rice_slot:
                    mine[(it.days[p], it.rice_slot)] += k * it.rice_min_per_bai
    return plans


def decide(it: Item, p: int, ss: list, mode: str, load, caps: Caps):
    a, lot, mn, mx = p + it.lead, it.lot, it.min, it.max
    step = it.extra["step"]
    fc = lambda x: it.fcst[x] if x < len(it.fcst) and it.fcst[x] is not None else it.extra["avg"]

    def size(base_stock):
        if mode == "lean":
            k = step
            while base_stock + k * lot < mn + fc(a + 1):
                k += step
            return k
        if mode == "batch":
            k = step
            while base_stock + k * lot < mn + fc(a + 1):
                k += step
            cap = min(mx, base_stock + sum(fc(x) for x in range(a + 1, a + 1 + int(it.shelf))))
            while base_stock + (k + step) * lot <= cap:
                k += step
            return k
        k = it.usual
        while base_stock + k * lot < mn:
            k += step
        return k

    if ss[a] < mn:                      # needed now: stock would drop below 最低 on arrival
        return size(ss[a])
    if not it.rice_slot:
        return 0.0
    k = size(ss[a])
    m = k * it.rice_min_per_bai
    for j in (1, 2):                    # pull forward if the day it will be needed is crowded
        if a + j < len(ss) and ss[a + j] < mn and load(it, p + j) + m > caps.target[it.rice_slot] \
                and load(it, p) + m <= caps.target[it.rice_slot] and ss[a] + k * lot <= mx:
            return k
    return 0.0


# ---------------------------------------------------------------- scoring + outputs

def score(items, plans, base, first_free, end, others, caps: Caps, own_locked):
    per_item, load = [], defaultdict(float)
    for k, v in others.items():
        load[k] += v
    for k, v in own_locked.items():
        load[k] += v
    for it in items:
        plan = plans[it.name]
        ss = stock_series(it, plan, base, end, first_free)
        window = ss[base:end + 1]
        batches = [x for x in range(first_free, end + 1) if plan[x]]
        for x in batches:
            if it.rice_slot:
                load[(it.days[x], it.rice_slot)] += plan[x] * it.rice_min_per_bai
        per_item.append(dict(
            品目名=it.name, 製造回数=len(batches), 倍合計=sum(plan[x] for x in batches),
            欠品見込み日=sum(1 for s in window if s < 0), 最低割れ日=sum(1 for s in window if s < it.min),
            最高超え日=sum(1 for s in window if s > it.max),
            平均在庫=round(sum(window) / len(window)), 最高比=round(sum(window) / len(window) / it.max * 100),
            最少在庫=round(min(window))))
    days = [items[0].days[x] for x in range(first_free, end + 1)] if items else []
    over = [(d, s, v) for (d, s), v in load.items() if d in days and v > caps.hard.get(s, 1e9)]
    near = [(d, s, v) for (d, s), v in load.items() if d in days and caps.target.get(s, 1e9) < v <= caps.hard.get(s, 1e9)]
    n = max(len(per_item), 1)
    summary = dict(
        製造回数=sum(r["製造回数"] for r in per_item),
        欠品見込み日=sum(r["欠品見込み日"] for r in per_item),
        最低割れ日=sum(r["最低割れ日"] for r in per_item),
        最高超え日=sum(r["最高超え日"] for r in per_item),
        平均在庫_最高比=round(sum(r["最高比"] for r in per_item) / n),
        米飯課_上限超え=len(over), 米飯課_目標超え=len(near))
    return summary, per_item, load


def request_list(items, plans, first_free, end, mine_requests):
    """Planned 米飯課 requests on free days vs what is already in AppSheet."""
    rows = []
    existing = {(r["date"], r["product"]): r for r in mine_requests}
    planned_keys = set()
    for it in items:
        if not it.rice_slot:
            continue
        for x in range(first_free, end + 1):
            k = plans[it.name][x]
            if not k:
                continue
            key = (it.days[x], it.rice_product)
            planned_keys.add(key)
            ex = existing.get(key)
            if ex and ex["status"] == "確定":
                action = "確定済み"
            else:
                action = "追加" if not ex else ("そのまま" if abs(ex["lots"] - k) < 1e-9 else f'変更（{ex["lots"]:g}→{k:g}）')
            rows.append(dict(製造日=it.days[x], 時間帯=it.rice_slot, 依頼品目=it.rice_product, ロット数=k,
                             分=round(k * it.rice_min_per_bai), 対応=action))
    last = items[0].days[end] if items else None
    first = items[0].days[first_free] if items else None
    for (d, prod), ex in existing.items():
        if first and first <= d <= last and (d, prod) not in planned_keys and ex["status"] != "確定":
            rows.append(dict(製造日=d, 時間帯=ex["slot"], 依頼品目=prod, ロット数=ex["lots"],
                             分=round(ex["minutes"]), 対応=f'削除（{ex["status"]}）'))
    return sorted(rows, key=lambda r: (r["製造日"], r["時間帯"], r["依頼品目"]))


def plan_table(items, plans, base, first_free, end):
    """Two rows per item, like the 在庫表: 計画（倍） and 在庫推移 (projected end-of-day stock).

    mark per cell: "diff" (倍 differs from the current plan), "neg" (< 0), "low" (< 最低), "high" (> 最高).
    """
    rows = []
    span = range(first_free, end + 1)
    for it in items:
        plan = plans[it.name]
        ss = stock_series(it, plan, base, end, first_free)
        rows.append({"品目名": it.name, "行": "計画（倍）",
                     "text": [f"{plan[x]:g}" if plan[x] else "" for x in span],
                     "value": [plan[x] or None for x in span],
                     "mark": ["diff" if (plan[x] or 0) != (it.plan[x] or 0) else "" for x in span]})
        rows.append({"品目名": f"　最低{it.min:g}／最高{it.max:g}", "行": "在庫推移",
                     "text": [f"{round(ss[x]):,}" for x in span],
                     "value": [round(ss[x]) for x in span],
                     "mark": ["neg" if ss[x] < 0 else "low" if ss[x] < it.min else "high" if ss[x] > it.max else ""
                              for x in span]})
    return rows


def export_excel(items, results, base, first_free, end, title):
    """One sheet per plan (品目 × 日 grid of 倍 + 米飯課依頼 list), print-ready (landscape, fit to width)."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    thin = Side(style="thin", color="999999")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    head = PatternFill("solid", fgColor="E8E8E8")
    changed = PatternFill("solid", fgColor="FFF2B3")
    days = items[0].days
    for label, res in results.items():
        ws = wb.create_sheet(label[:28].replace("（", "(").replace("）", ")"))
        ws.append([f"{title}　{label}　（{days[first_free]:%m/%d}〜{days[end]:%m/%d}）"])
        ws["A1"].font = Font(bold=True, size=13)
        ws.append([])
        hdr = ["品目名", "行"] + [f"{days[x]:%m/%d}\n{'月火水木金土日'[days[x].weekday()]}"
                                for x in range(first_free, end + 1)]
        ws.append(hdr)
        fills = {"diff": (changed, None),
                 "low": (PatternFill("solid", fgColor="F8D7DA"), Font(color="A4161A", bold=True)),
                 "neg": (PatternFill("solid", fgColor="E5383B"), Font(color="FFFFFF", bold=True)),
                 "high": (PatternFill("solid", fgColor="D0E4F7"), Font(color="1D4E89"))}
        for r in plan_table(items, res["plans"], base, first_free, end):
            ws.append([r["品目名"], r["行"]] + r["value"])
            rr = ws.max_row
            for j, m in enumerate(r["mark"]):
                if m:
                    fill, font = fills[m]
                    ws.cell(rr, 3 + j).fill = fill
                    if font:
                        ws.cell(rr, 3 + j).font = font
                if r["行"] == "在庫推移":
                    ws.cell(rr, 3 + j).number_format = "#,##0"
        pair_end = Border(left=thin, right=thin, top=thin, bottom=Side(style="medium", color="333333"))
        for row in ws.iter_rows(min_row=3, max_row=ws.max_row, max_col=len(hdr)):
            last_of_item = row[1].value == "在庫推移"
            for cell in row:
                cell.border = pair_end if last_of_item else box
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for cell in ws[3]:
            cell.fill = head
            cell.font = Font(bold=True)
        for row in ws.iter_rows(min_row=4, max_row=ws.max_row, max_col=2):
            row[0].alignment = Alignment(horizontal="left", vertical="center")
            row[0].font = Font(bold=True) if row[1].value == "計画（倍）" else Font(size=9, color="555555")
        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 10
        for j in range(3, len(hdr) + 1):
            ws.column_dimensions[get_column_letter(j)].width = 7
        ws.append([])
        ws.append(["黄色 = 今の計画（倍）と違う日　ピンク = 最低割れ　赤 = 欠品　青 = 最高超え（在庫推移は使用予測からの見込み）"])
        ws.append([])
        ws.append(["米飯課依頼"])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
        cols = ["製造日", "時間帯", "依頼品目", "ロット数", "分", "対応"]
        ws.append(cols)
        start = ws.max_row
        for r in res["requests"]:
            ws.append([f'{r["製造日"]:%m/%d}', r["時間帯"], r["依頼品目"], r["ロット数"], r["分"], r["対応"]])
        for row in ws.iter_rows(min_row=start, max_row=ws.max_row, max_col=len(cols)):
            for cell in row:
                cell.border = box
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_title_rows = "3:3"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
