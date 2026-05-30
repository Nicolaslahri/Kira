"""Safe deletion of Match rows.

`RenameHistory.match_id` is a FK into `matches.id`. The model declares it
``ondelete="SET NULL"``, but Kira migrates with ``Base.metadata.create_all``
+ targeted ``ALTER TABLE`` — and SQLite can't ALTER a FK's ON DELETE action
in place. So a database created BEFORE that clause was added still carries the
default ``RESTRICT`` FK. With ``PRAGMA foreign_keys = ON`` (which we set on
every connection), deleting a Match that a past rename points at then raises
``FOREIGN KEY constraint failed`` — exactly the auto-heal crash on a
previously-renamed file (`auto_heal: file 199 failed: IntegrityError`).

Nulling the back-references FIRST produces the same end state ``ON DELETE SET
NULL`` would, and works on every schema version without a risky rebuild of the
undo-history table. Every Match-delete path routes through here.
"""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kira.models import Match, RenameHistory


async def detach_and_delete_matches(
    session: AsyncSession,
    *,
    media_file_id: int | None = None,
    match_ids: list[int] | None = None,
    manual_false_only: bool = False,
) -> int:
    """Delete Match rows (by ``media_file_id`` or an explicit ``match_ids``
    list), first nulling any ``rename_history.match_id`` that references them.

    Pass exactly one selector:
      - ``media_file_id`` (optionally with ``manual_false_only=True`` to keep
        manual pins), or
      - ``match_ids`` — an explicit list of Match ids to remove.

    Returns the number of Match rows deleted. No-op (0) when nothing matches.
    """
    if match_ids is None:
        if media_file_id is None:
            raise ValueError("detach_and_delete_matches: pass media_file_id or match_ids")
        q = select(Match.id).where(Match.media_file_id == media_file_id)
        if manual_false_only:
            q = q.where(Match.is_manual.is_(False))
        match_ids = list((await session.scalars(q)).all())

    if not match_ids:
        return 0

    # Detach history rows first so the delete can't trip a RESTRICT FK.
    await session.execute(
        update(RenameHistory)
        .where(RenameHistory.match_id.in_(match_ids))
        .values(match_id=None)
    )
    await session.execute(delete(Match).where(Match.id.in_(match_ids)))
    return len(match_ids)
