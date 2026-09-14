import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from app.api.v1 import auth_info, login
from app.config import settings
from app.core.auth import clear_auth_cache
from app.services.cache import get_revenue_summary


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        clear_auth_cache()
        app = FastAPI()
        app.include_router(login.router)
        app.include_router(auth_info.router)
        self.client = TestClient(app)

    def token(self, **claims):
        payload = {
            "id": "user-ocean",
            "email": "ocean@propertyflow.com",
            "app_metadata": {"tenant_id": "tenant-b"},
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        }
        payload.update(claims)
        return jwt.encode(payload, settings.secret_key, algorithm="HS256")

    def me(self, token):
        return self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    def test_assignment_accounts(self):
        for email, password, tenant in [
            ("sunset@propertyflow.com", "client_a_2024", "tenant-a"),
            ("ocean@propertyflow.com", "client_b_2024", "tenant-b"),
        ]:
            with self.subTest(email=email):
                response = self.client.post("/auth/login", json={"email": email, "password": password})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.me(response.json()["access_token"]).json()["tenant_id"], tenant)

    def test_wrong_password_is_rejected(self):
        for email in ["sunset@propertyflow.com", "ocean@propertyflow.com", "candidate@propertyflow.com"]:
            with self.subTest(email=email):
                response = self.client.post("/auth/login", json={"email": email, "password": "wrong"})
                self.assertEqual(response.status_code, 401)

    def test_forged_and_static_tokens_are_rejected(self):
        forged = jwt.encode(
            {"email": "candidate@propertyflow.com", "exp": int(time.time()) + 3600},
            "not-the-signing-key", algorithm="HS256",
        )
        for token in [forged, "mock-token-123"]:
            with self.subTest(token=token):
                self.assertEqual(self.me(token).status_code, 401)

    def test_tenant_comes_from_verified_claims_not_email_or_user_metadata(self):
        token = self.token(user_metadata={"tenant_id": "tenant-a"}, email="renamed@example.com")
        response = self.me(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tenant_id"], "tenant-b")

    def test_missing_tenant_does_not_default_to_client_a(self):
        response = self.me(self.token(app_metadata={}, user_metadata={"tenant_id": "tenant-a"}))
        self.assertEqual(response.status_code, 403)

    def test_expired_token_is_rejected_even_when_auth_is_cached(self):
        token = self.token(exp=int(time.time()) + 3600)
        self.assertEqual(self.me(token).status_code, 200)
        with patch("time.time", return_value=time.time() + 7200):
            self.assertEqual(self.me(token).status_code, 401)


class RevenueCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_property_id_has_separate_tenant_entries(self):
        entries = {}

        async def setex(key, ttl, value):
            self.assertEqual(ttl, 300)
            entries[key] = value

        async def calculate(property_id, tenant_id, db_session, month, year):
            return {"property_id": property_id, "tenant_id": tenant_id, "total": tenant_id}

        redis = AsyncMock()
        redis.get.side_effect = entries.get
        redis.setex.side_effect = setex
        with patch("app.services.cache.get_property", AsyncMock()), patch("app.services.cache.redis_client", redis), patch(
            "app.services.reservations.calculate_total_revenue", side_effect=calculate
        ) as calculator:
            for tenant in ["tenant-a", "tenant-b", "tenant-a", "tenant-b"]:
                result = await get_revenue_summary("prop-001", tenant, AsyncMock())
                self.assertEqual(result["tenant_id"], tenant)
            self.assertEqual(calculator.call_count, 2)


if __name__ == "__main__":
    unittest.main()
