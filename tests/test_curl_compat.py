import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import curl_compat


REPO_ROOT = Path(__file__).resolve().parents[1]
MISSING_PROXY_SYMBOL = (
    "dlopen(/Users/test/Discogs SEARCH/.venv/lib/python3.13/site-packages/"
    "curl_cffi/_wrapper.abi3.so, 2): Symbol not found: _SCDynamicStoreCopyProxies"
)


class CurlCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.original_handle = curl_compat._system_configuration
        self.addCleanup(
            setattr, curl_compat, "_system_configuration", self.original_handle
        )

    def test_successful_import_does_not_load_a_framework(self):
        requests_module = object()
        with (
            patch.object(curl_compat.importlib, "import_module", return_value=requests_module),
            patch.object(curl_compat.ctypes, "CDLL") as load_framework,
        ):
            self.assertIs(curl_compat.load_curl_requests(), requests_module)
        load_framework.assert_not_called()

    def test_missing_macos_symbol_loads_framework_before_retrying(self):
        requests_module = object()
        handle = object()
        events = []

        def import_requests(name):
            self.assertEqual(name, "curl_cffi.requests")
            events.append("import")
            if len(events) == 1:
                raise ImportError(MISSING_PROXY_SYMBOL)
            self.assertIs(curl_compat._system_configuration, handle)
            return requests_module

        def load_framework(path, mode):
            self.assertEqual(path, curl_compat._SYSTEM_CONFIGURATION)
            self.assertEqual(mode, curl_compat.ctypes.RTLD_GLOBAL)
            events.append("framework")
            return handle

        with (
            patch.object(curl_compat.sys, "platform", "darwin"),
            patch.object(curl_compat.importlib, "import_module", side_effect=import_requests),
            patch.object(curl_compat.ctypes, "CDLL", side_effect=load_framework),
        ):
            self.assertIs(curl_compat.load_curl_requests(), requests_module)
        self.assertEqual(events, ["import", "framework", "import"])

    def test_other_import_errors_are_not_retried_or_hidden(self):
        for platform, message in (
            ("win32", MISSING_PROXY_SYMBOL),
            ("linux", MISSING_PROXY_SYMBOL),
            ("darwin", "No module named 'curl_cffi'"),
            ("darwin", "curl_cffi/_wrapper.abi3.so: incompatible architecture"),
            ("darwin", "another_library: Symbol not found: _SCDynamicStoreCopyProxies"),
        ):
            with self.subTest(platform=platform, message=message):
                original_error = ImportError(message)
                with (
                    patch.object(curl_compat.sys, "platform", platform),
                    patch.object(
                        curl_compat.importlib, "import_module", side_effect=original_error
                    ) as import_module,
                    patch.object(curl_compat.ctypes, "CDLL") as load_framework,
                    self.assertRaises(ImportError) as raised,
                ):
                    curl_compat.load_curl_requests()
                self.assertIs(raised.exception, original_error)
                self.assertEqual(import_module.call_count, 1)
                load_framework.assert_not_called()

    def test_failed_retry_preserves_the_error_and_does_not_loop(self):
        retry_error = ImportError("curl_cffi: a different native dependency is missing")
        with (
            patch.object(curl_compat.sys, "platform", "darwin"),
            patch.object(
                curl_compat.importlib,
                "import_module",
                side_effect=[ImportError(MISSING_PROXY_SYMBOL), retry_error],
            ) as import_module,
            patch.object(curl_compat.ctypes, "CDLL") as load_framework,
            self.assertRaises(ImportError) as raised,
        ):
            curl_compat.load_curl_requests()
        self.assertIs(raised.exception, retry_error)
        self.assertEqual(import_module.call_count, 2)
        load_framework.assert_called_once()

    def test_app_imports_and_serves_home_in_a_fresh_process(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app import app; "
                "assert app.test_client().get('/').status_code == 200",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(sys.platform == "darwin", "Requires Apple's framework")
    def test_recovery_loads_real_framework_and_imports_real_curl_in_fresh_process(self):
        # This Mac may already load the framework indirectly. Inject only the
        # initial loader error; framework loading and the second import are real.
        script = f"""
import curl_compat
import sys

class FailFirstWrapper:
    attempts = 0

    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'curl_cffi._wrapper':
            self.attempts += 1
            if self.attempts == 1:
                raise ImportError({MISSING_PROXY_SYMBOL!r})
        return None

failure = FailFirstWrapper()
sys.meta_path.insert(0, failure)
from app import app
assert failure.attempts == 2, failure.attempts
assert curl_compat._system_configuration.SCDynamicStoreCopyProxies
from process import curl_requests
with curl_requests.Session(impersonate='chrome') as session:
    assert session is not None
assert app.test_client().get('/').status_code == 200
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
