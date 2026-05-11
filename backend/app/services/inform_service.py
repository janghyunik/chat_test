from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil
from typing import Any

import psycopg2

from app.services.legacy.pg_vector_utils import PG_CONN_STR, PROCESS_TO_TABLE

PAGE_BLOCK_SIZE = 9
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _safe_datetime(value: Any) -> datetime:
    raw = str(value or "").strip()
    for candidate in (raw, raw[:19], raw[:10]):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(candidate, fmt)
            except Exception:
                continue
    return datetime.min


def _duplicate_group_key(row: dict[str, Any]) -> str:
    return "|".join([
        str(row.get("라인", "") or "").strip(),
        str(row.get("설비명", "") or "").strip(),
        str(row.get("에러명", "") or "").strip(),
    ])


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


def _build_page_info(page: int, page_size: int, total_items: int) -> dict[str, Any]:
    total_pages = ceil(total_items / page_size) if total_items > 0 else 0
    normalized_page = max(1, page)
    if total_pages > 0:
        normalized_page = min(normalized_page, total_pages)

    block_index = (normalized_page - 1) // PAGE_BLOCK_SIZE
    block_start = block_index * PAGE_BLOCK_SIZE + 1
    block_end = min(block_start + PAGE_BLOCK_SIZE - 1, total_pages) if total_pages > 0 else 1

    return {
        "page": normalized_page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "block_size": PAGE_BLOCK_SIZE,
        "block_start": block_start,
        "block_end": block_end,
        "has_prev_block": block_start > 1,
        "has_next_block": block_end < total_pages,
    }


def get_inform_records(
    process: str,
    line: str = "",
    equip: str = "",
    keyword: str = "",
    period: str = "",
    start: str = "",
    end: str = "",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    process = process.strip().upper()
    if process not in PROCESS_TO_TABLE:
        return {
            "data": [],
            "full": [],
            "options": {},
            "page_info": _build_page_info(page=1, page_size=DEFAULT_PAGE_SIZE, total_items=0),
            "error": "유효한 공정 파라미터가 필요합니다.",
        }

    table = PROCESS_TO_TABLE[process]
    safe_page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    requested_page = max(1, page)

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

    grouped_rows: dict[str, list[dict[str, Any]]] = {}
    for row in filtered:
        grouped_rows.setdefault(_duplicate_group_key(row), []).append(row)

    all_rows: list[dict[str, Any]] = []
    for group_key, rows_in_group in grouped_rows.items():
        sorted_group = sorted(rows_in_group, key=lambda x: _safe_datetime(x.get("날짜")), reverse=True)
        latest_row = sorted_group[0].copy()
        latest_row["중복수"] = len(sorted_group)
        latest_row["중복키"] = group_key
        all_rows.append(latest_row)

    all_rows = sorted(all_rows, key=lambda x: _safe_datetime(x.get("날짜")), reverse=True)
    page_info = _build_page_info(page=requested_page, page_size=safe_page_size, total_items=len(all_rows))

    if page_info["total_pages"] == 0:
        paged_rows: list[dict[str, Any]] = []
    else:
        start_index = (page_info["page"] - 1) * safe_page_size
        end_index = start_index + safe_page_size
        paged_rows = all_rows[start_index:end_index]

    return {
        "data": paged_rows,
        "full": filtered,
        "options": options,
        "page_info": page_info,
    }
