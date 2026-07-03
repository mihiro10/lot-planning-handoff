from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import date, timedelta, datetime, timezone
from typing import Optional, Any
import json
import re

from database import get_db, init_db
from formula import compute_product_grid
from importer import parse_excel, MANUAL_ROLES

app = FastAPI(title="Lot Planning API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── WebSocket broadcast manager ──────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        msg = json.dumps(data)
        for ws in list(self.active):
            try:
                await ws.send_text(msg)
            except Exception:
                self.active.remove(ws)


manager = ConnectionManager()


@app.on_event("startup")
def startup():
    init_db()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm_name(name: str) -> str:
    """Strip all whitespace (including full-width \\u3000) for product-name
    matching, since source sheets are inconsistent about spacing within an
    otherwise identical product name."""
    return re.sub(r"\s+", "", name or "")


def _parse_json_list(value) -> list[str]:
    """Return a JSON-stored list field, handling legacy plain-string values."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except (json.JSONDecodeError, TypeError):
        return [value]


def _serialize_json_list(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(value) if value else None
    return json.dumps([value]) if value else None


def _dates_in_range(start: str, end: str) -> list[date]:
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out = []
    while d <= e:
        out.append(d)
        d += timedelta(days=1)
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month_id_for_date(con, ds: str) -> int:
    """Find or auto-create the month row covering ds. months is a reporting
    label only now — the continuous formula never reads its starting_inventory
    beyond the very first seed (see _seed_for_product)."""
    d = date.fromisoformat(ds)
    row = con.execute(
        "SELECT id FROM months WHERE start_date<=? AND end_date>=?", (ds, ds)
    ).fetchone()
    if row:
        return row["id"]
    start = d.replace(day=1)
    end = (start.replace(month=start.month % 12 + 1, day=1) if start.month < 12
           else start.replace(year=start.year + 1, month=1, day=1)) - timedelta(days=1)
    cur = con.execute(
        "INSERT INTO months(year, month, start_date, end_date) VALUES (?,?,?,?)",
        (start.year, start.month, start.isoformat(), end.isoformat()),
    )
    return cur.lastrowid


def _seed_for_product(con, product_id: int) -> tuple[date, float]:
    """Earliest date we have any record for this product, and the starting
    inventory to seed the continuous walk from (only ever read once, at the
    earliest known month — later months' starting_inventory is bookkeeping
    only, not fed back into the formula)."""
    earliest_val = con.execute(
        "SELECT MIN(date) AS d FROM daily_values WHERE product_id=?", (product_id,)
    ).fetchone()["d"]
    seed_row = con.execute(
        "SELECT pmi.starting_inventory AS v, m.start_date AS d FROM product_month_inventory pmi "
        "JOIN months m ON pmi.month_id=m.id WHERE pmi.product_id=? ORDER BY m.start_date ASC LIMIT 1",
        (product_id,),
    ).fetchone()
    candidates = [d for d in (earliest_val, seed_row["d"] if seed_row else None) if d]
    earliest = date.fromisoformat(min(candidates)) if candidates else date.today()
    seed = seed_row["v"] if seed_row else 0.0
    return earliest, (seed or 0.0)


def _canceled_keys(con, product_id: int) -> set[tuple[int, str]]:
    rows = con.execute(
        "SELECT row_type_id, date FROM canceled_cells WHERE product_id=?", (product_id,)
    ).fetchall()
    return {(r["row_type_id"], r["date"]) for r in rows}


def _recompute_product(con, product_id: int, through: Optional[str] = None) -> list[dict]:
    """Recompute 入庫予定数 + 最終 across this product's whole continuous
    history (no month boundary), returning flat updates for broadcast.
    Canceled cells (canceled_cells) are excluded from the calculation —
    the raw number stays stored and visible, it just no longer counts
    toward 入庫予定数/最終, matching how a canceled production run
    won't actually happen."""
    earliest, seed = _seed_for_product(con, product_id)
    last_val = con.execute(
        "SELECT MAX(date) AS d FROM daily_values WHERE product_id=?", (product_id,)
    ).fetchone()["d"]
    horizon = max(d for d in [last_val, through, date.today().isoformat()] if d)
    if date.fromisoformat(horizon) < earliest:
        horizon = earliest.isoformat()

    dates = _dates_in_range(earliest.isoformat(), horizon)
    p_info = con.execute(
        "SELECT lot_size, lead_time FROM products WHERE id=?", (product_id,)
    ).fetchone()
    rows = con.execute(
        "SELECT row_type_id, date, value FROM daily_values WHERE product_id=?", (product_id,)
    ).fetchall()
    canceled = _canceled_keys(con, product_id)
    raw_for_calc = {(r["row_type_id"], r["date"]): r["value"] for r in rows if (r["row_type_id"], r["date"]) not in canceled}
    row_type_roles = {r["id"]: r["role"] for r in con.execute("SELECT id, role FROM row_types").fetchall()}

    computed = compute_product_grid(dates, seed, p_info["lot_size"], p_info["lead_time"], raw_for_calc, row_type_roles)
    return [{"product_id": product_id, "row_type_id": rtid, "date": ds, "value": val}
            for (rtid, ds), val in computed.items()]


def _build_continuous_grid(con, start_date: str, end_date: str) -> dict:
    dates = _dates_in_range(start_date, end_date)

    row_types = [dict(r) for r in con.execute(
        "SELECT id, name, role, display_order, is_system, is_visible_default FROM row_types ORDER BY display_order"
    ).fetchall()]
    row_type_roles = {rt["id"]: rt["role"] for rt in row_types}

    products = [dict(r) for r in con.execute(
        "SELECT id, code, name, planner, mfg_location, category, min_stock, max_stock "
        "FROM products WHERE is_active=1 ORDER BY display_order, id"
    ).fetchall()]
    for p in products:
        p["planner"]      = _parse_json_list(p.get("planner"))
        p["mfg_location"] = _parse_json_list(p.get("mfg_location"))

    result_products = []
    for p in products:
        pid = p["id"]
        earliest, seed = _seed_for_product(con, pid)
        walk_start = min(earliest, date.fromisoformat(start_date))
        walk_dates = _dates_in_range(walk_start.isoformat(), end_date)

        p_info = con.execute("SELECT lot_size, lead_time FROM products WHERE id=?", (pid,)).fetchone()
        rows = con.execute(
            "SELECT row_type_id, date, value, text_value FROM daily_values WHERE product_id=?", (pid,)
        ).fetchall()
        raw = {(r["row_type_id"], r["date"]): r["value"] for r in rows}
        raw_text = {(r["row_type_id"], r["date"]): r["text_value"] for r in rows if r["text_value"] is not None}
        canceled = _canceled_keys(con, pid)
        raw_for_calc = {k: v for k, v in raw.items() if k not in canceled}

        computed = compute_product_grid(walk_dates, seed, p_info["lot_size"], p_info["lead_time"], raw_for_calc, row_type_roles)

        values: dict[str, dict[str, Any]] = {}
        for rt in row_types:
            rtid = rt["id"]
            role = rt["role"]
            day_vals: dict[str, Any] = {}
            for d in dates:
                ds = d.isoformat()
                if role == "note":
                    day_vals[ds] = raw_text.get((rtid, ds))
                else:
                    day_vals[ds] = computed.get((rtid, ds)) if role in ("inbound_planned", "final") else raw.get((rtid, ds))
            values[str(rtid)] = day_vals

        result_products.append({**p, "values": values})

    return {
        "dates": [d.isoformat() for d in dates],
        "row_types": row_types,
        "products": result_products,
    }


def _build_grid_for_month(con, month_row) -> dict:
    month_id = month_row["id"]
    dates = _dates_in_range(month_row["start_date"], month_row["end_date"])

    row_types = [dict(r) for r in con.execute(
        "SELECT id, name, role, display_order, is_system, is_visible_default FROM row_types ORDER BY display_order"
    ).fetchall()]
    row_type_roles = {rt["id"]: rt["role"] for rt in row_types}

    products = [dict(r) for r in con.execute(
        "SELECT id, code, name, planner, mfg_location, category, min_stock, max_stock "
        "FROM products WHERE is_active=1 ORDER BY display_order, id"
    ).fetchall()]
    for p in products:
        p["planner"]       = _parse_json_list(p.get("planner"))
        p["mfg_location"]  = _parse_json_list(p.get("mfg_location"))

    result_products = []
    for p in products:
        pid = p["id"]
        inv = con.execute(
            "SELECT starting_inventory, pre_inventory FROM product_month_inventory "
            "WHERE product_id=? AND month_id=?", (pid, month_id)
        ).fetchone()
        starting = inv["starting_inventory"] if inv else 0.0
        pre      = inv["pre_inventory"]      if inv else 0.0

        # Load all stored manual values for this product/month
        rows = con.execute(
            "SELECT row_type_id, date, value FROM daily_values "
            "WHERE product_id=? AND month_id=?", (pid, month_id)
        ).fetchall()
        raw: dict = {(r["row_type_id"], r["date"]): r["value"] for r in rows}

        p_info = con.execute(
            "SELECT lot_size, lead_time FROM products WHERE id=?", (pid,)
        ).fetchone()
        computed = compute_product_grid(
            dates, starting, p_info["lot_size"], p_info["lead_time"],
            raw, row_type_roles
        )

        # Merge raw + computed into values dict keyed by row_type_id -> {date: value}
        values: dict[str, dict[str, Any]] = {}
        for rt in row_types:
            rtid = rt["id"]
            role = rt["role"]
            day_vals: dict[str, Any] = {}
            for d in dates:
                ds = d.isoformat()
                if role in ("inbound_planned", "final"):
                    v = computed.get((rtid, ds))
                else:
                    v = raw.get((rtid, ds))
                day_vals[ds] = v
            values[str(rtid)] = day_vals

        result_products.append({
            **p,
            "starting_inventory": starting,
            "pre_inventory": pre,
            "values": values,
        })

    return {
        "month": dict(month_row),
        "dates": [d.isoformat() for d in dates],
        "row_types": row_types,
        "products": result_products,
    }


# ── Month endpoints ───────────────────────────────────────────────────────────

class MonthCreate(BaseModel):
    year: int
    month: int
    start_date: str
    end_date: str


@app.get("/api/months")
def list_months():
    with get_db() as con:
        rows = con.execute("SELECT * FROM months ORDER BY year DESC, month DESC").fetchall()
        return [dict(r) for r in rows]


@app.post("/api/months", status_code=201)
def create_month(body: MonthCreate):
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO months(year, month, start_date, end_date) VALUES (?,?,?,?)",
            (body.year, body.month, body.start_date, body.end_date),
        )
        con.execute("UPDATE months SET is_active=0")
        con.execute("UPDATE months SET is_active=1 WHERE id=?", (cur.lastrowid,))
        return {"id": cur.lastrowid}


@app.get("/api/months/active/grid")
def get_active_grid():
    with get_db() as con:
        month = con.execute("SELECT * FROM months WHERE is_active=1").fetchone()
        if not month:
            raise HTTPException(404, "No active month")
        return _build_grid_for_month(con, month)


@app.get("/api/months/{month_id}/grid")
def get_grid(month_id: int):
    with get_db() as con:
        month = con.execute("SELECT * FROM months WHERE id=?", (month_id,)).fetchone()
        if not month:
            raise HTTPException(404, "Month not found")
        return _build_grid_for_month(con, month)


@app.get("/api/grid")
def get_continuous_grid(start: str, end: str):
    """Continuous planning grid over an arbitrary date range — no month
    boundary. months stays around only as a reporting label (see
    _month_id_for_date / _seed_for_product)."""
    with get_db() as con:
        if not con.execute("SELECT 1 FROM months LIMIT 1").fetchone():
            raise HTTPException(404, "No data yet")
        return _build_continuous_grid(con, start, end)


@app.get("/api/final-value")
def get_final_value(product_id: int, on: str):
    """System-calculated 最終 for one product on one date — used by the
    棚卸し/在庫修正 tool to show what the system currently thinks the
    inventory is before the user enters what they actually counted."""
    with get_db() as con:
        updates = _recompute_product(con, product_id, through=on)
        row_types = {r["id"]: r["role"] for r in con.execute("SELECT id, role FROM row_types").fetchall()}
        final_rtids = [rid for rid, role in row_types.items() if role == "final"]
        for u in updates:
            if u["row_type_id"] in final_rtids and u["date"] == on:
                return {"product_id": product_id, "date": on, "final": u["value"]}
        return {"product_id": product_id, "date": on, "final": 0.0}


# ── Product endpoints ─────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    code: Optional[str] = None
    name: str
    planner: Optional[list[str]] = None
    mfg_location: Optional[list[str]] = None
    category: Optional[str] = None
    storage: Optional[str] = None
    mfg_name: Optional[str] = None
    lot_size: float = 1
    lot_unit: str = "p"
    min_stock: float = 0
    max_stock: float = 0
    lead_time: int = 0
    notes: Optional[str] = None


class ProductUpdate(ProductCreate):
    name: Optional[str] = None


@app.get("/api/products")
def list_products(planner: Optional[str] = None, mfg_location: Optional[str] = None,
                  category: Optional[str] = None, q: Optional[str] = None):
    with get_db() as con:
        sql = "SELECT * FROM products WHERE is_active=1"
        params: list = []
        if category:
            sql += " AND category=?"; params.append(category)
        if q:
            sql += " AND name LIKE ?"; params.append(f"%{q}%")
        sql += " ORDER BY display_order, id"
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        for r in rows:
            r["planner"]      = _parse_json_list(r.get("planner"))
            r["mfg_location"] = _parse_json_list(r.get("mfg_location"))
        if planner:
            rows = [r for r in rows if planner in r["planner"]]
        if mfg_location:
            rows = [r for r in rows if mfg_location in r["mfg_location"]]
        return rows


@app.post("/api/products", status_code=201)
def create_product(body: ProductCreate):
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO products(code,name,planner,mfg_location,category,storage,mfg_name,"
            "lot_size,lot_unit,min_stock,max_stock,lead_time,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (body.code, body.name, _serialize_json_list(body.planner), _serialize_json_list(body.mfg_location), body.category,
             body.storage, body.mfg_name, body.lot_size, body.lot_unit,
             body.min_stock, body.max_stock, body.lead_time, body.notes),
        )
        return {"id": cur.lastrowid}


@app.patch("/api/products/{product_id}")
def update_product(product_id: int, body: ProductUpdate):
    with get_db() as con:
        p = con.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone()
        if not p:
            raise HTTPException(404)
        raw = body.model_dump()
        raw["planner"]      = _serialize_json_list(raw.get("planner"))
        raw["mfg_location"] = _serialize_json_list(raw.get("mfg_location"))
        fields = {k: v for k, v in raw.items() if v is not None}
        if fields:
            set_clause = ", ".join(f"{k}=?" for k in fields)
            con.execute(f"UPDATE products SET {set_clause} WHERE id=?",
                        list(fields.values()) + [product_id])
    return {"ok": True}


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int):
    with get_db() as con:
        con.execute("UPDATE products SET is_active=0 WHERE id=?", (product_id,))
    return {"ok": True}


@app.patch("/api/months/{month_id}/inventory/{product_id}")
def update_inventory(month_id: int, product_id: int,
                     body: dict):
    starting = body.get("starting_inventory")
    pre      = body.get("pre_inventory")
    with get_db() as con:
        con.execute(
            "INSERT INTO product_month_inventory(product_id, month_id, starting_inventory, pre_inventory)"
            " VALUES (?,?,?,?) ON CONFLICT(product_id,month_id) DO UPDATE SET "
            "starting_inventory=COALESCE(excluded.starting_inventory, starting_inventory),"
            "pre_inventory=COALESCE(excluded.pre_inventory, pre_inventory)",
            (product_id, month_id, starting, pre),
        )
    return {"ok": True}


# ── Row type endpoints ────────────────────────────────────────────────────────

class RowTypeCreate(BaseModel):
    name: str
    role: str
    display_order: int = 99
    is_visible_default: int = 1


@app.get("/api/row-types")
def list_row_types():
    with get_db() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM row_types ORDER BY display_order"
        ).fetchall()]


@app.post("/api/row-types", status_code=201)
def create_row_type(body: RowTypeCreate):
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO row_types(name, role, display_order, is_system, is_visible_default)"
            " VALUES (?,?,?,0,?)",
            (body.name, body.role, body.display_order, body.is_visible_default),
        )
        return {"id": cur.lastrowid}


class RowTypeReorder(BaseModel):
    ordered_ids: list[int]

@app.post("/api/row-types/reorder")
def reorder_row_types(body: RowTypeReorder):
    with get_db() as con:
        for i, rt_id in enumerate(body.ordered_ids):
            con.execute("UPDATE row_types SET display_order=? WHERE id=?", (i, rt_id))
    return {"ok": True}


@app.delete("/api/row-types/{rt_id}")
def delete_row_type(rt_id: int):
    with get_db() as con:
        rt = con.execute("SELECT is_system FROM row_types WHERE id=?", (rt_id,)).fetchone()
        if not rt:
            raise HTTPException(404)
        if rt["is_system"]:
            raise HTTPException(400, "Cannot delete system row types")
        con.execute("DELETE FROM daily_values WHERE row_type_id=?", (rt_id,))
        con.execute("DELETE FROM row_types WHERE id=?", (rt_id,))
    return {"ok": True}


# ── Cell value endpoint ───────────────────────────────────────────────────────

class CellUpdate(BaseModel):
    product_id: int
    row_type_id: int
    date: str
    value: Optional[float] = None


@app.put("/api/values")
async def update_value(body: CellUpdate):
    with get_db() as con:
        month_id = _month_id_for_date(con, body.date)
        # Save raw value (None = clear)
        if body.value is None:
            con.execute(
                "DELETE FROM daily_values WHERE product_id=? AND row_type_id=? AND date=?",
                (body.product_id, body.row_type_id, body.date),
            )
        else:
            con.execute(
                "INSERT INTO daily_values(product_id, row_type_id, month_id, date, value)"
                " VALUES (?,?,?,?,?) ON CONFLICT(product_id,row_type_id,date) DO UPDATE SET value=excluded.value",
                (body.product_id, body.row_type_id, month_id, body.date, body.value),
            )

        # Recompute 最終 + 入庫予定数 across the product's whole continuous history
        updates = _recompute_product(con, body.product_id, through=body.date)

    # Echo the raw cell change alongside the recomputed ones
    updates.append({
        "product_id": body.product_id,
        "row_type_id": body.row_type_id,
        "date": body.date,
        "value": body.value,
    })

    await manager.broadcast({"type": "cell_updates", "updates": updates})
    return {"updates": updates}


class CellUpdateBatch(BaseModel):
    updates: list[CellUpdate]


@app.put("/api/values/batch")
async def update_values_batch(body: CellUpdateBatch):
    """Write many raw cells at once (e.g. an Excel-style paste) and recompute
    each affected product only once, instead of once per cell — a paste of a
    month of dates for one product would otherwise trigger a full continuous
    recompute (and a WS broadcast) per cell."""
    affected_products: set[int] = set()
    with get_db() as con:
        for u in body.updates:
            month_id = _month_id_for_date(con, u.date)
            if u.value is None:
                con.execute(
                    "DELETE FROM daily_values WHERE product_id=? AND row_type_id=? AND date=?",
                    (u.product_id, u.row_type_id, u.date),
                )
            else:
                con.execute(
                    "INSERT INTO daily_values(product_id, row_type_id, month_id, date, value)"
                    " VALUES (?,?,?,?,?) ON CONFLICT(product_id,row_type_id,date) DO UPDATE SET value=excluded.value",
                    (u.product_id, u.row_type_id, month_id, u.date, u.value),
                )
            affected_products.add(u.product_id)

        all_updates = []
        for pid in affected_products:
            all_updates.extend(_recompute_product(con, pid))
        # Echo the raw cell changes alongside the recomputed ones
        for u in body.updates:
            all_updates.append({"product_id": u.product_id, "row_type_id": u.row_type_id, "date": u.date, "value": u.value})

    await manager.broadcast({"type": "cell_updates", "updates": all_updates})
    return {"updates": all_updates}


# ── 備考 notes (per-date, free text — not part of the numeric formula) ────────

class NoteUpdate(BaseModel):
    product_id: int
    date: str
    text: Optional[str] = None  # None/"" clears the note


@app.put("/api/notes")
async def update_note(body: NoteUpdate):
    with get_db() as con:
        note_rtid = con.execute("SELECT id FROM row_types WHERE role='note' LIMIT 1").fetchone()
        if not note_rtid:
            raise HTTPException(400, "No note row type configured")
        note_rtid = note_rtid["id"]
        month_id = _month_id_for_date(con, body.date)
        if not body.text:
            con.execute(
                "DELETE FROM daily_values WHERE product_id=? AND row_type_id=? AND date=?",
                (body.product_id, note_rtid, body.date),
            )
        else:
            con.execute(
                "INSERT INTO daily_values(product_id, row_type_id, month_id, date, text_value)"
                " VALUES (?,?,?,?,?) ON CONFLICT(product_id,row_type_id,date) DO UPDATE SET text_value=excluded.text_value",
                (body.product_id, note_rtid, month_id, body.date, body.text),
            )
    update = {"product_id": body.product_id, "row_type_id": note_rtid, "date": body.date, "value": body.text or None}
    await manager.broadcast({"type": "cell_updates", "updates": [update]})
    return update


# ── Attribute endpoints ───────────────────────────────────────────────────────

class AttrDefCreate(BaseModel):
    name: str
    label: str
    attr_type: str = "text"


@app.get("/api/products/{product_id}/attributes")
def get_product_attributes(product_id: int):
    with get_db() as con:
        defs = [dict(r) for r in con.execute(
            "SELECT * FROM attribute_definitions ORDER BY display_order"
        ).fetchall()]
        vals = {r["attribute_id"]: r["value"] for r in con.execute(
            "SELECT attribute_id, value FROM product_attribute_values WHERE product_id=?",
            (product_id,)
        ).fetchall()}
        for d in defs:
            d["value"] = vals.get(d["id"], "")
        return defs


@app.get("/api/attributes")
def list_attributes():
    with get_db() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM attribute_definitions ORDER BY display_order"
        ).fetchall()]


@app.post("/api/attributes", status_code=201)
def create_attribute(body: AttrDefCreate):
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO attribute_definitions(name, label, attr_type) VALUES (?,?,?)",
            (body.name, body.label, body.attr_type),
        )
        return {"id": cur.lastrowid}


@app.put("/api/attributes/{product_id}/{attr_id}")
def set_attribute_value(product_id: int, attr_id: int, body: dict):
    with get_db() as con:
        con.execute(
            "INSERT INTO product_attribute_values(product_id, attribute_id, value)"
            " VALUES (?,?,?) ON CONFLICT(product_id,attribute_id) DO UPDATE SET value=excluded.value",
            (product_id, attr_id, str(body.get("value", ""))),
        )
    return {"ok": True}


# ── Excel import ──────────────────────────────────────────────────────────────

@app.post("/api/import/excel")
async def import_excel(file: UploadFile = File(...)):
    """
    Upload a planning Excel file.  Parses products + daily values and returns
    a preview.  Does NOT write to DB — call /api/import/commit to save.
    """
    content = await file.read()
    try:
        data = parse_excel(content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return data


class ImportCommit(BaseModel):
    start_date: str
    end_date: str
    year: int
    month: int
    products: list  # same shape as parse_excel returns


@app.post("/api/import/commit")
def import_commit(body: ImportCommit):
    """Save parsed import data into the DB."""
    with get_db() as con:
        # Create or get month
        existing = con.execute(
            "SELECT id FROM months WHERE year=? AND month=?", (body.year, body.month)
        ).fetchone()
        if existing:
            month_id = existing["id"]
        else:
            cur = con.execute(
                "INSERT INTO months(year, month, start_date, end_date) VALUES (?,?,?,?)",
                (body.year, body.month, body.start_date, body.end_date),
            )
            month_id = cur.lastrowid

        con.execute("UPDATE months SET is_active=0")
        con.execute("UPDATE months SET is_active=1 WHERE id=?", (month_id,))

        # Get row type id map by name
        rt_rows = con.execute("SELECT id, name, role FROM row_types").fetchall()
        rt_by_name = {r["name"]: r["id"] for r in rt_rows}

        # Match products by whitespace-normalized name — source sheets are
        # inconsistent about full-width vs regular spaces in the same product
        # name (e.g. "小袋大根の…" vs "小袋　大根の…"), which would otherwise
        # create duplicate product rows for the same physical item.
        existing_by_norm = {
            _norm_name(r["name"]): r["id"]
            for r in con.execute("SELECT id, name FROM products").fetchall()
        }

        for p in body.products:
            planner_ser     = _serialize_json_list(p.get("planner"))
            mfg_loc_ser     = _serialize_json_list(p.get("mfg_location"))
            # Upsert product by whitespace-normalized name
            existing_id = existing_by_norm.get(_norm_name(p["name"]))
            existing_p = {"id": existing_id} if existing_id else None
            if existing_p:
                pid = existing_p["id"]
                con.execute(
                    "UPDATE products SET code=?, planner=?, mfg_location=?, category=?,"
                    " storage=?, mfg_name=?, lot_size=?, lot_unit=?, min_stock=?,"
                    " max_stock=?, lead_time=?, notes=?, is_active=1 WHERE id=?",
                    (p.get("code"), planner_ser, mfg_loc_ser, p.get("category"),
                     p.get("storage"), p.get("mfg_name"), p.get("lot_size", 1),
                     p.get("lot_unit", "p"), p.get("min_stock", 0), p.get("max_stock", 0),
                     p.get("lead_time", 0), p.get("notes"), pid),
                )
            else:
                cur = con.execute(
                    "INSERT INTO products(code,name,planner,mfg_location,category,storage,"
                    "mfg_name,lot_size,lot_unit,min_stock,max_stock,lead_time,notes)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (p.get("code"), p["name"], planner_ser, mfg_loc_ser,
                     p.get("category"), p.get("storage"), p.get("mfg_name"),
                     p.get("lot_size", 1), p.get("lot_unit", "p"),
                     p.get("min_stock", 0), p.get("max_stock", 0),
                     p.get("lead_time", 0), p.get("notes")),
                )
                pid = cur.lastrowid
                # Make this row visible to later products in the *same* batch —
                # otherwise the same name appearing twice in one sheet (a real
                # occurrence in these files) creates two rows instead of one.
                existing_by_norm[_norm_name(p["name"])] = pid

            # Inventory for this month
            con.execute(
                "INSERT INTO product_month_inventory(product_id, month_id, starting_inventory, pre_inventory)"
                " VALUES (?,?,?,?) ON CONFLICT(product_id,month_id) DO UPDATE SET"
                " starting_inventory=excluded.starting_inventory,"
                " pre_inventory=excluded.pre_inventory",
                (pid, month_id, p.get("starting_inventory", 0), p.get("pre_inventory", 0)),
            )

            # Daily values
            daily = p.get("daily_values", {})
            for rtype_name, date_vals in daily.items():
                rtid = rt_by_name.get(rtype_name)
                if not rtid:
                    continue
                for date_str, value in date_vals.items():
                    con.execute(
                        "INSERT INTO daily_values(product_id, row_type_id, month_id, date, value)"
                        " VALUES (?,?,?,?,?) ON CONFLICT(product_id,row_type_id,date)"
                        " DO UPDATE SET value=excluded.value",
                        (pid, rtid, month_id, date_str, value),
                    )

            # 備考 daily text notes
            daily_notes = p.get("daily_notes", {})
            if daily_notes:
                note_rtid = rt_by_name.get("備考")
                if note_rtid:
                    for date_str, text in daily_notes.items():
                        con.execute(
                            "INSERT INTO daily_values(product_id, row_type_id, month_id, date, text_value)"
                            " VALUES (?,?,?,?,?) ON CONFLICT(product_id,row_type_id,date)"
                            " DO UPDATE SET text_value=excluded.text_value",
                            (pid, note_rtid, month_id, date_str, text),
                        )

    return {"ok": True, "month_id": month_id}


# ── Canceled cells (excluded from 入庫予定数/最終, value stays visible) ────────

class CancelCellSet(BaseModel):
    product_id: int
    row_type_id: int
    date: str
    note: Optional[str] = None
    canceled_by: Optional[str] = None


@app.put("/api/canceled-cells")
async def cancel_cell(body: CancelCellSet):
    now = _now()
    with get_db() as con:
        con.execute(
            "INSERT INTO canceled_cells(product_id, row_type_id, date, note, canceled_by, canceled_at)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(product_id,row_type_id,date) DO UPDATE SET"
            " note=excluded.note, canceled_by=excluded.canceled_by, canceled_at=excluded.canceled_at",
            (body.product_id, body.row_type_id, body.date, body.note, body.canceled_by, now),
        )
        updates = _recompute_product(con, body.product_id, through=body.date)
    entry = {**body.model_dump(), "canceled_at": now}
    await manager.broadcast({"type": "cell_canceled", "cancel": entry})
    await manager.broadcast({"type": "cell_updates", "updates": updates})
    return entry


class CancelCellClear(BaseModel):
    product_id: int
    row_type_id: int
    date: str


@app.delete("/api/canceled-cells")
async def uncancel_cell(body: CancelCellClear):
    with get_db() as con:
        con.execute(
            "DELETE FROM canceled_cells WHERE product_id=? AND row_type_id=? AND date=?",
            (body.product_id, body.row_type_id, body.date),
        )
        updates = _recompute_product(con, body.product_id, through=body.date)
    payload = body.model_dump()
    await manager.broadcast({"type": "cell_uncanceled", "cancel": payload})
    await manager.broadcast({"type": "cell_updates", "updates": updates})
    return {"ok": True}


@app.get("/api/canceled-cells")
def list_canceled_cells(start: str, end: str):
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM canceled_cells WHERE date BETWEEN ? AND ?", (start, end)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Cell flags (manual edit-after-submit highlighting) ────────────────────────

class CellFlagSet(BaseModel):
    product_id: int
    row_type_id: int
    date: str
    color: str
    note: Optional[str] = None
    set_by: Optional[str] = None


@app.put("/api/cell-flags")
async def set_cell_flag(body: CellFlagSet):
    now = _now()
    with get_db() as con:
        con.execute(
            "INSERT INTO cell_flags(product_id, row_type_id, date, color, note, set_by, set_at)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT(product_id,row_type_id,date) DO UPDATE SET"
            " color=excluded.color, note=excluded.note, set_by=excluded.set_by, set_at=excluded.set_at",
            (body.product_id, body.row_type_id, body.date, body.color, body.note, body.set_by, now),
        )
    flag = {**body.model_dump(), "set_at": now}
    await manager.broadcast({"type": "cell_flag_set", "flag": flag})
    return flag


class CellFlagClear(BaseModel):
    product_id: int
    row_type_id: int
    date: str


@app.delete("/api/cell-flags")
async def clear_cell_flag(body: CellFlagClear):
    with get_db() as con:
        con.execute(
            "DELETE FROM cell_flags WHERE product_id=? AND row_type_id=? AND date=?",
            (body.product_id, body.row_type_id, body.date),
        )
    payload = body.model_dump()
    await manager.broadcast({"type": "cell_flag_cleared", "flag": payload})
    return {"ok": True}


@app.get("/api/cell-flags")
def list_cell_flags(start: str, end: str):
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM cell_flags WHERE date BETWEEN ? AND ?", (start, end)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Lot links (reschedule / split / transfer arrows) ──────────────────────────

class LotLinkCreate(BaseModel):
    link_type: str  # reschedule | split | transfer
    source_product_id: int
    source_row_type_id: int
    source_date: str
    target_product_id: int
    target_row_type_id: int
    target_date: str
    quantity: Optional[float] = None
    conversion_factor: float = 1
    note: Optional[str] = None
    created_by: Optional[str] = None


@app.post("/api/lot-links", status_code=201)
async def create_lot_link(body: LotLinkCreate):
    if body.link_type not in ("reschedule", "split", "transfer"):
        raise HTTPException(400, "link_type must be reschedule, split, or transfer")
    now = _now()
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO lot_links(link_type, source_product_id, source_row_type_id, source_date,"
            " target_product_id, target_row_type_id, target_date, quantity, conversion_factor, note,"
            " created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (body.link_type, body.source_product_id, body.source_row_type_id, body.source_date,
             body.target_product_id, body.target_row_type_id, body.target_date, body.quantity,
             body.conversion_factor, body.note, body.created_by, now),
        )
        link = {"id": cur.lastrowid, **body.model_dump(), "created_at": now}
    await manager.broadcast({"type": "lot_link_created", "link": link})
    return link


@app.get("/api/lot-links")
def list_lot_links(start: str, end: str):
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM lot_links WHERE source_date BETWEEN ? AND ? OR target_date BETWEEN ? AND ?",
            (start, end, start, end),
        ).fetchall()
        return [dict(r) for r in rows]


@app.delete("/api/lot-links/{link_id}")
async def delete_lot_link(link_id: int):
    with get_db() as con:
        con.execute("DELETE FROM lot_links WHERE id=?", (link_id,))
    await manager.broadcast({"type": "lot_link_deleted", "id": link_id})
    return {"ok": True}


# ── Stocktake / 在庫修正 ────────────────────────────────────────────────────────

class StocktakeApply(BaseModel):
    product_id: int
    date: str
    actual_quantity: float
    note: Optional[str] = None
    set_by: Optional[str] = None


def _apply_stocktake_one(con, product_id: int, date_str: str, actual_quantity: float,
                          note: Optional[str], set_by: Optional[str]) -> dict:
    """Diff a physically-counted quantity against the system's calculated
    最終 for that date, and write the diff into the 調整 row for that date —
    no separate starting-inventory field, works on any date. Shared by the
    single-product and batch (monthly) stocktake endpoints."""
    adjustment_rtid = con.execute(
        "SELECT id FROM row_types WHERE role='adjustment' LIMIT 1"
    ).fetchone()
    if not adjustment_rtid:
        raise HTTPException(400, "No adjustment row type configured")
    adjustment_rtid = adjustment_rtid["id"]

    before = _recompute_product(con, product_id, through=date_str)
    final_rtids = {r["id"] for r in con.execute("SELECT id FROM row_types WHERE role='final'").fetchall()}
    system_final = next(
        (u["value"] for u in before if u["row_type_id"] in final_rtids and u["date"] == date_str), 0.0
    ) or 0.0
    # system_final already includes whatever 調整 is currently sitting on this
    # date, so the correction has to be added on top of it, not overwrite it —
    # otherwise a second stocktake on the same date cancels the first one out.
    existing_adjustment = con.execute(
        "SELECT value FROM daily_values WHERE product_id=? AND row_type_id=? AND date=?",
        (product_id, adjustment_rtid, date_str),
    ).fetchone()
    existing_adjustment = (existing_adjustment["value"] if existing_adjustment else 0.0) or 0.0
    diff = actual_quantity - system_final
    new_adjustment = existing_adjustment + diff

    month_id = _month_id_for_date(con, date_str)
    con.execute(
        "INSERT INTO daily_values(product_id, row_type_id, month_id, date, value)"
        " VALUES (?,?,?,?,?) ON CONFLICT(product_id,row_type_id,date) DO UPDATE SET value=excluded.value",
        (product_id, adjustment_rtid, month_id, date_str, new_adjustment),
    )
    if note or set_by:
        con.execute(
            "INSERT INTO cell_flags(product_id, row_type_id, date, color, note, set_by, set_at)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT(product_id,row_type_id,date) DO UPDATE SET"
            " color=excluded.color, note=excluded.note, set_by=excluded.set_by, set_at=excluded.set_at",
            (product_id, adjustment_rtid, date_str, "#e6821e", note or "棚卸し反映", set_by, _now()),
        )

    updates = _recompute_product(con, product_id, through=date_str)
    updates.append({"product_id": product_id, "row_type_id": adjustment_rtid, "date": date_str, "value": new_adjustment})
    return {"diff": diff, "system_final": system_final, "actual_quantity": actual_quantity, "updates": updates}


@app.post("/api/stocktake")
async def apply_stocktake(body: StocktakeApply):
    with get_db() as con:
        result = _apply_stocktake_one(con, body.product_id, body.date, body.actual_quantity, body.note, body.set_by)
    await manager.broadcast({"type": "cell_updates", "updates": result["updates"]})
    return result


class StocktakeBatchItem(BaseModel):
    product_id: int
    actual_quantity: float
    note: Optional[str] = None


class StocktakeBatchApply(BaseModel):
    date: str
    batch_label: Optional[str] = None
    set_by: Optional[str] = None
    items: list[StocktakeBatchItem]


@app.post("/api/stocktake/batch")
async def apply_stocktake_batch(body: StocktakeBatchApply):
    """Monthly full-sweep stocktake — apply many products' counts for the same
    date in one call. Each item's note falls back to batch_label so the whole
    batch is identifiable later (e.g. '6月度棚卸し') distinct from ad-hoc
    single-product corrections."""
    results = []
    all_updates = []
    with get_db() as con:
        for item in body.items:
            note = item.note or body.batch_label
            r = _apply_stocktake_one(con, item.product_id, body.date, item.actual_quantity, note, body.set_by)
            results.append({"product_id": item.product_id, "diff": r["diff"],
                             "system_final": r["system_final"], "actual_quantity": r["actual_quantity"]})
            all_updates.extend(r["updates"])
    await manager.broadcast({"type": "cell_updates", "updates": all_updates})
    return {"applied": len(results), "results": results}


@app.get("/api/final-values")
def get_final_values(on: str):
    """System-calculated 最終 for every active product on one date — powers
    the bulk monthly stocktake screen's 'システム計算値' column."""
    with get_db() as con:
        final_rtids = {r["id"] for r in con.execute("SELECT id FROM row_types WHERE role='final'").fetchall()}
        products = con.execute("SELECT id FROM products WHERE is_active=1").fetchall()
        out = {}
        for p in products:
            updates = _recompute_product(con, p["id"], through=on)
            val = next((u["value"] for u in updates if u["row_type_id"] in final_rtids and u["date"] == on), 0.0)
            out[p["id"]] = val or 0.0
        return out


# ── Inventory analysis dashboard ───────────────────────────────────────────────

@app.get("/api/analysis")
def get_analysis(start: str, end: str):
    with get_db() as con:
        final_rtids = [r["id"] for r in con.execute("SELECT id FROM row_types WHERE role='final'").fetchall()]
        if not final_rtids:
            return []
        products = con.execute(
            "SELECT id, name, min_stock, max_stock FROM products WHERE is_active=1"
        ).fetchall()
        out = []
        for p in products:
            # 最終 is always computed, never stored raw — recompute across the range.
            updates = _recompute_product(con, p["id"], through=end)
            vals = [u["value"] for u in updates if u["row_type_id"] in final_rtids
                    and start <= u["date"] <= end and u["value"] is not None]
            if not vals:
                continue
            avg = sum(vals) / len(vals)
            buffer = p["min_stock"] or 0
            stockouts = sum(1 for v in vals if buffer and v < buffer)
            out.append({
                "product_id": p["id"],
                "name": p["name"],
                "avg_final": round(avg, 1),
                "min_final": min(vals),
                "max_final": max(vals),
                "buffer": buffer,
                "diff_from_buffer": round(avg - buffer, 1),
                "stockout_count": stockouts,
            })
        return out
