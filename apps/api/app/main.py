from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.cache import create_redis
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.mail import create_mailer
from app.core.middleware import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.storage import Storage
from app.modules.access.router import router as access_router
from app.modules.appointments.router import router as appointments_router
from app.modules.care_team.router import router as care_team_router
from app.modules.caregivers.dashboard import router as caregiver_dashboard_router
from app.modules.caregivers.router import router as caregivers_router
from app.modules.chart.router import router as chart_router
from app.modules.clinical.router import router as clinical_router
from app.modules.emergency.router import router as emergency_router
from app.modules.extraction.router import router as extraction_router
from app.modules.identity.router import me_router
from app.modules.identity.router import router as auth_router
from app.modules.identity.security import Passwords, TokenSigner
from app.modules.labs.router import router as labs_router
from app.modules.medications.router import router as medications_router
from app.modules.notifications.router import router as notifications_router
from app.modules.prescriptions.router import router as prescriptions_router
from app.modules.records.router import router as records_router
from app.modules.reminders.router import router as reminders_router
from app.modules.system.router import router as system_router

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Tests may pre-populate app.state with fakes; only create what is missing.
        if not hasattr(app.state, "db"):
            app.state.db = Database(settings)
        if not hasattr(app.state, "redis"):
            app.state.redis = create_redis(settings)
        if not hasattr(app.state, "storage"):
            app.state.storage = Storage(settings)
        log.info("startup", env=settings.env.value)
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.db.dispose()
            log.info("shutdown")

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.state.passwords = Passwords(settings)
    app.state.token_signer = TokenSigner(settings)
    app.state.mailer = create_mailer(settings)

    register_error_handlers(app)

    # Starlette runs the last-added middleware first.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["authorization", "content-type", "x-requested-with", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(system_router)

    api = APIRouter(prefix=settings.api_prefix)
    for module_router in (
        auth_router,
        me_router,
        access_router,
        caregiver_dashboard_router,
        caregivers_router,
        care_team_router,
        chart_router,
        clinical_router,
        prescriptions_router,
        medications_router,
        labs_router,
        records_router,
        appointments_router,
        emergency_router,
        extraction_router,
        reminders_router,
        notifications_router,
    ):
        api.include_router(module_router)
    app.include_router(api)

    return app


app = create_app()
