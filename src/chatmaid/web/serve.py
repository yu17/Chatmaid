"""Run the web app under uvicorn on every configured address."""

import socket
from collections.abc import Iterable

import uvicorn

from chatmaid.web.app import create_app
from chatmaid.web.services import Services


def bind_sockets(hosts: Iterable[str], port: int) -> list[socket.socket]:
    """One listening socket per host; closes any already bound if a later one fails."""
    socks: list[socket.socket] = []
    try:
        for host in hosts:
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            s = socket.socket(family, socket.SOCK_STREAM)
            socks.append(s)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            s.listen(128)
    except BaseException:
        for s in socks:
            s.close()
        raise
    return socks


def run(services: Services) -> None:
    web = services.cfg.web
    socks = bind_sockets(web.hosts, web.port)
    for host, s in zip(web.hosts, socks):
        shown = f"[{host}]" if ":" in host else host
        print(f"serving on http://{shown}:{s.getsockname()[1]}{web.base_path}/", flush=True)
    config = uvicorn.Config(create_app(services), log_level="warning")
    uvicorn.Server(config).run(sockets=socks)
