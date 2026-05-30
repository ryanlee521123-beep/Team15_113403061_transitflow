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
        schedule_id:   e.g. "NR_SCH01"
        travel_date:  e.g. "2025-06-01"
        fare_class:   "standard" or "first"

    Returns:
        List   of dicts: {seat_id, coach, row, column}
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
    """
    import psycopg2.extras
    import json
    from datetime import datetime, timezone

    conn = _connect()
    conn.autocommit = False  # 寫入操作，關閉自動提交以開啟交易 (Transaction)

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. 獲取班次資訊以計算票價與搭乘站數
            cur.execute("""
                SELECT stops_in_order, fare_classes, first_train_time 
                FROM national_rail_schedules 
                WHERE schedule_id = %s
            """, (schedule_id,))
            schedule = cur.fetchone()
            
            if not schedule:
                raise ValueError("Schedule not found")

            # 計算搭乘站數
            stops_list = schedule["stops_in_order"]
            try:
                origin_idx = stops_list.index(origin_station_id)
                dest_idx = stops_list.index(destination_station_id)
                stops_travelled = dest_idx - origin_idx
                if stops_travelled <= 0:
                    raise ValueError("Invalid direction or stations")
            except ValueError:
                raise ValueError("Stations not found in schedule route")

            # 計算票價
            fare_data = schedule["fare_classes"]
            if isinstance(fare_data, str):
                fare_data = json.loads(fare_data)
            
            if fare_class not in fare_data:
                raise ValueError(f"Fare class {fare_class} not available")
                
            base_fare = float(fare_data[fare_class]["base_fare_usd"])
            per_stop = float(fare_data[fare_class]["per_stop_rate_usd"])
            amount_usd = base_fare + (per_stop * stops_travelled)
            if ticket_type == "return":
                amount_usd *= 1.9  # 假設來回票打 95 折

            # 2. 獲取車廂 (Coach) 資訊
            if seat_id.lower() == "any":
                # 實作 auto-assign：找第一個該日期未被訂走的座位
                cur.execute("""
                    SELECT seat_id, coach FROM seat_layouts sl
                    WHERE schedule_id = %s AND fare_class = %s
                    AND NOT EXISTS (
                        SELECT 1 FROM national_rail_bookings b
                        WHERE b.schedule_id = sl.schedule_id 
                        AND b.travel_date = %s AND b.seat_id = sl.seat_id AND b.status = 'completed'
                    ) LIMIT 1
                """, (schedule_id, fare_class, travel_date))
                seat_row = cur.fetchone()
                if not seat_row:
                    raise ValueError("No seats available for auto-assignment")
                seat_id = seat_row["seat_id"]
                coach = seat_row["coach"]
            else:
                cur.execute("SELECT coach FROM seat_layouts WHERE schedule_id = %s AND seat_id = %s", 
                            (schedule_id, seat_id))
                coach_row = cur.fetchone()
                if not coach_row:
                    raise ValueError("Invalid seat_id for this schedule")
                coach = coach_row["coach"]

            # 3. 執行寫入 Booking 表格
            booking_id = _gen_booking_id()
            now_utc = datetime.now(timezone.utc)
            
            cur.execute("""
                INSERT INTO national_rail_bookings (
                    booking_id, user_id, schedule_id, origin_station_id, destination_station_id,
                    travel_date, departure_time, ticket_type, fare_class, coach, seat_id,
                    stops_travelled, amount_usd, status, booked_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) RETURNING *
            """, (
                booking_id, user_id, schedule_id, origin_station_id, destination_station_id,
                travel_date, schedule["first_train_time"], ticket_type, fare_class, coach, seat_id,
                stops_travelled, amount_usd, 'completed', now_utc
            ))
            
            inserted_booking = dict(cur.fetchone())
            
            conn.commit()  # 提交交易
            return (True, inserted_booking)

    except Exception as e:
        conn.rollback()  # 發生錯誤，退回整個交易
        return (False, str(e))
    finally:
        conn.close()


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a national rail booking owned by the given user.
    """
    import psycopg2.extras
    from datetime import datetime

    conn = _connect()
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. 檢查訂單是否存在且屬於該使用者
            cur.execute("""
                SELECT b.status, b.amount_usd, b.travel_date, s.service_type 
                FROM national_rail_bookings b
                JOIN national_rail_schedules s ON b.schedule_id = s.schedule_id
                WHERE b.booking_id = %s AND b.user_id = %s
            """, (booking_id, user_id))
            
            booking = cur.fetchone()
            if not booking:
                raise ValueError("Booking not found or access denied")
            if booking["status"] == "cancelled":
                raise ValueError("Booking is already cancelled")

            # 2. 計算退款邏輯 (簡化版：根據距離出發的日期與車種判斷)
            travel_date = booking["travel_date"]  # type: datetime.date
            days_to_travel = (travel_date - datetime.now().date()).days
            amount = float(booking["amount_usd"])
            service_type = booking["service_type"]
            
            refund_pct = 0.0
            policy_note = "0% refund due to late cancellation."
            
            if service_type.lower() == "normal":
                if days_to_travel > 14:
                    refund_pct, policy_note = 1.0, "100% refund (Normal service, >14 days)"
                elif days_to_travel > 7:
                    refund_pct, policy_note = 0.75, "75% refund (Normal service, >7 days)"
                elif days_to_travel > 3:
                    refund_pct, policy_note = 0.50, "50% refund (Normal service, >3 days)"
            else: # Express
                if days_to_travel > 14:
                    refund_pct, policy_note = 1.0, "100% refund (Express service, >14 days)"
                elif days_to_travel > 7:
                    refund_pct, policy_note = 0.50, "50% refund (Express service, >7 days)"

            refund_amount_usd = round(amount * refund_pct, 2)

            # 3. 執行狀態更新
            cur.execute("""
                UPDATE national_rail_bookings 
                SET status = 'cancelled' 
                WHERE booking_id = %s
            """, (booking_id,))

            conn.commit()
            return (True, {"refund_amount_usd": refund_amount_usd, "policy_note": policy_note})

    except Exception as e:
        conn.rollback()
        return (False, str(e))
    finally:
        conn.close()


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
    """Register a new user."""
    from datetime import datetime, timezone
    import random
    
    # 組合欄位以符合 Schema 設計
    user_id = f"RU{random.randint(10000, 99999)}"
    full_name = f"{first_name} {surname}"
    dob = f"{year_of_birth}-01-01"
    now = datetime.now(timezone.utc)
    
    sql = """
        INSERT INTO registered_users (
            user_id, full_name, email, password, date_of_birth, 
            secret_question, secret_answer, registered_at, is_active
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
    """
    
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    user_id, full_name, email, password, dob, 
                    secret_question, secret_answer, now
                ))
        return (True, user_id)
    except Exception as e:
        # e.g., unique constraint violation on email
        return (False, str(e))


def login_user(email: str, password: str) -> Optional[dict]:
    """Verify credentials."""
    import psycopg2.extras
    
    sql = """
        SELECT user_id, email, full_name, phone, date_of_birth, is_active 
        FROM registered_users 
        WHERE email = %s AND password = %s AND is_active = TRUE
    """
    
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email, password))
            row = cur.fetchone()
            
            if not row:
                return None
                
            user_data = dict(row)
            # 將 Schema 裡的 full_name 拆回 first_name 和 surname 以符合回傳規範
            name_parts = user_data.get("full_name", "").split(" ", 1)
            user_data["first_name"] = name_parts[0]
            user_data["surname"] = name_parts[1] if len(name_parts) > 1 else ""
            
            return user_data


def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question for a registered email, or None if not found."""
    sql = "SELECT secret_question FROM registered_users WHERE email = %s"
    
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """Return True if the provided answer matches the stored secret answer (case-insensitive)."""
    # 透過 SQL 的 LOWER() 函式確保比對不分大小寫
    sql = "SELECT 1 FROM registered_users WHERE email = %s AND LOWER(secret_answer) = LOWER(%s)"
    
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email, answer))
            return cur.fetchone() is not None


def update_password(email: str, new_password: str) -> bool:
    """Update the password for a user. Returns True if the row was updated."""
    sql = "UPDATE registered_users SET password = %s WHERE email = %s"
    
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (new_password, email))
            # cur.rowcount 會回傳被影響的資料筆數，若 > 0 代表密碼更新成功
            return cur.rowcount > 0


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
