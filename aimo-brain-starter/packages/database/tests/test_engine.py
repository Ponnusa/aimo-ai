import pytest
from database.engine import session_scope
from sqlalchemy import Integer, select
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, new_engine, new_session_factory


class _Widget(Base):
    __tablename__ = "widgets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


def test_new_engine_rejects_empty_url() -> None:
    with pytest.raises(ValueError):
        new_engine("")


@pytest.mark.asyncio
async def test_session_scope_commits_on_success() -> None:
    engine = new_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = new_session_factory(engine)
    async with session_scope(factory) as session:
        session.add(_Widget(id=1))

    async with factory() as session:
        result = await session.execute(select(_Widget))
        assert result.scalar_one().id == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_session_scope_rolls_back_on_error() -> None:
    engine = new_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = new_session_factory(engine)
    with pytest.raises(RuntimeError):
        async with session_scope(factory) as session:
            session.add(_Widget(id=2))
            raise RuntimeError("boom")

    async with factory() as session:
        result = await session.execute(select(_Widget))
        assert result.scalars().all() == []

    await engine.dispose()
