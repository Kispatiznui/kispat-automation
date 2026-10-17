import json
import logging
import threading
from datetime import UTC, datetime

from control.config import get_settings

_lock = threading.Lock()
log = logging.getLogger("kispat.audit")


def audit(event: str, **fields) -> None:
    """Append-only JSONL. Un fallo de auditoría se loguea pero no tumba la request."""
    path = get_settings().AUDIT_PATH
    record = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
    except OSError:
        log.exception("no se pudo escribir auditoría", extra={"event": event})
