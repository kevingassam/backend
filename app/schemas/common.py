from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.services.taxes import PROVINCES

T = TypeVar("T")

Loc = Literal["fr", "en"]


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


class Ok(BaseModel):
    ok: bool = True


class AddressIn(BaseModel):
    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    province: str = Field(min_length=2, max_length=2)
    postal_code: str = Field(min_length=3, max_length=20)
    country: str = Field(default="CA", min_length=2, max_length=2)

    @field_validator("province", "country")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.strip().upper()


class AddressOut(AddressIn):
    model_config = ConfigDict(from_attributes=True)


def validate_canadian_province(addr: AddressIn) -> None:
    if addr.country == "CA" and addr.province not in PROVINCES:
        from app.core.errors import AppError

        raise AppError("validation_error", 422, details=[{"field": "province", "message": "Unknown province"}])


class EmailIn(BaseModel):
    email: EmailStr
