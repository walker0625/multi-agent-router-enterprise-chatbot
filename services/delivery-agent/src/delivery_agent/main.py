"""uvicorn 진입점: `uv run uvicorn delivery_agent.main:app --port 8001`.

임포트 시점에는 설정만 읽는다. 데이터·인덱스·공개키는 lifespan 에서 적재한다.
"""

from delivery_agent.api import create_app

app = create_app()
