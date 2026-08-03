"""Entry point for `python -m lingtai.mcp_servers.whatsapp` and the lingtai-whatsapp script."""
from __future__ import annotations

from lingtai.mcp_servers._entrypoint import run_stdio_server_main

from .server import serve


def main() -> None:
    run_stdio_server_main(serve)


if __name__ == "__main__":
    main()
