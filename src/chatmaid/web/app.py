"""The FastAPI app: host check, CSRF token, templates, static files and the Inbox."""

import secrets
from datetime import datetime
from pathlib import Path

import jinja2
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from chatmaid.init.categories import CATEGORIES
from chatmaid.views import account_labels, inbox
from chatmaid.web.services import Services

HERE = Path(__file__).parent
PAGE_SIZE = 50
# No inline script, style or handlers in templates: the policy forbids them.
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
    ),
}
INBOX_FILTERS = ("default", "needs_reply", "drafts", "all", *CATEGORIES)


def _context(request: Request) -> dict:
    return {"csrf": request.app.state.csrf, "base": request.scope.get("root_path", "")}


def _when(ts: int | None) -> str:
    """A timestamp in the server's local time zone; a missing time shows as a dash."""
    if not ts:
        return "\u2014"
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M")


templates = Jinja2Templates(
    env=jinja2.Environment(loader=jinja2.FileSystemLoader(HERE / "templates"), autoescape=True),
    context_processors=[_context],
)
templates.env.filters["when"] = _when


def host_of(header: str) -> str:
    """The Host header without its port and without IPv6 brackets."""
    if header.startswith("["):
        return header[1:].split("]", 1)[0]
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


def check_csrf(request: Request, csrf: str = Form("")) -> None:
    """Dependency for every POST route: the form must carry this process's token."""
    if not secrets.compare_digest(csrf.encode(), request.app.state.csrf.encode()):
        raise HTTPException(status_code=403, detail="bad CSRF token")


def create_app(services: Services, *, jobs=None) -> FastAPI:
    cfg = services.cfg
    app = FastAPI(root_path=cfg.web.base_path, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services
    app.state.jobs = jobs
    app.state.csrf = secrets.token_urlsafe(32)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    allowed = frozenset(h.lower() for h in cfg.web.allowed_hosts)

    @app.middleware("http")
    async def host_check(request: Request, call_next):
        if host_of(request.headers.get("host", "")).lower() not in allowed:
            response = PlainTextResponse("unknown host", status_code=400)
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.get("/")
    def inbox_page(request: Request, filter: str = "default", before: int | None = None):
        if filter not in INBOX_FILTERS:
            raise HTTPException(status_code=400, detail="unknown filter")
        with services.session() as (store, connector):
            rows = inbox(
                store, filter=filter, before=before, limit=PAGE_SIZE,
                account_labels=account_labels(connector),
            )
        older = rows[-1].sent_at if len(rows) == PAGE_SIZE else None
        return templates.TemplateResponse(
            request, "inbox.html",
            {"rows": rows, "filter": filter, "filters": INBOX_FILTERS, "older": older},
        )

    return app
