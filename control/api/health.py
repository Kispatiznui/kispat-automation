from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from control.config import get_settings
from control.db.session import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
def health(session: Session = Depends(get_session)) -> JSONResponse:
    try:
        session.execute(text("SELECT 1"))
        db = "ok"
    except Exception:
        db = "fail"
    body = {
        "status": "ok" if db == "ok" else "degraded",
        "db": db,
        "version": get_settings().VERSION,
    }
    return JSONResponse(body, status_code=200 if db == "ok" else 503)
