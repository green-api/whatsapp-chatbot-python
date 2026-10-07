"""The optional call implementation must not affect ordinary chatbot imports."""

import subprocess
import sys
import unittest


class CallsImportTest(unittest.TestCase):
    def run_script(self, script):
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_base_import_does_not_require_audio_or_openai(self):
        self.run_script('''
import importlib.abc
import sys

class BlockOptionalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"openai", "aiortc", "av"}:
            raise ImportError("optional dependency blocked: " + fullname)

sys.meta_path.insert(0, BlockOptionalImports())
from whatsapp_chatbot_python import GreenAPIBot, Notification
assert "whatsapp_chatbot_python.calls" not in sys.modules
''')

    @unittest.skipIf(sys.version_info < (3, 11), "call contracts require Python 3.11+")
    def test_contracts_do_not_load_call_implementation(self):
        self.run_script('''
import sys
from whatsapp_chatbot_python.calls import CallSession, CallStateMachine
assert CallSession("sender", "chat", "en").state == "triggered"
assert "whatsapp_chatbot_python.calls.service" not in sys.modules
assert "whatsapp_chatbot_python.calls.realtime_voice" not in sys.modules
assert "openai" not in sys.modules
assert "aiortc" not in sys.modules
assert "av" not in sys.modules
''')

    def test_unsupported_python_has_explicit_error(self):
        self.run_script('''
import sys
import whatsapp_chatbot_python
sys.version_info = (3, 10, 0)
try:
    import whatsapp_chatbot_python.calls
except ImportError as error:
    assert "Python 3.11" in str(error)
else:
    raise AssertionError("calls accepted an unsupported Python version")
''')
