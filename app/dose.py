"""光照剂量核心计算:梯形积分、断点标记、达限日期预测。

剂量单位: lux·hour(勒克斯·小时)。
相邻两个读数 (t1, lux1)、(t2, lux2) 之间的剂量按梯形面积累计:
    dose = (lux1 + lux2) / 2 * (t2 - t1 的小时数)
相邻读数间隔超过 gap_hours 的区间被标记为"跨断点区间"(gap),
仍按梯形法计入剂量,但在审计证据中单独列出。
"""
from __future__ import annotations

from datetime import datetime, timezone
from bisect import bisect_left


def parse_ts(s: str) -> datetime:
    """解析 ISO 8601 时间戳,统一为 UTC aware datetime。"""
    s = s.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _interp(readings, ts_list, t: datetime) -> float:
    """在有序读数序列中线性插值 t 时刻的照度。调用方需保证 first <= t <= last。"""
    i = bisect_left(ts_list, t)
    if i < len(ts_list) and ts_list[i] == t:
        return readings[i][1]
    # t 严格位于两个读数之间(边界已由调用方裁剪)
    (t1, l1), (t2, l2) = readings[i - 1], readings[i]
    frac = (t - t1).total_seconds() / (t2 - t1).total_seconds()
    return l1 + frac * (l2 - l1)


def integrate_window(readings, start: datetime, end: datetime, gap_hours: float):
    """对 [start, end] 窗口内的读数做梯形积分。

    readings: [(datetime, lux), ...] 已按时间排序。
    返回 dict:
      dose_lux_hours  窗口内累计剂量
      intervals       每个相邻读数区间的明细(含 gap 标记)
      gaps            其中跨断点的区间子集
      uncovered       窗口内无读数覆盖的时间段(证据,不计剂量)
      curve           累计剂量曲线点 [{ts, dose}]
    """
    result = {
        "dose_lux_hours": 0.0,
        "intervals": [],
        "gaps": [],
        "uncovered": [],
        "curve": [],
    }
    if end <= start or not readings:
        if end > start:
            result["uncovered"].append({"start": iso(start), "end": iso(end),
                                        "hours": (end - start).total_seconds() / 3600})
        return result

    ts_list = [r[0] for r in readings]
    first, last = ts_list[0], ts_list[-1]

    # 窗口超出数据覆盖范围的部分:列为"无数据"证据,不做外推
    if start < first:
        result["uncovered"].append({"start": iso(start), "end": iso(min(end, first)),
                                    "hours": (min(end, first) - start).total_seconds() / 3600})
    if end > last:
        result["uncovered"].append({"start": iso(max(start, last)), "end": iso(end),
                                    "hours": (end - max(start, last)).total_seconds() / 3600})

    eff_start, eff_end = max(start, first), min(end, last)
    if eff_end <= eff_start:
        return result

    # 构造窗口内的有效序列:边界处线性插值
    series = [(eff_start, _interp(readings, ts_list, eff_start))]
    series += [r for r in readings if eff_start < r[0] < eff_end]
    if eff_end > eff_start:
        series.append((eff_end, _interp(readings, ts_list, eff_end)))

    total = 0.0
    curve = [{"ts": iso(series[0][0]), "dose": 0.0}]
    for (t1, l1), (t2, l2) in zip(series, series[1:]):
        hours = (t2 - t1).total_seconds() / 3600
        if hours <= 0:
            continue
        dose = (l1 + l2) / 2 * hours
        is_gap = hours > gap_hours
        total += dose
        interval = {
            "start": iso(t1),
            "end": iso(t2),
            "hours": round(hours, 4),
            "lux_start": round(l1, 2),
            "lux_end": round(l2, 2),
            "dose_lux_hours": round(dose, 2),
            "gap": is_gap,
        }
        result["intervals"].append(interval)
        if is_gap:
            result["gaps"].append(interval)
        curve.append({"ts": iso(t2), "dose": round(total, 2)})

    result["dose_lux_hours"] = round(total, 2)
    result["curve"] = curve
    return result


def daily_dose_rate(readings) -> float:
    """展柜历史平均剂量速率(lux·hour / 天):总剂量 / 数据覆盖天数。"""
    if len(readings) < 2:
        return 0.0
    span_days = (readings[-1][0] - readings[0][0]).total_seconds() / 86400
    if span_days <= 0:
        return 0.0
    full = integrate_window(readings, readings[0][0], readings[-1][0],
                            gap_hours=float("inf"))
    return full["dose_lux_hours"] / span_days


def predict_limit_date(current_dose: float, annual_limit: float,
                       rate_per_day: float, base: datetime):
    """按给定速率预测达到年度剂量上限的日期。返回 (date_str_or_None, days_or_None)。"""
    remaining = annual_limit - current_dose
    if remaining <= 0:
        return None, 0.0  # 已超限
    if rate_per_day <= 0:
        return None, None  # 无法预测(目标柜无数据)
    days = remaining / rate_per_day
    from datetime import timedelta
    return iso(base + timedelta(days=days)), round(days, 1)
