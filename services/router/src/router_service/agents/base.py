"""에이전트 클라이언트 포트.

원격(HTTP) 에이전트와 라우터 안의 일반 에이전트가 같은 모양으로 이벤트를 흘린다. 새 에이전트는
레지스트리(`dict[Domain, AgentClient]`)에 등록만 하면 된다.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Protocol

from chatcore.agent_api import AgentRequest
from chatcore.auth import Principal
from chatcore.deadline import Deadline
from chatcore.events import Event


class AgentClient(Protocol):
    def stream(
        self, req: AgentRequest, principal: Principal, deadline: Deadline
    ) -> AsyncGenerator[Event]: ...
