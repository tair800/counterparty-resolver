"""The console's controls: nothing interactive inside a link, and one control per action.

HTML forbids interactive content inside an `<a>`. The pair page's "Back to the queue" control was
a `<button>` wrapped in an `<a>`, which browsers expose as two focusable controls carrying the same
name for one action. The link carries the ghost-button styling itself instead.

The scan looks for an interactive element opened between an `<a ...>` and the next `</a>`, which is
what a nested control looks like in these templates; none of them nests anchors. It is a check on
these templates, not a general HTML validator.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from counterparty_resolver.api.app import Console, create_app

TEMPLATES = (
    Path(__file__).resolve().parents[1] / "src" / "counterparty_resolver" / "api" / "templates"
)
INTERACTIVE = r"a|button|input|select|textarea|label|details|summary"
INTERACTIVE_INSIDE_LINK = rf"<a\b[^>]*>(?:(?!</a>).)*?<(?:{INTERACTIVE})\b"
#: Which interactive elements carry a given name, for counting the controls one action offers. The
#: name is matched inside a lookahead, so an outer element's match does not swallow a nested one.
CONTROL_NAMED = r"<(a|button)\b[^>]*>(?=(?:(?!</\1>).)*?{})"
FLAGS = re.DOTALL | re.IGNORECASE


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("CR_APPROVER_TOKEN", raising=False)
    return TestClient(create_app(console=Console()))


def test_no_template_puts_an_interactive_element_inside_a_link() -> None:
    scanned = sorted(path.name for path in TEMPLATES.glob("*.html"))
    assert scanned, f"no templates found under {TEMPLATES}"
    offenders = [
        name
        for name in scanned
        if re.search(INTERACTIVE_INSIDE_LINK, (TEMPLATES / name).read_text(encoding="utf-8"), FLAGS)
    ]
    assert offenders == []


def test_the_pair_page_offers_one_control_back_to_the_queue(client: TestClient) -> None:
    queue = client.get("/")
    assert queue.status_code == 200
    pair = re.search(r"/pairs/[0-9a-f]+", queue.text)
    assert pair is not None, "the queue rendered no pairs"

    page = client.get(pair.group(0))
    assert page.status_code == 200
    assert re.search(INTERACTIVE_INSIDE_LINK, page.text, FLAGS) is None
    # Exactly one focusable element carries the name, and it is the link itself. Two would be the
    # nested pair this guards against: the anchor, and the button inside it.
    named = re.findall(CONTROL_NAMED.format("Back to the queue"), page.text, FLAGS)
    assert named == ["a"]
    assert '<a href="/" class="ghost">Back to the queue</a>' in page.text
