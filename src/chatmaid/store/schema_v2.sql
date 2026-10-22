CREATE TABLE triage (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL UNIQUE REFERENCES message(id),
    category TEXT NOT NULL,
    reason TEXT,
    decided_by TEXT NOT NULL,
    model TEXT,
    created_at INTEGER
);
CREATE TABLE cloud_call (
    id INTEGER PRIMARY KEY,
    draft_id INTEGER REFERENCES draft(id),
    backend TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    mapping_json TEXT,
    comment TEXT,
    response TEXT,
    response_hash TEXT,
    error TEXT,
    created_at INTEGER,
    consented_at INTEGER,
    finished_at INTEGER
);
CREATE TABLE draft (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id),
    message_id INTEGER NOT NULL REFERENCES message(id),
    status TEXT NOT NULL,
    language TEXT,
    context_json TEXT,
    final_text TEXT,
    outcome_ref TEXT,
    outcome_preview TEXT,
    created_at INTEGER,
    updated_at INTEGER
);
CREATE TABLE draft_version (
    id INTEGER PRIMARY KEY,
    draft_id INTEGER NOT NULL REFERENCES draft(id),
    round INTEGER NOT NULL,
    candidate INTEGER NOT NULL,
    origin TEXT NOT NULL,
    text TEXT NOT NULL,
    rationale TEXT,
    comment TEXT,
    cloud_call_id INTEGER REFERENCES cloud_call(id),
    created_at INTEGER
);
CREATE INDEX idx_draft_conversation_status ON draft(conversation_id, status);
CREATE INDEX idx_draft_version_draft ON draft_version(draft_id, round, candidate);
PRAGMA user_version = 2;
