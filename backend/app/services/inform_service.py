from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

import psycopg2

from app.services.legacy.pg_vector_utils import PG_CONN_STR, PROCESS_TO_TABLE


def _filter_by_date(rows: list[dict[str, Any]], period: str) -> list[dict[str, Any]]:
    today = datetime.today()
    delta_map = {
        "1d": timedelta(days=1),
        "1w": timedelta(weeks=1),
        "1m": timedelta(days=30),
        "3m": timedelta(days=90),
        "1y": timedelta(days=365),
        "3y": timedelta(days=1095),
    }
    if period not in delta_map:
        return rows

    start_date = today - delta_map[period]
    filtered: list[dict[str, Any]] = []
    for row in rows:
        date_str = str(row.get("날짜", ""))[:19]
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
            try:
                if datetime.strptime(date_str, fmt) >= start_date:
                    filtered.append(row)
                break
            except ValueError:
                continue
    return filtered


def get_inform_records(
    process: str,
    line: str = "",
    equip: str = "",
    keyword: str = "",
    period: str = "",
    start: str = "",
    end: str = "",
) -> dict[str, Any]:
    process = process.strip().upper()
    if process not in PROCESS_TO_TABLE:
        return {"data": [], "full": [], "options": {}, "error": "유효한 공정 파라미터가 필요합니다."}

    table = PROCESS_TO_TABLE[process]
    with psycopg2.connect(PG_CONN_STR) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, 날짜, 라인, 공정, 설비명, 에러명, 점검이력
                FROM {table}
                """
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]

    full_data: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        item = {"No": index}
        item.update(dict(zip(columns, row)))
        full_data.append(item)

    filtered = full_data
    if line and line != "전체":
        filtered = [row for row in filtered if str(row.get("라인", "")) == line]
    if equip and equip != "전체":
        filtered = [row for row in filtered if str(row.get("설비명", "")) == equip]

    keyword_norm = keyword.strip().lower()
    if keyword_norm:
        def matches_keyword(row: dict[str, Any]) -> bool:
            haystack = " ".join([
                str(row.get("라인", "")),
                str(row.get("설비명", "")),
                str(row.get("에러명", "")),
                str(row.get("점검이력", "")),
            ]).lower()
            return keyword_norm in haystack

        filtered = [row for row in filtered if matches_keyword(row)]

    if period:
        filtered = _filter_by_date(filtered, period)
    elif start and end:
        try:
            start_date = datetime.strptime(start[:10], "%Y-%m-%d")
            end_date = datetime.strptime(end[:10], "%Y-%m-%d")
            filtered = [
                row
                for row in filtered
                if start_date <= datetime.strptime(str(row.get("날짜", ""))[:10], "%Y-%m-%d") <= end_date
            ]
        except Exception:
            pass

    def unique_values(rows: list[dict[str, Any]], key: str) -> list[str]:
        return sorted({str(row.get(key, "")) for row in rows if row.get(key, "")})

    options = {
        "라인": unique_values(filtered if equip else full_data, "라인"),
        "설비명": unique_values(filtered if line else full_data, "설비명"),
    }

    error_counts = Counter((row.get("에러명") or "").strip() for row in filtered if row.get("에러명"))
    sorted_rows = sorted(filtered, key=lambda x: (str(x.get("에러명") or ""), str(x.get("날짜") or "")), reverse=True)

    unique_error_rows: dict[str, dict[str, Any]] = {}
    for row in sorted_rows:
        key = (row.get("에러명") or "").strip()
        if key and key not in unique_error_rows:
            unique_error_rows[key] = row.copy()

    data: list[dict[str, Any]] = []
    for error_name, row in unique_error_rows.items():
        row["중복수"] = error_counts.get(error_name, 1)
        data.append(row)

    data = sorted(data, key=lambda x: str(x.get("날짜", "")), reverse=True)
    return {"data": data, "full": filtered, "options": options}
