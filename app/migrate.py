"""
One-off deploy job: apply database migrations.

    python -m app.migrate
"""

from app.core.config import settings
from app.core.database import run_migrations
from app.core.logging import get_logger, setup_logging

if __name__ == "__main__":
    setup_logging(settings.log_level, json_logs=settings.is_production)
    run_migrations()
    from app.services.crm_service import CRMService
    CRMService().import_legacy_excel(settings.legacy_excel_file)
    get_logger("migrate").info("Database is up to date")
