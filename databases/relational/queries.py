"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

TWO ROLES ARE SERVED HERE:
  1. Relational  → dual-network transit (metro + national rail),
                   availability, fares, bookings, seat selection
  2. Vector      → policy document similarity search (pgvector)

STUDENT TASK
------------
Design your schema in databases/relational/schema.sql, seed it with
skeleton/seed_postgres.py, then implement the query functions below.

Functions prefixed with `query_`  are read-only lookups called by the agent.
Functions prefixed with `execute_` are write operations (booking/cancellation).

The vector functions (query_policy_vector_search, store_policy_document)
are already implemented — do not modify them.
"""

from __future__ import annotations

import json
import random
import string
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD


def _connect():
    """Return a new psycopg2 connection with autocommit enabled."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn


def _gen_booking_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BK-{suffix}"


def _gen_payment_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"PM-{suffix}"


# ── Example ───────────────────────────────────────────────────────────────────
# The block below shows the query pattern: open a cursor, run SQL, return rows.
# Use _connect() for read-only queries; for write operations use a manual
# connection with conn.commit() / conn.rollback() (see execute_booking below).

def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())

# TODO: Implement the query_ and execute_ functions below.
# ─────────────────────────────────────────────────────────────────────────────


# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules that serve both origin and destination stations
    in the correct order, along with seat occupancy for the requested travel date.

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        travel_date:     e.g. "2025-06-01" — used to count bookings; omit for general info
    """
    import psycopg2.extras
    # 假設 _connect 已經在此檔案最上方被 import，若無則需要從你的 db_utils 引入
    
    # 1. 撰寫基礎 SQL：撈取班次資訊，並利用子查詢計算總座位數
    sql = """
        SELECT
            s.schedule_id,
            s.line,
            s.service_type,
            s.direction,
            s.first_train_time,
            s.last_train_time,
            s.frequency_min,
            s.fare_classes,
            (
                SELECT COUNT(*) 
                FROM seat_layouts sl 
                WHERE sl.schedule_id = s.schedule_id
            ) AS total_seats
    """
    
    # 2. 根據是否有傳入日期，動態決定是否計算「已被預訂的座位數」
    if travel_date:
        sql += """,
            (
                SELECT COUNT(*)
                FROM national_rail_bookings b
                WHERE b.schedule_id = s.schedule_id
                  AND b.travel_date = %s
                  AND b.status = 'completed'
            ) AS booked_seats
        """
        params = [travel_date, origin_id, destination_id]
    else:
        sql += ",\n            0 AS booked_seats\n"
        params = [origin_id, destination_id]
        
    # 3. 補上核心的 WHERE 條件過濾器
    # 利用 array_position 確保 origin 在 destination 前面
    # 若陣列中找不到該站，array_position 會回傳 NULL，比較結果不成立，自然就會被過濾掉
    sql += """
        FROM national_rail_schedules s
        WHERE array_position(s.stops_in_order, %s::text) < array_position(s.stops_in_order, %s::text)
    """

    # 4. 建立連線並執行查詢 (遵循 AI_SESSION_CONTEXT 規定的寫法)
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]
        

def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """
    Calculate the fare for a national rail journey.

    Args:
        schedule_id:     e.g. "NR_SCH01"
        fare_class:      "standard" or "first"
        stops_travelled: number of stops between origin and destination (inclusive)

    Returns:
        dict with fare_class, base_fare_usd, per_stop_rate_usd, total_fare_usd
    """
    import psycopg2.extras
    import json
    
    # 1. 撰寫基礎 SQL：只把目標班次的艙等計費表 (JSONB) 給拉出來
    sql = "SELECT fare_classes FROM national_rail_schedules WHERE schedule_id = %s"
    
    # 2. 建立連線並執行查詢
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()
            
            # 如果找不到班次，或者該班次沒有計費資料，就回傳 None (符合 API 規範)
            if not row or not row.get("fare_classes"):
                return None
                
            fare_data = row["fare_classes"]
            
            # 防呆機制：有些 psycopg2 的設定會把 JSONB 解析成字串，有些會直接變字典
            # 確保它最後一定是一顆 Python 字典
            if isinstance(fare_data, str):
                fare_data = json.loads(fare_data)
                
            # 如果使用者傳入的艙等（例如不小心打錯字）不存在於 JSON 裡，回傳 None
            if fare_class not in fare_data:
                return None
                
            # 3. 把基礎票價跟每站加給挖出來，並轉成浮點數
            base_fare = float(fare_data[fare_class]["base_fare_usd"])
            per_stop_rate = float(fare_data[fare_class]["per_stop_rate_usd"])
            
            # 4. 關鍵公式：總票價 = 基礎票價 + (每站票價 * 經過的站數)
            total_fare = base_fare + (per_stop_rate * stops_travelled)
            
            # 5. 根據 AI_SESSION_CONTEXT 的規範，回傳指定的字典格式
            return {
                "fare_class": fare_class,
                "base_fare_usd": base_fare,
                "per_stop_rate_usd": per_stop_rate,
                "total_fare_usd": round(total_fare, 2) # 小數點後保留兩位，這對算錢很重要！
            }
    
# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    Return metro schedules that serve both origin and destination in the correct order.

    Args:
        origin_id:       e.g. "MS01"
        destination_id:  e.g. "MS09"
    """
    import psycopg2.extras
    # 假設 _connect 已經在檔案上方匯入
    
    # 撰寫 SQL：利用 array_position 確保方向正確
    sql = """
        SELECT 
            schedule_id, 
            line, 
            direction, 
            first_train_time, 
            last_train_time, 
            frequency_min
        FROM metro_schedules
        WHERE array_position(stops_in_order, %s::text) < array_position(stops_in_order, %s::text)
    """
    
    # 建立連線並執行查詢
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 傳入起點與終點代碼，防範 SQL Injection
            cur.execute(sql, (origin_id, destination_id))
            return [dict(row) for row in cur.fetchall()]
        

def query_metro_fare(
    schedule_id: str,
    stops_travelled: int,
) -> Optional[dict]:
    """
    Calculate the metro fare for a single-ticket journey.

    Args:
        schedule_id:     e.g. "MS_SCH01"
        stops_travelled: number of stops between origin and destination

    Returns:
        dict with base_fare_usd, per_stop_rate_usd, total_fare_usd
    """
    import psycopg2.extras
    
    # 1. 撰寫 SQL：直接撈取該班次的基本票價與每站費率
    sql = "SELECT base_fare_usd, per_stop_rate_usd FROM metro_schedules WHERE schedule_id = %s"
    
    # 2. 建立連線並執行查詢
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()
            
            # 找不到該班次時，優雅地回傳 None
            if not row:
                return None
                
            # 3. 取出數字並進行票價計算
            base_fare = float(row["base_fare_usd"])
            per_stop_rate = float(row["per_stop_rate_usd"])
            total_fare = base_fare + (per_stop_rate * stops_travelled)
            
            # 4. 回傳字典格式，並確保總價維持小數點後兩位
            return {
                "base_fare_usd": base_fare,
                "per_stop_rate_usd": per_stop_rate,
                "total_fare_usd": round(total_fare, 2)
            }
        

# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """
    Return available seats for a national rail journey on a given date.

    Args:
        schedule_id:  e.g. "NR_SCH01"
        travel_date:  e.g. "2025-06-01"
        fare_class:   "standard" or "first"

    Returns:
        List of dicts: {seat_id, coach, row, column}
    """
    import psycopg2.extras
    
    # 撰寫 SQL：從座位表找出該班次與艙等的所有座位
    # 並利用 NOT EXISTS 排除在指定日期已經被訂走的座位
    sql = """
        SELECT 
            sl.seat_id, 
            sl.coach, 
            sl.seat_row AS row, 
            sl.seat_column AS "column"
        FROM seat_layouts sl
        WHERE sl.schedule_id = %s
          AND sl.fare_class = %s
          AND NOT EXISTS (
              SELECT 1 
              FROM national_rail_bookings nrb
              WHERE nrb.schedule_id = sl.schedule_id
                AND nrb.travel_date = %s
                AND nrb.seat_id = sl.seat_id
                AND nrb.status = 'completed'
          )
        ORDER BY sl.coach, sl.seat_row, sl.seat_column
    """
    
    # 建立連線並執行查詢
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 依序傳入對應的參數防範 SQL Injection
            cur.execute(sql, (schedule_id, fare_class, travel_date))
            return [dict(row) for row in cur.fetchall()]
        


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """
    Select `count` seats that are as close together as possible (same row preferred,
    then adjacent rows). Returns a list of seat_ids.

    Args:
        available_seats: output of query_available_seats()
        count:           number of seats needed
    """
    if not available_seats or count <= 0:
        return []
    if count >= len(available_seats):
        return [s["seat_id"] for s in available_seats[:count]]

    from collections import defaultdict
    rows: dict[int, list[dict]] = defaultdict(list)
    for seat in available_seats:
        rows[seat["row"]].append(seat)

    for row_seats in sorted(rows.values(), key=lambda s: s[0]["row"]):
        if len(row_seats) >= count:
            return [s["seat_id"] for s in row_seats[:count]]

    sorted_seats = sorted(available_seats, key=lambda s: (s["row"], s["column"]))
    return [s["seat_id"] for s in sorted_seats[:count]]


# ── USER & BOOKING QUERIES ────────────────────────────────────────────────────

def query_user_profile(user_email: str) -> Optional[dict]:
    """Return a user's profile by email."""
    import psycopg2.extras
    
    # 撰寫 SQL：刻意避開 password 與 secret_answer 等敏感欄位
    sql = """
        SELECT 
            user_id, 
            full_name, 
            email, 
            phone, 
            date_of_birth, 
            registered_at, 
            is_active
        FROM registered_users
        WHERE email = %s
    """
    
    # 建立連線並執行查詢
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (user_email,))
            row = cur.fetchone()
            
            # 如果找不到該使用者，回傳 None；否則將資料轉為字典回傳
            return dict(row) if row else None
        


def query_user_bookings(user_email: str) -> dict:
    """
    Return a user's combined booking history (national rail + metro).

    Returns:
        dict with keys 'national_rail' (list) and 'metro' (list)
    """
    import psycopg2.extras
    
    # 準備回傳的預設空字典格式
    result = {
        "national_rail": [],
        "metro": []
    }
    
    # SQL 1: 用 Email 查詢 user_id
    sql_get_user = "SELECT user_id FROM registered_users WHERE email = %s"
    
    # SQL 2: 查詢國鐵紀錄 (依訂票時間由新到舊排序)
    sql_nr = """
        SELECT * FROM national_rail_bookings 
        WHERE user_id = %s 
        ORDER BY booked_at DESC
    """
    
    # SQL 3: 查詢捷運紀錄 (依購買時間由新到舊排序)
    sql_metro = """
        SELECT * FROM metro_travels 
        WHERE user_id = %s 
        ORDER BY purchased_at DESC
    """
    
    # 建立連線並執行一系列查詢
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 步驟一：先找出使用者的 user_id
            cur.execute(sql_get_user, (user_email,))
            user_row = cur.fetchone()
            
            # 如果資料庫裡根本沒有這個 Email，直接回傳空的紀錄
            if not user_row:
                return result
                
            user_id = user_row["user_id"]
            
            # 步驟二：拿 user_id 去撈國鐵紀錄
            cur.execute(sql_nr, (user_id,))
            result["national_rail"] = [dict(row) for row in cur.fetchall()]
            
            # 步驟三：拿 user_id 去撈捷運紀錄
            cur.execute(sql_metro, (user_id,))
            result["metro"] = [dict(row) for row in cur.fetchall()]
            
    return result



def query_payment_info(booking_id: str) -> Optional[dict]:
    """Return payment record for a booking or metro trip."""
    import psycopg2.extras
    
    # 撰寫 SQL：從 payments 表格精準撈出該筆訂單的付款紀錄
    sql = """
        SELECT 
            payment_id, 
            booking_id, 
            amount_usd, 
            method, 
            status, 
            paid_at
        FROM payments
        WHERE booking_id = %s
    """
    
    # 建立連線並執行查詢
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (booking_id,))
            row = cur.fetchone()
            
            # 如果找不到付款紀錄，優雅地回傳 None；有找到就轉成字典回傳
            return dict(row) if row else None
        


# ── TRANSACTIONAL OPERATIONS ──────────────────────────────────────────────────

def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """
    Create a national rail booking for a logged-in user.

    Args:
        user_id:                e.g. "RU01" — must match the logged-in user
        schedule_id:            e.g. "NR_SCH01"
        origin_station_id:      e.g. "NR01"
        destination_station_id: e.g. "NR05"
        travel_date:            e.g. "2025-06-01"
        fare_class:             "standard" or "first"
        seat_id:                e.g. "B05" (or "any" to auto-assign)
        ticket_type:            "single" (default) or "return"

    Returns:
        (True, booking_dict)   on success
        (False, error_message) on failure
    """
    raise NotImplementedError("TODO: implement after designing your schema")


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a national rail booking owned by the given user.

    Calculates the refund amount according to the booking's service type:
      - Normal service: RF001 windows (100% / 75% / 50% / 0%)
      - Express service: RF002 windows (100% / 50% / 0%)

    Args:
        booking_id: e.g. "BK001"
        user_id:    must match the booking's user_id

    Returns:
        (True, result_dict)  with refund_amount_usd and policy note
        (False, error_msg)
    """
    raise NotImplementedError("TODO: implement after designing your schema")


# ── AUTHENTICATION QUERIES ────────────────────────────────────────────────────

def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:
    """
    Register a new user.
    Returns (True, user_id) on success or (False, error_message) on failure.

    NOTE: passwords are stored as plain text here intentionally for teaching
    purposes. In production, replace with a salted hash (e.g. bcrypt).
    """
    raise NotImplementedError("TODO: implement after designing your schema")


def login_user(email: str, password: str) -> Optional[dict]:
    """
    Verify credentials. Returns a user dict on success or None on failure.
    Dict keys: user_id, email, full_name, first_name, surname, phone, date_of_birth, is_active.
    """
    raise NotImplementedError("TODO: implement after designing your schema")


def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question for a registered email, or None if not found."""
    raise NotImplementedError("TODO: implement after designing your schema")


def verify_secret_answer(email: str, answer: str) -> bool:
    """Return True if the provided answer matches the stored secret answer (case-insensitive)."""
    raise NotImplementedError("TODO: implement after designing your schema")


def update_password(email: str, new_password: str) -> bool:
    """Update the password for a user. Returns True if the row was updated."""
    raise NotImplementedError("TODO: implement after designing your schema")


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    """
    Find the most relevant policy documents for a given query embedding.

    Args:
        embedding: Query vector from llm.embed(user_question)
        top_k:     Number of results to return

    Returns:
        List of dicts with title, category, content, and similarity score
    """
    sql = """
        SELECT
            title,
            category,
            content,
            1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        WHERE 1 - (embedding <=> %s::vector) > %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (vec_str, vec_str, VECTOR_SIMILARITY_THRESHOLD, vec_str, top_k))
            return [dict(row) for row in cur.fetchall()]


def store_policy_document(
    title: str,
    category: str,
    content: str,
    embedding: list[float],
    source_file: str = "",
) -> int:
    """
    Insert a policy document with its embedding into the database.
    Used by skeleton/seed_vectors.py — students don't need to call this directly.

    Returns:
        The new document's id
    """
    sql = """
        INSERT INTO policy_documents (title, category, content, embedding, source_file)
        VALUES (%s, %s, %s, %s::vector, %s)
        RETURNING id
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (title, category, content, vec_str, source_file))
            return cur.fetchone()[0]
