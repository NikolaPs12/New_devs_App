from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text


async def get_property(property_id: str, tenant_id: str, db_session):
    result = await db_session.execute(text("""
        SELECT id, name, timezone FROM properties
        WHERE id = :property_id AND tenant_id = :tenant_id
    """), {"property_id": property_id, "tenant_id": tenant_id})
    property_data = result.mappings().first()
    if property_data is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return property_data


async def calculate_total_revenue(
    property_id: str, tenant_id: str, db_session, month: int = None, year: int = None,
):
    property_data = await get_property(property_id, tenant_id, db_session)
    params = {"property_id": property_id, "tenant_id": tenant_id}
    period_filter = ""
    if year is not None:
        property_tz = ZoneInfo(property_data["timezone"])
        start = datetime(year, month or 1, 1, tzinfo=property_tz)
        if month is not None and month < 12:
            end = datetime(year, month + 1, 1, tzinfo=property_tz)
        else:
            end = datetime(year + 1, 1, 1, tzinfo=property_tz)
        # Build both local boundaries independently so each uses its own DST offset.
        params.update(start=start.astimezone(timezone.utc), end=end.astimezone(timezone.utc))
        period_filter = "AND check_in_date >= :start AND check_in_date < :end"

    result = await db_session.execute(text(f"""
        SELECT currency, SUM(total_amount) AS total_revenue, COUNT(*) AS reservation_count
        FROM reservations
        WHERE property_id = :property_id AND tenant_id = :tenant_id
        {period_filter}
        GROUP BY currency
    """), params)
    rows = result.mappings().all()
    if len(rows) > 1 or (rows and not rows[0]["currency"]):
        raise HTTPException(status_code=409, detail="Cannot total reservations with different or missing currencies")
    total = rows[0]["total_revenue"] if rows else Decimal("0")
    # Keep the database's sub-cent precision until the complete sum is known.
    total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "total": str(total),
        "currency": rows[0]["currency"] if rows else "USD",
        "count": rows[0]["reservation_count"] if rows else 0,
    }


async def calculate_monthly_revenue(
    property_id: str, month: int, year: int, tenant_id: str, db_session,
) -> Decimal:
    result = await calculate_total_revenue(property_id, tenant_id, db_session, month, year)
    return Decimal(result["total"])
