from fastapi import APIRouter, Depends, Query

from app.core.auth import SessionUser, get_current_user
from app.schemas.inform import InformListResponse
from app.services.inform_service import get_inform_records

router = APIRouter(prefix="/api/inform", tags=["inform"])


@router.get("/records", response_model=InformListResponse)
def read_inform_records(
    process: str = Query(default="MP"),
    line: str = Query(default=""),
    equip: str = Query(default=""),
    keyword: str = Query(default=""),
    period: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    current_user: SessionUser = Depends(get_current_user),
):
    _ = current_user
    return get_inform_records(
        process=process,
        line=line,
        equip=equip,
        keyword=keyword,
        period=period,
        start=start,
        end=end,
    )
