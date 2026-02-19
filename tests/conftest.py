"""Pytest configuration and shared fixtures."""


def pytest_addoption(parser):
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run integration tests that hit real external APIs (network required)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (skipped by default, use --integration)",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--integration"):
        skip = __import__("pytest").mark.skip(
            reason="Integration tests skipped. Run with --integration to enable."
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)
