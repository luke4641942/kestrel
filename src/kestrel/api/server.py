"""ASGI entry point for uvicorn (``kestrel serve``).

Reads the data root (KESTREL_DATA), config file (KESTREL_CONFIG) and research notes
root (KESTREL_RESEARCH) from the environment.
"""

import os

from kestrel.api.app import create_app

app = create_app(
    data_dir=os.environ.get("KESTREL_DATA", "data"),
    config_path=os.environ.get("KESTREL_CONFIG", "kestrel.toml"),
    research_dir=os.environ.get("KESTREL_RESEARCH", "research"),
)
