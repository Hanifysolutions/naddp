-- NADDP — extensions required by the demo schema.
-- Runs once, on first initialisation of an empty data volume
-- (docker-entrypoint-initdb.d). Safe to re-run: every statement is IF NOT EXISTS.

-- pgvector: embedding vector(1536) columns on documents + knowledge_articles.
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_trgm: trigram similarity for fuzzy stakeholder / diaspora name search.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- uuid-ossp: server-side UUID helpers (ULIDs are minted app-side, this is a safety net).
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
