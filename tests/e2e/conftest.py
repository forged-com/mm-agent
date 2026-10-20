from collections.abc import Iterator

import pytest

from tests.e2e.fake_control import FakeControlPlane
from tests.e2e.fake_landing import FakeLanding
from tests.fixtures import golden

TOKEN = "e2e-token"


@pytest.fixture
def landing() -> Iterator[FakeLanding]:
    fake = FakeLanding()
    fake.start()
    try:
        yield fake
    finally:
        fake.stop()


@pytest.fixture
def control(landing: FakeLanding) -> Iterator[FakeControlPlane]:
    fake = FakeControlPlane(TOKEN, golden.MM, landing)
    fake.start()
    try:
        yield fake
    finally:
        fake.stop()
