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
            item.get("is_interchange_metro", False),
            item.get("interchange_metro_lines"),
            item.get("is_interchange_national_rail", False),
            item.get("interchange_national_rail_station_id")
        )
        rows.append(row)
    inserted_count = insert_many(cur, table_name, columns, rows)
    print(f"✅ 成功插入 {inserted_count} 筆捷運車站資料！")


def seed_national_rail_stations(cur):
    data = load("national_rail_stations.json")
    table_name = "national_rail_stations" 
    columns = [
        "station_id", "name", "lines", 
        "is_interchange_national_rail", "interchange_national_rail_lines", 
        "is_interchange_metro", "interchange_metro_station_id"
    ]
    rows = []
    for item in data:
        row = (
            item.get("station_id"),
            item.get("name"),
            item.get("lines"), 
            item.get("is_interchange_national_rail", False),
            item.get("interchange_national_rail_lines"),
            item.get("is_interchange_metro", False),
            item.get("interchange_metro_station_id")
        )
        rows.append(row)
    inserted_count = insert_many(cur, table_name, columns, rows)
    print(f"✅ 成功插入 {inserted_count} 筆國鐵車站資料！")


def seed_metro_schedules(cur):
    data = load("metro_schedules.json")
    table_name = "metro_schedules"
    columns = [
        "schedule_id", "line", "direction", "origin_station_id", "destination_station_id",
        "stops_in_order", "first_train_time", "last_train_time", "travel_time_from_origin_min",
        "base_fare_usd", "per_stop_rate_usd", "frequency_min", "operates_on"
    ]
    rows = []
    for item in data:
        tt = item.get("travel_time_from_origin_min")
        row = (
            item.get("schedule_id"),
            item.get("line"),
            item.get("direction"),
            item.get("origin_station_id"),
            item.get("destination_station_id"),
            item.get("stops_in_order"),
            item.get("first_train_time"),
            item.get("last_train_time"),
            json.dumps(tt) if tt else None, # 更安全的 JSON 處理
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
    table_name = "national_rail_schedules"
    columns = [
        "schedule_id", "line", "service_type", "direction", "origin_station_id", "destination_station_id",
        "stops_in_order", "first_train_time", "last_train_time", "travel_time_from_origin_min",
        "fare_classes", "frequency_min", "operates_on"
    ]
    rows = []
    for item in data:
        tt = item.get("travel_time_from_origin_min")
        fc = item.get("fare_classes")
        row = (
            item.get("schedule_id"),
            item.get("line"),
            item.get("service_type"),
            item.get("direction"),
            item.get("origin_station_id"),
            item.get("destination_station_id"),
            item.get("stops_in_order"),
            item.get("first_train_time"),
            item.get("last_train_time"),
            json.dumps(tt) if tt else None,
            json.dumps(fc) if fc else None, # 更安全的 JSON 處理
            item.get("frequency_min"),
            item.get("operates_on")
        )
        rows.append(row)
    inserted_count = insert_many(cur, table_name, columns, rows)
    print(f"✅ 成功插入 {inserted_count} 筆國鐵班次表資料！")


def seed_seat_layouts(cur):
    data = load("national_rail_seat_layouts.json")
    table_name = "seat_layouts"
    columns = ["layout_id", "schedule_id", "coach", "fare_class", "seat_id", "seat_row", "seat_column"]
    
    rows = []
    for layout in data:
        layout_id = layout.get("layout_id")
        schedule_id = layout.get("schedule_id")
        
        for coach_item in layout.get("coaches", []):
            coach = coach_item.get("coach")
            fare_class = coach_item.get("fare_class")
            
            for seat in coach_item.get("seats", []):
                rows.append((
                    layout_id,
                    schedule_id,
                    coach,
                    fare_class,
                    seat.get("seat_id"),
                    seat.get("row"),      # 完美對應 JSON 與 Schema
                    seat.get("column")
                ))
    inserted_count = insert_many(cur, table_name, columns, rows)
    print(f"✅ 成功插入 {inserted_count} 筆座位配置資料！")


def seed_users(cur):
    data = load("registered_users.json")
    table_name = "registered_users"
    columns = [
        "user_id", "full_name", "email", "password", "phone",
        "date_of_birth", "secret_question", "secret_answer", "registered_at", "is_active"
    ]
    rows = []
    for user in data:
        row = (
            user.get("user_id"),
            user.get("full_name"),
            user.get("email"),
            user.get("password"),
            user.get("phone"),
            user.get("date_of_birth"),
            user.get("secret_question"),
            user.get("secret_answer"),
            user.get("registered_at"),
            user.get("is_active", True) # 加上預設值確保不為空
        )
        rows.append(row)
    inserted_count = insert_many(cur, table_name, columns, rows)
    print(f"✅ 成功插入 {inserted_count} 筆註冊使用者資料！")


def seed_national_rail_bookings(cur):
    data = load("bookings.json")
    table_name = "national_rail_bookings"
    columns = [
        "booking_id", "user_id", "schedule_id", "origin_station_id", "destination_station_id",
        "travel_date", "departure_time", "ticket_type", "fare_class", "coach", "seat_id",
        "stops_travelled", "amount_usd", "status", "booked_at", "travelled_at"
    ]
    rows = []
    for item in data:
        row = (
            item.get("booking_id"),
            item.get("user_id"),
            item.get("schedule_id"),
            item.get("origin_station_id"),
            item.get("destination_station_id"),
            item.get("travel_date"),
            item.get("departure_time"),
            item.get("ticket_type"),
            item.get("fare_class"),
            item.get("coach"),
            item.get("seat_id"),
            item.get("stops_travelled"),
            item.get("amount_usd"),
            item.get("status"),
            item.get("booked_at"),
            item.get("travelled_at")
        )
        rows.append(row)
    inserted_count = insert_many(cur, table_name, columns, rows)
    print(f"✅ 成功插入 {inserted_count} 筆國鐵訂票紀錄！")


def seed_metro_travels(cur):
    data = load("metro_travel_history.json")
    table_name = "metro_travels"
    columns = [
        "trip_id", "user_id", "schedule_id", "origin_station_id", "destination_station_id",
        "travel_date", "ticket_type", "day_pass_ref", "stops_travelled", "amount_usd",
        "status", "purchased_at", "travelled_at"
    ]
    rows = []
    for item in data:
        row = (
            item.get("trip_id"),
            item.get("user_id"),
            item.get("schedule_id"),
            item.get("origin_station_id"),
            item.get("destination_station_id"),
            item.get("travel_date"),
            item.get("ticket_type"),
            item.get("day_pass_ref"),
            item.get("stops_travelled"),
            item.get("amount_usd"),
            item.get("status"),
            item.get("purchased_at"),
            item.get("travelled_at")
        )
        rows.append(row)
    inserted_count = insert_many(cur, table_name, columns, rows)
    print(f"✅ 成功插入 {inserted_count} 筆捷運乘車歷史！")


def seed_payments(cur):
    data = load("payments.json")
    table_name = "payments"
    columns = [
        "payment_id", "booking_id", "amount_usd", "method", "status", "paid_at"
    ]
    rows = []
    for item in data:
        row = (
            item.get("payment_id"),
            item.get("booking_id"),
            item.get("amount_usd"),
            item.get("method"),
            item.get("status"),
            item.get("paid_at")
        )
        rows.append(row)
    inserted_count = insert_many(cur, table_name, columns, rows)
    print(f"✅ 成功插入 {inserted_count} 筆支付紀錄！")


def seed_feedback(cur):
    data = load("feedback.json")
    table_name = "feedback"
    columns = [
        "feedback_id", "booking_id", "user_id", "rating", "comment", "submitted_at"
    ]
    rows = []
    for item in data:
        row = (
            item.get("feedback_id"),
            item.get("booking_id"),
            item.get("user_id"),
            item.get("rating"),
            item.get("comment"),
            item.get("submitted_at")
        )
        rows.append(row)
    inserted_count = insert_many(cur, table_name, columns, rows)
    print(f"✅ 成功插入 {inserted_count} 筆意見回饋！")


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