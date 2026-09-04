CREATE TABLE session (
    id TEXT, time_created INTEGER, model TEXT, tokens_input INTEGER,
    tokens_output INTEGER, tokens_cache_read INTEGER,
    tokens_cache_write INTEGER, cost REAL, directory TEXT
);
CREATE TABLE message (
    id TEXT, session_id TEXT, time_created INTEGER, data TEXT
);
CREATE TABLE part (session_id TEXT, time_created INTEGER, data TEXT);

INSERT INTO session VALUES (
    'opencode-session-1', 1754056800000,
    '{"id": "gpt-5.6", "providerID": "openai"}',
    10, 20, 30, 40, 1.25, '/repo/project-a'
);
INSERT INTO message VALUES (
    'user-1', 'opencode-session-1', 1754056700000,
    '{"role": "user"}'
);
INSERT INTO message VALUES (
    'assistant-1', 'opencode-session-1', 1754056800000,
    '{"role": "assistant", "modelID": "gpt-5.6", "providerID": "openai", "parentID": "user-1"}'
);
INSERT INTO part VALUES (
    'opencode-session-1', 1754056800000, '{"type": "tool"}'
);
