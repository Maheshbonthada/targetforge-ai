import os
from pathlib import Path

os.environ.setdefault(
    "TARGETFORGE_PATHWAYS_PATH", str(Path(__file__).resolve().parents[3] / "data" / "knowledge" / "pathways.yaml")
)
os.environ.setdefault("AUTH_DISABLED_FOR_LOCAL_DEV", "true")
