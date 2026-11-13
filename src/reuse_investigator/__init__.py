"""Frame Reuse Investigator.

A standalone, resumable tool that traces edited probe footage back to the
uncompressed AVI clips registered with the Frame Proxy Inspector service.

The package deliberately does not import any production service module for
writing: it only reuses the production AVI parser and frame decoder as
read-only media adapters. It keeps its own data root, its own records, and
its own lifecycle, so the production service is never modified.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"

# Bumped whenever the signature or verification semantics change. Stored
# evidence created under an older profile is only reused when the profile and
# the material analysis conditions still match exactly.
SIGNATURE_PROFILE = "reuse-sig-v1"
