"""
TransitFlow — Neo4j Seeder
Run once after starting Docker:
    python skeleton/seed_neo4j.py

Loads station and network data from train-mock-data/:
  - metro_stations.json         — city metro stations and adjacencies
  - national_rail_stations.json — national rail stations and adjacencies

Design your graph schema (node labels, relationship types, properties)
based on the data in these files, then implement the seed() function below.
"""

import json
import os
import sys

sys.path.insert(0, ".")

from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)


def _load(filename):
    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def seed():
    metro_stations = _load("metro_stations.json")
    rail_stations  = _load("national_rail_stations.json")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:

        session.run("MATCH (n) DETACH DELETE n")
        print("  Cleared existing graph data")

        # 1. 建立捷運車站節點 (Nodes)
        # 給予雙重標籤 Station 和 MetroStation，方便後續演算法搜尋
        for s in metro_stations:
            session.run(
                """
                MERGE (n:Station:MetroStation {station_id: $id})
                SET n.name = $name, n.lines = $lines
                """,
                id=s["station_id"], name=s["name"], lines=s["lines"]
            )
        print("  Created MetroStation nodes")

        # 2. 建立國鐵車站節點 (Nodes)
        for s in rail_stations:
            session.run(
                """
                MERGE (n:Station:NationalRailStation {station_id: $id})
                SET n.name = $name, n.lines = $lines
                """,
                id=s["station_id"], name=s["name"], lines=s["lines"]
            )
        print("  Created NationalRailStation nodes")

        # 3. 建立捷運路線連線 (Edges: METRO_LINK)
        # 遍歷 adjacent_stations 來建立相鄰車站的單向箭頭
        for s in metro_stations:
            for adj in s.get("adjacent_stations", []):
                session.run(
                    """
                    MATCH (a:Station {station_id: $from_id})
                    MATCH (b:Station {station_id: $to_id})
                    MERGE (a)-[r:METRO_LINK {line: $line}]->(b)
                    SET r.travel_time_min = $time
                    """,
                    from_id=s["station_id"], to_id=adj["station_id"],
                    line=adj["line"], time=adj["travel_time_min"]
                )
        print("  Created metro links")

        # 4. 建立國鐵路線連線 (Edges: RAIL_LINK)
        for s in rail_stations:
            for adj in s.get("adjacent_stations", []):
                session.run(
                    """
                    MATCH (a:Station {station_id: $from_id})
                    MATCH (b:Station {station_id: $to_id})
                    MERGE (a)-[r:RAIL_LINK {line: $line}]->(b)
                    SET r.travel_time_min = $time
                    """,
                    from_id=s["station_id"], to_id=adj["station_id"],
                    line=adj["line"], time=adj["travel_time_min"]
                )
        print("  Created national rail links")

        # 5. 建立跨網轉乘連線 (Edges: INTERCHANGE)
        # 找出共構車站，並建立雙向的轉乘通道，預設轉乘步行時間設為 5 分鐘
        for s in metro_stations:
            if s.get("is_interchange_national_rail"):
                nr_id = s.get("interchange_national_rail_station_id")
                if nr_id:
                    session.run(
                        """
                        MATCH (m:MetroStation {station_id: $m_id})
                        MATCH (r:NationalRailStation {station_id: $r_id})
                        MERGE (m)-[:INTERCHANGE {travel_time_min: 5}]->(r)
                        MERGE (r)-[:INTERCHANGE {travel_time_min: 5}]->(m)
                        """,
                        m_id=s["station_id"], r_id=nr_id
                    )
        print("  Created interchange links")
        
    driver.close()
    print("\nNeo4j graph seeded successfully.")
    print("   Open http://localhost:7475 to explore the graph.")


if __name__ == "__main__":
    print("Connecting to Neo4j...")
    seed()
