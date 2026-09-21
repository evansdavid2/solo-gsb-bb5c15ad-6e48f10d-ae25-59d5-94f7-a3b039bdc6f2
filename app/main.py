"""博物馆展品光照剂量审计 API。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import dose
from .database import connect, init_db

app = FastAPI(title="Museum Light Dose Audit", version="1.0.0")
STATIC_DIR = Path(__file__).parent / "static"

DEFAULT_GAP_HOURS = 6.0


@app.on_event("startup")
def startup():
    init_db()


# ---------- 请求模型 ----------

class CaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ExhibitIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    material: str = ""
    annual_limit_lux_hours: float = Field(default=50000, gt=0)


class PlacementIn(BaseModel):
    case_id: int
    start: str
    end: str | None = None  # null = 至今在柜


class SimulateIn(BaseModel):
    exhibit_id: int
    target_case_id: int
    gap_hours: float = Field(default=DEFAULT_GAP_HOURS, gt=0)


# ---------- 工具 ----------

def _row_to_dict(row):
    return {k: row[k] for k in row.keys()}


def _get_case(conn, case_id: int):
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"展柜 {case_id} 不存在")
    return row


def _get_exhibit(conn, exhibit_id: int):
    row = conn.execute("SELECT * FROM exhibits WHERE id = ?", (exhibit_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"展品 {exhibit_id} 不存在")
    return row


def _case_readings(conn, case_id: int):
    rows = conn.execute(
        "SELECT ts, lux FROM readings WHERE case_id = ? ORDER BY ts", (case_id,)
    ).fetchall()
    return [(dose.parse_ts(r["ts"]), r["lux"]) for r in rows]


def _build_audit(conn, exhibit_id: int, gap_hours: float):
    """汇总展品所有在柜时间段的剂量,返回审计结构(供 audit / simulate / export 复用)。"""
    ex = _get_exhibit(conn, exhibit_id)
    placements = conn.execute(
        """SELECT p.*, c.name AS case_name FROM placements p
           JOIN cases c ON c.id = p.case_id
           WHERE p.exhibit_id = ? ORDER BY p.start_ts""",
        (exhibit_id,),
    ).fetchall()

    now = datetime.now(timezone.utc)
    segments, all_gaps, total = [], [], 0.0
    for p in placements:
        start = dose.parse_ts(p["start_ts"])
        end = dose.parse_ts(p["end_ts"]) if p["end_ts"] else now
        if end <= start:
            continue
        readings = _case_readings(conn, p["case_id"])
        seg = dose.integrate_window(readings, start, end, gap_hours)
        total += seg["dose_lux_hours"]
        for g in seg["gaps"]:
            all_gaps.append({**g, "case_id": p["case_id"], "case_name": p["case_name"],
                             "placement_id": p["id"]})
        segments.append({
            "placement_id": p["id"],
            "case_id": p["case_id"],
            "case_name": p["case_name"],
            "start": dose.iso(start),
            "end": dose.iso(end),
            "ongoing": p["end_ts"] is None,
            **seg,
        })

    limit = ex["annual_limit_lux_hours"]
    return {
        "exhibit": _row_to_dict(ex),
        "generated_at": dose.iso(now),
        "gap_hours_threshold": gap_hours,
        "integration_method": "trapezoidal",
        "total_dose_lux_hours": round(total, 2),
        "annual_limit_lux_hours": limit,
        "limit_used_pct": round(100 * total / limit, 2) if limit else None,
        "segments": segments,
        "gap_evidence": sorted(all_gaps, key=lambda g: g["start"]),
    }


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


# ---------- 展柜与读数 ----------

@app.post("/api/cases", status_code=201)
def create_case(body: CaseIn):
    with connect() as conn:
        try:
            cur = conn.execute("INSERT INTO cases(name) VALUES (?)", (body.name.strip(),))
        except Exception:
            raise HTTPException(409, "同名展柜已存在")
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_dict(row)


@app.get("/api/cases")
def list_cases():
    with connect() as conn:
        rows = conn.execute(
            """SELECT c.*, COUNT(r.id) AS reading_count,
                      MIN(r.ts) AS first_reading, MAX(r.ts) AS last_reading
               FROM cases c LEFT JOIN readings r ON r.case_id = c.id
               GROUP BY c.id ORDER BY c.id"""
        ).fetchall()
        result = []
        for row in rows:
            d = _row_to_dict(row)
            readings = _case_readings(conn, row["id"])
            d["avg_daily_dose_lux_hours"] = round(dose.daily_dose_rate(readings), 2)
            result.append(d)
        return result


@app.delete("/api/cases/{case_id}", status_code=204)
def delete_case(case_id: int):
    with connect() as conn:
        _get_case(conn, case_id)
        conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))


@app.post("/api/cases/{case_id}/readings")
async def upload_readings(case_id: int, file: UploadFile = File(...)):
    """上传 JSONL:每行 {"ts": "2026-01-01T08:00:00Z", "lux": 152.3}"""
    with connect() as conn:
        _get_case(conn, case_id)
        raw = (await file.read()).decode("utf-8", errors="replace")
        inserted, skipped, errors = 0, 0, []
        for lineno, line in enumerate(raw.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts = dose.iso(dose.parse_ts(str(obj["ts"])))
                lux = float(obj["lux"])
                if lux < 0:
                    raise ValueError("lux 不能为负")
            except (KeyError, TypeError, ValueError) as e:
                errors.append({"line": lineno, "error": str(e)})
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO readings(case_id, ts, lux) VALUES (?,?,?)",
                (case_id, ts, lux),
            )
            inserted += cur.rowcount
            skipped += 1 - cur.rowcount
        return {"inserted": inserted, "duplicates_skipped": skipped,
                "errors": errors[:50], "error_count": len(errors)}


# ---------- 展品与在柜时间段 ----------

@app.post("/api/exhibits", status_code=201)
def create_exhibit(body: ExhibitIn):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO exhibits(name, material, annual_limit_lux_hours) VALUES (?,?,?)",
            (body.name.strip(), body.material, body.annual_limit_lux_hours),
        )
        row = conn.execute("SELECT * FROM exhibits WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_dict(row)


@app.get("/api/exhibits")
def list_exhibits():
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.*, COUNT(p.id) AS placement_count
               FROM exhibits e LEFT JOIN placements p ON p.exhibit_id = e.id
               GROUP BY e.id ORDER BY e.id"""
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


@app.delete("/api/exhibits/{exhibit_id}", status_code=204)
def delete_exhibit(exhibit_id: int):
    with connect() as conn:
        _get_exhibit(conn, exhibit_id)
        conn.execute("DELETE FROM exhibits WHERE id = ?", (exhibit_id,))


@app.post("/api/exhibits/{exhibit_id}/placements", status_code=201)
def add_placement(exhibit_id: int, body: PlacementIn):
    with connect() as conn:
        _get_exhibit(conn, exhibit_id)
        _get_case(conn, body.case_id)
        try:
            start = dose.parse_ts(body.start)
            end = dose.parse_ts(body.end) if body.end else None
        except ValueError as e:
            raise HTTPException(422, f"时间格式错误: {e}")
        if end and end <= start:
            raise HTTPException(422, "结束时间必须晚于开始时间")
        cur = conn.execute(
            "INSERT INTO placements(exhibit_id, case_id, start_ts, end_ts) VALUES (?,?,?,?)",
            (exhibit_id, body.case_id, dose.iso(start), dose.iso(end) if end else None),
        )
        row = conn.execute("SELECT * FROM placements WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_dict(row)


@app.get("/api/exhibits/{exhibit_id}/placements")
def list_placements(exhibit_id: int):
    with connect() as conn:
        _get_exhibit(conn, exhibit_id)
        rows = conn.execute(
            """SELECT p.*, c.name AS case_name FROM placements p
               JOIN cases c ON c.id = p.case_id
               WHERE p.exhibit_id = ? ORDER BY p.start_ts""",
            (exhibit_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


@app.delete("/api/placements/{placement_id}", status_code=204)
def delete_placement(placement_id: int):
    with connect() as conn:
        conn.execute("DELETE FROM placements WHERE id = ?", (placement_id,))


# ---------- 审计 / 模拟 / 导出 ----------

@app.get("/api/exhibits/{exhibit_id}/audit")
def audit(exhibit_id: int, gap_hours: float = DEFAULT_GAP_HOURS):
    with connect() as conn:
        return _build_audit(conn, exhibit_id, gap_hours)


@app.post("/api/simulate")
def simulate(body: SimulateIn):
    """模拟把展品移入另一展柜:按两柜历史平均日剂量速率预测达限日期。"""
    with connect() as conn:
        audit_data = _build_audit(conn, body.exhibit_id, body.gap_hours)
        target = _get_case(conn, body.target_case_id)
        now = datetime.now(timezone.utc)

        # 当前柜 = 展品最近一次(仍在进行优先)所在展柜
        current_case_id = None
        if audit_data["segments"]:
            current_case_id = audit_data["segments"][-1]["case_id"]

        def projection(case_id):
            readings = _case_readings(conn, case_id)
            rate = dose.daily_dose_rate(readings)
            date_str, days = dose.predict_limit_date(
                audit_data["total_dose_lux_hours"],
                audit_data["annual_limit_lux_hours"], rate, now)
            return {"daily_rate_lux_hours": round(rate, 2),
                    "predicted_limit_date": date_str,
                    "days_to_limit": days}

        result = {
            "exhibit": audit_data["exhibit"],
            "current_dose_lux_hours": audit_data["total_dose_lux_hours"],
            "annual_limit_lux_hours": audit_data["annual_limit_lux_hours"],
            "remaining_lux_hours": round(
                audit_data["annual_limit_lux_hours"] - audit_data["total_dose_lux_hours"], 2),
            "already_exceeded": audit_data["total_dose_lux_hours"] >= audit_data["annual_limit_lux_hours"],
            "target_case": {"id": target["id"], "name": target["name"],
                            **projection(target["id"])},
            "current_case": None,
        }
        if current_case_id is not None:
            cur = _get_case(conn, current_case_id)
            result["current_case"] = {"id": cur["id"], "name": cur["name"],
                                      **projection(cur["id"])}
            if (result["current_case"]["days_to_limit"] is not None
                    and result["target_case"]["days_to_limit"] is not None):
                result["days_gained_by_rotation"] = round(
                    result["target_case"]["days_to_limit"]
                    - result["current_case"]["days_to_limit"], 1)
        return result


@app.get("/api/exhibits/{exhibit_id}/export")
def export_audit(exhibit_id: int, gap_hours: float = DEFAULT_GAP_HOURS):
    with connect() as conn:
        data = _build_audit(conn, exhibit_id, gap_hours)
        # 导出文件包含全部区间明细(页面审计同构),作为独立审计证据
        name = f"audit_exhibit_{exhibit_id}.json"
        return JSONResponse(
            data,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )


# ---------- 演示数据 ----------

@app.post("/api/demo", status_code=201)
def load_demo():
    """一键载入演示数据:两个展柜 120 天照度曲线 + 一件敏感展品。"""
    import math
    import random
    from datetime import timedelta

    random.seed(42)
    with connect() as conn:
        for name in ("A 柜 · 临窗书画厅", "B 柜 · 内厅恒温柜"):
            conn.execute("INSERT OR IGNORE INTO cases(name) VALUES (?)", (name,))
        case_a = conn.execute("SELECT id FROM cases WHERE name LIKE 'A 柜%'").fetchone()["id"]
        case_b = conn.execute("SELECT id FROM cases WHERE name LIKE 'B 柜%'").fetchone()["id"]

        base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=120)
        # 照度水平按敏感展品展厅调定:A 柜约 50 lux(日剂量 ~500 lux·h),
        # B 柜约 15 lux —— 80 天后累计约 4 万 lux·h,逼近 5 万年度上限
        for case_id, level, noise in ((case_a, 60, 15), (case_b, 17, 5)):
            for day in range(120):
                for hour in (8, 20):  # 每天开馆/闭馆各一条
                    ts = base + timedelta(days=day, hours=hour)
                    # 白天开灯曲线:8 点起 20 点落;周末观众多灯更亮
                    daytime = math.sin(math.pi * (hour - 8) / 12) if 8 <= hour <= 20 else 0
                    weekend = 1.25 if ts.weekday() >= 5 else 1.0
                    lux = max(0.0, level * (0.35 + 0.65 * daytime) * weekend
                              + random.uniform(-noise, noise))
                    conn.execute(
                        "INSERT OR IGNORE INTO readings(case_id, ts, lux) VALUES (?,?,?)",
                        (case_id, dose.iso(ts), round(lux, 1)),
                    )
            # 人为制造两处断点(传感器离线)
            for gap_day in (35, 80):
                conn.execute(
                    """DELETE FROM readings WHERE case_id = ? AND ts BETWEEN ? AND ?""",
                    (case_id,
                     dose.iso(base + timedelta(days=gap_day, hours=8)),
                     dose.iso(base + timedelta(days=gap_day + 3, hours=8))),
                )

        cur = conn.execute(
            """INSERT INTO exhibits(name, material, annual_limit_lux_hours)
               VALUES ('清代绢本设色立轴', '绢本彩绘(高度光敏)', 50000)"""
        )
        exhibit_id = cur.lastrowid
        # 展品 80 天前进 A 柜,至今在柜
        conn.execute(
            "INSERT INTO placements(exhibit_id, case_id, start_ts, end_ts) VALUES (?,?,?,NULL)",
            (exhibit_id, case_a, dose.iso(base + timedelta(days=40))),
        )
        return {"cases": [case_a, case_b], "exhibit_id": exhibit_id}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
