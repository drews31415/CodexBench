"""Streamlit Community Cloud entrypoint: AAH experiment results dashboard.

Reads only ``public_data/`` (versioned public exports committed to the repository). It needs no Codex
account, Docker, private evaluator, credentials or network access, and it never calls a model.
Local run:  streamlit run dashboard/streamlit_app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aah_dashboard.pages import run_app  # noqa: E402

run_app()
