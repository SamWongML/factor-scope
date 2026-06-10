"""The text-stream ingest adapter — the rolling corpus theme discovery reads.

Every row is one dated document (news/research/filing) keyed by ``doc_id`` and stamped with its own
publish ``as_of`` so the corpus stays point-in-time. The payload carries the document's ``source``
and ``text``. A malformed header or empty key is a hard parse error.
"""

from __future__ import annotations

import pytest

from factor_scope.ingest import textstream
from factor_scope.ingest.base import IngestError

pytestmark = pytest.mark.unit

FETCHED_AT = "2026-06-05T22:00:00Z"


def test_textstream_is_one_dated_document_per_row_keyed_by_doc_id() -> None:
    readings = textstream.parse(
        "doc_id,as_of,source,text\n"
        "d1,2026-05-30,财新,储能装机持续放量\n"
        "d2,2026-05-31,券商研报,固态电池中试线落地\n",
        fetched_at=FETCHED_AT,
    )
    assert [r.key for r in readings] == ["d1", "d2"]
    assert readings[0].series == textstream.SERIES
    assert readings[0].as_of == "2026-05-30"
    assert readings[0].fetched_at == FETCHED_AT
    assert readings[0].payload == {"source": "财新", "text": "储能装机持续放量"}


def test_textstream_rejects_a_malformed_header() -> None:
    with pytest.raises(IngestError):
        textstream.parse("doc_id,as_of,text\nd1,2026-05-30,x\n", fetched_at=FETCHED_AT)


def test_textstream_rejects_an_empty_doc_id() -> None:
    with pytest.raises(IngestError):
        textstream.parse(
            "doc_id,as_of,source,text\n,2026-05-30,财新,x\n", fetched_at=FETCHED_AT
        )
