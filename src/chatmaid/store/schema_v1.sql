CREATE TABLE contact (
    id INTEGER PRIMARY KEY,
    name TEXT,
    is_owner INTEGER DEFAULT 0,
    tier TEXT,
    notes TEXT,
    created_at INTEGER
);

CREATE TABLE identity (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    address TEXT NOT NULL,
    display_name TEXT,
    contact_id INTEGER NOT NULL REFERENCES contact(id),
    UNIQUE(platform, address)
);

CREATE TABLE conversation (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    key TEXT NOT NULL UNIQUE,
    title TEXT,
    kind TEXT,
    tier TEXT,
    tracked INTEGER DEFAULT 1,
    bulk INTEGER DEFAULT 0,
    summary TEXT,
    summary_updated_at INTEGER,
    events_json TEXT,
    last_message_at INTEGER,
    last_owner_at INTEGER,
    message_count INTEGER DEFAULT 0
);

CREATE TABLE conversation_participant (
    conversation_id INTEGER NOT NULL REFERENCES conversation(id),
    contact_id INTEGER NOT NULL REFERENCES contact(id),
    PRIMARY KEY (conversation_id, contact_id)
);

CREATE TABLE message (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id),
    ref_key TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    account TEXT NOT NULL,
    message_id TEXT,
    references_json TEXT,
    sender_identity_id INTEGER REFERENCES identity(id),
    sent_at INTEGER,
    kind TEXT,
    subject TEXT,
    preview TEXT,
    labels_json TEXT,
    has_attachments INTEGER,
    size INTEGER,
    is_owner INTEGER,
    bulk INTEGER,
    body_missing INTEGER,
    UNIQUE(platform, account, message_id)
);

CREATE TABLE memory (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    source_ref TEXT,
    observed_at INTEGER,
    created_at INTEGER,
    contact_id INTEGER REFERENCES contact(id),
    conversation_id INTEGER REFERENCES conversation(id),
    superseded_by INTEGER REFERENCES memory(id)
);

CREATE TABLE style_profile (
    language TEXT NOT NULL,
    tier TEXT NOT NULL,
    text TEXT,
    sample_refs_json TEXT,
    updated_at INTEGER,
    PRIMARY KEY (language, tier)
);

CREATE TABLE checkpoint (
    task TEXT NOT NULL,
    key TEXT NOT NULL,
    state_json TEXT,
    updated_at INTEGER,
    PRIMARY KEY (task, key)
);

CREATE TABLE embedding_item (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    text_hash TEXT,
    UNIQUE(kind, ref_id)
);

CREATE TABLE event_log (
    id INTEGER PRIMARY KEY,
    ts INTEGER,
    source TEXT,
    kind TEXT,
    ref TEXT,
    payload_json TEXT
);

CREATE INDEX idx_message_conv_sent ON message(conversation_id, sent_at);
CREATE INDEX idx_message_sent ON message(sent_at);
CREATE INDEX idx_memory_conversation ON memory(conversation_id);
CREATE INDEX idx_memory_contact ON memory(contact_id);

PRAGMA user_version = 1;
