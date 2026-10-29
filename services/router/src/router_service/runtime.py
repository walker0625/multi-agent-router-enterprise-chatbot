"""실행 중 공유 객체 조립(lifespan 에서 한 번).

데이터 로딩·앵커 임베딩·저장소 연결 같은 I/O 는 모두 여기서 한다. 임포트 시점에는 아무것도 읽지
않는다. 판정 재료 YAML 은 적재할 때 검사하므로(중복 키워드, 알 수 없는 도메인 등) 잘못된 파일로는
서비스가 뜨지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from chatcore.auth import TokenIssuer, TokenVerifier, generate_keypair
from chatcore.prompts import VersionedFile
from router_service.agents import AgentClient, GeneralAgent, HttpAgentClient
from router_service.catalog import DemoUser, Project, load_faq, load_projects, load_users
from router_service.deps import Deps
from router_service.routing import Cascade, Contextualizer, Domain, JudgeLayer, KeywordLayer, VectorGateLayer
from router_service.runner import HitlResponder, Recommender, TurnRunner
from router_service.settings import Settings
from router_service.store import TurnStore

log = logging.getLogger(__name__)

AUDIENCE = "router"


@dataclass
class Runtime:
    settings: Settings
    store: TurnStore
    issuer: TokenIssuer
    verifier: TokenVerifier
    users: dict[str, DemoUser]
    projects: dict[str, Project]
    cascade: Cascade
    runner: TurnRunner
    agents: dict[Domain, AgentClient]
    http: httpx.AsyncClient

    async def aclose(self) -> None:
        await self.store.close()
        await self.http.aclose()


def load_signing_keys(settings: Settings) -> tuple[bytes, bytes]:
    """서명 키쌍을 읽는다. dev·test 에서 키가 없으면 만들어 둔다(다른 서비스가 같은 공개키를 읽는다)."""
    private_path, public_path = settings.private_key_path, settings.auth.public_key_path
    if settings.app_env != "prod" and not private_path.exists():
        private_pem, public_pem = generate_keypair()
        for path, pem in ((private_path, private_pem), (public_path, public_pem)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pem)
        private_path.chmod(0o600)
        log.warning("generated development signing keys", extra={"public_key": str(public_path)})
    return private_path.read_bytes(), public_path.read_bytes()


async def build_runtime(settings: Settings, deps: Deps) -> Runtime:
    private_pem, public_pem = load_signing_keys(settings)
    issuer = TokenIssuer(private_pem, issuer=settings.auth.issuer)
    verifier = TokenVerifier(public_pem, issuer=settings.auth.issuer, audience=AUDIENCE)

    prompts = VersionedFile.load(settings.prompts_path)
    routing = settings.routing_dir
    vector_gate = await VectorGateLayer.build(
        VersionedFile.load(routing / "anchors.yaml"),
        VersionedFile.load(routing / "thresholds.yaml"),
        deps.embedder,
    )
    cascade = Cascade(
        [
            KeywordLayer.load(routing / "keywords.yaml"),
            vector_gate,
            JudgeLayer.load(deps.chat, routing / "judge.yaml"),
        ],
        contextualizer=Contextualizer(deps.chat, prompts),
        chat_model=deps.chat.name,
        embedder=deps.embedder.name,
    )

    users = load_users(settings.data_dir / "users.json")
    projects = load_projects(settings.data_dir / "delivery" / "projects.json")
    general = await GeneralAgent.build(
        load_faq(settings.data_dir / "general" / "faq.json"),
        chat=deps.chat,
        embedder=deps.embedder,
        web=deps.web_search,
        prompts=prompts,
        max_chars=settings.answer_max_chars,
        min_overlap=settings.faq_min_overlap,
    )
    http = httpx.AsyncClient()
    ttl = settings.auth.service_token_ttl_s
    agents: dict[Domain, AgentClient] = {
        Domain.DELIVERY: HttpAgentClient(
            "delivery-agent", settings.delivery_url, issuer, http=http, token_ttl_s=ttl
        ),
        Domain.SALES: HttpAgentClient("sales-agent", settings.sales_url, issuer, http=http, token_ttl_s=ttl),
        Domain.GENERAL: general,
        **deps.agents,
    }

    store = TurnStore(settings.db_path, pending_ttl_s=settings.turn_budget_s)
    await store.open()
    runner = TurnRunner(
        settings=settings,
        store=store,
        agents=agents,
        chat=deps.chat,
        prompts=prompts,
        hitl=HitlResponder(deps.chat, prompts, projects),
        recommender=Recommender(deps.chat, prompts),
    )
    return Runtime(
        settings=settings,
        store=store,
        issuer=issuer,
        verifier=verifier,
        users=users,
        projects=projects,
        cascade=cascade,
        runner=runner,
        agents=agents,
        http=http,
    )
