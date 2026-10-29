"""SQLite store (schema v2) with a sqlite-vec embedding index."""

import contextlib
import json
import sqlite3
import time
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

import sqlite_vec

from chatmaid.model import NormalizedMessage, SourceRef, make_preview

_COUNT_TABLES = {
    "identities": "identity",
    "contacts": "contact",
    "conversations": "conversation",
    "messages": "message",
    "memories": "memory",
    "embeddings": "embedding_item",
    "events": "event_log",
    "drafts": "draft",
    "cloud_calls": "cloud_call",
    "senders": "sender_stat",
}


def _now() -> int:
    return int(time.time())


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Store:
    SCHEMA_VERSION = 3

    def __init__(self, path: Path, *, embedding_dim: int):
        # autocommit=True: no implicit transactions, and executescript() does not COMMIT an
        # open transaction first, so a schema step can run inside BEGIN IMMEDIATE.
        self._conn = sqlite3.connect(path, autocommit=True, check_same_thread=False)
        try:
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            current = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for n in range(current + 1, self.SCHEMA_VERSION + 1):
                self._upgrade_to(n)
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS vec_embedding USING vec0("
                f"item_id INTEGER PRIMARY KEY, kind TEXT partition key, embedding float[{int(embedding_dim)}])"
            )
        except BaseException:
            self._conn.close()
            raise

    def _upgrade_to(self, n: int) -> None:
        """Apply `schema_v{n}.sql` as one transaction (ruling R20).

        The version is re-read under the write lock, so a concurrent opener that already
        applied the step is not repeated; an interrupted step rolls back whole and is retried
        on the next open. Each file ends with its own `PRAGMA user_version = n`.
        """
        schema = resources.files("chatmaid.store").joinpath(f"schema_v{n}.sql").read_text()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            if self._conn.execute("PRAGMA user_version").fetchone()[0] < n:
                self._conn.executescript(schema)
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- identities and contacts ---

    def owner_contact_id(self) -> int:
        row = self._conn.execute("SELECT id FROM contact WHERE is_owner = 1").fetchone()
        if row:
            return row["id"]
        cur = self._conn.execute(
            "INSERT INTO contact (name, is_owner, created_at) VALUES (NULL, 1, ?)", (_now(),)
        )
        return cur.lastrowid

    def upsert_identity(
        self, platform: str, address: str, display_name: str | None, *, is_owner: bool = False
    ) -> int:
        row = self._conn.execute(
            "SELECT id, display_name FROM identity WHERE platform = ? AND address = ?",
            (platform, address),
        ).fetchone()
        if row:
            if display_name and not row["display_name"]:
                self._conn.execute(
                    "UPDATE identity SET display_name = ? WHERE id = ?", (display_name, row["id"])
                )
            return row["id"]
        if is_owner:
            contact_id = self.owner_contact_id()
        else:
            contact_id = self._conn.execute(
                "INSERT INTO contact (name, is_owner, created_at) VALUES (?, 0, ?)",
                (display_name, _now()),
            ).lastrowid
        return self._conn.execute(
            "INSERT INTO identity (platform, address, display_name, contact_id) VALUES (?, ?, ?, ?)",
            (platform, address, display_name, contact_id),
        ).lastrowid

    def contact_id_of(self, identity_id: int) -> int:
        row = self._conn.execute(
            "SELECT contact_id FROM identity WHERE id = ?", (identity_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no identity {identity_id}")
        return row["contact_id"]

    # --- conversations ---

    def upsert_conversation(self, platform: str, key: str, title: str, kind: str) -> int:
        """Create the conversation or return the existing one; the title is set only on insert."""
        self._conn.execute(
            "INSERT INTO conversation (platform, key, title, kind) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET kind = excluded.kind",
            (platform, key, title, kind),
        )
        return self._conn.execute("SELECT id FROM conversation WHERE key = ?", (key,)).fetchone()["id"]

    def conversation_id_for_key(self, key: str) -> int | None:
        row = self._conn.execute("SELECT id FROM conversation WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["id"]

    def merge_conversations(self, keep_id: int, drop_id: int) -> int:
        """Fold `drop_id` into `keep_id` (ruling R17) and return how many messages moved.

        Messages, participants, memories and drafts are re-pointed (of the pending drafts now
        in `keep_id` only the newest stays pending; the older ones are superseded), the
        counters of `keep_id` are refreshed and `drop_id` is deleted together with its digest
        state and embedding. The kept summary no longer describes the conversation, so it (and
        its digest state) is cleared for the next digest pass to rewrite. Call inside
        `transaction()`.
        """
        if keep_id == drop_id:
            return 0
        c = self._conn
        moved = c.execute(
            "UPDATE message SET conversation_id = ? WHERE conversation_id = ?", (keep_id, drop_id)
        ).rowcount
        c.execute(
            "INSERT OR IGNORE INTO conversation_participant (conversation_id, contact_id) "
            "SELECT ?, contact_id FROM conversation_participant WHERE conversation_id = ?",
            (keep_id, drop_id),
        )
        c.execute("DELETE FROM conversation_participant WHERE conversation_id = ?", (drop_id,))
        c.execute(
            "UPDATE memory SET conversation_id = ? WHERE conversation_id = ?", (keep_id, drop_id)
        )
        c.execute(
            "UPDATE draft SET conversation_id = ? WHERE conversation_id = ?", (keep_id, drop_id)
        )
        c.execute(  # one pending draft per conversation: the newest stays pending
            "UPDATE draft SET status = 'superseded', updated_at = ? "
            "WHERE conversation_id = ? AND status = 'pending' AND id < "
            "(SELECT MAX(id) FROM draft WHERE conversation_id = ? AND status = 'pending')",
            (_now(), keep_id, keep_id),
        )
        for cid in (keep_id, drop_id):
            c.execute("DELETE FROM checkpoint WHERE task = 'digest' AND key = ?", (str(cid),))
            self._delete_embedding("conversation", cid)
        c.execute("DELETE FROM conversation WHERE id = ?", (drop_id,))
        c.execute(
            "UPDATE conversation SET summary = NULL, summary_updated_at = NULL, events_json = NULL, "
            "category = NULL, tracked = 1 "
            "WHERE id = ?",
            (keep_id,),
        )
        self._refresh_conversation(keep_id)
        return moved

    def _refresh_conversation(self, conversation_id: int) -> None:
        self._conn.execute(
            "UPDATE conversation SET "
            "message_count = (SELECT COUNT(*) FROM message WHERE conversation_id = ?), "
            "bulk = COALESCE((SELECT MIN(bulk) FROM message WHERE conversation_id = ?), 0), "
            "last_message_at = (SELECT MAX(sent_at) FROM message WHERE conversation_id = ?), "
            "last_owner_at = (SELECT MAX(sent_at) FROM message "
            "WHERE conversation_id = ? AND is_owner = 1) "
            "WHERE id = ?",
            (conversation_id,) * 5,
        )

    def set_conversation_summary(
        self,
        conversation_id: int,
        summary: str,
        *,
        tier: str | None = None,
        events: list[dict] | None = None,
        category: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE conversation SET summary = ?, summary_updated_at = ?, "
            "tier = COALESCE(?, tier), events_json = COALESCE(?, events_json), "
            "category = COALESCE(?, category) WHERE id = ?",
            (
                summary,
                _now(),
                tier,
                None if events is None else json.dumps(events, ensure_ascii=False),
                category,
                conversation_id,
            ),
        )

    def set_conversation_category(self, conversation_id: int, category: str | None) -> None:
        self._conn.execute(
            "UPDATE conversation SET category = ? WHERE id = ?", (category, conversation_id)
        )

    def add_participant(self, conversation_id: int, contact_id: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO conversation_participant (conversation_id, contact_id) VALUES (?, ?)",
            (conversation_id, contact_id),
        )

    def set_tracked(self, conversation_id: int, tracked: bool) -> None:
        self._conn.execute(
            "UPDATE conversation SET tracked = ? WHERE id = ?", (int(tracked), conversation_id)
        )

    def participant_contact_ids(self, conversation_id: int) -> list[int]:
        rows = self._conn.execute(
            "SELECT contact_id FROM conversation_participant WHERE conversation_id = ? "
            "ORDER BY contact_id",
            (conversation_id,),
        ).fetchall()
        return [r["contact_id"] for r in rows]

    def conversations(
        self,
        *,
        limit: int | None = None,
        needing_summary: bool | None = None,
        exclude_bulk: bool = True,
        category: str | None = None,
    ) -> list[sqlite3.Row]:
        where = []
        params: list = []
        if category is not None:
            where.append("category = ?")
            params.append(category)
        if exclude_bulk:
            where.append("bulk = 0")
        if needing_summary is True:
            # untracked conversations (nothing left to read) are never offered for summarizing
            where.append("summary IS NULL AND tracked != 0")
        elif needing_summary is False:
            where.append("summary IS NOT NULL")
        sql = "SELECT * FROM conversation"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_message_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return self._conn.execute(sql, params).fetchall()

    # --- messages ---

    def upsert_message(
        self, conversation_id: int, msg: NormalizedMessage, sender_identity_id: int
    ) -> int:
        platform = self._conn.execute(
            "SELECT platform FROM conversation WHERE id = ?", (conversation_id,)
        ).fetchone()["platform"]
        account = msg.ref.account
        if msg.message_id is not None:
            # Same RFC message in another folder: keep the first-seen row.
            dup = self._conn.execute(
                "SELECT id FROM message WHERE platform = ? AND account = ? AND message_id = ? "
                "AND ref_key != ?",
                (platform, account, msg.message_id, msg.ref.key()),
            ).fetchone()
            if dup:
                return dup["id"]
        refs = list(msg.references)
        if msg.in_reply_to is not None and msg.in_reply_to not in refs:
            refs.append(msg.in_reply_to)
        self._conn.execute(
            "INSERT INTO message (conversation_id, ref_key, platform, account, message_id, "
            "references_json, sender_identity_id, sent_at, kind, subject, preview, labels_json, "
            "has_attachments, size, is_owner, bulk, body_missing) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ref_key) DO UPDATE SET conversation_id = excluded.conversation_id, "
            "message_id = excluded.message_id, references_json = excluded.references_json, "
            "sender_identity_id = excluded.sender_identity_id, sent_at = excluded.sent_at, "
            "kind = excluded.kind, subject = excluded.subject, preview = excluded.preview, "
            "labels_json = excluded.labels_json, has_attachments = excluded.has_attachments, "
            "size = excluded.size, is_owner = excluded.is_owner, bulk = excluded.bulk, "
            "body_missing = excluded.body_missing",
            (
                conversation_id,
                msg.ref.key(),
                platform,
                account,
                msg.message_id,
                json.dumps(refs, ensure_ascii=False),
                sender_identity_id,
                msg.sent_at,
                msg.kind,
                msg.subject,
                make_preview(msg.preview or msg.text),
                json.dumps(list(msg.labels), ensure_ascii=False),
                int(msg.has_attachments),
                msg.size,
                int(msg.is_owner),
                int(msg.bulk),
                int(msg.body_missing),
            ),
        )
        message_id = self._conn.execute(
            "SELECT id FROM message WHERE ref_key = ?", (msg.ref.key(),)
        ).fetchone()["id"]
        self._conn.execute(
            "UPDATE conversation SET "
            "message_count = (SELECT COUNT(*) FROM message WHERE conversation_id = ?), "
            "bulk = COALESCE((SELECT MIN(bulk) FROM message WHERE conversation_id = ?), 0), "
            "last_message_at = MAX(COALESCE(last_message_at, 0), ?), "
            "last_owner_at = CASE WHEN ? THEN MAX(COALESCE(last_owner_at, 0), ?) ELSE last_owner_at END "
            "WHERE id = ?",
            (conversation_id, conversation_id, msg.sent_at, int(msg.is_owner), msg.sent_at, conversation_id),
        )
        return message_id

    def has_message_id(self, platform: str, account: str, message_id: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM message WHERE platform = ? AND account = ? AND message_id = ?",
                (platform, account, message_id),
            ).fetchone()
            is not None
        )

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """All-or-nothing group of writes on the autocommit connection."""
        self._conn.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def has_ref(self, ref_key: str) -> bool:
        return (
            self._conn.execute("SELECT 1 FROM message WHERE ref_key = ?", (ref_key,)).fetchone()
            is not None
        )

    def message_refs_for(self, conversation_id: int) -> list[tuple[SourceRef, int]]:
        rows = self._conn.execute(
            "SELECT id, ref_key FROM message WHERE conversation_id = ? ORDER BY sent_at, id",
            (conversation_id,),
        ).fetchall()
        return [(SourceRef.from_key(r["ref_key"]), r["id"]) for r in rows]

    def messages_for(
        self,
        conversation_id: int,
        *,
        since: int | None = None,
        limit: int = 50,
        newest_first: bool = False,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM message WHERE conversation_id = ?"
        params: list = [conversation_id]
        if since is not None:
            sql += " AND sent_at >= ?"
            params.append(since)
        sql += (" ORDER BY sent_at DESC, id DESC" if newest_first else " ORDER BY sent_at, id") + " LIMIT ?"
        params.append(limit)
        return self._conn.execute(sql, params).fetchall()

    def search_messages(
        self,
        *,
        text: str | None = None,
        sender: str | None = None,
        since: int | None = None,
        until: int | None = None,
        limit: int = 50,
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT m.* FROM message m LEFT JOIN identity i ON i.id = m.sender_identity_id "
            "WHERE 1=1"
        )
        params: list = []
        if text:
            sql += " AND (m.subject LIKE ? ESCAPE '\\' OR m.preview LIKE ? ESCAPE '\\')"
            params += [f"%{_like_escape(text)}%"] * 2
        if sender:
            sql += " AND (i.address LIKE ? ESCAPE '\\' OR i.display_name LIKE ? ESCAPE '\\')"
            params += [f"%{_like_escape(sender)}%"] * 2
        if since is not None:
            sql += " AND m.sent_at >= ?"
            params.append(since)
        if until is not None:
            sql += " AND m.sent_at < ?"
            params.append(until)
        sql += " ORDER BY m.sent_at DESC, m.id DESC LIMIT ?"
        params.append(limit)
        return self._conn.execute(sql, params).fetchall()

    def conversation(self, conversation_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM conversation WHERE id = ?", (conversation_id,)
        ).fetchone()

    def memory(self, memory_id: int) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM memory WHERE id = ?", (memory_id,)).fetchone()

    def message(self, message_id: int) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM message WHERE id = ?", (message_id,)).fetchone()

    def message_by_ref(self, ref_key: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM message WHERE ref_key = ?", (ref_key,)).fetchone()

    def contact(self, contact_id: int) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM contact WHERE id = ?", (contact_id,)).fetchone()

    def identities_of(self, contact_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM identity WHERE contact_id = ? ORDER BY id", (contact_id,)
        ).fetchall()

    def set_conversation_tier(
        self, conversation_id: int, tier: str, *, only_if_unset: bool = True
    ) -> None:
        sql = "UPDATE conversation SET tier = ? WHERE id = ?"
        if only_if_unset:
            sql += " AND tier IS NULL"
        self._conn.execute(sql, (tier, conversation_id))

    def memories_for(
        self, *, contact_id: int | None = None, conversation_id: int | None = None, limit: int = 20
    ) -> list[sqlite3.Row]:
        if contact_id is None and conversation_id is None:
            return []
        return self._conn.execute(
            "SELECT * FROM memory WHERE superseded_by IS NULL AND (contact_id = ? OR conversation_id = ?) "
            "ORDER BY observed_at DESC NULLS LAST, id DESC LIMIT ?",
            (contact_id, conversation_id, limit),
        ).fetchall()

    def participant_labels(self, conversation_id: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT COALESCE(NULLIF(c.name, ''), (SELECT MIN(address) FROM identity "
            "WHERE contact_id = c.id), '?') AS label FROM conversation_participant p "
            "JOIN contact c ON c.id = p.contact_id WHERE p.conversation_id = ? ORDER BY c.id",
            (conversation_id,),
        ).fetchall()
        return [r["label"] for r in rows]

    def identity_label(self, identity_id: int | None) -> str:
        if identity_id is None:
            return "?"
        row = self._conn.execute(
            "SELECT COALESCE(NULLIF(display_name, ''), address) AS label FROM identity WHERE id = ?",
            (identity_id,),
        ).fetchone()
        return row["label"] if row else "?"

    def owner_message_refs(self, *, limit: int) -> list[SourceRef]:
        rows = self._conn.execute(
            "SELECT ref_key FROM message WHERE is_owner = 1 AND bulk = 0 "
            "ORDER BY sent_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [SourceRef.from_key(r["ref_key"]) for r in rows]

    def message_ids_for_account(
        self, platform: str, account: str
    ) -> list[tuple[str | None, tuple[str, ...]]]:
        """(message_id, references) of every message that can link threads, in insertion order.

        Replaying them into a `ThreadIndex` in this order reproduces the keys ingest assigned.
        """
        rows = self._conn.execute(
            "SELECT message_id, references_json FROM message WHERE platform = ? AND account = ? "
            "AND (message_id IS NOT NULL OR COALESCE(references_json, '[]') != '[]') ORDER BY id",
            (platform, account),
        ).fetchall()
        return [(r["message_id"], tuple(json.loads(r["references_json"] or "[]"))) for r in rows]

    # --- memories, style, checkpoints ---

    def add_memory(
        self,
        *,
        kind: str,
        text: str,
        source_ref: str,
        observed_at: int | None,
        contact_id: int | None = None,
        conversation_id: int | None = None,
    ) -> int:
        return self._conn.execute(
            "INSERT INTO memory (kind, text, source_ref, observed_at, created_at, contact_id, "
            "conversation_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, text, source_ref, observed_at, _now(), contact_id, conversation_id),
        ).lastrowid

    def memories(self, *, limit: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM memory ORDER BY id"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return self._conn.execute(sql, params).fetchall()

    def set_style_profile(
        self, language: str, tier: str, text: str, sample_refs: list[str]
    ) -> None:
        self._conn.execute(
            "INSERT INTO style_profile (language, tier, text, sample_refs_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(language, tier) DO UPDATE SET "
            "text = excluded.text, sample_refs_json = excluded.sample_refs_json, "
            "updated_at = excluded.updated_at",
            (language, tier, text, json.dumps(sample_refs, ensure_ascii=False), _now()),
        )

    def style_profile(self, language: str, tier: str) -> str | None:
        row = self._conn.execute(
            "SELECT text FROM style_profile WHERE language = ? AND tier = ?", (language, tier)
        ).fetchone()
        return row["text"] if row else None

    def get_checkpoint(self, task: str, key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT state_json FROM checkpoint WHERE task = ? AND key = ?", (task, key)
        ).fetchone()
        return json.loads(row["state_json"]) if row else None

    def set_checkpoint(self, task: str, key: str, state: dict) -> None:
        self._conn.execute(
            "INSERT INTO checkpoint (task, key, state_json, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(task, key) DO UPDATE SET state_json = excluded.state_json, "
            "updated_at = excluded.updated_at",
            (task, key, json.dumps(state, ensure_ascii=False), _now()),
        )

    def checkpoints(self, task: str) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT key, state_json FROM checkpoint WHERE task = ?", (task,)
        ).fetchall()
        return {r["key"]: json.loads(r["state_json"]) for r in rows}

    def clear_checkpoints(self, task: str) -> int:
        return self._conn.execute("DELETE FROM checkpoint WHERE task = ?", (task,)).rowcount

    # --- triage ---

    def add_triage(
        self,
        message_id: int,
        category: str,
        reason: str,
        *,
        decided_by: str,
        model: str | None = None,
    ) -> int:
        self._conn.execute(
            "INSERT INTO triage (message_id, category, reason, decided_by, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(message_id) DO UPDATE SET "
            "category = excluded.category, reason = excluded.reason, "
            "decided_by = excluded.decided_by, model = excluded.model, "
            "created_at = excluded.created_at",
            (message_id, category, reason, decided_by, model, _now()),
        )
        return self._conn.execute(
            "SELECT id FROM triage WHERE message_id = ?", (message_id,)
        ).fetchone()["id"]

    def triage_for(self, message_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM triage WHERE message_id = ?", (message_id,)
        ).fetchone()

    # --- drafts ---

    def create_draft(
        self, conversation_id: int, message_id: int, *, language: str, context: dict
    ) -> int:
        now = _now()
        return self._conn.execute(
            "INSERT INTO draft (conversation_id, message_id, status, language, context_json, "
            "created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?, ?)",
            (conversation_id, message_id, language, json.dumps(context, ensure_ascii=False), now, now),
        ).lastrowid

    def add_draft_version(
        self,
        draft_id: int,
        *,
        round: int,
        candidate: int,
        origin: str,
        text: str,
        rationale: str | None,
        comment: str | None = None,
        cloud_call_id: int | None = None,
    ) -> int:
        return self._conn.execute(
            "INSERT INTO draft_version (draft_id, round, candidate, origin, text, rationale, "
            "comment, cloud_call_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (draft_id, round, candidate, origin, text, rationale, comment, cloud_call_id, _now()),
        ).lastrowid

    def draft(self, draft_id: int) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM draft WHERE id = ?", (draft_id,)).fetchone()

    def draft_versions(self, draft_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM draft_version WHERE draft_id = ? ORDER BY round, candidate, id",
            (draft_id,),
        ).fetchall()

    def next_round(self, draft_id: int) -> int:
        row = self._conn.execute(
            "SELECT MAX(round) AS r FROM draft_version WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        return 1 if row["r"] is None else row["r"] + 1

    def drafts(self, *, status: str | None = "pending", limit: int = 50) -> list[sqlite3.Row]:
        sql = "SELECT * FROM draft"
        params: list = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        return self._conn.execute(sql, params).fetchall()

    def pending_draft_for(self, conversation_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM draft WHERE conversation_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()

    def set_draft_status(
        self,
        draft_id: int,
        status: str,
        *,
        final_text: str | None = None,
        outcome_ref: str | None = None,
        outcome_preview: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE draft SET status = ?, final_text = COALESCE(?, final_text), "
            "outcome_ref = COALESCE(?, outcome_ref), "
            "outcome_preview = COALESCE(?, outcome_preview), updated_at = ? WHERE id = ?",
            (status, final_text, outcome_ref, outcome_preview, _now(), draft_id),
        )

    # --- cloud calls ---

    def add_cloud_call(
        self,
        *,
        draft_id: int | None,
        backend: str,
        model: str | None,
        payload: str,
        mapping: dict[str, str],
        comment: str | None,
    ) -> int:
        return self._conn.execute(
            "INSERT INTO cloud_call (draft_id, backend, model, status, payload, mapping_json, "
            "comment, created_at) VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?)",
            (
                draft_id,
                backend,
                model,
                payload,
                json.dumps(mapping, ensure_ascii=False),
                comment,
                _now(),
            ),
        ).lastrowid

    def cloud_call(self, call_id: int) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM cloud_call WHERE id = ?", (call_id,)).fetchone()

    def cloud_calls(self, *, limit: int = 50) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM cloud_call ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()

    def set_cloud_call_status(
        self,
        call_id: int,
        status: str,
        *,
        consented_at: int | None = None,
        response: str | None = None,
        response_hash: str | None = None,
        error: str | None = None,
    ) -> None:
        finished = _now() if status in ("sent", "failed", "declined") else None
        self._conn.execute(
            "UPDATE cloud_call SET status = ?, consented_at = COALESCE(?, consented_at), "
            "response = COALESCE(?, response), response_hash = COALESCE(?, response_hash), "
            "error = COALESCE(?, error), finished_at = COALESCE(?, finished_at) WHERE id = ?",
            (status, consented_at, response, response_hash, error, finished, call_id),
        )

    # --- embeddings ---

    def upsert_embedding(self, kind: str, ref_id: int, text_hash: str, vector: list[float]) -> None:
        self._conn.execute("BEGIN")
        try:
            row = self._conn.execute(
                "SELECT id FROM embedding_item WHERE kind = ? AND ref_id = ?", (kind, ref_id)
            ).fetchone()
            if row:
                item_id = row["id"]
                self._conn.execute("UPDATE embedding_item SET text_hash = ? WHERE id = ?", (text_hash, item_id))
                self._conn.execute("DELETE FROM vec_embedding WHERE item_id = ?", (item_id,))
            else:
                item_id = self._conn.execute(
                    "INSERT INTO embedding_item (kind, ref_id, text_hash) VALUES (?, ?, ?)",
                    (kind, ref_id, text_hash),
                ).lastrowid
            self._conn.execute(
                "INSERT INTO vec_embedding (item_id, kind, embedding) VALUES (?, ?, ?)",
                (item_id, kind, sqlite_vec.serialize_float32(vector)),
            )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def _delete_embedding(self, kind: str, ref_id: int) -> None:
        row = self._conn.execute(
            "SELECT id FROM embedding_item WHERE kind = ? AND ref_id = ?", (kind, ref_id)
        ).fetchone()
        if row:
            self._conn.execute("DELETE FROM vec_embedding WHERE item_id = ?", (row["id"],))
            self._conn.execute("DELETE FROM embedding_item WHERE id = ?", (row["id"],))

    def embedding_hash(self, kind: str, ref_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT text_hash FROM embedding_item WHERE kind = ? AND ref_id = ?", (kind, ref_id)
        ).fetchone()
        return row["text_hash"] if row else None

    def search_embeddings(
        self, vector: list[float], *, k: int = 10, kind: str | None = None
    ) -> list[tuple[str, int, float]]:
        # sqlite-vec 0.1.x needs `k = ?` inside the KNN query; kind is a partition key, so the
        # filter applies inside the KNN and exactly k rows come back.
        rows = self._conn.execute(
            "SELECT e.kind, e.ref_id, v.distance FROM "
            "(SELECT item_id, distance FROM vec_embedding WHERE embedding MATCH ? AND k = ?"
            + (" AND kind = ?" if kind is not None else "")
            + " ORDER BY distance) v JOIN embedding_item e ON e.id = v.item_id "
            "ORDER BY v.distance",
            (sqlite_vec.serialize_float32(vector), k) + ((kind,) if kind is not None else ()),
        ).fetchall()
        return [(r["kind"], r["ref_id"], r["distance"]) for r in rows]

    # --- event log and counts ---

    def log_event(self, source: str, kind: str, ref: str, payload: dict) -> int:
        return self._conn.execute(
            "INSERT INTO event_log (ts, source, kind, ref, payload_json) VALUES (?, ?, ?, ?, ?)",
            (_now(), source, kind, ref, json.dumps(payload, ensure_ascii=False)),
        ).lastrowid

    def events(self, *, limit: int = 100) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM event_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def counts(self) -> dict[str, int]:
        return {
            name: self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for name, table in _COUNT_TABLES.items()
        }

