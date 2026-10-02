"""Local dev entrypoint: runs the app with auto-reload against your local
Ollama (LLM_PROVIDER/LLM_MODEL default to ollama / qwen3.5:9b, see
app/llm/config.py). Set LLM_PROVIDER=none before launching to run in manual
mode without any AI.
"""

import sys
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app", host="127.0.0.1", port=8000, reload=True, app_dir=project_root
    )
