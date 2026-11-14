-- TG-Space-Bot Supabase Schema Definition
CREATE TYPE file_category_enum AS ENUM (
    'documents',
    'media',
    'audio',
    'archives',
    'other'
);

CREATE TABLE IF NOT EXISTS files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL,
    file_id TEXT NOT NULL,
    file_unique_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size BIGINT NOT NULL,
    mime_type TEXT,
    category file_category_enum NOT NULL DEFAULT 'other',
    storage_chat_id BIGINT NOT NULL,
    storage_message_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_files_user_id ON files(user_id);
CREATE INDEX IF NOT EXISTS idx_files_category ON files(category);
CREATE INDEX IF NOT EXISTS idx_files_user_category ON files(user_id, category);
CREATE INDEX IF NOT EXISTS idx_files_file_unique_id ON files(file_unique_id);
CREATE INDEX IF NOT EXISTS idx_files_created_at ON files(created_at DESC);

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_files_name_trgm ON files USING gin (file_name gin_trgm_ops);

CREATE OR REPLACE VIEW user_storage_stats AS
SELECT 
    user_id,
    COUNT(*) AS total_files,
    COALESCE(SUM(file_size), 0) AS total_bytes,
    COUNT(*) FILTER (WHERE category = 'documents') AS documents_count,
    COUNT(*) FILTER (WHERE category = 'media') AS media_count,
    COUNT(*) FILTER (WHERE category = 'audio') AS audio_count,
    COUNT(*) FILTER (WHERE category = 'archives') AS archives_count,
    COUNT(*) FILTER (WHERE category = 'other') AS other_count
FROM files
GROUP BY user_id;
