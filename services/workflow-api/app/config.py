from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATABASE_PATH = Path(os.environ.get("WORKFLOW_DB_PATH", PROJECT_ROOT / "workflow.db"))
EXAMPLES_PATH = Path(os.environ.get("WORKFLOW_EXAMPLES_PATH", PROJECT_ROOT / "examples"))
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

