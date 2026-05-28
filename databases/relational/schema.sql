-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational  → dual-network transit data you design below
--    2. Vector      → policy documents for RAG (provided — do not modify)
-- ============================================================

-- ============================================================
--  STUDENT TASK — Design and create your relational tables here
--
--  Start from the mock data in train-mock-data/:
--    metro_stations.json, national_rail_stations.json
--    metro_schedules.json, national_rail_schedules.json
--    national_rail_seat_layouts.json
--    registered_users.json
--    bookings.json, metro_travel_history.json
--    payments.json, feedback.json
--
--  Think about:
--    - What tables do you need?
--    - What columns and data types?
--    - Which fields are primary keys? Which are foreign keys?
--    - What constraints make sense?
--
--  Apply your schema with:
--    docker-compose down -v && docker-compose up -d
-- ============================================================




-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,  -- 'refund', 'booking', 'conduct'
    content     TEXT         NOT NULL,
    -- 768-dim  → Ollama nomic-embed-text (default)
    -- 3072-dim → Gemini gemini-embedding-001
    -- If you switch LLM_PROVIDER to gemini, change to vector(3072) and reset the database.
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

-- Index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS policy_documents_idx ON policy_documents USING hnsw (embedding vector_cosine_ops);

CREATE TABLE metro_stations (
    station_id VARCHAR(10) PRIMARY KEY,
    name TEXT NOT NULL,
    lines TEXT[] NOT NULL, -- 使用陣列來儲存多條路線 (例如 '{"M1", "M2"}')
    is_interchange_metro BOOLEAN DEFAULT FALSE,
    interchange_metro_lines TEXT[],
    is_interchange_national_rail BOOLEAN DEFAULT FALSE,
    interchange_national_rail_station_id VARCHAR(10)
);
CREATE TABLE national_rail_stations (
    station_id VARCHAR(10) PRIMARY KEY,
    name TEXT NOT NULL,
    lines TEXT[] NOT NULL,
    is_interchange_national_rail BOOLEAN DEFAULT FALSE,
    interchange_national_rail_lines TEXT[],
    is_interchange_metro BOOLEAN DEFAULT FALSE,
    interchange_metro_station_id VARCHAR(10)
);
CREATE TABLE metro_schedules (
    schedule_id VARCHAR(20) PRIMARY KEY,
    line VARCHAR(5) NOT NULL,
    direction VARCHAR(20),
    origin_station_id VARCHAR(10),
    destination_station_id VARCHAR(10),
    stops_in_order TEXT[],
    first_train_time TEXT,
    last_train_time TEXT,
    travel_time_from_origin_min JSONB,
    base_fare_usd REAL,
    per_stop_rate_usd REAL,
    frequency_min INT,
    operates_on TEXT[]
);
