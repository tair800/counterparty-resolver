"""The steward console, exercised through HTTP.

The two things worth testing here are not the screens. They are:

**The write gate fails closed.** With no `CR_APPROVER_TOKEN` the console must refuse every write,
and it must refuse with 403 rather than by having no button — a disabled input is a courtesy to the
person looking at the page, not a control. The test asks the endpoint directly.

**The hold-out never reaches the console.** `demo.json` is drawn from the development split. A
console that browsed the held-out pairs would be a slower way of scoring them a second time, and
ADR-001 fixes that they are scored once.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from counterparty_resolver.api.app import Console, create_app

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
#: Not a secret: it never leaves this file, and the real one is read from the environment.
TOKEN = "test-token-not-a-secret"  # noqa: S105


@pytest.fixture
def read_only(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("CR_APPROVER_TOKEN", raising=False)
    return TestClient(create_app(console=Console()))


@pytest.fixture
def writable(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CR_APPROVER_TOKEN", TOKEN)
    return TestClient(create_app(console=Console()))


def _a_pair_id(client: TestClient) -> str:
    match = re.search(r"/pairs/([0-9a-f]+)", client.get("/").text)
    assert match, "the queue rendered no pairs"
    return match.group(1)


# ------------------------------------------------------------------------------- the four screens


@pytest.mark.parametrize("path", ["/", "/ledger", "/evaluation", "/health"])
def test_every_screen_renders(read_only: TestClient, path: str) -> None:
    response = read_only.get(path)

    assert response.status_code == 200


def test_the_pair_screen_shows_every_feature_and_what_it_contributed(
    read_only: TestClient,
) -> None:
    """ADR-001 wants evidence a person can disagree with: the parts, not only the total."""
    body = read_only.get(f"/pairs/{_a_pair_id(read_only)}").text

    for feature in ("name similarity", "distinctive token agreement", "country agreement"):
        assert feature in body
    assert "contributed" in body


def test_the_evaluation_screen_publishes_the_baseline_that_beats_the_system_on_f1(
    read_only: TestClient,
) -> None:
    """A console that showed only the flattering row would be worse than no console."""
    body = read_only.get("/evaluation").text

    assert "identifier_first" in body
    assert "beats it on F1" in body


def test_an_unknown_pair_is_a_404_rather_than_a_blank_screen(read_only: TestClient) -> None:
    assert read_only.get("/pairs/no-such-pair").status_code == 404


# ----------------------------------------------------------------------------- the write gate


def test_without_a_token_every_write_is_refused(read_only: TestClient) -> None:
    """Fails closed. A missing configuration disables writes; it never enables them."""
    pair_id = _a_pair_id(read_only)

    approve = read_only.post(f"/pairs/{pair_id}/approve", data={"token": "anything"})
    unmerge = read_only.post("/ledger/1/unmerge", data={"token": "anything"})

    assert approve.status_code == 403
    assert unmerge.status_code == 403
    assert read_only.get("/health").json()["writable"] is False
    assert "Read-only demo" in read_only.get("/").text


def test_a_wrong_token_is_rejected(writable: TestClient) -> None:
    pair_id = _a_pair_id(writable)

    assert writable.post(f"/pairs/{pair_id}/approve", data={"token": "wrong"}).status_code == 401


# ------------------------------------------------------------------- approve, replay, and reverse


def test_an_approval_is_recorded_once_however_many_times_it_is_submitted(
    writable: TestClient,
) -> None:
    """A steward double-clicking is a retry, and a retry must not be a second merge."""
    pair_id = _a_pair_id(writable)

    for _ in range(3):
        response = writable.post(
            f"/pairs/{pair_id}/approve", data={"token": TOKEN}, follow_redirects=False
        )
        assert response.status_code == 303

    assert writable.get("/health").json()["ledger_entries"] == 1


def test_reversing_a_merge_returns_the_pair_to_the_queue(writable: TestClient) -> None:
    """The console's own version of the unmerge guarantee: the steward sees the state restored."""
    before = writable.get("/").text.count('class="card" href')
    pair_id = _a_pair_id(writable)

    writable.post(f"/pairs/{pair_id}/approve", data={"token": TOKEN}, follow_redirects=False)
    assert writable.get("/").text.count('class="card" href') == before - 1

    writable.post("/ledger/1/unmerge", data={"token": TOKEN}, follow_redirects=False)

    assert writable.get("/").text.count('class="card" href') == before
    assert writable.get("/health").json()["ledger_entries"] == 2, "the reversal is an extra entry"


def test_submitting_the_same_reversal_twice_records_it_once(writable: TestClient) -> None:
    """A replayed reversal is a retry, and the console keys it as one: `unmerge:{entry_id}`.

    The 409 path is the different case -- some *other* request trying to reverse a merge that has
    already been reversed -- and `test_store.py` covers it at the ledger, where the rule lives.
    """
    pair_id = _a_pair_id(writable)
    writable.post(f"/pairs/{pair_id}/approve", data={"token": TOKEN}, follow_redirects=False)
    writable.post("/ledger/1/unmerge", data={"token": TOKEN}, follow_redirects=False)

    again = writable.post("/ledger/1/unmerge", data={"token": TOKEN}, follow_redirects=False)

    assert again.status_code == 303
    assert writable.get("/health").json()["ledger_entries"] == 2


# ------------------------------------------------------------------------------- the hold-out


def test_the_console_never_sees_a_held_out_pair() -> None:
    """Scored once, at `b67b83e`. A console browsing them is a second look wearing a UI."""
    demo: dict[str, Any] = json.loads((ARTIFACTS / "demo.json").read_text(encoding="utf-8"))
    split: dict[str, Any] = json.loads((ARTIFACTS / "holdout.json").read_text(encoding="utf-8"))
    held_out = set(split["holdout_pair_ids"])

    leaked = [pair["pair_id"] for pair in demo["pairs"] if pair["pair_id"] in held_out]

    assert not leaked, f"{len(leaked)} held-out pairs reached the console: {leaked[:5]}"
