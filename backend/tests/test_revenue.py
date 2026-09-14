import os
import time
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from jose import jwt
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.v1 import dashboard, login
from app.config import settings
from app.core.auth import clear_auth_cache
from app.core.database_pool import DatabasePool, get_db_session


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to a seeded PostgreSQL test database")
class RevenueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        clear_auth_cache()
        self.engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.session = AsyncSession(bind=self.connection)
        self.addAsyncCleanup(self.cleanup_database)
        await self.session.execute(text("INSERT INTO tenants (id, name) VALUES ('test-tenant', 'Revenue test')"))
        await self.session.execute(text("""
            INSERT INTO properties (id, tenant_id, name, timezone)
            VALUES ('test-property', 'test-tenant', 'Test property', 'Europe/Paris')
        """))
        self.counter = 0
        self.entries = {}
        self.redis = AsyncMock()
        self.redis.get.side_effect = self.entries.get

        async def cache_result(key, ttl, value):
            self.assertEqual(ttl, 300)
            self.entries[key] = value

        self.redis.setex.side_effect = cache_result
        self.cache_patch = patch("app.services.cache.redis_client", self.redis)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        app = FastAPI()
        app.include_router(login.router, prefix="/api/v1")
        app.include_router(dashboard.router, prefix="/api/v1")

        async def session_override():
            yield self.session

        app.dependency_overrides[get_db_session] = session_override
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.headers = self.tenant_headers("test-tenant")

    async def cleanup_database(self):
        await self.session.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    def tenant_headers(self, tenant):
        token = jwt.encode({
            "id": "test-user", "email": "test@example.com",
            "app_metadata": {"tenant_id": tenant}, "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        }, settings.secret_key, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    async def account_headers(self, email, password):
        response = await self.client.post("/api/v1/auth/login", json={"email": email, "password": password})
        self.assertEqual(response.status_code, 200)
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    async def booking(self, check_in, amount, currency="USD", check_out=None):
        self.counter += 1
        start = datetime.fromisoformat(check_in)
        await self.session.execute(text("""
            INSERT INTO reservations (id, property_id, tenant_id, check_in_date, check_out_date, total_amount, currency)
            VALUES (:id, 'test-property', 'test-tenant', :start, :end, :amount, :currency)
        """), {"id": f"test-res-{self.counter}", "start": start,
               "end": datetime.fromisoformat(check_out) if check_out else start + timedelta(days=3),
               "amount": Decimal(amount), "currency": currency})

    async def summary(self, property_id="test-property", headers=None, **period):
        return await self.client.get("/api/v1/dashboard/summary", headers=headers or self.headers,
                                     params={"property_id": property_id, **period})

    async def assert_total(self, total, count, **period):
        response = await self.summary(**period)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total_revenue"], total)
        self.assertEqual(response.json()["reservations_count"], count)
        self.assertEqual(response.headers["cache-control"], "private, no-store")

    async def test_seed_totals_and_shared_property_id_for_both_accounts(self):
        a = await self.account_headers("sunset@propertyflow.com", "client_a_2024")
        b = await self.account_headers("ocean@propertyflow.com", "client_b_2024")
        for headers, expected, count in [(a, "2250.00", 4), (b, "0.00", 0), (b, "0.00", 0), (a, "2250.00", 4)]:
            await self.assert_total(expected, count, property_id="prop-001", headers=headers, month=3, year=2024)
        self.assertEqual(len(self.entries), 2)

    async def test_foreign_and_unknown_properties_are_not_found(self):
        for tenant, property_id in [("tenant-a", "prop-004"), ("tenant-b", "prop-002"), ("tenant-a", "missing")]:
            response = await self.summary(property_id, self.tenant_headers(tenant))
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.entries, {})

    async def test_ownership_is_checked_before_returning_cached_data(self):
        await self.assert_total("0.00", 0)
        await self.session.execute(text("DELETE FROM properties WHERE id = 'test-property'"))
        self.assertEqual((await self.summary()).status_code, 404)

    async def test_property_lists_are_scoped_by_tenant(self):
        for tenant, ids, name in [
            ("tenant-a", ["prop-001", "prop-002", "prop-003"], "Beach House Alpha"),
            ("tenant-b", ["prop-001", "prop-004", "prop-005"], "Mountain Lodge Beta"),
        ]:
            response = await self.client.get("/api/v1/dashboard/properties", headers=self.tenant_headers(tenant))
            self.assertEqual(response.status_code, 200)
            self.assertEqual([p["id"] for p in response.json()], ids)
            self.assertEqual(response.json()[0]["name"], name)

    async def test_periods_have_separate_cache_entries(self):
        for date, amount in [("2024-03-15T12:00:00+00:00", "10"), ("2024-04-15T12:00:00+00:00", "20"), ("2025-03-15T12:00:00+00:00", "30")]:
            await self.booking(date, amount)
        for _ in range(2):
            await self.assert_total("10.00", 1, month=3, year=2024)
            await self.assert_total("20.00", 1, month=4, year=2024)
            await self.assert_total("30.00", 1, month=3, year=2025)
            await self.assert_total("30.00", 2, year=2024)
            await self.assert_total("60.00", 3)
        self.assertEqual(len(self.entries), 5)

    async def test_paris_month_boundaries_include_start_exclude_end_across_dst(self):
        for date, amount in [
            ("2024-02-29T22:59:59+00:00", "1"), ("2024-02-29T23:00:00+00:00", "2"),
            ("2024-03-31T21:59:59+00:00", "3"), ("2024-03-31T22:00:00+00:00", "4"),
        ]:
            await self.booking(date, amount)
        await self.assert_total("5.00", 2, month=3, year=2024)

    async def test_new_york_month_boundaries_across_dst(self):
        await self.session.execute(text("UPDATE properties SET timezone='America/New_York' WHERE id='test-property'"))
        for date, amount in [
            ("2024-03-01T04:59:59+00:00", "1"), ("2024-03-01T05:00:00+00:00", "2"),
            ("2024-04-01T03:59:59+00:00", "3"), ("2024-04-01T04:00:00+00:00", "4"),
        ]:
            await self.booking(date, amount)
        await self.assert_total("5.00", 2, month=3, year=2024)

    async def test_fall_dst_repeated_hour_and_year_rollover(self):
        await self.session.execute(text("UPDATE properties SET timezone='America/New_York' WHERE id='test-property'"))
        for date in ["2024-11-03T05:30:00+00:00", "2024-11-03T06:30:00+00:00", "2025-01-01T04:59:59+00:00", "2025-01-01T05:00:00+00:00"]:
            await self.booking(date, "10")
        await self.assert_total("20.00", 2, month=11, year=2024)
        await self.assert_total("10.00", 1, month=12, year=2024)
        await self.assert_total("30.00", 3, year=2024)
        await self.assert_total("10.00", 1, month=1, year=2025)

    async def test_cross_month_stay_belongs_to_local_check_in_month(self):
        await self.booking("2024-02-28T12:00:00+00:00", "100", check_out="2024-03-05T12:00:00+00:00")
        await self.assert_total("100.00", 1, month=2, year=2024)
        await self.assert_total("0.00", 0, month=3, year=2024)

    async def test_subcent_amounts_are_summed_before_rounding(self):
        for amount in ["333.333", "333.333", "333.334"]:
            await self.booking("2024-03-15T12:00:00+00:00", amount)
        await self.assert_total("1000.00", 3, month=3, year=2024)

    async def test_half_cent_rounding_is_decimal_and_deterministic(self):
        await self.booking("2024-03-15T12:00:00+00:00", "1.005")
        await self.booking("2024-04-15T12:00:00+00:00", "-1.005")
        await self.assert_total("1.01", 1, month=3, year=2024)
        await self.assert_total("-1.01", 1, month=4, year=2024)

    async def test_mixed_currencies_are_not_added_together(self):
        await self.booking("2024-03-15T12:00:00+00:00", "10", "EUR")
        await self.assert_total("10.00", 1, month=3, year=2024)
        response = await self.summary(month=3, year=2024)
        self.assertEqual(response.json()["currency"], "EUR")
        await self.booking("2024-03-15T12:00:00+00:00", "10", "USD")
        self.entries.clear()
        self.assertEqual((await self.summary(month=3, year=2024)).status_code, 409)
        self.assertFalse(self.entries)

    async def test_invalid_period_and_missing_auth_are_rejected(self):
        for params in [{"month": 0, "year": 2024}, {"month": 13, "year": 2024}, {"month": 3}, {"year": 1}, {"year": 9999}]:
            self.assertEqual((await self.summary(**params)).status_code, 422)
        response = await self.client.get("/api/v1/dashboard/summary?property_id=prop-001")
        self.assertEqual(response.status_code, 401)

    async def test_client_tenant_parameters_cannot_change_ownership(self):
        headers = {**self.tenant_headers("tenant-b"), "X-Simulated-Tenant": "tenant-a"}
        response = await self.summary("prop-002", headers, tenant_id="tenant-a", client_id="tenant-a")
        self.assertEqual(response.status_code, 404)

    async def test_redis_outage_still_uses_real_database_values(self):
        self.redis.get.side_effect = RedisConnectionError("offline")
        self.redis.setex.side_effect = RedisConnectionError("offline")
        await self.assert_total("2250.00", 4, property_id="prop-001", headers=self.tenant_headers("tenant-a"), month=3, year=2024)

    async def test_database_failure_does_not_return_mock_revenue(self):
        from sqlalchemy.exc import OperationalError
        with patch.object(self.session, "execute", side_effect=OperationalError("SELECT", {}, Exception("offline"))):
            self.assertEqual((await self.summary()).status_code, 503)
        self.assertFalse(self.entries)

    async def test_pool_uses_database_url_and_reuses_engine(self):
        pool = DatabasePool()
        with patch.object(settings, "database_url", os.environ["TEST_DATABASE_URL"]):
            await pool.initialize()
            engine = pool.engine
            await pool.initialize()
            self.assertIs(pool.engine, engine)
            async with pool.get_session() as session:
                self.assertEqual((await session.execute(text("SELECT 1"))).scalar_one(), 1)
            await pool.close()
            self.assertIsNone(pool.engine)


if __name__ == "__main__":
    unittest.main()
