"""
Minimal tenant resolver for authentication.
"""
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class TenantResolver:
    """Minimal tenant resolver that extracts tenant_id from JWT claims."""

    @staticmethod
    def resolve_tenant_from_token(token_payload: dict) -> Optional[str]:
        """
        Extract tenant_id from JWT token payload.

        Args:
            token_payload: Decoded JWT payload

        Returns:
            Tenant ID if found, None otherwise
        """
        # Only server-controlled claims may grant tenant access.
        if 'app_metadata' in token_payload:
            tenant_id = (token_payload['app_metadata'] or {}).get('tenant_id')
            if tenant_id:
                return tenant_id

        # Try root level
        tenant_id = token_payload.get('tenant_id')
        if tenant_id:
            return tenant_id

        logger.warning("No tenant_id found in token payload")
        return None

    @staticmethod
    def resolve_tenant_from_user(user_data: dict) -> Optional[str]:
        """
        Extract tenant_id from user data.

        Args:
            user_data: User data dictionary

        Returns:
            Tenant ID if found, None otherwise
        """
        return TenantResolver.resolve_tenant_from_token(user_data)

    @staticmethod
    async def resolve_tenant_id(user_id: str, user_email: str, token: Optional[str] = None) -> Optional[str]:
        """
        Resolve tenant ID for a user.
        
        Args:
            user_id: User ID
            user_email: User email
            
        Returns:
            Tenant ID
        """
        from ..database import supabase
        from ..config import settings
        from jose import JWTError, jwt

        if token:
            try:
                payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"], audience="authenticated")
                return TenantResolver.resolve_tenant_from_token(payload)
            except JWTError:
                response = supabase.auth.get_user(token)
        else:
            response = supabase.auth.admin.get_user_by_id(user_id)
        user = getattr(response, "user", None)
        if user:
            return TenantResolver.resolve_tenant_from_user({"app_metadata": user.app_metadata})
        return None

    @staticmethod
    async def update_user_tenant_metadata(user_id: str, tenant_id: str) -> None:
        """
        Update user metadata with tenant_id.
        
        Args:
            user_id: User ID
            tenant_id: Tenant ID
        """
        # No-op in this resolver implementation.
        pass
