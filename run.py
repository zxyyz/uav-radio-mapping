"""Run directly from a checkout without installing dependencies."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from uav_radio_mapping.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
