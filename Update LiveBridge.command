#!/bin/bash
# Double-click in Finder: updates this LiveBridge folder to the newest version, then runs the
# installer. Quit Claude Desktop first (it runs the MCP server from this folder). The first
# time, macOS may ask: right-click -> Open. See installers/update.py.
# One block on purpose: the update may replace this file while it runs, and bash reads the
# whole block before it runs it.
{
    cd "$(dirname "$0")" && bash ./installers/install.sh --update "$@"
    echo
    read -n 1 -s -r -p "Press any key to close this window."
    echo
    exit
}
