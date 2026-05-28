"""
Seed PostgreSQL with all TransitFlow mock data from train-mock-data/.

Usage:
    python skeleton/seed_postgres.py

Run AFTER docker-compose up -d.
You must first design and create your tables in databases/relational/schema.sql.
Safe to re-run: implement your inserts with ON CONFLICT DO NOTHING.
"""

import json
import os
import sys

import psycopg2
from psycopg2.extras import execute_values

# ── resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR    = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)
from skeleton import config as cfg


def load(filename):
    with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def connect():
    return psycopg2.connect(
        host=cfg.PG_HOST,
        port=cfg.PG_PORT,
        dbname=cfg.PG_DB,
        user=cfg.PG_USER,
        password=cfg.PG_PASSWORD,
    )


def insert_many(cur, table, columns, rows):
    """Bulk insert with ON CONFLICT DO NOTHING. Returns row count inserted."""
    if not rows:
        return 0
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT DO NOTHING"
    )
    execute_values(cur, sql, rows)
    return cur.rowcount


# ── seeders ──────────────────────────────────────────────────────────────────

def seed_metro_stations(cur):
    data = load("metro_stations.json")
    table_name = "metro_stations"
    columns = [
        "station_id", "name", "lines", 
        "is_interchange_metro", "interchange_metro_lines", 
        "is_interchange_national_rail", "interchange_national_rail_station_id"
    ]
    rows = []
    for item in data:
        row = (
            item.get("station_id"),
            item.get("name"),
            item.get("lines"),
            item.get("is_interchange_metro"),
            item.get("interchange_metro_lines"),
            item.get("is_interchange_national_rail"),
            item.get("interchange_national_rail_station_id")
        )
        rows.append(row)
    inserted_count = insert_many(cur, table_name, columns, rows)
    print(f"✅ 成功插入 {inserted_count} 筆捷運車站資料！")


def seed_national_rail_stations(cur):
    data = load("national_rail_stations.json")
    
    table_name = "national_rail_stations" 
    columns = [
        "station_id", 
        "name", 
        "lines", 
        "is_interchange_national_rail", 
        "interchange_national_rail_lines", 
        "is_interchange_metro", 
        "interchange_metro_station_id"
    ]
    
    rows = []
    for item in data:
        row = (
            item.get("station_id"),
            item.get("name"),
            item.get("lines"), 
            item.get("is_interchange_national_rail"),
            item.get("interchange_national_rail_lines"),
            item.get("is_interchange_metro"),
            item.get("interchange_metro_station_id")
        )
        rows.append(row)
        
    inserted_count = insert_many(cur, table_name, columns, rows)
    print(f"✅ 成功插入 {inserted_count} 筆國鐵車站資料！")


def seed_metro_schedules(cur):
    import json
    data = load("metro_schedules.json")
    table_name = "metro_schedules"
    columns = [
        "schedule_id", "line", "direction", "origin_station_id", "destination_station_id",
        "stops_in_order", "first_train_time", "last_train_time", "travel_time_from_origin_min",
        "base_fare_usd", "per_stop_rate_usd", "frequency_min", "operates_on"
    ]
    rows = []
    for item in data:
        row = (
            item.get("schedule_id"),
            item.get("line"),
            item.get("direction"),
            item.get("origin_station_id"),
            item.get("destination_station_id"),
            item.get("stops_in_order"),
            item.get("first_train_time"),
            item.get("last_train_time"),
            json.dumps(item.get("travel_time_from_origin_min")),
            item.get("base_fare_usd"),
            item.get("per_stop_rate_usd"),
            item.get("frequency_min"),
            item.get("operates_on")
        )
        rows.append(row)
    inserted_count = insert_many(cur, table_name, columns, rows)
    print(f"✅ 成功插入 {inserted_count} 筆捷運班次表資料！")

def seed_national_rail_schedules(cur):
    data = load("national_rail_schedules.json")
    # TODO: Design your table schema, then implement the INSERT logic here.
    pass


def seed_seat_layouts(cur):
    data = load("national_rail_seat_layouts.json")
    # TODO: Design your table schema, then implement the INSERT logic here.
    pass


def seed_users(cur):
    data = load("registered_users.json")
    # TODO: Design your table schema, then implement the INSERT logic here.
    pass


def seed_national_rail_bookings(cur):
    data = load("bookings.json")
    # TODO: Design your table schema, then implement the INSERT logic here.
    pass


def seed_metro_travels(cur):
    data = load("metro_travel_history.json")
    # TODO: Design your table schema, then implement the INSERT logic here.
    pass


def seed_payments(cur):
    data = load("payments.json")
    # TODO: Design your table schema, then implement the INSERT logic here.
    pass


def seed_feedback(cur):
    data = load("feedback.json")
    # TODO: Design your table schema, then implement the INSERT logic here.
    pass


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to PostgreSQL...")
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")
        seed_metro_stations(cur)
        seed_national_rail_stations(cur)
        seed_metro_schedules(cur)
        seed_national_rail_schedules(cur)
        seed_seat_layouts(cur)
        seed_users(cur)
        seed_national_rail_bookings(cur)
        seed_metro_travels(cur)
        seed_payments(cur)
        seed_feedback(cur)
        conn.commit()
        print("\nAll done. Database seeded successfully.")
    except Exception as e:
        conn.rollback()
        print(f"\nError: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
