import logging

import pytest

from understand_anypaper.main import _HealthCheckAccessFilter


@pytest.mark.parametrize(
    ("request_target", "expected"),
    [
        ("/health", False),
        ("/health?source=probe", False),
        ("/health/details", True),
        ("/api/papers", True),
    ],
)
def test_health_check_access_filter(request_target: str, expected: bool) -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:12345", "GET", request_target, "1.1", 200),
        exc_info=None,
    )

    assert _HealthCheckAccessFilter().filter(record) is expected
