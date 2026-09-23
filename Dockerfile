# The console, and nothing that only a developer needs.
#
# Two stages so the image carries no build toolchain and no dev dependencies: `uv` resolves against
# the committed lockfile in the first stage, and the second stage gets the virtualenv and the
# source. `--frozen` means a lockfile that has drifted from `pyproject.toml` fails the build rather
# than being silently re-resolved into an image nobody tested.

FROM ghcr.io/astral-sh/uv:0.9-python3.12-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# The dependency layer on its own, so editing source does not re-resolve the world.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm

# Not root. The console writes nothing to disk -- its database is in memory -- so there is no
# reason for the process to be able to.
RUN useradd --create-home --uid 10001 steward
WORKDIR /app

COPY --from=build --chown=steward:steward /app/.venv /app/.venv
COPY --from=build --chown=steward:steward /app/src /app/src

# The evidence the four screens read. Copied explicitly rather than with `COPY . .`, so an image
# cannot quietly acquire a `.env`, a cache directory or a raw GLEIF payload.
COPY --chown=steward:steward artifacts/demo.json artifacts/evaluation.json /app/artifacts/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER steward
EXPOSE 8000

# No CR_APPROVER_TOKEN is baked in, so the container starts read-only. Supplying one at run time is
# a deliberate act; a token in an image is a credential in a registry.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "counterparty_resolver.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
