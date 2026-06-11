# AI Session Context — TransitFlow

**How to use this file:**
At the start of every AI coding session, paste the full contents of this file as your first message to your AI assistant. This gives the AI the context it needs to produce code that fits your codebase and is consistent with your teammates' work.

**Who maintains this file:**
Whoever makes a schema change or architectural decision updates this file in the same commit. Treat it like a team contract.

---

## Project Overview

TransitFlow is a Python-based AI chat assistant for a fictional transit operator. It queries three databases — PostgreSQL (relational + vector), Neo4j (graph) — and uses an LLM to answer user questions. Our task as students is to design the database schema and implement the query functions in `databases/relational/queries.py` and `databases/graph/queries.py`.

## Tech Stack

- Language: Python 3.11+
- Relational DB: PostgreSQL via `psycopg2` with `RealDictCursor`
- Graph DB: Neo4j via the `neo4j` Python driver
- Vector search: `pgvector` extension (already implemented — do not modify)
- Web UI: Gradio
- LLM: Google Gemini or local Ollama (configured via `.env`)

## Coding Conventions

- **Naming:** `snake_case` for all Python names and SQL identifiers
- **Docstrings:** All functions must have a docstring with `Args:` and `Returns:` sections
- **Return types:** Use type hints. Read-only functions return `list[dict]` or `Optional[dict]`
- **Empty results:** Return `[]` or `None` (as documented), never raise an exception for "not found"
- **SQL:** Use `%s` placeholders for all user inputs — never string-format into SQL
- **Relational pattern:** Use `_connect()` helper + `psycopg2.extras.RealDictCursor`:
  ```python
  with _connect() as conn:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT ...", (param,))
          return [dict(row) for row in cur.fetchall()]
  ```
- **Graph pattern:** Use `_driver()` helper + session:
  ```python
  with _driver() as driver:
      with driver.session() as session:
          result = session.run("MATCH ...", station_id=station_id)
          return [dict(record) for record in result]
  ```

## Agreed Relational Schema

<!-- ============================================================
  FILL THIS IN after your team completes the schema design workshop.
  Paste your final CREATE TABLE statements here.
  ============================================================ -->

```sql
--Core Network & Stations
CREATE TABLE stations (
    station_id VARCHAR(10) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    network_type VARCHAR(20) NOT NULL,
    is_interchange_metro BOOLEAN NOT NULL DEFAULT FALSE,
    is_interchange_national_rail BOOLEAN NOT NULL DEFAULT FALSE,
    linked_interchange_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT
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

CREATE TABLE station_connections (
    from_station_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT,
    to_station_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT,
    line_id VARCHAR(10) REFERENCES lines(line_id) ON DELETE RESTRICT,
    travel_time_min INT NOT NULL,
    PRIMARY KEY (from_station_id, to_station_id, line_id)
);

--Schedules & Fares
CREATE TABLE schedules (
    schedule_id VARCHAR(20) PRIMARY KEY,
    line_id VARCHAR(10) REFERENCES lines(line_id) ON DELETE RESTRICT,
    service_type VARCHAR(50),
    direction VARCHAR(20) NOT NULL,
    origin_station_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT,
    first_train_time TIME NOT NULL,
    last_train_time TIME NOT NULL,
    frequency_min INT NOT NULL
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

--Seating & Layouts (National Rail)
CREATE TABLE train_layouts (
    layout_id VARCHAR(20) PRIMARY KEY,
    schedule_id VARCHAR(20) REFERENCES schedules(schedule_id) ON DELETE RESTRICT
);

CREATE TABLE coaches (
    coach_id SERIAL PRIMARY KEY,
    layout_id VARCHAR(20) REFERENCES train_layouts(layout_id) ON DELETE RESTRICT,
    coach_label VARCHAR(5) NOT NULL,
    fare_class VARCHAR(20) NOT NULL
);

CREATE TABLE seats (
    seat_pk SERIAL PRIMARY KEY,
    coach_id INT REFERENCES coaches(coach_id) ON DELETE RESTRICT,
    seat_label VARCHAR(10) NOT NULL,
    row_num INT NOT NULL,
    column_label VARCHAR(2) NOT NULL
);

--Users & Authentication
CREATE TABLE users (
    user_id VARCHAR(20) PRIMARY KEY,
    full_name VARCHAR(150) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    phone VARCHAR(20),
    date_of_birth DATE,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE user_credentials (
    user_id VARCHAR(20) PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    password_hash VARCHAR(255) NOT NULL,
    secret_question TEXT,
    secret_answer_hash VARCHAR(255),
    last_password_change TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

--Bookings, Payments, & Feedback
CREATE TABLE bookings (
    booking_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) REFERENCES users(user_id) ON DELETE RESTRICT,
    schedule_id VARCHAR(20) REFERENCES schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10) REFERENCES stations(station_id) ON DELETE RESTRICT,
    travel_date DATE NOT NULL,
    departure_time TIME,
    ticket_type VARCHAR(20) NOT NULL,
    fare_class VARCHAR(20),
    seat_pk INT REFERENCES seats(seat_pk),
    stops_travelled INT NOT NULL,
    amount_usd DECIMAL(8,2) NOT NULL,
    status VARCHAR(20) NOT NULL,
    booked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
```

## Agreed Graph Schema

<!-- ============================================================
  FILL THIS IN after your team agrees on Neo4j node labels and
  relationship types.
  ============================================================ -->

```
Node labels:
- `MetroStation`: Represents a station in the city metro network.
- `NationalRailStation`: Represents a station in the national rail network.
- `Station`: General label applied to all station nodes for flexible polymorphic queries.

Relationship types:
- `METRO_LINK`: Directed edge connecting adjacent MetroStation nodes.
- `RAIL_LINK`: Directed edge connecting adjacent NationalRailStation nodes.
- `INTERCHANGE_TO`: Edge connecting cross-network transfer stations (Metro <-> Rail).

Key properties:
- **Nodes:** `station_id` (String, Unique ID), `name` (String), `lines` (List of Strings)
- **Relationships (`METRO_LINK` / `RAIL_LINK`):** `travel_time_min` (Integer, weight for shortest route), `line` (String), `fare_usd` (Float, weight for cheapest route)
- **Relationships (`INTERCHANGE_TO`):** `travel_time_min` (Integer, walking/transfer time, default 5)
```

## Function Signatures We Are Implementing

These are fixed contracts. AI-generated code must match these signatures exactly.

### Relational (`databases/relational/queries.py`)

```python
# Read-only
def query_national_rail_availability(origin_id: str, destination_id: str, travel_date: Optional[str] = None) -> list[dict]: ...
def query_national_rail_fare(schedule_id: str, fare_class: str, stops_travelled: int) -> Optional[dict]: ...
def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]: ...
def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]: ...
def query_available_seats(schedule_id: str, travel_date: str, fare_class: str) -> list[dict]: ...
def query_user_profile(user_email: str) -> Optional[dict]: ...
def query_user_bookings(user_email: str) -> dict: ...  # returns {"national_rail": [...], "metro": [...]}
def query_payment_info(booking_id: str) -> Optional[dict]: ...

# Write operations
def execute_booking(user_id, schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type="single") -> tuple[bool, dict | str]: ...
def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]: ...

# Auth
def register_user(email, first_name, surname, year_of_birth, password, secret_question, secret_answer) -> tuple[bool, str]: ...
def login_user(email: str, password: str) -> Optional[dict]: ...
def get_user_secret_question(email: str) -> Optional[str]: ...
def verify_secret_answer(email: str, answer: str) -> bool: ...
def update_password(email: str, new_password: str) -> bool: ...
```

### Graph (`databases/graph/queries.py`)

```python
def query_shortest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict: ...
def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto", fare_class: str = "standard") -> dict: ...
def query_alternative_routes(origin_id, destination_id, avoid_station_id, network="auto", max_routes=3) -> list[list[dict]]: ...
def query_interchange_path(origin_id: str, destination_id: str) -> dict: ...
def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]: ...
def query_station_connections(station_id: str) -> list[dict]: ...
```

## Team Decisions Log

<!-- Add entries as you make decisions. Format: "Decision: X. Why: Y." -->

- [ ] Schema design:
  - **Decision:** Consolidated metro and national rail stations into a unified `stations` table. 
    **Why:** To maintain a clean, normalized relational schema, simplify foreign key constraints for bookings, and avoid complex UNION queries.
  - **Decision:** Upgraded all timestamp columns to `TIMESTAMPTZ` and explicitly defined `ON DELETE RESTRICT` (for core data) or `ON DELETE CASCADE` (for child records).
    **Why:** To ensure timezone-aware data integrity and prevent accidental deletion of critical operational records, strictly adhering to the static evaluation requirements.
  - **Decision:** Implemented Argon2 hashing for all user passwords and secret answers.
    **Why:** To guarantee robust security for user credentials over outdated methods.
  - **Decision:** Wrapped `execute_booking` operations inside a single atomic transaction (`conn.autocommit = False`).
    **Why:** To ensure database consistency; a booking is never created without its corresponding payment record.
- [ ] Graph schema: 
  - **Decision:** Applied a dual-labeling strategy (`Station` + `MetroStation` / `NationalRailStation`) for node creation.
    **Why:** To satisfy strict grading requirements for specific network labels while keeping APOC Dijkstra queries flexible and concise by searching the generic `Station` label.
  - **Decision:** Strictly named relationships as `METRO_LINK`, `RAIL_LINK`, and `INTERCHANGE_TO` with `travel_time_min` and `fare_usd` properties.
    **Why:** To perfectly align with the live routing query requirements (shortest and cheapest routes) defined in the grading rubric.
  - **Decision:** Configured Cypher delay ripple query to use `*0..$hops` instead of `*1..$hops`.
    **Why:** To prevent Cypher syntax crashes during live edge-case testing when `hops=0` is requested.
- [ ] (example) Metro schedule stop ordering: using `jsonb_array_elements` approach — easier to debug than containment operators

## Prompts That Worked

<!-- Share prompts that produced good output so teammates can reuse them. -->

### Schema design prompt that worked:
```
"I need to design or update the PostgreSQL schema for TransitFlow. We are using a unified 'stations' table pattern instead of separated tables.
Rules to follow:
Use TIMESTAMPTZ for all datetime columns (never just TIMESTAMP).
Explicitly define ON DELETE RESTRICT or ON DELETE CASCADE for every foreign key.
Include inline SQL comments explaining the design choice between natural string IDs (VARCHAR) and auto-incrementing IDs (SERIAL) for primary keys.
Please convert this mock data structure into a normalized schema: [PASTE_JSON_STRUCTURE_HERE]"
```

### Query implementation prompt that worked:
```
"I need to implement a database query function for TransitFlow.
Function signature: [PASTE_FUNCTION_SIGNATURE_HERE]
If Relational (PostgreSQL): Use _connect() and psycopg2.extras.RealDictCursor. For write operations, you MUST set conn.autocommit = False, wrap in try/except, and return (True, result) or (False, error). Never raise exceptions for missing data (return [] or None).
If Graph (Neo4j): Use _driver() and with driver.session():. Strictly use node labels (MetroStation, NationalRailStation) and relationship types (METRO_LINK, RAIL_LINK, INTERCHANGE_TO). Ensure 0-hop queries do not crash (e.g., use *0..$hops).
Here is the relevant schema context: [PASTE_RELATED_SCHEMA_HERE]"
```
