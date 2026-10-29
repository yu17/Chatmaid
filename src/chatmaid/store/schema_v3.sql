ALTER TABLE conversation ADD COLUMN category TEXT;
CREATE INDEX idx_conversation_category ON conversation(category);
CREATE TABLE sender_stat (
    identity_id INTEGER PRIMARY KEY REFERENCES identity(id),
    contact_id INTEGER NOT NULL REFERENCES contact(id),
    messages INTEGER NOT NULL,
    conversations INTEGER NOT NULL,
    first_seen INTEGER,
    last_seen INTEGER,
    known INTEGER NOT NULL,
    answered INTEGER NOT NULL,
    bulk_share REAL NOT NULL,
    categories_json TEXT,
    updated_at INTEGER
);
DROP TABLE IF EXISTS vec_embedding;
DELETE FROM embedding_item;
CREATE INDEX idx_message_sender ON message(sender_identity_id);
CREATE INDEX idx_participant_contact ON conversation_participant(contact_id);
PRAGMA user_version = 3;
