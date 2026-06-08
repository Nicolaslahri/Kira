"""Match.media_file_id must cascade on delete (audit: missing ondelete).

With foreign_keys=ON (now set on every connection), a Core delete of a
MediaFile would otherwise be rejected because matches still reference it. The
model now declares ON DELETE CASCADE so fresh DBs clean up automatically; the
scan-cleanup path also deletes child matches first so existing DBs are safe too.
"""
from __future__ import annotations

from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateTable

from kira.models import Match


def test_fk_object_has_ondelete_cascade():
    fks = list(Match.__table__.c.media_file_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "CASCADE"


def test_create_table_ddl_emits_on_delete_cascade():
    ddl = str(CreateTable(Match.__table__).compile(dialect=sqlite.dialect()))
    assert "ON DELETE CASCADE" in ddl
