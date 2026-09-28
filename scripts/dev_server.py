"""Local dev entrypoint. Defaults LLM_PROVIDER to "none" so the app never
blocks on a half-configured local Ollama -- set LLM_PROVIDER=ollama (see
README) once your Ollama setup is ready; this default never overrides an
explicitly-set env var.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("LLM_PROVIDER", "none")

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app", host="127.0.0.1", port=8000, reload=True, app_dir=project_root
    )
