import pytest
from escondite import Cache


@pytest.fixture
def cache() -> Cache:
    return Cache()
