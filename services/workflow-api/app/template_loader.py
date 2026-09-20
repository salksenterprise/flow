from __future__ import annotations

import json
from pathlib import Path

from workflow_core import WorkflowEngine


def load_examples(engine: WorkflowEngine, directory: Path) -> None:
    if not directory.exists():
        return
    existing = {(item["key"], item["version_number"]) for item in engine.list_templates()}
    for path in sorted(directory.glob("*/workflow.json")):
        template = json.loads(path.read_text())
        identity = (template["key"], template.get("version", 1))
        if identity not in existing:
            engine.import_template(template)

