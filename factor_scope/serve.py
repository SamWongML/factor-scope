"""A read-only HTTP view over the dashboard history — the seam a frontend consumes.

Serves the recorded nights (see :mod:`factor_scope.history`) as JSON: the index manifest, any
single night, and the latest one. Read-only by construction — it never ingests or reasons, so the
snapshot boundary holds. The contract models double as the response models, so ``/openapi.json``
is the typed schema a frontend client is generated from. FastAPI is a pinned ``serve`` extra
imported lazily inside :func:`create_app` (see digest/claude_code.py for the pattern), so the
offline suite never needs a web stack.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING

from factor_scope.contract import Dashboard, DashboardIndex
from factor_scope.history import load, read_index

if TYPE_CHECKING:
    from fastapi import FastAPI

# The date IS the filename; rejecting anything else also forecloses path traversal.
_AS_OF = re.compile(r"\d{4}-\d{2}-\d{2}")

# A recorded night never changes (re-runs rewrite it byte-for-byte), so dated responses are
# cacheable forever; the index and `latest` move nightly and must revalidate.
_IMMUTABLE = "public, max-age=31536000, immutable"
_REVALIDATE = "no-cache"


def create_app(history_dir: Path, *, allow_origins: tuple[str, ...] = ("*",)) -> "FastAPI":
    """The read-only history API over one history directory.

    ``allow_origins`` defaults wide open — this is a localhost, single-user surface; narrow it
    before binding beyond the machine.
    """

    # lazy: the pinned serve extra is only needed once an app is actually created
    from fastapi import FastAPI, HTTPException, Response
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="factor-scope history", description=__doc__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allow_origins),
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/dashboards", response_model=DashboardIndex)
    def dashboards(response: Response) -> DashboardIndex:
        """Every recorded night, oldest first."""

        response.headers["Cache-Control"] = _REVALIDATE
        return read_index(history_dir)

    @app.get("/dashboards/latest", response_model=Dashboard)
    def latest(response: Response) -> Dashboard:
        """The newest recorded night."""

        entries = read_index(history_dir).entries
        dash = load(history_dir, entries[-1].as_of) if entries else None
        if dash is None:
            raise HTTPException(status_code=404, detail="no nights recorded yet")
        response.headers["Cache-Control"] = _REVALIDATE
        return dash

    @app.get("/dashboards/{as_of}", response_model=Dashboard)
    def night(as_of: str, response: Response) -> Dashboard:
        """One recorded night by its as-of date."""

        dash = load(history_dir, as_of) if _AS_OF.fullmatch(as_of) else None
        if dash is None:
            raise HTTPException(status_code=404, detail=f"no dashboard for {as_of!r}")
        response.headers["Cache-Control"] = _IMMUTABLE
        return dash

    return app
