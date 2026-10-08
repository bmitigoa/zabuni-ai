"""Shared fixtures. Tests run against the REAL database built from the frozen snapshot:
build it first with `python data/loader.py` (tests are skipped, not faked, if it is missing)."""
import pytest

from mcp_server.common import DB_PATH, rows


def pytest_collection_modifyitems(config, items):
    if not DB_PATH.is_file():
        skip = pytest.mark.skip(reason=f"{DB_PATH} missing - run `python data/loader.py` first")
        for item in items:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def known_award():
    """A real award from a large buyer, used wherever a test needs a valid ocid/award_id."""
    r = rows("SELECT ocid, award_id, buyer_name, title, category FROM award_facts "
             "WHERE buyer_norm = 'MAKUENI COUNTY GOVERNMENT' AND award_amount > 0 AND category IS NOT NULL "
             "ORDER BY award_amount DESC LIMIT 1")
    assert r, "expected Makueni County Government awards in the snapshot"
    return r[0]
