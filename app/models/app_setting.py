"""
Key/value store for runtime settings editable from the dashboard
(automation schedule, calling hours, ...). Secrets stay in .env.
"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # JSON-encoded
