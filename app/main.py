import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.api.routes_admin import router as admin_router
from app.api.routes_analysis import router as analysis_router
from app.api.routes_auth import COOKIE_NAME, router as auth_router
from app.api.routes_evaluation import router as evaluation_router
from app.api.routes_index import router as index_router
from app.api.routes_images import router as images_router
from app.api.routes_rag import router as rag_router
from app.api.routes_security import router as security_router
from app.api.routes_sns import router as sns_router
from app.api.routes_workspaces import router as workspaces_router
from app.db.models import OperationLog, init_db
from app.db.session import SessionLocal, engine
from app.services.auth import resolve_session, users_exist
from app.services.scheduler import scheduler_loop
from app.services.sns_scheduler import sns_scheduler_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db(engine)
    task = asyncio.create_task(scheduler_loop())
    sns_task = asyncio.create_task(sns_scheduler_loop())
    yield
    task.cancel()
    sns_task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    with suppress(asyncio.CancelledError):
        await sns_task


app = FastAPI(title="Multi-domain System Intelligence RAG", version="4.0.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(index_router)
app.include_router(images_router)
app.include_router(rag_router)
app.include_router(security_router)
app.include_router(evaluation_router)
app.include_router(analysis_router)
app.include_router(admin_router)
app.include_router(sns_router)
app.include_router(workspaces_router)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "web" / "templates"))

PUBLIC_PATHS = {"/", "/health", "/auth/status", "/auth/setup", "/auth/login",
                "/auth/google/start", "/auth/google/callback", "/favicon.ico"}
OPERATOR_MUTATIONS = {"/index/scan", "/index/dry-run", "/security/audit", "/admin/index/run", "/admin/documents"}
ADMIN_MUTATIONS = {"/evaluation/run", "/analysis/reanalyze", "/admin/users", "/admin/backups", "/admin/restore", "/admin/schedule", "/admin/sns/schedule", "/admin/workspaces"}


@app.middleware("http")
async def authentication_and_audit(request: Request, call_next):
    started = time.perf_counter()
    path = request.url.path.rstrip("/") or "/"
    user = None
    setup_required = False
    try:
        with SessionLocal() as db:
            setup_required = not users_exist(db)
            user = resolve_session(db, request.cookies.get(COOKIE_NAME))
    except Exception:
        logger.exception("Authentication lookup failed")
    request.state.user = user

    rejection = None
    if path not in PUBLIC_PATHS and not path.startswith("/docs") and not path.startswith("/openapi.json"):
        if setup_required:
            rejection = JSONResponse({"detail": "最初に管理者を登録してください", "setup_required": True}, status_code=428)
        elif not user:
            rejection = JSONResponse({"detail": "ログインしてください"}, status_code=401)
        elif request.method not in {"GET", "HEAD", "OPTIONS"}:
            role = user.role
            if path in ADMIN_MUTATIONS or any(path.startswith(prefix + "/") for prefix in ADMIN_MUTATIONS):
                if role != "admin":
                    rejection = JSONResponse({"detail": "管理者権限が必要です"}, status_code=403)
            elif path in OPERATOR_MUTATIONS or path.startswith("/admin/"):
                if role not in {"admin", "operator"}:
                    rejection = JSONResponse({"detail": "担当者以上の権限が必要です"}, status_code=403)
        elif path == "/admin/users" and user.role != "admin":
            rejection = JSONResponse({"detail": "管理者権限が必要です"}, status_code=403)
        elif path.startswith("/admin/") and user.role not in {"admin", "operator"}:
            rejection = JSONResponse({"detail": "担当者以上の権限が必要です"}, status_code=403)

    try:
        response = rejection or await call_next(request)
    except Exception:
        response = JSONResponse({"detail": "内部エラーが発生しました"}, status_code=500)
        logger.exception("Unhandled request error path=%s", path)

    if path != "/health":
        try:
            with SessionLocal() as db:
                db.add(OperationLog(user_id=user.id if user else None, username=user.username if user else None,
                                    action=f"{request.method} {path}", method=request.method, path=path,
                                    status_code=response.status_code,
                                    duration_ms=int((time.perf_counter() - started) * 1000),
                                    client_ip=request.client.host if request.client else None))
                db.commit()
        except Exception:
            logger.exception("Operation log write failed")
    return response


@app.get("/health")
def health():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok", "version": "4.0.0"}
    except Exception:
        return JSONResponse({"status": "degraded", "database": "unavailable", "version": "4.0.0"}, status_code=503)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    response = templates.TemplateResponse(request=request, name="index.html")
    response.headers["Cache-Control"] = "no-store"
    return response
