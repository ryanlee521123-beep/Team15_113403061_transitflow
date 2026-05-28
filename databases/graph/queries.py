"""
TransitFlow — Neo4j Graph Database Layer
=========================================
This module handles all queries to Neo4j.

GRAPH ROLE:
  - Model the dual transit network (city metro M1–M4 + national rail NR1–NR2)
  - Find fastest routes (Dijkstra by travel_time_min via APOC)
  - Find cheapest routes (Dijkstra by fare via APOC)
  - Find alternative routes avoiding a given station
  - Find cross-network interchange paths (metro → rail or rail → metro)
  - Show delay ripple: which stations are affected within N hops

STUDENT TASK
------------
Design your graph schema (node labels, relationship types, properties)
based on the data in train-mock-data/, seed it with skeleton/seed_neo4j.py,
then implement the query_ functions below.

Functions prefixed with `query_` are called by the agent (skeleton/agent.py).
"""

from __future__ import annotations

from typing import Optional

from neo4j import GraphDatabase

from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ── Example ───────────────────────────────────────────────────────────────────
# The block below shows the query pattern: open a session, run Cypher, return data.

def example_count_nodes() -> int:
    """Example: count all nodes currently in the graph."""
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS total")
            return result.single()["total"]

# TODO: Implement the query_ functions below.
# ─────────────────────────────────────────────────────────────────────────────


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """
    Find the fastest path between two stations, minimising total travel time.
    Uses apoc.algo.dijkstra (APOC required; enabled in docker-compose.yml).

    Args:
        origin_id:       e.g. "MS01" or "NR01"
        destination_id:  e.g. "MS09" or "NR05"
        network:         "metro", "rail", or "auto" (inferred from IDs)

    Returns:
        dict with keys: found, origin_id, destination_id,
                        total_time_min, path (list of station dicts), legs
    """
    # 決定路網的關係類型過濾器 (預設 '' 代表允許所有路網關係)
    rel_filter = ""
    if network == "metro":
        rel_filter = "METRO_LINK>"
    elif network == "rail":
        rel_filter = "RAIL_LINK>"
        
    # 撰寫 Cypher 語法：
    # 1. 找到起點與終點節點
    # 2. 呼叫 APOC 的 Dijkstra 演算法，指定權重欄位為 'travel_time_min'
    cypher = """
        MATCH (start:Station {station_id: $origin_id})
        MATCH (end:Station {station_id: $destination_id})
        CALL apoc.algo.dijkstra(start, end, $rel_filter, 'travel_time_min') YIELD path, weight
        RETURN 
            weight AS total_time_min,
            [n IN nodes(path) | n {.*}] AS path_nodes
    """
    
    # 遵循 AI_SESSION_CONTEXT 規定的 graph 查詢寫法
    with _driver() as driver:
        with driver.session() as session:
            # 執行 Cypher 並把變數傳進去
            result = session.run(
                cypher, 
                origin_id=origin_id, 
                destination_id=destination_id,
                rel_filter=rel_filter
            )
            record = result.single()
            
            # 如果找不到路徑 (例如站名打錯，或是兩站沒有連通)
            if not record:
                return {
                    "found": False,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "total_time_min": 0,
                    "path": [],
                    "legs": []
                }
            
            # 成功找到路徑，打包回傳
            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": record["total_time_min"],
                "path": record["path_nodes"],
                "legs": [] # 基礎實作中可先留空，供進階轉乘擴充使用
            }
        


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    Find the cheapest path between two stations, minimising total estimated fare.

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        network:         "metro", "rail", or "auto"
        fare_class:      "standard" or "first" (national rail only)

    Returns:
        dict with found, total_fare_usd (approximate), stations, legs
    """
    # 1. 決定路網的關係類型過濾器
    rel_filter = ""
    if network == "metro":
        rel_filter = "METRO_LINK>"
    elif network == "rail":
        rel_filter = "RAIL_LINK>"
        
    # 2. 動態決定要用哪一個欄位來計算票價權重
    # 根據參數決定要加總標準艙還是頭等艙的票價
    weight_property = "first_class_fare_usd" if fare_class == "first" else "standard_fare_usd"
        
    # 3. 撰寫 Cypher 語法：
    # 將 $weight_property 作為參數傳給 Dijkstra 演算法
    cypher = """
        MATCH (start:Station {station_id: $origin_id})
        MATCH (end:Station {station_id: $destination_id})
        CALL apoc.algo.dijkstra(start, end, $rel_filter, $weight_property) YIELD path, weight
        RETURN 
            weight AS total_fare_usd,
            [n IN nodes(path) | n {.*}] AS stations
    """
    
    # 4. 執行圖形資料庫查詢
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                cypher, 
                origin_id=origin_id, 
                destination_id=destination_id,
                rel_filter=rel_filter,
                weight_property=weight_property
            )
            record = result.single()
            
            # 找不到路徑的防呆處理
            if not record:
                return {
                    "found": False,
                    "total_fare_usd": 0,
                    "stations": [],
                    "legs": []
                }
            
            # 成功找到最便宜路徑，打包回傳
            return {
                "found": True,
                "total_fare_usd": round(record["total_fare_usd"], 2), # 算錢一樣要記得四捨五入到小數點後兩位
                "stations": record["stations"],
                "legs": []
            }
        


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    """
    Find paths between two stations that avoid a specific intermediate station.
    Useful for routing around a delayed or closed station.

    Args:
        origin_id:         e.g. "NR01"
        destination_id:    e.g. "NR05"
        avoid_station_id:  e.g. "NR03"
        network:           "metro", "rail", or "auto"
        max_routes:        max number of alternatives to return

    Returns:
        List of routes, each route is a list of leg dicts
    """
    # 1. 動態組合關係類型字串 
    # (注意：Cypher 不支援將關係類型當作變數傳遞，所以用 f-string 組合這部分是標準做法)
    rel_type = "*1..15" # 預設 auto，允許 1 到 15 步的任意連線
    if network == "metro":
        rel_type = ":METRO_LINK*1..15"
    elif network == "rail":
        rel_type = ":RAIL_LINK*1..15"
        
    # 2. 撰寫 Cypher 語法：
    # 使用 NONE() 函數，過濾掉包含故障車站的路徑
    cypher = f"""
        MATCH (start:Station {{station_id: $origin_id}})
        MATCH (end:Station {{station_id: $destination_id}})
        MATCH path = (start)-[{rel_type}]->(end)
        WHERE NONE(n IN nodes(path) WHERE n.station_id = $avoid_station_id)
        RETURN [n IN nodes(path) | n {{.*}}] AS stations
        ORDER BY length(path) ASC
        LIMIT $max_routes
    """
    
    # 3. 執行圖形資料庫查詢
    with _driver() as driver:
        with driver.session() as session:
            # 傳入車站 ID 與最大路線數量作為安全參數
            result = session.run(
                cypher, 
                origin_id=origin_id, 
                destination_id=destination_id,
                avoid_station_id=avoid_station_id,
                max_routes=max_routes
            )
            
            # 將每一條找到的路徑 (車站列表) 取出，組成一個 list[list[dict]] 回傳
            return [record["stations"] for record in result]
        


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(
    origin_id: str,
    destination_id: str,
) -> dict:
    """
    Find a path between a metro station and a national rail station (or vice versa)
    crossing the network boundary via interchange relationships.

    Args:
        origin_id:       e.g. "MS03" (metro) or "NR05" (national rail)
        destination_id:  e.g. "NR05" (national rail) or "MS09" (metro)

    Returns:
        dict with found, stations list, interchange points, total_time_min
    """
    # 1. 撰寫 Cypher：
    # 這裡的 relationship filter 我們留空 ('')，代表允許 Dijkstra 演算法
    # 自由穿越 :METRO_LINK, :RAIL_LINK 以及 :INTERCHANGE 任何一種連線
    cypher = """
        MATCH (start:Station {station_id: $origin_id})
        MATCH (end:Station {station_id: $destination_id})
        CALL apoc.algo.dijkstra(start, end, '', 'travel_time_min') YIELD path, weight
        RETURN 
            weight AS total_time_min,
            [n IN nodes(path) | n {.*}] AS stations
    """
    
    # 2. 執行圖形資料庫查詢
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                cypher, 
                origin_id=origin_id, 
                destination_id=destination_id
            )
            record = result.single()
            
            # 找不到跨網連線時的防呆處理
            if not record:
                return {
                    "found": False,
                    "stations": [],
                    "interchange_points": [],
                    "total_time_min": 0
                }
                
            stations = record["stations"]
            
            # 3. 尋找轉乘點 (Interchange points)：
            # 透過觀察路線中連續兩站的 station_id 前綴，如果從 "MS" 變成 "NR" 
            # (或反過來)，就代表乘客在這裡跨越了路網！
            interchange_points = []
            for i in range(len(stations) - 1):
                current_prefix = stations[i]["station_id"][:2]
                next_prefix = stations[i+1]["station_id"][:2]
                
                # 前綴不同，代表發生轉乘，把這個車站記錄下來
                if current_prefix != next_prefix:
                    interchange_points.append(stations[i]["station_id"])
            
            # 打包回傳
            return {
                "found": True,
                "stations": stations,
                "interchange_points": interchange_points,
                "total_time_min": record["total_time_min"]
            }
        


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(
    delayed_station_id: str,
    hops: int = 2,
) -> list[dict]:
    """
    Find all stations within N hops of a delayed or disrupted station.
    Works on both metro and national rail networks.

    Args:
        delayed_station_id: e.g. "NR03" or "MS01"
        hops:               how many connections out to search (default 2)

    Returns:
        List of dicts: {station_id, name, hops_away, lines_affected}
    """
    # 1. 撰寫 Cypher：
    # 注意：Cypher 語法不支援將「路徑長度 (*1..N)」當作變數 $hops 傳入，
    # 所以這裡我們使用 Python 的 f-string 將 {hops} 安全地寫入字串中。
    # (因為 hops 已經被型別提示限制為 int，所以不用擔心 Injection 風險)
    cypher = f"""
        MATCH path = (start:Station {{station_id: $delayed_station_id}})-[*1..{hops}]-(affected:Station)
        WITH affected, min(length(path)) AS hops_away
        RETURN 
            affected.station_id AS station_id,
            affected.name AS name,
            hops_away,
            affected.lines AS lines_affected
        ORDER BY hops_away ASC, station_id ASC
    """
    
    # 2. 執行圖形資料庫查詢
    with _driver() as driver:
        with driver.session() as session:
            # 傳入發生誤點的車站 ID
            result = session.run(
                cypher, 
                delayed_station_id=delayed_station_id
            )
            
            # 遍歷結果並轉為字典清單回傳 (符合 AI_SESSION_CONTEXT 的要求)
            return [dict(record) for record in result]
        


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    List all direct connections from a given station.

    Args:
        station_id: e.g. "MS01" or "NR01"
    """
    # 1. 撰寫 Cypher 語法：
    # MATCH (起點)-[r:任意關係]->(鄰居)
    # 利用 type(r) 把連接這兩站的路線種類 (例如 METRO_LINK 或 INTERCHANGE) 抓出來
    cypher = """
        MATCH (start:Station {station_id: $station_id})-[r]->(neighbor:Station)
        RETURN 
            neighbor.station_id AS station_id,
            neighbor.name AS name,
            type(r) AS connection_type
        ORDER BY connection_type ASC, station_id ASC
    """
    
    # 2. 執行圖形資料庫查詢
    with _driver() as driver:
        with driver.session() as session:
            # 安全地將車站 ID 傳入查詢中
            result = session.run(
                cypher, 
                station_id=station_id
            )
            
            # 將每一筆相鄰車站的紀錄轉成字典並回傳
            return [dict(record) for record in result]
        
