"""The FastAPI app: host check, CSRF token, templates, static files and the Inbox."""

import secrets
from datetime import datetime
from pathlib import Path

import jinja2
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from chatmaid.cli.common import parse_since
from chatmaid.config import ConfigError
from chatmaid.init.categories import CATEGORIES
from chatmaid.llm.base import LLMError
from chatmaid.lookup import related, search
from chatmaid.model import SourceRef
from chatmaid.views import account_labels, conversation_view, inbox, message_view
from chatmaid.web.services import Services

HERE = Path(__file__).parent
PAGE_SIZE = 50
RELATED_LIMIT = 10
SEARCH_LIMIT = 20
FULL_TEXT_NOTICE = "full-text only: no LM Studio token"
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

    @app.exception_handler(StarletteHTTPException)
    async def error_page(request: Request, exc: StarletteHTTPException):
        if exc.status_code != 404:
            return await http_exception_handler(request, exc)
        return templates.TemplateResponse(
            request, "error.html", {"status": 404, "detail": "Not found."}, status_code=404
        )

    def model_or_none():
        """The model client, or None with the notice to show when there is no token."""
        try:
            return services.make_llm(), None
        except ConfigError:
            return None, FULL_TEXT_NOTICE

    def with_fallback(run):
        """run(llm) with the model; on a model failure repeat it full-text only."""
        llm, notice = model_or_none()
        try:
            return run(llm), notice
        except LLMError as e:
            return run(None), f"full-text only: the model failed ({e})"

    def previews_of(store, conv):
        return {r.message_id: store.message(r.message_id)["preview"] or "" for r in conv.messages}

    @app.get("/messages/{message_id}")
    def message_page(request: Request, message_id: int):
        with services.session() as (store, connector):
            try:
                msg = message_view(store, connector, message_id)
            except KeyError:
                raise HTTPException(status_code=404, detail="no such message") from None
            conv = conversation_view(store, msg.conversation_id, limit=PAGE_SIZE)
            account = SourceRef.from_key(store.message(message_id)["ref_key"]).account
            account = account_labels(connector).get(account, account)
            draft = store.pending_draft_for(msg.conversation_id)
            hits, notice = with_fallback(
                lambda llm: related(
                    store, llm, msg.conversation_id,
                    embedding_model=cfg.lmstudio.model_embedding, limit=RELATED_LIMIT,
                )
            )
            return templates.TemplateResponse(
                request, "message.html",
                {"msg": msg, "conv": conv, "account": account, "draft": draft, "hits": hits,
                 "notice": notice, "previews": previews_of(store, conv)},
            )

    @app.get("/conversations/{conversation_id}")
    def conversation_page(request: Request, conversation_id: int, before: int | None = None):
        with services.session() as (store, _connector):
            try:
                conv = conversation_view(store, conversation_id, before=before, limit=PAGE_SIZE)
            except KeyError:
                raise HTTPException(status_code=404, detail="no such conversation") from None
            return templates.TemplateResponse(
                request, "conversation.html", {"conv": conv, "previews": previews_of(store, conv)}
            )

    @app.get("/search")
    def search_page(
        request: Request, q: str = "", category: list[str] = Query(default=[]),
        contact: str = "", since: str = "", until: str = "",
    ):
        if any(c not in CATEGORIES for c in category):
            raise HTTPException(status_code=400, detail="unknown category")
        try:
            since_ts = parse_since(since) if since else None
            until_ts = parse_since(until) + 86399 if until else None  # the whole day
        except ValueError:
            raise HTTPException(status_code=400, detail="dates are YYYY-MM-DD") from None
        hits, notice = [], None
        if q.strip():
            with services.session() as (store, _connector):
                hits, notice = with_fallback(
                    lambda llm: search(
                        store, llm, query=q, embedding_model=cfg.lmstudio.model_embedding,
                        contact=contact or None, since=since_ts, until=until_ts,
                        categories=tuple(category), limit=SEARCH_LIMIT,
                    )
                )
        return templates.TemplateResponse(
            request, "search.html",
            {"q": q, "contact": contact, "since": since, "until": until, "chosen": category,
             "categories": CATEGORIES, "hits": hits, "notice": notice},
        )

    return app
