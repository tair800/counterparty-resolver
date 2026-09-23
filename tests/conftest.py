"""Session-level guards for the predeclared kill test.

`test_predeclaration.py` checks the kill test as **text**. Project 4 learned the hard way that this
is not enough: banning the literal string `importorskip` bans one spelling of one mechanism, and
adding `pytest.mark.skip` left every text assertion green while zero kill tests ran. So the real
guard is here, and it asks pytest what it is about to run instead of reading source.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

KILL_TEST_FILE = "test_kill_criteria.py"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Refuse the whole session if a predeclared kill test has been disabled by a mark.

    `tryfirst` so this sees collected items **before** `-m` deselection removes them: deselection is
    not disablement, and a lane that legitimately deselects these must still not be able to silence
    them. `UsageError` rather than a failing test, because a failing test can be deselected too.
    """
    if importlib.util.find_spec("counterparty_resolver") is None:
        return  # Genuinely still predeclared: the package does not exist yet.

    kill = [item for item in items if Path(str(item.fspath)).name == KILL_TEST_FILE]
    if not kill:
        return

    disabled = sorted(
        item.nodeid
        for item in kill
        if item.get_closest_marker("skip") is not None
        or item.get_closest_marker("skipif") is not None
    )
    if disabled:
        raise pytest.UsageError(
            "the predeclared kill test has been disabled by a mark, so the criterion that decides "
            "whether this project ships is not being evaluated: " + ", ".join(disabled)
        )
