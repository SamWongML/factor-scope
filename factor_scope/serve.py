"""A read-only HTTP view over the dashboard history — the seam a frontend consumes.

Serves the recorded nights (see :mod:`factor_scope.history`) as JSON: the index manifest, any
single night, and the latest one. Read-only by construction — it never ingests or reasons, so the
snapshot boundary holds. The contract models double as the response models, so ``/openapi.json``
is the typed schema a frontend client is generated from. FastAPI is a pinned ``serve`` extra
imported lazily inside :func:`create_app` (see digest/claude_code.py for the pattern), so the
offline suite never needs a web stack.
"""

import hashlib
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
# cacheable forever; the index and `latest` move once a night, so they stay cacheable but
# revalidate against a strong ETag (a cheap 304) rather than refetching every time.
_IMMUTABLE = "public, max-age=31536000, immutable"
_REVALIDATE = "public, max-age=0, must-revalidate"


def _etag(payload: str) -> str:
    """A strong validator over the response's identity — content for the index, the pointer's
    target for ``latest`` — so an unchanged night index revalidates to 304."""

    return '"' + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32] + '"'


def _page_links(offset: int, limit: int, total: int) -> str:
    """RFC 5988 ``next``/``prev`` relations so a client can walk the whole history a page at a
    time — present only where there is a further page in that direction."""

    rels = []
    if offset + limit < total:
        rels.append(f'</dashboards?limit={limit}&offset={offset + limit}>; rel="next"')
    if offset > 0:
        rels.append(f'</dashboards?limit={limit}&offset={max(offset - limit, 0)}>; rel="prev"')
    return ", ".join(rels)


def create_app(history_dir: Path, *, allow_origins: tuple[str, ...] = ()) -> "FastAPI":
    """The read-only history API over one history directory.

    ``allow_origins`` is closed by default — no cross-origin reads until an operator names the
    front-ends allowed to read this surface (the ``serve`` command opens it wide only for a
    localhost bind).
    """

    # lazy: the pinned serve extra is only needed once an app is actually created
    from fastapi import FastAPI, HTTPException, Query, Request, Response
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.gzip import GZipMiddleware

    app = FastAPI(title="factor-scope history", description=__doc__)
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allow_origins),
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/dashboards", response_model=DashboardIndex)
    def dashboards(
        request: Request,
        response: Response,
        limit: int = Query(100, ge=1, le=500, description="Max nights per page."),
        offset: int = Query(0, ge=0, description="Nights to skip from the oldest."),
    ) -> "DashboardIndex | Response":
        """A bounded window into the recorded nights, oldest first — O(1) from the materialized
        catalog. The full count rides in ``X-Total-Count`` and page links in ``Link``, so the
        response itself is never unbounded."""

        full = read_index(history_dir)
        total = len(full.entries)
        page = DashboardIndex(
            schema_version=full.schema_version,
            entries=full.entries[offset : offset + limit],
        )
        etag = _etag(f"{total}:{page.model_dump_json()}")
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": _REVALIDATE})
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = _REVALIDATE
        response.headers["X-Total-Count"] = str(total)
        links = _page_links(offset, limit, total)
        if links:
            response.headers["Link"] = links
        return page

    @app.get("/dashboards/latest", response_model=Dashboard)
    def latest(request: Request, response: Response) -> "Dashboard | Response":
        """The newest recorded night — a pointer to the last catalog entry, not a re-scan."""

        entries = read_index(history_dir).entries
        if not entries:
            raise HTTPException(status_code=404, detail="no nights recorded yet")
        latest_as_of = entries[-1].as_of
        etag = _etag(f"{latest_as_of}:{entries[-1].snapshot_id}")
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": _REVALIDATE})
        dash = load(history_dir, latest_as_of)
        if dash is None:
            raise HTTPException(status_code=404, detail="no nights recorded yet")
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = _REVALIDATE
        return dash

    @app.get("/dashboards/{as_of}", response_model=Dashboard)
    def night(as_of: str, request: Request, response: Response) -> "Dashboard | Response":
        """One recorded night by its as-of date — immutable and content-addressed by its
        ``snapshot_id``, the strong validator a static/CDN tier revalidates against."""

        dash = load(history_dir, as_of) if _AS_OF.fullmatch(as_of) else None
        if dash is None:
            raise HTTPException(status_code=404, detail=f"no dashboard for {as_of!r}")
        etag = _etag(dash.snapshot_id)
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": _IMMUTABLE})
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = _IMMUTABLE
        return dash

    return app
