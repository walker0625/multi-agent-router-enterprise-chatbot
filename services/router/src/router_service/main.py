"""`uv run uvicorn router_service.main:app --port 8000` 진입점.

임포트 시점에는 환경 변수로 설정만 읽는다. 데이터 로딩은 lifespan 에서 한다. 설정이 잘못되면
(예: 운영 환경에서 데모 로그인) 여기서 실패해 서비스가 뜨지 않는다.
"""

from chatcore.logs import configure_logging
from router_service.app import create_app

configure_logging("router")
app = create_app()
