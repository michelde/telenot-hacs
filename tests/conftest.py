import pytest

from .simulator import Gateway


@pytest.fixture
async def gateway():
    server = await Gateway().start()
    yield server
    await server.close()
