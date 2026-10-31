"""ASGI entry point for uvicorn (``kestrel serve``).

Reads the data root from KESTREL_DATA and the config file from KESTREL_CONFIG.
"""

import os

from kestrel.api.app import create_app

app = create_app(
    data_dir=os.environ.get("KESTREL_DATA", "data"),
    config_path=os.environ.get("KESTREL_CONFIG", "kestrel.toml"),
)
