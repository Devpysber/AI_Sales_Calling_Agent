"""Organisations and the people who sign in to them. Role -> permissions lives in app/core/permissions.py."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.lead import iso


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    business_type: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "business_type": self.business_type}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)  # lowercased
    name: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))  # NULL on the owner = env ADMIN_PASSWORD
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | disabled
    legacy_team_id: Mapped[str | None] = mapped_column(String(64), unique=True)  # id in "team_members"
    phone: Mapped[str | None] = mapped_column(String(32))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def to_dict(self) -> dict:
        return {"id": self.id, "organization_id": self.organization_id, "email": self.email, "name": self.name,
                "role": self.role, "status": self.status, "phone": self.phone, "last_login_at": iso(self.last_login_at)}
