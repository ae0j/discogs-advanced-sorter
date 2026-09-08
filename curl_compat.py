"""Compatibility for curl-cffi's missing macOS framework linkage."""

import ctypes
import importlib
import sys


_SYSTEM_CONFIGURATION = (
    "/System/Library/Frameworks/SystemConfiguration.framework/SystemConfiguration"
)
_system_configuration = None


def load_curl_requests():
    try:
        return importlib.import_module("curl_cffi.requests")
    except ImportError as exc:
        if (
            sys.platform != "darwin"
            or "curl_cffi" not in str(exc)
            or "_SCDynamicStoreCopyProxies" not in str(exc)
        ):
            raise

        # curl-cffi 0.15's macOS wheel references this framework without linking
        # it. Expose Apple's symbols to the native loader, then retry once.
        # Keep the handle alive for the lifetime of the imported extension.
        global _system_configuration
        _system_configuration = ctypes.CDLL(
            _SYSTEM_CONFIGURATION, mode=ctypes.RTLD_GLOBAL
        )
        return importlib.import_module("curl_cffi.requests")
