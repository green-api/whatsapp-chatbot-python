"""Keep base installations testable without installing the optional audio stack."""

import sys
from importlib.util import find_spec

collect_ignore_glob = []
if sys.version_info < (3, 11):
    collect_ignore_glob = ["test_*.py"]
elif any(find_spec(name) is None for name in ("openai", "aiortc", "av")):
    collect_ignore_glob = [
        "test_call_service.py", "test_realtime_voice.py", "test_call_recording.py"
    ]
