from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional

__all__ = ["SIPSubscription"]


@dataclass
class SIPSubscription:
    call_id: str
    target: str
    target_uri: str
    event: str
    accept: List[str]
    local_tag: str
    remote_tag: str = ""
    remote_target: Optional[str] = None
    expires: int = 3600
    pending_expires: int = 3600
    status: str = "pending"
    subscription_state: Optional[str] = None
    reason: Optional[str] = None
    last_response_code: Optional[int] = None
    last_response_phrase: Optional[str] = None
    last_notify_body: str = ""
    last_notify_headers: Dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "target": self.target,
            "target_uri": self.target_uri,
            "event": self.event,
            "accept": list(self.accept),
            "local_tag": self.local_tag,
            "remote_tag": self.remote_tag,
            "remote_target": self.remote_target,
            "expires": self.expires,
            "pending_expires": self.pending_expires,
            "status": self.status,
            "subscription_state": self.subscription_state,
            "reason": self.reason,
            "last_response_code": self.last_response_code,
            "last_response_phrase": self.last_response_phrase,
            "last_notify_body": self.last_notify_body,
            "last_notify_headers": dict(self.last_notify_headers),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
