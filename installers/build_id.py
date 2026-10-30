"""Print the build fingerprint of this checkout's MCP server: the value ``live_status`` shows as
``mcp_host.build`` when the running server loaded this code.

Usage: ``python installers/build_id.py``
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "mcp_server"))

from livebridge_mcp.tools.system import _BUILD  # noqa: E402

if __name__ == "__main__":
    print(_BUILD)
