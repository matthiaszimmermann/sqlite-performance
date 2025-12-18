CREATE TABLE IF NOT EXISTS schema_migrations (version uint64, dirty bool);

CREATE UNIQUE INDEX IF NOT EXISTS version_unique ON schema_migrations (version);

CREATE TABLE IF NOT EXISTS string_attributes (
    entity_key BLOB NOT NULL,
    from_block INTEGER NOT NULL,
    to_block INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (entity_key, key, from_block)
);

CREATE TABLE IF NOT EXISTS numeric_attributes (
    entity_key BLOB NOT NULL,
    from_block INTEGER NOT NULL,
    to_block INTEGER NOT NULL,
    key TEXT NOT NULL,
    value INTEGER NOT NULL,
    PRIMARY KEY (entity_key, key, from_block)
);

CREATE TABLE IF NOT EXISTS payloads (
    entity_key BLOB NOT NULL,
    from_block INTEGER NOT NULL,
    to_block INTEGER NOT NULL,
    payload BLOB NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    string_attributes TEXT NOT NULL DEFAULT '{}',
    numeric_attributes TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (entity_key, from_block)
);

CREATE TABLE IF NOT EXISTS last_block (
    id INTEGER NOT NULL DEFAULT 1 CHECK (id = 1),
    block INTEGER NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS string_attributes_entity_key_value_index ON string_attributes (
    from_block,
    to_block,
    key,
    value
);

CREATE INDEX IF NOT EXISTS string_attributes_kv_temporal_idx ON string_attributes (
    key,
    value,
    from_block DESC,
    to_block DESC
);

CREATE INDEX IF NOT EXISTS string_attributes_entity_key_index ON string_attributes (from_block, to_block, key);

CREATE INDEX IF NOT EXISTS string_attributes_delete_index ON string_attributes (to_block);

CREATE INDEX IF NOT EXISTS string_attributes_entity_kv_idx ON string_attributes (
    entity_key,
    key,
    from_block DESC
);

CREATE INDEX IF NOT EXISTS numeric_attributes_entity_key_value_index ON numeric_attributes (
    from_block,
    to_block,
    key,
    value
);

CREATE INDEX IF NOT EXISTS numeric_attributes_entity_key_index ON numeric_attributes (from_block, to_block, key);

CREATE INDEX IF NOT EXISTS numeric_attributes_kv_temporal_idx ON numeric_attributes (
    key,
    value,
    from_block DESC,
    to_block DESC
);

CREATE INDEX IF NOT EXISTS numeric_attributes_delete_index ON numeric_attributes (to_block);

CREATE INDEX IF NOT EXISTS payloads_entity_key_index ON payloads (
    entity_key,
    from_block,
    to_block
);

CREATE INDEX IF NOT EXISTS payloads_delete_index ON payloads (to_block);