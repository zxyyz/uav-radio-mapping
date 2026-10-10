"""Dependency-free CLI for initializing and inspecting local geometry."""
import argparse
from importlib.resources import files
import json
from pathlib import Path
import sqlite3
import sys

from . import store


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local UAV geometric map database")
    parser.add_argument("--db", type=Path, default=Path("data/geometry/maps.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create the empty database")
    commands.add_parser("status", help="Show counts and map versions")
    commands.add_parser("demo", help="Load clearly labeled synthetic buildings (idempotent)")
    importer = commands.add_parser("import-buildings", help="Import an ENU building JSON document")
    importer.add_argument("input", type=Path)
    buildings = commands.add_parser("buildings", help="List buildings or query bounding-box candidates")
    buildings.add_argument("map_id")
    buildings.add_argument("--bbox", nargs=4, type=float, metavar=("XMIN", "YMIN", "XMAX", "YMAX"))
    exporter = commands.add_parser("export-buildings", help="Export a portable ENU JSON document")
    exporter.add_argument("map_id")
    exporter.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            store.initialize(args.db)
            result = store.status(args.db)
        elif args.command == "demo":
            store.initialize(args.db)
            document = json.loads(files(__package__).joinpath("demo-buildings.json").read_text())
            result = store.import_map(args.db, document)
        elif args.command == "status":
            result = store.status(args.db)
        elif args.command == "import-buildings":
            result = store.import_map(args.db, json.loads(args.input.read_text(encoding="utf-8")))
        elif args.command == "buildings":
            result = store.list_buildings(args.db, args.map_id, args.bbox)
        else:
            if args.output.resolve() == args.db.resolve():
                raise ValueError("Export output must not overwrite the database.")
            result = store.export_map(args.db, args.map_id)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
            result = {"output": str(args.output), "buildings": len(result["buildings"])}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
