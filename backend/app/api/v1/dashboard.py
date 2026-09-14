import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.auth import authenticate_request as get_current_user
from app.core.database_pool import get_db_session
from app.models.auth import AuthenticatedUser
from app.services.cache import get_revenue_summary

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/dashboard/properties")
async def get_dashboard_properties(
    response: Response,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db_session=Depends(get_db_session),
):
    response.headers["Cache-Control"] = "private, no-store"
    if not current_user.tenant_id:
        raise HTTPException(status_code=403, detail="No tenant assigned")
    try:
        result = await db_session.execute(text("""
            SELECT id, name, timezone FROM properties
            WHERE tenant_id = :tenant_id ORDER BY id
        """), {"tenant_id": current_user.tenant_id})
        return [dict(row) for row in result.mappings()]
    except SQLAlchemyError:
        logger.exception("Could not load dashboard properties")
        raise HTTPException(status_code=503, detail="Property data temporarily unavailable")


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    response: Response,
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = Query(default=None, ge=1900, le=9998),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db_session=Depends(get_db_session),
) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    if not current_user.tenant_id:
        raise HTTPException(status_code=403, detail="No tenant assigned")
    if month is not None and year is None:
        raise HTTPException(status_code=422, detail="A year is required with a month")
    try:
        revenue_data = await get_revenue_summary(
            property_id, current_user.tenant_id, db_session, month, year,
        )
    except SQLAlchemyError:
        logger.exception("Could not calculate revenue")
        raise HTTPException(status_code=503, detail="Revenue data temporarily unavailable")
    return {
        "property_id": revenue_data["property_id"],
        "total_revenue": revenue_data["total"],
        "currency": revenue_data["currency"],
        "reservations_count": revenue_data["count"],
    }
