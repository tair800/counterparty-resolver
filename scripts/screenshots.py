"""Capture the console's four screens into `docs/screenshots/`.

    uv run playwright install chromium     # once
    uv run python scripts/screenshots.py

Scripted rather than hand-captured so the images in the README can be regenerated after a change
and be *checked* — a screenshot nobody can reproduce is a claim about a UI that may no longer exist.
The server is started by this script, the token it uses is generated here and never leaves the
process, and the database is in memory, so running it changes nothing on disk except the images.

Each screen is captured twice, light and dark, because the console follows the reader's system
theme and a README that only shows one of them is showing half the work.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"

#: Wide enough for the evidence table to breathe, narrow enough to stay legible in a README.
#: Passed as a literal at the call site rather than as a constant, because playwright types the
#: parameter as a TypedDict and a `dict[str, int]` constant is not one.
WIDTH, HEIGHT = 1180, 900

#: Given up on after this long. A console that has not answered in 30 seconds is broken, and
#: waiting longer only makes the failure slower to find.
BOOT_TIMEOUT_SECONDS = 30


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(url: str) -> None:
    deadline = time.monotonic() + BOOT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        with (
            contextlib.suppress(urllib.error.URLError, ConnectionError, OSError),
            # S310: a localhost URL this function built, not a scheme from anywhere else.
            urllib.request.urlopen(url, timeout=1) as response,  # noqa: S310
        ):
            if response.status == 200:
                return
        time.sleep(0.2)
    raise RuntimeError(f"the console did not answer at {url} within {BOOT_TIMEOUT_SECONDS}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=SHOTS)
    args = parser.parse_args()

    try:
        # PLC0415: imported here rather than at the top, so the rest of the repository does not
        # depend on a dev-only package. A missing playwright prints one line; it breaks nothing.
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:  # pragma: no cover - the dev extra is not installed
        print("playwright is not installed: uv sync --dev && uv run playwright install chromium")
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    environment = dict(os.environ, CR_APPROVER_TOKEN=secrets.token_urlsafe(16))

    server = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-m",
            "uvicorn",
            "counterparty_resolver.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=environment,
    )
    try:
        _wait_for(f"{base}/health")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            pair_id = _first_pair_id(browser, base)
            screens = {
                "queue": "/",
                "pair": f"/pairs/{pair_id}",
                "ledger": "/ledger",
                "evaluation": "/evaluation",
            }
            for scheme in ("light", "dark"):
                context = browser.new_context(
                    viewport={"width": WIDTH, "height": HEIGHT}, color_scheme=scheme
                )
                page = context.new_page()
                for name, path in screens.items():
                    page.goto(f"{base}{path}", wait_until="networkidle")
                    out = args.out / f"{name}-{scheme}.png"
                    page.screenshot(path=str(out), full_page=name != "queue")
                    print(f"  wrote {out.relative_to(ROOT)}")
                context.close()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    return 0


def _first_pair_id(browser: object, base: str) -> str:
    """Whichever pair the queue puts first, so the shot follows the data, not a fixed id."""
    page = browser.new_page()  # type: ignore[attr-defined]
    page.goto(f"{base}/", wait_until="networkidle")
    href = str(page.locator("a.card").first.get_attribute("href"))
    page.close()
    return href.rsplit("/", 1)[-1]


if __name__ == "__main__":
    raise SystemExit(main())
