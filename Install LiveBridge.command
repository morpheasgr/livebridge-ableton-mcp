#!/bin/bash
# Double-click in Finder: installs LiveBridge (Remote Script, Max devices, MCP server, skill)
# from this folder. The first time, macOS may ask: right-click -> Open.
# One block on purpose: bash reads the whole block before it runs it.
{
    cd "$(dirname "$0")" && bash ./installers/install.sh "$@"
    echo
    read -n 1 -s -r -p "Press any key to close this window."
    echo
    exit
}
