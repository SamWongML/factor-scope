"""Text-stream adapter — the rolling corpus theme discovery clusters into candidate industries.

``textstream.csv → {doc_id, as_of, source, text}``. Each row is one dated document (news / research
note / filing) keyed by ``doc_id`` and stamped with its own publish ``as_of`` so the corpus stays
point-in-time. The payload carries the document's ``source`` and ``text``. The live backend pulls
the stream from its feed (opt-in, lazily imported, never wired into CI); only the fixture backend
runs offline.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.ingest.base import read_rows, required_str
from factor_scope.store import Reading

SERIES = "textstream"
FIXTURE = "textstream.csv"
_REQUIRED = ("doc_id", "as_of", "source", "text")


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        doc_id = required_str(row, "doc_id", line_no, SERIES)
        as_of = required_str(row, "as_of", line_no, SERIES)
        payload: dict[str, object] = {
            "source": required_str(row, "source", line_no, SERIES),
            "text": required_str(row, "text", line_no, SERIES),
        }
        readings.append(
            Reading(series=SERIES, key=doc_id, as_of=as_of, fetched_at=fetched_at, payload=payload)
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)


def fetch_live(  # pragma: no cover - live backend, host-only deps
    feed_url: str, *, fetched_at: str
) -> list[Reading]:
    """Pull the rolling corpus from a feed that serves the same ``doc_id,as_of,source,text`` CSV.

    The production backend: ``httpx`` is imported lazily here so the core installs and the offline
    test mode run without it (that mode reads :func:`load_fixture` over the bundled corpus instead).
    """

    import httpx

    response = httpx.get(feed_url, timeout=30.0)
    response.raise_for_status()
    return parse(response.text, fetched_at=fetched_at)
