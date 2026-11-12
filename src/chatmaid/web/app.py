"""The FastAPI app: host check, CSRF token, templates, static files and the Inbox."""

import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import jinja2
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
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
from chatmaid.web.jobs import JobRunner
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
CLOUD_BACKENDS = ("codex", "claude")
CLOUD_LIST_LIMIT = 200
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


def create_app(services: Services, *, jobs: JobRunner | None = None) -> FastAPI:
    cfg = services.cfg

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runner = app.state.jobs = jobs or JobRunner(services)
        try:
            yield
        finally:
            runner.stop()

    app = FastAPI(
        root_path=cfg.web.base_path, docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=lifespan,
    )
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
            jobs = request.app.state.jobs.for_subject("draft", message_id)
            hits, notice = with_fallback(
                lambda llm: related(
                    store, llm, msg.conversation_id,
                    embedding_model=cfg.lmstudio.model_embedding, limit=RELATED_LIMIT,
                )
            )
            return templates.TemplateResponse(
                request, "message.html",
                {"msg": msg, "conv": conv, "account": account, "draft": draft, "hits": hits,
                 "notice": notice, "previews": previews_of(store, conv), "jobs": jobs},
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

    def redirect(request: Request, path: str) -> RedirectResponse:
        return RedirectResponse(request.scope.get("root_path", "") + path, status_code=303)

    @app.post("/messages/{message_id}/draft", dependencies=[Depends(check_csrf)])
    def draft_message(request: Request, message_id: int):
        with services.session() as (store, _connector):
            row = store.message(message_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such message")
        if request.app.state.jobs.busy("draft", message_id):
            return redirect(request, f"/messages/{message_id}")  # already queued; the page shows it
        request.app.state.jobs.submit(
            "draft", "Drafting a reply", message_id, lambda flow: flow.draft_for_message(message_id)
        )
        return redirect(request, f"/messages/{message_id}")

    def draft_page_response(request: Request, draft_id: int, flash: str | None = None):
        with services.session() as (store, connector):
            draft = store.draft(draft_id)
            if draft is None:
                raise HTTPException(status_code=404, detail="no such draft")
            versions = store.draft_versions(draft_id)
            latest = max((v["round"] for v in versions), default=0)
            current = [v for v in versions if v["round"] == latest]
            earlier = {}
            for v in versions:
                if v["round"] < latest:
                    earlier.setdefault(v["round"], []).append(v)
            try:
                msg = message_view(store, connector, draft["message_id"])
            except KeyError:
                msg = None
            return templates.TemplateResponse(
                request, "draft.html",
                {"draft": draft, "msg": msg, "current": current, "earlier": earlier,
                 "jobs": request.app.state.jobs.for_subject("regenerate", draft_id)
                 + request.app.state.jobs.for_subject("propose", draft_id),
                 "proposals": [
                     c for c in store.cloud_calls(limit=CLOUD_LIST_LIMIT)
                     if c["draft_id"] == draft_id and c["status"] == "proposed"
                 ],
                 "backends": CLOUD_BACKENDS, "default_backend": cfg.cloud.default_backend,
                 "flash": flash},
            )

    @app.get("/drafts/{draft_id}")
    def draft_page(request: Request, draft_id: int):
        return draft_page_response(request, draft_id)

    def act(request: Request, draft_id: int, action: str, **kwargs):
        """Run a synchronous draft action; a refusal comes back as a message on the page."""
        with services.session() as (store, connector):
            if store.draft(draft_id) is None:
                raise HTTPException(status_code=404, detail="no such draft")
            try:
                services.flow(store, connector, model=False).resolve(draft_id, action, **kwargs)
            except ValueError as e:
                error = str(e)
            else:
                error = None
        if error is not None:
            return draft_page_response(request, draft_id, flash=error)
        return redirect(request, f"/drafts/{draft_id}")

    @app.post("/drafts/{draft_id}/regenerate", dependencies=[Depends(check_csrf)])
    def regenerate_draft(
        request: Request, draft_id: int, comment: str = Form(""), from_version: str = Form("")
    ):
        with services.session() as (store, _connector):
            draft = store.draft(draft_id)
            if draft is None:
                raise HTTPException(status_code=404, detail="no such draft")
        if draft["status"] != "pending":
            return draft_page_response(
                request, draft_id, flash=f"draft {draft_id} is {draft['status']}, not pending"
            )
        if not comment.strip():
            return draft_page_response(request, draft_id, flash="Say what to change first.")
        try:
            version = int(from_version) if from_version else None
        except ValueError:
            raise HTTPException(status_code=400, detail="bad version") from None
        if request.app.state.jobs.busy("regenerate", draft_id):
            return draft_page_response(request, draft_id, flash="Already working on it.")
        request.app.state.jobs.submit(
            "regenerate", "Regenerating", draft_id,
            lambda flow: flow.regenerate(draft_id, comment, from_version=version),
        )
        return redirect(request, f"/drafts/{draft_id}")

    @app.post("/drafts/{draft_id}/approve", dependencies=[Depends(check_csrf)])
    def approve_draft(request: Request, draft_id: int, version_id: str = Form("")):
        try:
            version = int(version_id) if version_id else None
        except ValueError:
            raise HTTPException(status_code=400, detail="bad version") from None
        return act(request, draft_id, "approve", version_id=version)

    @app.post("/drafts/{draft_id}/edit", dependencies=[Depends(check_csrf)])
    def edit_draft(request: Request, draft_id: int, text: str = Form("")):
        return act(request, draft_id, "edit", text=text.strip())

    @app.post("/drafts/{draft_id}/dismiss", dependencies=[Depends(check_csrf)])
    def dismiss_draft(request: Request, draft_id: int):
        return act(request, draft_id, "dismiss")

    @app.post("/drafts/{draft_id}/cloud", dependencies=[Depends(check_csrf)])
    def propose_improvement(
        request: Request, draft_id: int, backend: str = Form(""), comment: str = Form(""),
        from_version: str = Form(""),
    ):
        """Queue the proposal: it builds the payload and stores it; nothing leaves the machine."""
        backend = backend or cfg.cloud.default_backend
        if backend not in CLOUD_BACKENDS:
            raise HTTPException(status_code=400, detail="unknown backend")
        try:
            version = int(from_version) if from_version else None
        except ValueError:
            raise HTTPException(status_code=400, detail="bad version") from None
        with services.session() as (store, _connector):
            draft = store.draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="no such draft")
        if draft["status"] != "pending":
            return draft_page_response(
                request, draft_id, flash=f"draft {draft_id} is {draft['status']}, not pending"
            )
        if request.app.state.jobs.busy("propose", draft_id):
            return draft_page_response(request, draft_id, flash="Already preparing a proposal.")
        request.app.state.jobs.submit(
            "propose", "Preparing the cloud payload", draft_id,
            lambda flow: flow.propose_cloud(
                draft_id, backend=backend, comment=comment.strip() or None, from_version=version
            ).call_id,
        )
        return redirect(request, f"/drafts/{draft_id}")

    def call_page_response(request: Request, call_id: int, flash: str | None = None):
        with services.session() as (store, _connector):
            call = store.cloud_call(call_id)
            if call is None:
                raise HTTPException(status_code=404, detail="no such cloud call")
            # the payload is pseudonymized; the mapping back to real names is never shown
            return templates.TemplateResponse(
                request, "cloud_call.html",
                {"call": call, "jobs": request.app.state.jobs.for_subject("send", call_id),
                 "flash": flash},
            )

    @app.get("/cloud")
    def cloud_page(request: Request):
        with services.session() as (store, _connector):
            calls = store.cloud_calls(limit=CLOUD_LIST_LIMIT)
        return templates.TemplateResponse(request, "cloud.html", {"calls": calls})

    @app.get("/cloud/{call_id}")
    def cloud_call_page(request: Request, call_id: int):
        return call_page_response(request, call_id)

    @app.post("/cloud/{call_id}/send", dependencies=[Depends(check_csrf)])
    def send_call(request: Request, call_id: int):
        """The owner's consent: send exactly the stored payload."""
        with services.session() as (store, _connector):
            call = store.cloud_call(call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="no such cloud call")
        if call["status"] != "proposed":
            return call_page_response(
                request, call_id, flash=f"cloud call {call_id} is {call['status']}, not proposed"
            )
        if request.app.state.jobs.busy("send", call_id):
            return redirect(request, f"/cloud/{call_id}")  # already queued; the page shows it
        request.app.state.jobs.submit(
            "send", f"Sending to {call['backend']}", call_id, lambda flow: flow.send_cloud(call_id)
        )
        return redirect(request, f"/cloud/{call_id}")

    @app.post("/cloud/{call_id}/decline", dependencies=[Depends(check_csrf)])
    def decline_call(request: Request, call_id: int):
        with services.session() as (store, connector):
            if store.cloud_call(call_id) is None:
                raise HTTPException(status_code=404, detail="no such cloud call")
            try:
                services.flow(store, connector, model=False).decline_cloud(call_id)
            except ValueError as e:
                error = str(e)
            else:
                error = None
        if error is not None:
            return call_page_response(request, call_id, flash=error)
        return redirect(request, f"/cloud/{call_id}")

    @app.get("/jobs/{job_id}")
    def job_state(request: Request, job_id: str):
        job = request.app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return JSONResponse({"state": job.state, "result": job.result, "error": job.error})

    return app
