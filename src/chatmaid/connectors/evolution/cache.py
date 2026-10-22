"""Read-only access to Evolution's CamelStoreDB cache (folders.db plus body files)."""

import sqlite3
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_BATCH = 500
_NUMERIC = re.compile(r"^\d+$")


@dataclass(frozen=True)
class FolderInfo:
    folder_id: int
    name: str
    saved_count: int


@dataclass(frozen=True)
class MessageRow:
    uid: str
    flags: int
    dsent: int
    dreceived: int
    subject: str
    mail_from: str
    mail_to: str
    mail_cc: str
    size: int


def _to_row(r: sqlite3.Row) -> MessageRow:
    return MessageRow(
        uid=str(r["uid"]),
        flags=r["flags"] or 0,
        dsent=int(r["dsent"] or 0),
        dreceived=int(r["dreceived"] or 0),
        subject=r["subject"] or "",
        mail_from=r["mail_from"] or "",
        mail_to=r["mail_to"] or "",
        mail_cc=r["mail_cc"] or "",
        size=r["size"] or 0,
    )


class EvolutionCache:
    def __init__(self, cache_dir: Path, account_uid: str):
        self._root = Path(cache_dir) / account_uid
        self._connection: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path:
        return self._root / "folders.db"

    @property
    def _conn(self) -> sqlite3.Connection:
        # Evolution uses a rollback journal and short busy budget: read-only, no long transactions.
        if self._connection is None:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True, timeout=2.0, isolation_level=None
            )
            conn.row_factory = sqlite3.Row
            self._connection = conn
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def folders(self) -> list[FolderInfo]:
        rows = self._conn.execute(
            "SELECT folder_id, folder_name, saved_count FROM folders ORDER BY folder_id"
        ).fetchall()
        return [FolderInfo(r["folder_id"], r["folder_name"], r["saved_count"] or 0) for r in rows]

    def uid_mode(self, folder: FolderInfo) -> Literal["int", "time"]:
        """"int" when uids are numeric (or the folder is empty), else "time" (opaque uids)."""
        row = self._conn.execute(f"SELECT uid FROM messages_{int(folder.folder_id)} LIMIT 1").fetchone()
        return "time" if row is not None and not _NUMERIC.match(str(row[0])) else "int"

    def iter_rows(self, folder: FolderInfo, *, after: int | None = None) -> Iterator[MessageRow]:
        """Rows past the watermark: uid > after (int mode) or dreceived >= after (time mode)."""
        sql = (
            "SELECT uid, flags, dsent, dreceived, subject, mail_from, mail_to, mail_cc, size"
            f" FROM messages_{int(folder.folder_id)}"
        )
        time_mode = self.uid_mode(folder) == "time"
        params: tuple = ()
        if after is not None:
            sql += " WHERE dreceived >= ?" if time_mode else " WHERE CAST(uid AS INTEGER) > ?"
            params = (after,)
        sql += " ORDER BY dreceived, uid" if time_mode else " ORDER BY CAST(uid AS INTEGER)"
        cur = self._conn.execute(sql, params)
        try:
            while batch := cur.fetchmany(_BATCH):
                for r in batch:
                    yield _to_row(r)
        finally:
            cur.close()

    def row(self, folder: FolderInfo, uid: str) -> MessageRow | None:
        r = self._conn.execute(
            "SELECT uid, flags, dsent, dreceived, subject, mail_from, mail_to, mail_cc, size"
            f" FROM messages_{int(folder.folder_id)} WHERE uid = ?",
            (uid,),
        ).fetchone()
        return None if r is None else _to_row(r)

    def watermark(self, folder: FolderInfo, rows: Iterable[MessageRow]) -> int | None:
        """Max uid (int mode) or max dreceived (time mode) over the rows read; None if no rows."""
        if self.uid_mode(folder) == "time":
            values = [r.dreceived for r in rows]
        else:
            values = [int(r.uid) for r in rows]
        return max(values) if values else None

    def folder_dir(self, name: str) -> Path:
        return self._root / "folders" / name.replace("/", "/subfolders/")

    def body_path(self, folder: FolderInfo, uid: str) -> Path | None:
        return next(iter(sorted((self.folder_dir(folder.name) / "cur").glob(f"*/{uid}"))), None)
