import json
import logging

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import settings
from app.services.reservations import get_property

logger = logging.getLogger(__name__)
redis_client = redis.Redis.from_url(settings.redis_url)


async def get_revenue_summary(property_id: str, tenant_id: str, db_session, month=None, year=None):
    # Check ownership even on cache hits, including properties whose owner changed.
    await get_property(property_id, tenant_id, db_session)
    cache_key = f"revenue:v3:{tenant_id}:{property_id}:{year or 'all'}:{month or 'all'}"
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except RedisError:
        logger.warning("Revenue cache unavailable; calculating from database")

    from app.services.reservations import calculate_total_revenue

    result = await calculate_total_revenue(property_id, tenant_id, db_session, month, year)
    try:
        await redis_client.setex(cache_key, 300, json.dumps(result))
    except RedisError:
        logger.warning("Could not cache revenue summary")
    return result
