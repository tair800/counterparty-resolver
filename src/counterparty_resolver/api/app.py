"""The steward console: four screens over the resolver, the ledger and the evaluation.

**Read-only unless an approver token is configured.** `CR_APPROVER_TOKEN` gates every write. Unset,
the console renders exactly as it does in production and every approve/reject button is disabled,
with a banner saying so. That is the safe demo mode: a public link that shows the whole system and
cannot be used to write into it, and it fails *closed* — a missing token disables writes rather than
enabling them.

**Nothing here decides anything.** `resolve.py` decided when `artifacts/demo.json` was built, and a
steward's approval is recorded against that decision rather than replacing it. The console's job is
to put the evidence in front of a person and to write down what they did, which is why the merge
ledger is the only thing it mutates.

**The hold-out is not in here.** `demo.json` is drawn from the development split only, and
`test_api.py` asserts it, because a console browsing the held-out pairs is a slower way of scoring
them twice.
"""

from __future__ import annotations

import functools
import hmac
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from counterparty_resolver.store import crosswalk, ledger

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / "artifacts"
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

#: Where the console's records live. `:memory:` by default, because the demo's whole state is one
#: ledger that may be reset by a restart -- and a deployment that persisted approvals would need a
#: retention answer this project has not written.
DATABASE_PATH = os.environ.get("CR_DATABASE", ":memory:")

#: One logger for the whole console. Structured in the sense that matters here -- every record
#: carries the fields somebody answering "why was this merged" needs, rather than a sentence they
#: have to parse. There was none at all until a review pointed out that a system arguing "the person
#: asking why a payment went to the wrong counterparty needs the difference" was discarding every
#: refused approval in silence.
log = logging.getLogger("counterparty_resolver.console")

#: Where the console's records go, and how loud. `CR_LOG_LEVEL` for the level.
LOG_LEVEL = os.environ.get("CR_LOG_LEVEL", "INFO").upper()


def configure_logging() -> None:
    """Give the console's logger somewhere to write.

    Without this the calls below emit nothing: Python's default configuration drops records from a
    logger with no handler, and uvicorn configures its own loggers rather than the root. A logging
    call that produces no output is worse than none -- it reads as an audit trail in review and is
    silence in production, which is precisely the defect this module was criticised for having.

    Guarded, because the application owns the root logger and a test or an embedding process may
    have configured it already.
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=LOG_LEVEL,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    log.setLevel(LOG_LEVEL)


def _load(name: str) -> dict[str, Any]:
    path = ARTIFACTS / name
    if not path.is_file():
        raise RuntimeError(f"{name} is missing; run `make artifacts`")
    return dict(json.loads(path.read_text(encoding="utf-8")))


class Console:
    """Everything the four screens read, assembled once at startup.

    Held on the app rather than in module globals so a test can build a second one, and so the
    dependency on the artifacts is visible in one place instead of implied by imports.
    """

    def __init__(self, *, database: str = DATABASE_PATH) -> None:
        self.demo = _load("demo.json")
        self.evaluation = _load("evaluation.json")
        self.pairs: dict[str, dict[str, Any]] = {p["pair_id"]: p for p in self.demo["pairs"]}
        # `check_same_thread=False` plus one lock, because Starlette runs sync endpoints on
        # a thread pool and an in-memory database cannot be reopened per request -- it *is*
        # the state. Every method below takes the lock and no endpoint touches `connection`
        # directly, so serialisation is a property of this class rather than a rule that
        # each handler has to remember.
        self.connection = crosswalk.connect(database, check_same_thread=False)
        self._lock = threading.RLock()
        self._seed()
        log.info(
            "console ready",
            extra={
                "pairs": len(self.pairs),
                "writable": self.writable,
                "database": database,
                "corpus_revision": self.demo["corpus_revision"],
            },
        )

    def _seed(self) -> None:
        """Put the demo's records into the legacy tables, each under its own source's schema.

        Skipped when the tables already hold rows. With `CR_DATABASE` pointed at a file -- which
        `render.yaml` and `.env.example` both invite -- the second boot re-inserted the same LEIs
        and died on the primary key, so the service came up once and crash-looped thereafter.
        """
        if self.connection.execute("SELECT 1 FROM legacy_gleif LIMIT 1").fetchone() is not None:
            log.info("legacy tables already populated; skipping seed")
            return

        gleif: list[dict[str, Any]] = []
        companies_house: list[dict[str, Any]] = []
        seen: set[str] = set()
        for pair in self.demo["pairs"]:
            for side in ("left", "right"):
                record = pair[side]
                if record["source_id"] in seen:
                    continue
                seen.add(record["source_id"])
                gleif.append(
                    {
                        "lei": record["source_id"],
                        "legal_name": record["legal_name"],
                        "country": record["country"],
                        "city": record["city"],
                        "postal_code": record["postal_code"],
                        "address_line": (record["address_lines"] or [None])[0],
                        "registered_at": record["registration_authority"],
                        "registered_as": record["registered_as"],
                        "registration_status": "ISSUED",
                        "successor_lei": None,
                        # Deduplicated: GLEIF publishes the same prior name under more than
                        # one `otherNames` type, and the child table keys on (lei, type, name).
                        "other_names": [("OTHER", n) for n in dict.fromkeys(record["other_names"])],
                    }
                )
        crosswalk.seed(self.connection, gleif=gleif, companies_house=companies_house)

    @property
    def writable(self) -> bool:
        """Whether an approver token is configured. Missing means read-only, never open."""
        return bool(os.environ.get("CR_APPROVER_TOKEN"))

    def queue(self) -> list[dict[str, Any]]:
        """Pairs still awaiting a person, highest score first: the nearest-decided first."""
        # A merge that has since been reversed puts the pair back in the queue. Filtering on
        # `action = merge` alone left it out, which made an unmerge look like it had half worked:
        # the ledger showed the reversal and the queue still hid the pair.
        with self._lock:
            decided = {
                f"{row[0]}|{row[1]}"
                for row in self.connection.execute(
                    "SELECT m.left_id, m.right_id FROM merge_ledger m "
                    "WHERE m.action = 'merge' AND NOT EXISTS ("
                    "  SELECT 1 FROM merge_ledger u WHERE u.action = 'unmerge' "
                    "    AND u.reverses_entry_id = m.entry_id)"
                )
            }
        return [
            pair
            for pair in self.demo["pairs"]
            if pair["decision"] == "review"
            and f"{pair['left']['source_id']}|{pair['right']['source_id']}" not in decided
        ]

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            self.connection.row_factory = sqlite3.Row
            rows = self.connection.execute(
                "SELECT * FROM merge_ledger ORDER BY entry_id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def resolved_id_for(self, source: str, source_id: str) -> str | None:
        with self._lock:
            return ledger.resolved_id_for(self.connection, source, source_id)

    def record_merge(self, pair: dict[str, Any], approver: str) -> None:
        """A steward's approval. Idempotent on the pair, because a double-click is a retry."""
        with self._lock:
            ledger.merge(
                self.connection,
                idempotency_key=f"approve:{pair['pair_id']}",
                left=("gleif", pair["left"]["source_id"]),
                right=("gleif", pair["right"]["source_id"]),
                resolved_id=f"cp-{pair['pair_id'][:12]}",
                decision="match",
                score=pair["score"],
                evidence=list(pair["contributions"]),
                approver_id=approver,
            )

    def record_unmerge(self, entry_id: int, approver: str) -> None:
        """Reverse one merge by appending. Nothing is edited; the database would refuse."""
        with self._lock:
            ledger.unmerge(
                self.connection,
                idempotency_key=f"unmerge:{entry_id}",
                reverses_entry_id=entry_id,
                approver_id=approver,
                reason="reversed from the steward console",
            )


def _console_of(request: Request) -> Console:
    """The console this app was built with.

    Module level, not a closure inside `create_app`, because `from __future__ import annotations`
    turns every annotation into a string and FastAPI resolves those against the *module* namespace.
    A dependency alias defined inside the factory is invisible there, and the symptom is not an
    error: FastAPI decides `console` must be a query parameter and every screen returns 422.
    """
    return request.app.state.console  # type: ignore[no-any-return]


def _require_approver(request: Request, token: str = Form(default="")) -> str:
    """The write gate. Fails closed: no configured token means no writes at all.

    `compare_digest` rather than `==` because the comparison is against a secret and an early exit
    on the first wrong byte is a timing oracle. The cost of getting this right is one import.
    """
    configured = os.environ.get("CR_APPROVER_TOKEN")
    if not configured:
        log.warning("write refused: read-only demo", extra={"path": request.url.path})
        raise HTTPException(
            status_code=403,
            detail=(
                "This console is running in read-only demo mode: no approver token is "
                "configured, so no approval can be recorded."
            ),
        )
    # Bytes, not str: `compare_digest` raises TypeError on a non-ASCII `str`, which turned a wrong
    # token containing an accented character into a 500 on an endpoint whose contract is 401.
    if not hmac.compare_digest(token.encode(), configured.encode()):
        log.warning("write refused: token mismatch", extra={"path": request.url.path})
        raise HTTPException(status_code=401, detail="approver token does not match")
    # The identity is the token's, not the caller's. An earlier version read `x-approver-id` from
    # the request and wrote it into the ledger, so the append-only audit trail recorded whoever the
    # client *said* they were -- an unauthenticated string in the one column whose purpose is
    # attribution. One shared token means one identity until there is real authentication, and
    # saying so is better than recording a name nobody verified.
    return "steward"


Current = Annotated[Console, Depends(_console_of)]
Approver = Annotated[str, Depends(_require_approver)]


def create_app(*, console: Console | None = None) -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="counterparty-resolver — steward console",
        description=__doc__,
        version="0.1.0",
    )
    app.state.console = console or Console()

    # ------------------------------------------------------------------------------- screens

    @app.get("/", response_class=HTMLResponse)
    def review_queue(request: Request, console: Current) -> Any:
        """Screen 1. What a person has to look at, and how much of it there is."""
        queue = console.queue()
        return TEMPLATES.TemplateResponse(
            request,
            "queue.html",
            {
                "queue": queue,
                "counts": console.demo["counts"],
                "writable": console.writable,
                "evaluation": console.evaluation,
            },
        )

    @app.get("/pairs/{pair_id}", response_class=HTMLResponse)
    def pair_detail(request: Request, pair_id: str, console: Current) -> Any:
        """Screen 2. Every feature, what it saw, and what it contributed."""
        pair = console.pairs.get(pair_id)
        if pair is None:
            raise HTTPException(status_code=404, detail="no such pair in this console")
        return TEMPLATES.TemplateResponse(
            request,
            "pair.html",
            {
                "pair": pair,
                "writable": console.writable,
                "resolved_id": console.resolved_id_for("gleif", pair["left"]["source_id"]),
            },
        )

    @app.get("/ledger", response_class=HTMLResponse)
    def merge_ledger(request: Request, console: Current) -> Any:
        """Screen 3. Append-only history, with the evidence each decision was made on."""
        return TEMPLATES.TemplateResponse(
            request,
            "ledger.html",
            {"entries": console.history(), "writable": console.writable},
        )

    @app.get("/evaluation", response_class=HTMLResponse)
    def evaluation(request: Request, console: Current) -> Any:
        """Screen 4. The published numbers, including the ones that do not flatter the system."""
        return TEMPLATES.TemplateResponse(
            request,
            "evaluation.html",
            {"evaluation": console.evaluation, "writable": console.writable},
        )

    # -------------------------------------------------------------------------------- writes

    @app.post("/pairs/{pair_id}/approve")
    def approve(
        pair_id: str,
        console: Current,
        approver: Approver,
    ) -> RedirectResponse:
        """Record a steward's merge. Idempotent on the pair, because a double-click is a retry."""
        pair = console.pairs.get(pair_id)
        if pair is None:
            raise HTTPException(status_code=404, detail="no such pair in this console")
        console.record_merge(pair, approver)
        log.info(
            "merge approved",
            extra={"pair_id": pair_id, "approver_id": approver, "score": pair["score"]},
        )
        return RedirectResponse(url="/ledger", status_code=303)

    @app.post("/ledger/{entry_id}/unmerge")
    def undo(
        entry_id: int,
        console: Current,
        approver: Approver,
    ) -> RedirectResponse:
        """Reverse one merge by appending to the ledger.

        Nothing is edited and nothing is deleted; the database would refuse either.
        """
        try:
            console.record_unmerge(entry_id, approver)
        except ledger.UnmergeRefusedError as refusal:
            log.warning("unmerge refused", extra={"entry_id": entry_id, "reason": str(refusal)})
            raise HTTPException(status_code=409, detail=str(refusal)) from refusal
        log.info("merge reversed", extra={"entry_id": entry_id, "approver_id": approver})
        return RedirectResponse(url="/ledger", status_code=303)

    # --------------------------------------------------------------------------------- health

    @app.get("/health")
    def health(console: Current) -> dict[str, Any]:
        """Enough to tell a restarted container from a broken one."""
        return {
            "status": "ok",
            "writable": console.writable,
            "pairs_loaded": len(console.pairs),
            "corpus_revision": console.demo["corpus_revision"],
            "ledger_entries": len(console.history()),
        }

    return app


@functools.cache
def _default_app() -> FastAPI:
    return create_app()


def __getattr__(name: str) -> FastAPI:
    """`app` on first access, so `uvicorn counterparty_resolver.api.app:app` needs no flag.

    Lazily rather than at import, because building it reads two artifacts and seeds a
    database. Importing `create_app` for a test should do neither, and a deployment still
    gets the one object it would have got from a module-level assignment.
    """
    if name != "app":
        raise AttributeError(name)
    return _default_app()
