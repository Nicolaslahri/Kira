"""Scan ingest resilience — one bad row must not drop a whole commit batch.

The discovery loop commits every `SCAN_COMMIT_EVERY` files. Before `_resilient_commit`
a single row that failed to commit (a constraint conflict, an encoding gremlin) took
the whole batch down with it — up to 5 good files silently vanished until the next
scan. `_resilient_commit` rolls back and re-commits each row individually so only the
genuinely-bad row is dropped, and returns it so the scan can tell the user.
"""

from __future__ import annotations

import pytest

from kira.api.scans import _resilient_commit


class _Row:
    """Minimal stand-in for a pending MediaFile row (`_resilient_commit` only reads
    `file_path` and stamps `id`)."""

    def __init__(self, path: str) -> None:
        self.file_path = path
        self.id: int | None = None


class _FakeSession:
    """Async session whose commit fails while ANY pending row's path is in
    `fail_paths`. Mirrors SQLAlchemy semantics the helper relies on: a rollback
    expunges pending adds (back to transient), and a successful commit stamps ids."""

    def __init__(self, fail_paths: set[str]) -> None:
        self.fail_paths = fail_paths
        self.added: list = []
        self.committed: list = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1
        if any(getattr(o, "file_path", None) in self.fail_paths for o in self.added):
            raise RuntimeError("constraint violation")
        for o in self.added:
            o.id = len(self.committed) + 1
        self.committed.extend(self.added)
        self.added = []

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.added = []  # pending adds are expunged on rollback


@pytest.mark.asyncio
async def test_resilient_commit_happy_path_is_a_single_commit() -> None:
    r1, r2 = _Row("x"), _Row("y")
    sess = _FakeSession(fail_paths=set())
    sess.add(r1)
    sess.add(r2)
    dropped = await _resilient_commit(sess, [r1, r2])
    assert dropped == []
    assert sess.commits == 1 and sess.rollbacks == 0  # no per-row retry needed
    assert r1.id is not None and r2.id is not None


@pytest.mark.asyncio
async def test_resilient_commit_isolates_the_bad_row() -> None:
    good1, bad, good2 = _Row("a"), _Row("BAD"), _Row("b")
    sess = _FakeSession(fail_paths={"BAD"})
    for r in (good1, bad, good2):
        sess.add(r)
    dropped = await _resilient_commit(sess, [good1, bad, good2])
    # batch failed → rolled back → each row re-committed individually
    assert sess.rollbacks >= 1
    # the two good rows landed; the bad one did not (id stays None → naturally
    # excluded from the match set downstream)
    assert good1.id is not None and good2.id is not None
    assert bad.id is None
    # …and the bad row is reported so the scan can surface it
    assert dropped == [("BAD", "constraint violation")]


@pytest.mark.asyncio
async def test_resilient_commit_reports_every_bad_row() -> None:
    rows = [_Row("ok1"), _Row("BAD1"), _Row("ok2"), _Row("BAD2")]
    sess = _FakeSession(fail_paths={"BAD1", "BAD2"})
    for r in rows:
        sess.add(r)
    dropped = await _resilient_commit(sess, rows)
    assert {p for p, _ in dropped} == {"BAD1", "BAD2"}
    assert rows[0].id is not None and rows[2].id is not None  # good rows survived
    assert rows[1].id is None and rows[3].id is None
