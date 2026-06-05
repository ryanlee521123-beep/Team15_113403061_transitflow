-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational  → dual-network transit data you design below
--    2. Vector      → policy documents for RAG (provided — do not modify)
-- ============================================================
-- ============================================================
-- DESIGN DECISIONS (Task 1 Requirements)
-- 1. PK Strategy: We use natural string IDs (VARCHAR) for entities provided by the 
--    external mock data (e.g., station_id, schedule_id) to maintain data consistency. 
--    We use SERIAL (auto-incrementing integers) for purely internal bridging tables 
--    (e.g., seats, policy_documents) for storage efficiency.
-- 2. Delete Strategy: We use a "Hard Delete with Restrictions" strategy. Most tables 
--    use 'ON DELETE RESTRICT' to prevent accidental deletion of core operational data 
--    (e.g., you cannot delete a station if it has schedules). 'ON DELETE CASCADE' is 
--    only used for tightly coupled child records (e.g., user_credentials deleted if user is deleted).
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
--  STUDENT TASK — Design and create your relational tables here
-- ============================================================

-- 1. Stations & Connections
CREATE TABLE stations (
    station_id VARCHAR(10) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    is_interchange_metro BOOLEAN DEFAULT FALSE,
    is_interchange_national_rail BOOLEAN DEFAULT FALSE,
    interchange_national_rail_station_id VARCHAR(10),
    interchange_metro_station_id VARCHAR(10)
);

CREATE TABLE lines (
    line_id VARCHAR(10) PRIMARY KEY,
    line_name VARCHAR(50)
);

CREATE TABLE station_lines (
    station_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT,
    line_id VARCHAR(10) REFERENCES lines(line_id) ON DELETE RESTRICT,
    PRIMARY KEY (station_id, line_id)
);

-- JSON arrays: adjacent_stations
CREATE TABLE station_connections (
    from_station_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT,
    to_station_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT,
    line VARCHAR(10), -- Matching JSON key "line"
    travel_time_min INT NOT NULL,
    PRIMARY KEY (from_station_id, to_station_id, line)
);


-- 2. Schedules & Fares
CREATE TABLE schedules (
    schedule_id VARCHAR(20) PRIMARY KEY,
    line VARCHAR(10),
    service_type VARCHAR(50),
    direction VARCHAR(20) NOT NULL,
    origin_station_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT,
    first_train_time TIME,
    last_train_time TIME,
    frequency_min INT
);

CREATE TABLE schedule_stops (
    schedule_id VARCHAR(20) REFERENCES schedules(schedule_id) ON DELETE RESTRICT,
    station_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT,
    stop_sequence INT NOT NULL,
    time_from_origin_min INT NOT NULL,
    PRIMARY KEY (schedule_id, station_id)
);

CREATE TABLE schedule_fares (
    schedule_id VARCHAR(20) REFERENCES schedules(schedule_id) ON DELETE RESTRICT,
    fare_class VARCHAR(20),
    base_fare_usd DECIMAL(8,2) NOT NULL,
    per_stop_rate_usd DECIMAL(8,2) NOT NULL,
    PRIMARY KEY (schedule_id, fare_class)
);

CREATE TABLE schedule_operating_days (
    schedule_id VARCHAR(20) REFERENCES schedules(schedule_id) ON DELETE RESTRICT,
    day_of_week VARCHAR(3),
    PRIMARY KEY (schedule_id, day_of_week)
);


-- 3. Seating & Layouts (National Rail)
CREATE TABLE train_layouts (
    layout_id VARCHAR(20) PRIMARY KEY,
    schedule_id VARCHAR(20) REFERENCES schedules(schedule_id) ON DELETE RESTRICT
);

CREATE TABLE coaches (
    layout_id VARCHAR(20) REFERENCES train_layouts(layout_id) ON DELETE RESTRICT,
    coach VARCHAR(5) NOT NULL, -- Matched to JSON key "coach"
    fare_class VARCHAR(20) NOT NULL,
    PRIMARY KEY (layout_id, coach)
);

CREATE TABLE seats (
    layout_id VARCHAR(20),
    coach VARCHAR(5),
    seat_id VARCHAR(10) NOT NULL, -- Matched to JSON key "seat_id" (e.g. "A01")
    "row" INT NOT NULL,           -- Quoted because ROW is a SQL keyword, matched to JSON
    "column" VARCHAR(2) NOT NULL, -- Quoted because COLUMN is a SQL keyword, matched to JSON
    PRIMARY KEY (layout_id, coach, seat_id),
    FOREIGN KEY (layout_id, coach) REFERENCES coaches(layout_id, coach) ON DELETE RESTRICT
);


-- 4. Users (Merged back to exactly match registered_users.json)
CREATE TABLE users (
    user_id VARCHAR(20) PRIMARY KEY,
    full_name VARCHAR(150) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL, -- Reverted to users table for seeder compatibility
    phone VARCHAR(20),
    date_of_birth DATE,
    secret_question TEXT,
    secret_answer VARCHAR(255),
    registered_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE
);


-- 5. Bookings, History, Payments, & Feedback
-- Split into two tables to respect the two distinct JSON files & PKs

CREATE TABLE bookings (
    booking_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) REFERENCES users(user_id),
    schedule_id VARCHAR(20) REFERENCES schedules(schedule_id),
    origin_station_id VARCHAR(10) REFERENCES stations(station_id),
    destination_station_id VARCHAR(10) REFERENCES stations(station_id),
    travel_date DATE NOT NULL,
    departure_time TIME,
    ticket_type VARCHAR(20) NOT NULL,
    fare_class VARCHAR(20),
    coach VARCHAR(5),
    seat_id VARCHAR(10),
    stops_travelled INT NOT NULL,
    amount_usd DECIMAL(8,2) NOT NULL,
    status VARCHAR(20) NOT NULL,
    booked_at TIMESTAMPTZ,
    travelled_at TIMESTAMPTZ
);

CREATE TABLE metro_travel_history (
    trip_id VARCHAR(20) PRIMARY KEY, -- Restored JSON primary key
    user_id VARCHAR(20) REFERENCES users(user_id),
    schedule_id VARCHAR(20) REFERENCES schedules(schedule_id),
    origin_station_id VARCHAR(10) REFERENCES stations(station_id),
    destination_station_id VARCHAR(10) REFERENCES stations(station_id),
    travel_date DATE NOT NULL,
    ticket_type VARCHAR(20) NOT NULL,
    stops_travelled INT NOT NULL,
    amount_usd DECIMAL(8,2) NOT NULL,
    status VARCHAR(20) NOT NULL,
    purchased_at TIMESTAMPTZ,
    travelled_at TIMESTAMPTZ
);

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
CREATE TABLE national_rail_schedules (
    schedule_id VARCHAR(20) PRIMARY KEY,
    line VARCHAR(5) NOT NULL,
    service_type VARCHAR(20),
    direction VARCHAR(20),
    origin_station_id VARCHAR(10),
    destination_station_id VARCHAR(10),
    stops_in_order TEXT[],
    first_train_time TEXT,
    last_train_time TEXT,
    travel_time_from_origin_min JSONB,
    fare_classes JSONB,
    frequency_min INT,
    operates_on TEXT[]
);
CREATE TABLE seat_layouts (
    layout_id VARCHAR(20),
    schedule_id VARCHAR(20) NOT NULL,
    coach VARCHAR(5) NOT NULL,
    fare_class VARCHAR(20) NOT NULL,
    seat_id VARCHAR(10) NOT NULL,
    seat_row INT NOT NULL,        -- 改成這個安全名稱
    seat_column VARCHAR(5) NOT NULL, -- 改成這個安全名稱
    PRIMARY KEY (layout_id, seat_id)
);
CREATE TABLE registered_users (
    user_id VARCHAR(20) PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    phone VARCHAR(20),
    date_of_birth DATE,
    secret_question VARCHAR(200),
    secret_answer VARCHAR(200),
    registered_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE national_rail_bookings (
    booking_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) NOT NULL,
    schedule_id VARCHAR(20) NOT NULL,
    origin_station_id VARCHAR(10) NOT NULL,
    destination_station_id VARCHAR(10) NOT NULL,
    travel_date DATE NOT NULL,
    departure_time TEXT NOT NULL,
    ticket_type VARCHAR(20) NOT NULL,
    fare_class VARCHAR(20) NOT NULL,
    coach VARCHAR(5) NOT NULL,
    seat_id VARCHAR(10) NOT NULL,
    stops_travelled INT NOT NULL,
    amount_usd REAL NOT NULL,
    status VARCHAR(20) NOT NULL,
    booked_at TIMESTAMPTZ NOT NULL,
    travelled_at TIMESTAMPTZ
);
CREATE TABLE metro_travels (
    trip_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) NOT NULL,
    schedule_id VARCHAR(20) NOT NULL,
    origin_station_id VARCHAR(10) NOT NULL,
    destination_station_id VARCHAR(10) NOT NULL,
    travel_date DATE NOT NULL,
    ticket_type VARCHAR(20) NOT NULL,
    day_pass_ref VARCHAR(20),
    stops_travelled INT,
    amount_usd REAL NOT NULL,
    status VARCHAR(20) NOT NULL,
    purchased_at TIMESTAMPTZ,    -- ✨ 就是這裡！把 NOT NULL 拿掉
    travelled_at TIMESTAMPTZ
);
CREATE TABLE payments (
    payment_id VARCHAR(20) PRIMARY KEY,
    booking_id VARCHAR(20) NOT NULL,
    amount_usd REAL NOT NULL,
    method VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL,
    paid_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE feedback (
    feedback_id VARCHAR(20) PRIMARY KEY,
    booking_id VARCHAR(20) NOT NULL,
    user_id VARCHAR(20) NOT NULL,
    rating INT NOT NULL,
    comment TEXT,                -- 允許為空，所以不加 NOT NULL
    submitted_at TIMESTAMPTZ NOT NULL
);
