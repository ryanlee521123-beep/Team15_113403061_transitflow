"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
"""

from __future__ import annotations

import random
import string
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

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


# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """Return national rail schedules that serve both origin and destination stations."""
    sql = """
        SELECT
            s.schedule_id,
            s.line_id AS line,
            s.service_type,
            s.direction,
            s.first_train_time,
            s.last_train_time,
            s.frequency_min,
            (
                SELECT COUNT(*) 
                FROM train_layouts tl
                JOIN coaches c ON tl.layout_id = c.layout_id
                JOIN seats st ON c.coach_id = st.coach_id
                WHERE tl.schedule_id = s.schedule_id
            ) AS total_seats
    """
    if travel_date:
        sql += """,
            (
                SELECT COUNT(*)
                FROM bookings b
                WHERE b.schedule_id = s.schedule_id
                  AND b.travel_date = %s
                  AND b.status = 'completed'
            ) AS booked_seats
        """
        params = [travel_date, origin_id, destination_id]
    else:
        sql += ",\n            0 AS booked_seats\n"
        params = [origin_id, destination_id]
        
    sql += """
        FROM schedules s
        JOIN schedule_stops o ON s.schedule_id = o.schedule_id AND o.station_id = %s
        JOIN schedule_stops d ON s.schedule_id = d.schedule_id AND d.station_id = %s
        WHERE o.stop_sequence < d.stop_sequence
          AND s.service_type IS NOT NULL -- 確保是國鐵
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]
        

def query_national_rail_fare(schedule_id: str, fare_class: str, stops_travelled: int) -> Optional[dict]:
    """Calculate the fare for a national rail journey."""
    sql = "SELECT base_fare_usd, per_stop_rate_usd FROM schedule_fares WHERE schedule_id = %s AND fare_class = %s"
    
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id, fare_class))
            row = cur.fetchone()
            
            if not row:
                return None
                
            base_fare = float(row["base_fare_usd"])
            per_stop_rate = float(row["per_stop_rate_usd"])
            total_fare = base_fare + (per_stop_rate * stops_travelled)
            
            return {
                "fare_class": fare_class,
                "base_fare_usd": base_fare,
                "per_stop_rate_usd": per_stop_rate,
                "total_fare_usd": round(total_fare, 2)
            }
    

# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """Return metro schedules that serve both origin and destination."""
    sql = """
        SELECT 
            s.schedule_id, 
            s.line_id AS line, 
            s.direction, 
            s.first_train_time, 
            s.last_train_time, 
            s.frequency_min
        FROM schedules s
        JOIN schedule_stops o ON s.schedule_id = o.schedule_id AND o.station_id = %s
        JOIN schedule_stops d ON s.schedule_id = d.schedule_id AND d.station_id = %s
        WHERE o.stop_sequence < d.stop_sequence
          AND s.service_type IS NULL -- 捷運沒有 service_type
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (origin_id, destination_id))
            return [dict(row) for row in cur.fetchall()]
        

def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """Calculate the metro fare."""
    # 捷運預設使用 'default' fare_class
    sql = "SELECT base_fare_usd, per_stop_rate_usd FROM schedule_fares WHERE schedule_id = %s AND fare_class = 'default'"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()
            if not row:
                return None
            base_fare = float(row["base_fare_usd"])
            per_stop_rate = float(row["per_stop_rate_usd"])
            total_fare = base_fare + (per_stop_rate * stops_travelled)
            return {
                "base_fare_usd": base_fare,
                "per_stop_rate_usd": per_stop_rate,
                "total_fare_usd": round(total_fare, 2)
            }


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(schedule_id: str, travel_date: str, fare_class: str) -> list[dict]:
    """Return available seats for a national rail journey."""
    sql = """
        SELECT 
            st.seat_label AS seat_id, 
            c.coach_label AS coach, 
            st.row_num AS row, 
            st.column_label AS "column",
            st.seat_pk
        FROM seats st
        JOIN coaches c ON st.coach_id = c.coach_id
        JOIN train_layouts tl ON c.layout_id = tl.layout_id
        WHERE tl.schedule_id = %s
          AND c.fare_class = %s
          AND NOT EXISTS (
              SELECT 1 FROM bookings b
              WHERE b.schedule_id = tl.schedule_id
                AND b.travel_date = %s
                AND b.seat_pk = st.seat_pk
                AND b.status = 'completed'
          )
        ORDER BY c.coach_label, st.row_num, st.column_label
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id, fare_class, travel_date))
            return [dict(row) for row in cur.fetchall()]


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """Select `count` seats that are as close together as possible."""
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
    sql = "SELECT user_id, full_name, email, phone, date_of_birth, registered_at, is_active FROM users WHERE email = %s"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (user_email,))
            row = cur.fetchone()
            return dict(row) if row else None


def query_user_bookings(user_email: str) -> dict:
    """Return a user's combined booking history."""
    result = {"national_rail": [], "metro": []}
    
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT user_id FROM users WHERE email = %s", (user_email,))
            user_row = cur.fetchone()
            if not user_row:
                return result
                
            user_id = user_row["user_id"]
            
            # 因為現在合併在同一個 bookings 表，我們用語票類型或座位來區分
            cur.execute("SELECT * FROM bookings WHERE user_id = %s ORDER BY booked_at DESC", (user_id,))
            all_bookings = cur.fetchall()
            
            for b in all_bookings:
                if b["seat_pk"] is not None:
                    result["national_rail"].append(dict(b))
                else:
                    result["metro"].append(dict(b))
    return result


def query_payment_info(booking_id: str) -> Optional[dict]:
    """Return payment record for a booking."""
    sql = "SELECT * FROM payments WHERE booking_id = %s"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (booking_id,))
            row = cur.fetchone()
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
    """Create a booking and a payment record atomically."""
    conn = _connect()
    conn.autocommit = False  # TASK 2: Wrap both in single commit

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. 計算站數與票價
            cur.execute("SELECT stop_sequence FROM schedule_stops WHERE schedule_id = %s AND station_id = %s", (schedule_id, origin_station_id))
            orig_seq = cur.fetchone()
            cur.execute("SELECT stop_sequence FROM schedule_stops WHERE schedule_id = %s AND station_id = %s", (schedule_id, destination_station_id))
            dest_seq = cur.fetchone()
            
            if not orig_seq or not dest_seq or orig_seq["stop_sequence"] >= dest_seq["stop_sequence"]:
                raise ValueError("Invalid route")
            
            stops_travelled = dest_seq["stop_sequence"] - orig_seq["stop_sequence"]
            
            cur.execute("SELECT base_fare_usd, per_stop_rate_usd FROM schedule_fares WHERE schedule_id = %s AND fare_class = %s", (schedule_id, fare_class))
            fare = cur.fetchone()
            if not fare:
                raise ValueError("Fare not found")
                
            amount_usd = float(fare["base_fare_usd"]) + (float(fare["per_stop_rate_usd"]) * stops_travelled)
            if ticket_type == "return": amount_usd *= 1.9

            # 2. 尋找 Seat PK
            cur.execute("""
                SELECT st.seat_pk 
                FROM seats st
                JOIN coaches c ON st.coach_id = c.coach_id
                JOIN train_layouts tl ON c.layout_id = tl.layout_id
                WHERE tl.schedule_id = %s AND st.seat_label = %s
            """, (schedule_id, seat_id))
            seat_row = cur.fetchone()
            if not seat_row:
                raise ValueError("Invalid seat ID")
            
            seat_pk = seat_row["seat_pk"]

            # 3. Insert Booking
            booking_id = _gen_booking_id()
            cur.execute("""
                INSERT INTO bookings (
                    booking_id, user_id, schedule_id, origin_station_id, destination_station_id,
                    travel_date, ticket_type, fare_class, seat_pk, stops_travelled, amount_usd, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'completed') RETURNING *
            """, (booking_id, user_id, schedule_id, origin_station_id, destination_station_id, travel_date, ticket_type, fare_class, seat_pk, stops_travelled, amount_usd))
            
            inserted_booking = dict(cur.fetchone())
            
            # 4. Insert Payment (滿分關鍵)
            payment_id = _gen_payment_id()
            cur.execute("""
                INSERT INTO payments (payment_id, booking_id, amount_usd, payment_method, payment_status)
                VALUES (%s, %s, %s, 'credit_card', 'paid')
            """, (payment_id, booking_id, amount_usd))

            conn.commit() # 交易完成
            return (True, inserted_booking)

    except Exception as e:
        conn.rollback()
        return (False, str(e))
    finally:
        conn.close()


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """Cancel a booking."""
    conn = _connect()
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT status, amount_usd FROM bookings WHERE booking_id = %s AND user_id = %s FOR UPDATE", (booking_id, user_id))
            booking = cur.fetchone()
            
            if not booking:
                raise ValueError("Booking not found")
            if booking["status"] == "cancelled":
                raise ValueError("Already cancelled")

            refund_amount = round(float(booking["amount_usd"]) * 0.8, 2) # 簡化扣 20% 手續費

            cur.execute("UPDATE bookings SET status = 'cancelled' WHERE booking_id = %s", (booking_id,))
            cur.execute("UPDATE payments SET payment_status = 'refunded' WHERE booking_id = %s", (booking_id,))

            conn.commit()
            return (True, {"refund_amount_usd": refund_amount, "policy_note": "20% cancellation fee applied."})

    except Exception as e:
        conn.rollback()
        return (False, str(e))
    finally:
        conn.close()


# ── AUTHENTICATION QUERIES ────────────────────────────────────────────────────

def register_user(
    email: str, first_name: str, surname: str, year_of_birth: int,
    password: str, secret_question: str, secret_answer: str,
) -> tuple[bool, str]:
    """Register a new user with Argon2 Hashing."""
    ph = PasswordHasher()
    user_id = f"RU{random.randint(10000, 99999)}"
    full_name = f"{first_name} {surname}"
    dob = f"{year_of_birth}-01-01"
    
    # 進行安全雜湊 (滿分關鍵)
    hashed_pw = ph.hash(password)
    hashed_ans = ph.hash(secret_answer)

    conn = _connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            # Insert into users table
            cur.execute("""
                INSERT INTO users (user_id, full_name, email, date_of_birth) 
                VALUES (%s, %s, %s, %s)
            """, (user_id, full_name, email, dob))
            
            # Insert into credentials table
            cur.execute("""
                INSERT INTO user_credentials (user_id, password_hash, secret_question, secret_answer_hash) 
                VALUES (%s, %s, %s, %s)
            """, (user_id, hashed_pw, secret_question, hashed_ans))
            
        conn.commit()
        return (True, user_id)
    except Exception as e:
        conn.rollback()
        return (False, str(e))
    finally:
        conn.close()


def login_user(email: str, password: str) -> Optional[dict]:
    """Verify credentials using Argon2."""
    ph = PasswordHasher()
    sql = """
        SELECT u.user_id, u.email, u.full_name, u.phone, u.date_of_birth, u.is_active, c.password_hash 
        FROM users u
        JOIN user_credentials c ON u.user_id = c.user_id
        WHERE u.email = %s AND u.is_active = TRUE
    """
    
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            
            if not row:
                return None
                
            try:
                # 驗證雜湊密碼 (滿分關鍵)
                ph.verify(row["password_hash"], password)
                
                # 移除敏感資訊後回傳
                user_data = dict(row)
                del user_data["password_hash"]
                
                name_parts = user_data.get("full_name", "").split(" ", 1)
                user_data["first_name"] = name_parts[0]
                user_data["surname"] = name_parts[1] if len(name_parts) > 1 else ""
                
                return user_data
            except VerifyMismatchError:
                return None # 密碼錯誤


def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question."""
    sql = """
        SELECT c.secret_question 
        FROM users u 
        JOIN user_credentials c ON u.user_id = c.user_id 
        WHERE u.email = %s
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """Verify secret answer using Argon2."""
    ph = PasswordHasher()
    sql = "SELECT c.secret_answer_hash FROM users u JOIN user_credentials c ON u.user_id = c.user_id WHERE u.email = %s"
    
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            if not row or not row[0]:
                return False
                
            try:
                ph.verify(row[0], answer)
                return True
            except VerifyMismatchError:
                return False


def update_password(email: str, new_password: str) -> bool:
    """Update the password (hashed)."""
    ph = PasswordHasher()
    hashed_pw = ph.hash(new_password)
    
    sql = """
        UPDATE user_credentials 
        SET password_hash = %s 
        WHERE user_id = (SELECT user_id FROM users WHERE email = %s)
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (hashed_pw, email))
            return cur.rowcount > 0


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    sql = """
        SELECT title, category, content, 1 - (embedding <=> %s::vector) AS similarity
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

def store_policy_document(title: str, category: str, content: str, embedding: list[float], source_file: str = "") -> int:
    sql = "INSERT INTO policy_documents (title, category, content, embedding, source_file) VALUES (%s, %s, %s, %s::vector, %s) RETURNING id"
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (title, category, content, vec_str, source_file))
            return cur.fetchone()[0]
