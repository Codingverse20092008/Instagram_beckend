from __future__ import annotations
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    keywords: List[str] = Field(default_factory=list)


class LeadOut(BaseModel):
    username: str
    bio: str = ""
    matched_keyword: Optional[str] = None


class LeadStored(BaseModel):
    username: str
    bio: str = ""
    status: Literal["PENDING", "CHAT_OPENED", "SENT"] = "PENDING"


class StatusUpdate(BaseModel):
    username: str
    status: Literal["PENDING", "CHAT_OPENED", "SENT"]
