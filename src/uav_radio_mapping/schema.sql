-- Coordinates: local ENU in metres. Time: UTC Unix nanoseconds.
-- Large binary assets remain on disk; only metadata belongs here.
BEGIN;
PRAGMA user_version = 1;
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY CHECK (version = 1),
    created_at_utc TEXT NOT NULL
);
INSERT INTO schema_version VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

CREATE TABLE sites (
    site_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    origin_lon_deg REAL NOT NULL CHECK (origin_lon_deg BETWEEN -180 AND 180),
    origin_lat_deg REAL NOT NULL CHECK (origin_lat_deg BETWEEN -90 AND 90),
    origin_alt_m REAL NOT NULL,
    vertical_datum TEXT NOT NULL CHECK (vertical_datum = 'WGS84_ellipsoidal'),
    frame TEXT NOT NULL CHECK (frame = 'ENU'),
    created_at_utc TEXT NOT NULL
);
CREATE TABLE assets (
    asset_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(site_id),
    kind TEXT NOT NULL CHECK (kind IN ('point_cloud','mesh','orthophoto','flight_log','other')),
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    coordinate_reference TEXT NOT NULL,
    vertical_reference TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE map_versions (
    map_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(site_id),
    role TEXT NOT NULL CHECK (role IN ('prior_2d','lidar_truth','rf_reconstruction','synthetic')),
    source_asset_id TEXT REFERENCES assets(asset_id),
    provenance_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE buildings (
    id INTEGER PRIMARY KEY,
    map_id TEXT NOT NULL REFERENCES map_versions(map_id),
    building_id TEXT NOT NULL,
    footprint_xy_json TEXT NOT NULL,
    base_z_m REAL NOT NULL,
    height_m REAL CHECK (height_m >= 0),
    min_x_m REAL NOT NULL,
    max_x_m REAL NOT NULL,
    min_y_m REAL NOT NULL,
    max_y_m REAL NOT NULL,
    UNIQUE (map_id, building_id),
    CHECK (min_x_m < max_x_m AND min_y_m < max_y_m)
);
CREATE VIRTUAL TABLE building_bounds USING rtree(id, min_x, max_x, min_y, max_y);
CREATE TRIGGER buildings_insert AFTER INSERT ON buildings BEGIN
    INSERT INTO building_bounds VALUES (new.id, new.min_x_m, new.max_x_m, new.min_y_m, new.max_y_m);
END;
CREATE TRIGGER buildings_delete AFTER DELETE ON buildings BEGIN
    DELETE FROM building_bounds WHERE id = old.id;
END;
CREATE TRIGGER buildings_update AFTER UPDATE ON buildings BEGIN
    DELETE FROM building_bounds WHERE id = old.id;
    INSERT INTO building_bounds VALUES (new.id, new.min_x_m, new.max_x_m, new.min_y_m, new.max_y_m);
END;

-- Reserved contracts for later collection/replay; no hardware adapter in v0.1.
CREATE TABLE flights (
    flight_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(site_id),
    device_id TEXT NOT NULL,
    started_at_ns INTEGER NOT NULL,
    ended_at_ns INTEGER,
    metadata_json TEXT NOT NULL,
    CHECK (ended_at_ns IS NULL OR ended_at_ns >= started_at_ns)
);
CREATE TABLE poses (
    pose_id TEXT PRIMARY KEY,
    flight_id TEXT NOT NULL REFERENCES flights(flight_id),
    measured_at_ns INTEGER NOT NULL,
    x_m REAL NOT NULL,
    y_m REAL NOT NULL,
    z_m REAL NOT NULL,
    lon_deg REAL CHECK (lon_deg BETWEEN -180 AND 180),
    lat_deg REAL CHECK (lat_deg BETWEEN -90 AND 90),
    ellipsoid_alt_m REAL,
    quality_json TEXT NOT NULL
);
CREATE INDEX poses_flight_time ON poses(flight_id, measured_at_ns);
CREATE TABLE radio_observations (
    observation_id TEXT PRIMARY KEY,
    pose_id TEXT NOT NULL REFERENCES poses(pose_id),
    device_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    query_started_at_ns INTEGER NOT NULL,
    query_finished_at_ns INTEGER NOT NULL,
    received_at_ns INTEGER NOT NULL,
    pose_time_offset_ns INTEGER NOT NULL,
    raw_response TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    UNIQUE (device_id, sequence),
    CHECK (query_finished_at_ns >= query_started_at_ns)
);
CREATE INDEX observations_time ON radio_observations(query_started_at_ns, observation_id);
CREATE TABLE cell_measurements (
    id INTEGER PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES radio_observations(observation_id),
    role TEXT NOT NULL CHECK (role IN ('serving','neighbor')),
    rat TEXT NOT NULL,
    mcc TEXT,
    mnc TEXT,
    cell_id TEXT,
    tac TEXT,
    pci INTEGER CHECK (pci >= 0),
    arfcn INTEGER CHECK (arfcn >= 0),
    rsrp_dbm REAL,
    rsrq_db REAL,
    rssi_dbm REAL,
    sinr_db REAL,
    extras_json TEXT NOT NULL
);
CREATE INDEX cells_observation ON cell_measurements(observation_id);
CREATE TABLE base_stations (
    station_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(site_id),
    rat TEXT NOT NULL,
    mcc TEXT,
    mnc TEXT,
    cell_id TEXT,
    pci INTEGER,
    arfcn INTEGER,
    x_m REAL NOT NULL,
    y_m REAL NOT NULL,
    z_m REAL NOT NULL,
    uncertainty_m REAL CHECK (uncertainty_m >= 0),
    provenance_json TEXT NOT NULL
);
CREATE TABLE reconstruction_runs (
    run_id TEXT PRIMARY KEY,
    input_map_id TEXT NOT NULL REFERENCES map_versions(map_id),
    output_map_id TEXT NOT NULL REFERENCES map_versions(map_id),
    algorithm TEXT NOT NULL,
    git_commit TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    input_manifest_json TEXT NOT NULL,
    metrics_json TEXT,
    created_at_utc TEXT NOT NULL
);
COMMIT;
