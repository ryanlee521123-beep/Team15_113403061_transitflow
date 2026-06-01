"""
TransitFlow — Neo4j Graph Database Layer
=========================================
This module handles all queries to Neo4j.
"""

from __future__ import annotations

from typing import Optional
from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def example_count_nodes() -> int:
    """Example: count all nodes currently in the graph."""
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS total")
            return result.single()["total"]


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """Find the fastest path between two stations using APOC Dijkstra."""
    # 修正：將 n.id 改為 n.station_id 以對應 schema
    cypher = """
    MATCH (start:Station {station_id: $origin_id}), (end:Station {station_id: $destination_id})
    CALL apoc.algo.dijkstra(start, end, 'CONNECTED_TO>|INTERCHANGE_WITH>', 'travel_time_min', 1000.0)
    YIELD path, weight
    RETURN [n IN nodes(path) | n.station_id] AS stations,
           [r IN relationships(path) | {
               type: type(r), 
               line: coalesce(r.line, 'Walk'), 
               time: coalesce(r.travel_time_min, coalesce(r.walk_time_min, 5))
           }] AS legs,
           weight AS total_time_min
    """
    
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, origin_id=origin_id, destination_id=destination_id)
            record = result.single()
            
            if not record:
                return {"found": False, "origin_id": origin_id, "destination_id": destination_id}
                
            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": record["total_time_min"],
                "stations": record["stations"],
                "legs": record["legs"]
            }


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    Find the cheapest path minimizing total estimated fare.
    Assumes `seed_neo4j.py` assigns a `fare_usd` property to CONNECTED_TO edges based on standard fare.
    """
    cypher = """
    MATCH (start:Station {station_id: $origin_id}), (end:Station {station_id: $destination_id})
    CALL apoc.algo.dijkstra(start, end, 'CONNECTED_TO>|INTERCHANGE_WITH>', 'fare_usd', 1000.0)
    YIELD path, weight
    RETURN [n IN nodes(path) | n.station_id] AS stations,
           [r IN relationships(path) | {
               line: coalesce(r.line, 'Transfer'),
               fare: coalesce(r.fare_usd, 0)
           }] AS legs,
           weight AS total_fare_usd
    """
    
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, origin_id=origin_id, destination_id=destination_id)
            record = result.single()
            
            if not record:
                return {"found": False}
                
            return {
                "found": True,
                "total_fare_usd": record["total_fare_usd"],
                "stations": record["stations"],
                "legs": record["legs"]
            }


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    """Find paths that avoid a specific intermediate station."""
    cypher = """
    MATCH path = (start:Station {station_id: $origin_id})-[:CONNECTED_TO|INTERCHANGE_WITH*1..15]->(end:Station {station_id: $destination_id})
    WHERE NOT ANY(n IN nodes(path) WHERE n.station_id = $avoid_station_id)
    WITH path, reduce(time = 0, r IN relationships(path) | time + coalesce(r.travel_time_min, coalesce(r.walk_time_min, 5))) AS total_time
    ORDER BY total_time ASC
    LIMIT $max_routes
    RETURN [r IN relationships(path) | {
        from: startNode(r).station_id,
        to: endNode(r).station_id,
        line: coalesce(r.line, 'Transfer'),
        time: coalesce(r.travel_time_min, coalesce(r.walk_time_min, 5))
    }] AS route_legs
    """
    
    routes = []
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, origin_id=origin_id, destination_id=destination_id, avoid_station_id=avoid_station_id, max_routes=max_routes)
            for record in result:
                routes.append(record["route_legs"])
    return routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """Find a path specifically emphasizing the interchange step."""
    cypher = """
    MATCH path = (start:Station {station_id: $origin_id})-[:CONNECTED_TO*0..10]->(ic1:Station)-[ic_rel:INTERCHANGE_WITH]-(ic2:Station)-[:CONNECTED_TO*0..10]->(end:Station {station_id: $destination_id})
    WITH path, ic1, ic2, reduce(time = 0, r IN relationships(path) | time + coalesce(r.travel_time_min, coalesce(r.walk_time_min, 5))) AS total_time
    ORDER BY total_time ASC
    LIMIT 1
    RETURN [n IN nodes(path) | n.station_id] AS stations,
           {from: ic1.station_id, to: ic2.station_id} AS interchange_point,
           total_time AS total_time_min
    """
    
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, origin_id=origin_id, destination_id=destination_id)
            record = result.single()
            
            if not record:
                return {"found": False}
                
            return {
                "found": True,
                "stations": record["stations"],
                "interchange_point": record["interchange_point"],
                "total_time_min": record["total_time_min"]
            }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """Find all stations within N hops of a delayed station."""
    cypher = """
    MATCH path = (start:Station {station_id: $delayed_id})-[:CONNECTED_TO*1..$hops]-(affected:Station)
    RETURN affected.station_id AS station_id, 
           affected.name AS name, 
           min(length(path)) AS hops_away,
           collect(DISTINCT last(relationships(path)).line) AS lines_affected
    ORDER BY hops_away ASC, station_id ASC
    """
    
    affected_stations = []
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, delayed_id=delayed_station_id, hops=hops)
            for record in result:
                affected_stations.append({
                    "station_id": record["station_id"],
                    "name": record["name"],
                    "hops_away": record["hops_away"],
                    "lines_affected": record["lines_affected"]
                })
    return affected_stations


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """List all direct physical connections from a given station."""
    cypher = """
    MATCH (s:Station {station_id: $station_id})-[r:CONNECTED_TO]->(neighbor:Station)
    RETURN neighbor.station_id AS to_station_id, 
           neighbor.name AS name, 
           r.line AS line, 
           r.travel_time_min AS time
    ORDER BY time ASC
    """
    
    connections = []
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, station_id=station_id)
            for record in result:
                connections.append({
                    "to_station_id": record["to_station_id"],
                    "name": record["name"],
                    "line": record["line"],
                    "travel_time_min": record["time"]
                })
    return connections