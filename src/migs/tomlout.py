"""Writing TOML by hand (the tools emit small files without a TOML writer library)."""
from __future__ import annotations

import json


def toml_str(s: str) -> str:
    """A TOML basic string. JSON's string escapes (\\\\, \\", \\n, \\uXXXX, ...) are all valid
    TOML, so json.dumps produces one."""
    return json.dumps(s, ensure_ascii=False)
