from __future__ import annotations

import json
import hmac
from dataclasses import dataclass
from typing import Iterable


ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}


@dataclass(frozen=True)
class UserCredential:
    username: str
    token: str
    role: str = "viewer"

    def masked(self) -> dict[str, str]:
        return {"username": self.username, "token": mask_secret(self.token), "role": normalize_role(self.role)}


def normalize_role(role: str | None) -> str:
    candidate = (role or "viewer").strip().lower()
    return candidate if candidate in ROLE_RANK else "viewer"


def role_allows(actual: str, required: str) -> bool:
    return ROLE_RANK[normalize_role(actual)] >= ROLE_RANK[normalize_role(required)]


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}***{value[-2:]}"


def parse_users(value: str | None) -> tuple[UserCredential, ...]:
    """Parse config users from JSON or compact `name:token:role,name2:token2:role` text."""
    if not value:
        return ()
    text = value.strip()
    if not text:
        return ()
    users: list[UserCredential] = []
    if text.startswith("["):
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return ()
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                username = str(item.get("username") or item.get("name") or "").strip()
                token = str(item.get("token") or "").strip()
                role = normalize_role(str(item.get("role") or "viewer"))
                if username and token:
                    users.append(UserCredential(username=username, token=token, role=role))
        return tuple(users)

    for chunk in text.split(","):
        parts = [part.strip() for part in chunk.split(":")]
        if len(parts) < 2:
            continue
        username, token = parts[0], parts[1]
        role = normalize_role(parts[2] if len(parts) >= 3 else "viewer")
        if username and token:
            users.append(UserCredential(username=username, token=token, role=role))
    return tuple(users)


def authenticate_token(users: Iterable[UserCredential], legacy_admin_token: str | None, token: str | None) -> UserCredential | None:
    candidate = token or ""
    if legacy_admin_token and hmac.compare_digest(candidate, legacy_admin_token):
        return UserCredential(username="legacy-admin", token=legacy_admin_token, role="admin")
    for user in users:
        if hmac.compare_digest(candidate, user.token):
            return user
    return None
