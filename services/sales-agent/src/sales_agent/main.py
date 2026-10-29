"""uvicorn 진입점: `uv run uvicorn sales_agent.main:app --port 8002`.

여기서는 설정만 읽고 앱을 만든다. 공개키·데이터·인덱스는 lifespan 에서 적재한다.
"""

from chatcore.logs import configure_logging
from sales_agent.api import create_app

configure_logging("sales-agent")
app = create_app()
