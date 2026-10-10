"""Small SQLite geometry store. No flight control or reconstruction code."""
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.resources import files
import json
import math
from pathlib import Path
import sqlite3

SCHEMA_VERSION = 1
ROLES = {"prior_2d", "lidar_truth", "rf_reconstruction", "synthetic"}
SITE_FIELDS = (
    "site_id", "name", "origin_lon_deg", "origin_lat_deg", "origin_alt_m",
    "vertical_datum", "frame",
)


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def connection(path, *, create=False):
    path = Path(path)
    if not create and not path.is_file():
        raise ValueError(f"Database does not exist: {path}. Run init first.")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        conn.close()


def require_schema(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise ValueError(f"Unsupported database version {version}; expected {SCHEMA_VERSION}.")


def initialize(path):
    with connection(path, create=True) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if tables:
            require_schema(conn)
            return
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            conn.executescript(files(__package__).joinpath("schema.sql").read_text())
        except Exception:
            conn.rollback()
            raise


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number.")
    return float(value)


def text_value(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string.")
    return value


def cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(a, b, c, d):
    def on_segment(p, q, r):
        return (min(p[0], r[0]) <= q[0] <= max(p[0], r[0])
                and min(p[1], r[1]) <= q[1] <= max(p[1], r[1]))
    orientations = (cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b))
    if orientations[0] * orientations[1] < 0 and orientations[2] * orientations[3] < 0:
        return True
    return any(
        value == 0 and on_segment(p, q, r)
        for value, p, q, r in (
            (orientations[0], a, c, b), (orientations[1], a, d, b),
            (orientations[2], c, a, d), (orientations[3], c, b, d),
        )
    )


def validate_ring(ring):
    if not isinstance(ring, list) or len(ring) < 4:
        raise ValueError("A footprint requires at least three vertices and a closing vertex.")
    result = []
    for point in ring:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("Footprint vertices must be [east_m, north_m].")
        result.append([number(point[0], "east_m"), number(point[1], "north_m")])
    if result[0] != result[-1]:
        raise ValueError("Footprint ring must be explicitly closed.")
    vertices = result[:-1]
    if len({tuple(p) for p in vertices}) != len(vertices):
        raise ValueError("Footprint ring contains duplicate vertices.")
    edges = list(zip(result, result[1:]))
    for i, (a, b) in enumerate(edges):
        for j in range(i + 1, len(edges)):
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            if segments_intersect(a, b, *edges[j]):
                raise ValueError("Footprint ring must not self-intersect or touch itself.")
    # Reject adjacent edges that reverse onto one another.
    for i, b in enumerate(vertices):
        a, c = vertices[i - 1], vertices[(i + 1) % len(vertices)]
        dot = (a[0] - b[0]) * (c[0] - b[0]) + (a[1] - b[1]) * (c[1] - b[1])
        if cross(a, b, c) == 0 and dot > 0:
            raise ValueError("Adjacent footprint edges must not overlap.")
    anchor = vertices[0]
    area2 = sum(cross(anchor, a, b) for a, b in edges)
    if not math.isfinite(area2) or area2 == 0:
        raise ValueError("Footprint must have a finite, nonzero area.")
    if area2 < 0:
        result = list(reversed(result))
    return result


def validate_document(document):
    if not isinstance(document, dict) or type(document.get("format_version")) is not int or document["format_version"] != 1:
        raise ValueError("Expected a format_version=1 geometry document.")
    site = document.get("site")
    metadata = document.get("map")
    if not isinstance(site, dict) or not isinstance(metadata, dict):
        raise ValueError("site and map objects are required.")
    normalized_site = {field: site.get(field) for field in SITE_FIELDS}
    for field in ("site_id", "name"):
        text_value(normalized_site[field], field)
    for field in ("origin_lon_deg", "origin_lat_deg", "origin_alt_m"):
        normalized_site[field] = number(normalized_site[field], field)
    if not -180 <= normalized_site["origin_lon_deg"] <= 180 or not -90 <= normalized_site["origin_lat_deg"] <= 90:
        raise ValueError("Site origin must use WGS84 longitude/latitude in degrees.")
    if site.get("frame") != "ENU" or site.get("vertical_datum") != "WGS84_ellipsoidal":
        raise ValueError("Expected ENU coordinates referenced to WGS84_ellipsoidal origin.")
    map_id = text_value(metadata.get("map_id"), "map_id")
    role = metadata.get("role")
    if role not in ROLES:
        raise ValueError(f"Invalid map role; expected one of {sorted(ROLES)}.")
    provenance = metadata.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        raise ValueError("Map provenance must be a nonempty object.")
    normalized_map = {"map_id": map_id, "role": role, "provenance": provenance}
    if metadata.get("source_asset_id") is not None:
        normalized_map["source_asset_id"] = text_value(metadata["source_asset_id"], "source_asset_id")
    source = document.get("buildings")
    if not isinstance(source, list):
        raise ValueError("buildings must be an array.")
    buildings, ids = [], set()
    for building in source:
        if not isinstance(building, dict):
            raise ValueError("Each building must be an object.")
        building_id = text_value(building.get("building_id"), "building_id")
        if building_id in ids:
            raise ValueError(f"Duplicate building_id: {building_id}")
        ids.add(building_id)
        ring = validate_ring(building.get("footprint_xy_m"))
        base = number(building.get("base_z_m"), "base_z_m")
        height = building.get("height_m")
        if height is not None:
            height = number(height, "height_m")
            if height < 0:
                raise ValueError("height_m must be nonnegative.")
        if role == "prior_2d" and height is not None:
            raise ValueError("prior_2d must have height_m=null to keep truth heights out of inputs.")
        if role == "lidar_truth" and height is None:
            raise ValueError("lidar_truth buildings require measured heights.")
        buildings.append({"building_id": building_id, "footprint_xy_m": ring,
                          "base_z_m": base, "height_m": height})
    result = {"format_version": 1, "site": normalized_site, "map": normalized_map,
              "buildings": sorted(buildings, key=lambda b: b["building_id"])}
    encode(result)  # Also reject non-JSON or NaN/Infinity provenance values.
    return result


def _export_map(conn, map_id):
    metadata = conn.execute("SELECT * FROM map_versions WHERE map_id=?", (map_id,)).fetchone()
    if metadata is None:
        raise ValueError(f"Map does not exist: {map_id}")
    site = conn.execute("SELECT * FROM sites WHERE site_id=?", (metadata["site_id"],)).fetchone()
    map_data = {"map_id": map_id, "role": metadata["role"],
                "provenance": json.loads(metadata["provenance_json"])}
    if metadata["source_asset_id"] is not None:
        map_data["source_asset_id"] = metadata["source_asset_id"]
    return {"format_version": 1, "site": {field: site[field] for field in SITE_FIELDS},
            "map": map_data, "buildings": _buildings(conn, map_id)}


def import_map(path, document):
    document = validate_document(document)
    site, metadata = document["site"], document["map"]
    with connection(path) as conn, conn:
        require_schema(conn)
        existing = conn.execute("SELECT site_id FROM map_versions WHERE map_id=?", (metadata["map_id"],)).fetchone()
        if existing:
            if _export_map(conn, metadata["map_id"]) != document:
                raise ValueError("Map versions are immutable; use a new map_id for changed geometry.")
            return {"map_id": metadata["map_id"], "inserted": 0, "already_present": True}
        current_site = conn.execute("SELECT * FROM sites WHERE site_id=?", (site["site_id"],)).fetchone()
        if current_site:
            if {field: current_site[field] for field in SITE_FIELDS} != site:
                raise ValueError("Site origin/metadata is immutable; use another site_id.")
        else:
            conn.execute("INSERT INTO sites VALUES (?,?,?,?,?,?,?,?)",
                         (*[site[field] for field in SITE_FIELDS], utc_now()))
        asset_id = metadata.get("source_asset_id")
        if asset_id:
            asset = conn.execute("SELECT site_id FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
            if asset is None or asset["site_id"] != site["site_id"]:
                raise ValueError("source_asset_id must refer to an asset from the same site.")
        conn.execute("INSERT INTO map_versions VALUES (?,?,?,?,?,?)",
                     (metadata["map_id"], site["site_id"], metadata["role"], asset_id,
                      encode(metadata["provenance"]), utc_now()))
        for building in document["buildings"]:
            ring = building["footprint_xy_m"]
            xs, ys = [p[0] for p in ring], [p[1] for p in ring]
            conn.execute("""INSERT INTO buildings
                (map_id,building_id,footprint_xy_json,base_z_m,height_m,min_x_m,max_x_m,min_y_m,max_y_m)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (metadata["map_id"], building["building_id"], encode(ring), building["base_z_m"],
                 building["height_m"], min(xs), max(xs), min(ys), max(ys)))
    return {"map_id": metadata["map_id"], "inserted": len(document["buildings"]), "already_present": False}


def _buildings(conn, map_id, bbox=None):
    sql = "SELECT b.* FROM buildings b"
    params = [map_id]
    if bbox is not None:
        sql += " JOIN building_bounds r ON r.id=b.id"
    sql += " WHERE b.map_id=?"
    if bbox is not None:
        x_min, y_min, x_max, y_max = bbox
        # RTree bounds round to float32; exact stored bounds remove rounding false positives.
        sql += """ AND r.max_x>=? AND r.min_x<=? AND r.max_y>=? AND r.min_y<=?
                   AND b.max_x_m>=? AND b.min_x_m<=? AND b.max_y_m>=? AND b.min_y_m<=?"""
        params += [x_min, x_max, y_min, y_max] * 2
    sql += " ORDER BY b.building_id"
    return [{"building_id": row["building_id"], "footprint_xy_m": json.loads(row["footprint_xy_json"]),
             "base_z_m": row["base_z_m"], "height_m": row["height_m"]}
            for row in conn.execute(sql, params)]


def list_buildings(path, map_id, bbox=None):
    if bbox is not None:
        if len(bbox) != 4:
            raise ValueError("bbox requires xmin ymin xmax ymax.")
        bbox = [number(v, "bbox") for v in bbox]
        if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
            raise ValueError("bbox minimums must not exceed maximums.")
    with connection(path) as conn:
        require_schema(conn)
        if not conn.execute("SELECT 1 FROM map_versions WHERE map_id=?", (map_id,)).fetchone():
            raise ValueError(f"Map does not exist: {map_id}")
        return _buildings(conn, map_id, bbox)


def export_map(path, map_id):
    with connection(path) as conn:
        require_schema(conn)
        return _export_map(conn, map_id)


def status(path):
    with connection(path) as conn:
        require_schema(conn)
        names = ("sites", "assets", "map_versions", "buildings", "flights", "poses",
                 "radio_observations", "cell_measurements", "base_stations", "reconstruction_runs")
        counts = {name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in names}
        maps = [dict(row) for row in conn.execute(
            "SELECT map_id,site_id,role,created_at_utc FROM map_versions ORDER BY map_id")]
        return {"schema_version": SCHEMA_VERSION, "counts": counts, "maps": maps}
