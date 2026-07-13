"""Firebase project config + shared Admin SDK client."""
######################################################################
import logging
import os

from google.cloud.firestore import AsyncClient

log = logging.getLogger(__name__)

PROJECT_ID = "esef-514bf"
SYSINFO_INTERVAL = 10  # seconds between pushes

_db: AsyncClient | None = None


def get_db() -> AsyncClient:
    """Return the shared Firestore AsyncClient, creating it on first call."""
    global _db
    if _db is None:
        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if creds_path is None:
            raise RuntimeError(
                "GOOGLE_APPLICATION_CREDENTIALS is not set. "
                "Point it to your service-account JSON file."
            )
        _db = AsyncClient()
        log.info("Firestore Admin SDK initialised (project %s)", _db.project)
    return _db
