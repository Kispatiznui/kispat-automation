import logging

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from control.api import router as api_router
from control.config import get_settings
from control.errors import ApiError
from control.logging_json import setup_logging

settings = get_settings()
setup_logging(settings.LOG_LEVEL)
log = logging.getLogger("kispat.control")

app = FastAPI(title="KISPAT Automation Control", version=settings.VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _err(status: int, code: str, message: str, details: list | None = None) -> JSONResponse:
    body: dict = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status)


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
    return _err(exc.status_code, exc.code, str(exc.detail), exc.details)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
    return _err(exc.status_code, code.get(exc.status_code, "http_error"), str(exc.detail))


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()
    ]
    return _err(422, "validation_error", "Petición inválida", details)


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("error no controlado")
    return _err(500, "internal_error", "Error interno")


app.include_router(api_router)


def run() -> None:
    uvicorn.run("control.main:app", host=settings.HOST, port=settings.PORT)
