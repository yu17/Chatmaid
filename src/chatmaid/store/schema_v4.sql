-- Full-text index over each conversation: title, digest summary, and every message's subject
-- and preview. Trigram tokens match substrings, which also covers Chinese text without a
-- segmenter. Triggers keep it current; rowid = conversation.id.
CREATE VIRTUAL TABLE conversation_fts USING fts5(title, summary, body, tokenize = 'trigram');

CREATE TRIGGER conversation_fts_conv_insert AFTER INSERT ON conversation BEGIN
    INSERT INTO conversation_fts (rowid, title, summary, body)
    VALUES (new.id, coalesce(new.title, ''), coalesce(new.summary, ''), '');
END;

CREATE TRIGGER conversation_fts_conv_update AFTER UPDATE OF title, summary ON conversation BEGIN
    UPDATE conversation_fts SET title = coalesce(new.title, ''), summary = coalesce(new.summary, '')
    WHERE rowid = new.id;
END;

CREATE TRIGGER conversation_fts_conv_delete AFTER DELETE ON conversation BEGIN
    DELETE FROM conversation_fts WHERE rowid = old.id;
END;

CREATE TRIGGER conversation_fts_msg_insert AFTER INSERT ON message BEGIN
    UPDATE conversation_fts SET body = (
        SELECT coalesce(group_concat(coalesce(m.subject, '') || ' ' || coalesce(m.preview, ''), char(10)), '')
        FROM message m WHERE m.conversation_id = new.conversation_id
    ) WHERE rowid = new.conversation_id;
END;

CREATE TRIGGER conversation_fts_msg_update AFTER UPDATE OF conversation_id, subject, preview ON message BEGIN
    UPDATE conversation_fts SET body = (
        SELECT coalesce(group_concat(coalesce(m.subject, '') || ' ' || coalesce(m.preview, ''), char(10)), '')
        FROM message m WHERE m.conversation_id = old.conversation_id
    ) WHERE rowid = old.conversation_id;
    UPDATE conversation_fts SET body = (
        SELECT coalesce(group_concat(coalesce(m.subject, '') || ' ' || coalesce(m.preview, ''), char(10)), '')
        FROM message m WHERE m.conversation_id = new.conversation_id
    ) WHERE rowid = new.conversation_id;
END;

CREATE TRIGGER conversation_fts_msg_delete AFTER DELETE ON message BEGIN
    UPDATE conversation_fts SET body = (
        SELECT coalesce(group_concat(coalesce(m.subject, '') || ' ' || coalesce(m.preview, ''), char(10)), '')
        FROM message m WHERE m.conversation_id = old.conversation_id
    ) WHERE rowid = old.conversation_id;
END;

INSERT INTO conversation_fts (rowid, title, summary, body)
SELECT c.id, coalesce(c.title, ''), coalesce(c.summary, ''), coalesce((
    SELECT group_concat(coalesce(m.subject, '') || ' ' || coalesce(m.preview, ''), char(10))
    FROM message m WHERE m.conversation_id = c.id
), '')
FROM conversation c;

PRAGMA user_version = 4;
