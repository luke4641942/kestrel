"""ASGI entry point for uvicorn (``kestrel serve``).

Reads the data root (KESTREL_DATA), config file (KESTREL_CONFIG) and research notes
root (KESTREL_RESEARCH) from the environment.
"""

import os

from kestrel.api.app import LOCAL_HOSTS, create_app
from kestrel.assistant import ResearchAssistant

app = create_app(
    data_dir=os.environ.get("KESTREL_DATA", "data"),
    config_path=os.environ.get("KESTREL_CONFIG", "kestrel.toml"),
    research_dir=os.environ.get("KESTREL_RESEARCH", "research"),
    assistant=ResearchAssistant(),
    # A non-local --host (e.g. a LAN address) must be allowed by name too.
    allowed_hosts=(*LOCAL_HOSTS, *filter(None, [os.environ.get("KESTREL_HOST")])),
)
