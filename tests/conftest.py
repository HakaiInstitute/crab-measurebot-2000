import pytest_asyncio
from tortoise import Tortoise


@pytest_asyncio.fixture(autouse=True)
async def init_db():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["crab_measurebot_2000.main"]},
    )
    await Tortoise.generate_schemas()
    yield
    await Tortoise.close_connections()
