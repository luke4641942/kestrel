"""ASGI entry point for uvicorn (``kestrel serve``). Reads the data root from KESTREL_DATA."""

import os

from kestrel.api.app import create_app

app = create_app(data_dir=os.environ.get("KESTREL_DATA", "data"))
