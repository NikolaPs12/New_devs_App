from contextlib import asynccontextmanager
import logging

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import settings

logger = logging.getLogger(__name__)


class DatabasePool:
    def __init__(self):
        self.engine = None
        self.session_factory = None

    async def initialize(self):
        if self.engine is not None:
            return
        database_url = make_url(settings.database_url).set(drivername="postgresql+asyncpg")
        self.engine = create_async_engine(
            database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,
            pool_recycle=settings.database_pool_recycle,
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        logger.info("Database connection pool initialized")

    async def close(self):
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
            self.session_factory = None

    @asynccontextmanager
    async def get_session(self):
        if self.session_factory is None:
            await self.initialize()
        async with self.session_factory() as session:
            yield session


# Share one pool across requests.
db_pool = DatabasePool()


async def get_db_session() -> AsyncSession:
    async with db_pool.get_session() as session:
        yield session
