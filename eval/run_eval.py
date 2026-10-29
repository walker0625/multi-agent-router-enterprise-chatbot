"""라우팅 평가 — 골드셋으로 캐스케이드를 돌려 레이어별 결정률·정밀도와 최종 정확도를 잰다.

    uv run python eval/run_eval.py run --split test --run-id 2026-10-29-fake
    uv run python eval/run_eval.py run --split test --ablate L2          # 레이어 하나를 빼고
    uv run python eval/run_eval.py run --split test --repeat 3           # 같은 입력에 판정이 바뀌는 비율
    uv run python eval/run_eval.py calibrate-l2 --target 0.95 [--write]  # τ 는 dev 에서만 정한다

LLM·임베더는 `LLM_*` 환경 변수를 따른다(기본 fake). 리포트에는 모델·임베더 이름과 판정 재료
파일 태그(버전·해시)를 함께 남긴다 — 수치가 바뀌었을 때 프롬프트가 바뀐 건지 모델이 흔들린 건지
가를 수 있게. τ 를 시험셋에서 고르면 시험 점수가 부풀려지므로 보정은 dev, 확인은 test 로 나눈다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from chatcore.deadline import Deadline
from chatcore.events import Outcome
from chatcore.llm import Embedder, LLMSettings, build_llm
from chatcore.prompts import VersionedFile
from chatcore.search import Document, InMemoryIndex, embed_documents
from router_service.fakes import fake_handlers
from router_service.routing import (
    Cascade,
    Domain,
    JudgeLayer,
    KeywordLayer,
    Layer,
    RouteResult,
    VectorGateLayer,
)
from router_service.routing.layer2 import TOP_K, gate
from router_service.settings import SERVICE_DIR

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "eval" / "gold"
RUNS = ROOT / "eval" / "runs"
ROUTING = SERVICE_DIR / "routing"
LABELS = ("delivery", "sales", "general", "parallel", "hitl")
NEAR_DUP = 0.6  # 앵커와 글자 bigram Jaccard 가 이 이상이면 사실상 같은 문장으로 본다


@dataclass(frozen=True)
class Item:
    id: str
    query: str
    label: str
    split: str


def load_gold(version: str) -> tuple[list[Item], str]:
    gold = VersionedFile.load(GOLD / version / "gold.yaml")
    items = [
        Item(f"{label[:3]}-{i + 1:03d}", query, label, "dev" if i % 2 == 0 else "test")
        for label in LABELS
        for i, query in enumerate(gold.data["items"][label])
    ]
    return items, gold.tag


def label_of(outcome: Outcome, domains: list[Domain]) -> str:
    if outcome is Outcome.SINGLE and len(domains) == 1:
        return domains[0].value
    return {Outcome.PARALLEL: "parallel", Outcome.HITL: "hitl", Outcome.BLOCKED: "blocked"}.get(outcome, "?")


def _bigrams(text: str) -> set[str]:
    s = "".join(text.split())
    return {s[i : i + 2] for i in range(len(s) - 1)}


def near_duplicates(items: list[Item]) -> list[tuple[Item, str, float]]:
    anchors = [a["text"] for a in VersionedFile.load(ROUTING / "anchors.yaml").data["anchors"]]
    grams = [(a, _bigrams(a)) for a in anchors]
    found = []
    for item in items:
        mine = _bigrams(item.query)
        text, score = max(((a, len(mine & g) / len(mine | g)) for a, g in grams), key=lambda x: x[1])
        if score >= NEAR_DUP:
            found.append((item, text, score))
    return found


async def build_cascade(ablate: set[str]) -> tuple[Cascade, list[str], str, str]:
    chat, embedder = build_llm(LLMSettings(), fake_handlers())
    layers: list[Layer] = []
    if "L1" not in ablate:
        layers.append(KeywordLayer.load(ROUTING / "keywords.yaml"))
    if "L2" not in ablate:
        anchors = VersionedFile.load(ROUTING / "anchors.yaml")
        thresholds = VersionedFile.load(ROUTING / "thresholds.yaml")
        layers.append(await VectorGateLayer.build(anchors, thresholds, embedder))
    if "L3" not in ablate:
        layers.append(JudgeLayer.load(chat, ROUTING / "judge.yaml"))
    tags = [tag for layer in layers for tag in layer.tags]
    return Cascade(layers, chat_model=chat.name, embedder=embedder.name), tags, chat.name, embedder.name


# ── run ──────────────────────────────────────────────────────────────


def _pct(n: int, d: int) -> str:
    return f"{n / d:.1%} ({n}/{d})" if d else "-"


def render_report(
    items: list[Item],
    runs: list[list[RouteResult]],
    *,
    meta: dict[str, str],
    dups: list[tuple[Item, str, float]],
) -> str:
    routes = runs[0]
    got = [label_of(r.outcome, r.domains) for r in routes]
    ok = [g == item.label for g, item in zip(got, items, strict=True)]
    lines = ["# 라우팅 평가 리포트", ""]
    lines += [f"- {k}: `{v}`" for k, v in meta.items()]
    lines += [
        "",
        "> fake LLM·해싱 임베더로 돈 결과는 **파이프라인이 설계대로 동작하는지** 보여 줄 뿐"
        " 모델 성능이 아니다.",
        "> 실제 모델 수치는 `LLM_PROVIDER=openai` 로 다시 돌려 얻는다."
        " 목표치(예: 정밀도 0.95)와 측정치는 다른 값이다.",
        "",
        "## 최종 정확도",
        "",
        f"전체 {_pct(sum(ok), len(ok))}",
        "",
        "| 정답 라벨 | 정확도 |",
        "|---|---|",
    ]
    for label in LABELS:
        idx = [i for i, item in enumerate(items) if item.label == label]
        lines.append(f"| {label} | {_pct(sum(ok[i] for i in idx), len(idx))} |")

    lines += [
        "",
        "## 레이어별",
        "",
        "| 레이어 | 도달 | 결정(결정률) | 정밀도 | 건너뜀 |",
        "|---|---|---|---|---|",
    ]
    for name in ("L1", "L2", "L3"):
        reached = decided = correct = skipped = 0
        for r, good in zip(routes, ok, strict=True):
            for layer in r.trace.layers:
                if layer.layer != name:
                    continue
                if layer.status == "skipped":
                    skipped += 1
                    continue
                reached += 1
                if layer.status == "decided":
                    decided += 1
                    correct += good
        lines.append(
            f"| {name} | {reached} | {_pct(decided, reached)} | {_pct(correct, decided)} | {skipped} |"
        )
    undecided = sum(1 for r in routes if r.decided_by == "cascade")
    lines += ["", f"어느 레이어도 결정하지 못해 HITL 로 간 질문: {undecided}건", ""]

    pred_hitl = [g == "hitl" for g in got]
    gold_hitl = [item.label == "hitl" for item in items]
    both = sum(p and g for p, g in zip(pred_hitl, gold_hitl, strict=True))
    lines += [
        "## 되묻기(HITL)",
        "",
        f"- 정밀도(되물은 것 중 되물어야 했던 것): {_pct(both, sum(pred_hitl))}",
        f"- 재현율(되물어야 했던 것 중 되물은 것): {_pct(both, sum(gold_hitl))}",
        f"- 불필요한 되묻기: {sum(p and not g for p, g in zip(pred_hitl, gold_hitl, strict=True))}건",
        "",
        "## 혼동 행렬 (행=정답, 열=예측)",
        "",
    ]
    cols = [*LABELS, "blocked"]
    lines += ["| 정답 \\ 예측 | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for label in LABELS:
        counts = Counter(g for g, item in zip(got, items, strict=True) if item.label == label)
        lines.append(f"| {label} | " + " | ".join(str(counts.get(c, 0)) for c in cols) + " |")

    if len(runs) > 1:
        flips = [
            item
            for i, item in enumerate(items)
            if len({label_of(run[i].outcome, run[i].domains) for run in runs}) > 1
        ]
        lines += [
            "",
            f"## 반복 실행 흔들림 ({len(runs)}회)",
            "",
            f"같은 입력에 판정이 한 번이라도 바뀐 질문: {_pct(len(flips), len(items))}",
            "",
            "이 비율보다 작은 정확도 차이는 실행 간 잡음과 구분할 수 없다.",
        ]
        lines += [f"- `{f.id}` {f.query}" for f in flips[:20]]

    usage_in = sum(r.usage.input_tokens for r in routes)
    usage_out = sum(r.usage.output_tokens for r in routes)
    elapsed = sorted(r.trace.elapsed_ms for r in routes)
    lines += [
        "",
        "## 비용·지연",
        "",
        f"- 라우팅 토큰 합계: 입력 {usage_in:,} / 출력 {usage_out:,}",
        f"- 라우팅 지연 중앙값 {elapsed[len(elapsed) // 2]}ms, 최대 {elapsed[-1]}ms",
        "",
        "## 틀린 질문",
        "",
        "| id | 질문 | 정답 | 예측 | 결정 |",
        "|---|---|---|---|---|",
    ]
    for item, g, r, good in zip(items, got, routes, ok, strict=True):
        if not good:
            lines.append(f"| {item.id} | {item.query} | {item.label} | {g} | {r.decided_by} |")
    if dups:
        lines += ["", "## 앵커와 거의 같은 문장 (점수가 부풀 수 있음)", ""]
        lines += [f"- `{i.id}` {i.query} ≈ {a} ({s:.2f})" for i, a, s in dups]
    return "\n".join(lines) + "\n"


async def cmd_run(args: argparse.Namespace) -> None:
    items, gold_tag = load_gold(args.gold)
    items = [i for i in items if args.split == "all" or i.split == args.split]
    ablate = set(args.ablate or [])
    cascade, tags, chat_name, embedder_name = await build_cascade(ablate)
    runs: list[list[RouteResult]] = []
    for _ in range(args.repeat):
        runs.append([await cascade.route(item.query, Deadline(60)) for item in items])

    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = RUNS / run_id
    out.mkdir(parents=True, exist_ok=True)
    with (out / "traces.jsonl").open("w", encoding="utf-8") as f:
        for item, route in zip(items, runs[0], strict=True):
            row = {
                "id": item.id,
                "label": item.label,
                "split": item.split,
                "route": route.model_dump(mode="json"),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    meta = {
        "run-id": run_id,
        "골드셋": gold_tag,
        "분할": f"{args.split} ({len(items)}문장)",
        "LLM": chat_name,
        "임베더": embedder_name,
        "뺀 레이어": ",".join(sorted(ablate)) or "없음",
        "판정 재료": ", ".join(tags),
    }
    report = render_report(items, runs, meta=meta, dups=near_duplicates(items))
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report.split("## 레이어별")[0].strip())
    print(f"\n→ {out.relative_to(ROOT)}/report.md")


# ── calibrate-l2 ──────────────────────────────────────────────────────


async def _top3(items: list[Item], embedder: Embedder) -> list[list[tuple[Domain, float]]]:
    anchors = VersionedFile.load(ROUTING / "anchors.yaml").data["anchors"]
    docs = [
        Document(id=f"a{i}", text=a["text"], fields={"domain": Domain(a["domain"])})
        for i, a in enumerate(anchors)
    ]
    await embed_documents(docs, embedder)
    index = InMemoryIndex("anchors", docs, filterable=())
    vectors = await embedder.embed([i.query for i in items])
    out = []
    for vector in vectors:
        hits = await index.search(vector=vector, top_k=TOP_K)
        out.append([(Domain(h.doc.fields["domain"]), h.score) for h in hits])
    return out


async def cmd_calibrate(args: argparse.Namespace) -> None:
    items, gold_tag = load_gold(args.gold)
    dev = [i for i in items if i.split == "dev"]
    _, embedder = build_llm(LLMSettings(), fake_handlers())
    tops = await _top3(dev, embedder)
    rows = []
    for step in range(30, 96):
        tau = step / 100
        decided = [
            (item, passed[0])
            for item, top in zip(dev, tops, strict=True)
            if len(passed := gate(top, tau)) == 1
        ]
        correct = sum(item.label == d.value for item, d in decided)
        rows.append((tau, len(decided), correct))
    feasible = [r for r in rows if r[1] >= args.min_decided and r[2] / r[1] >= args.target]
    chosen = feasible[0] if feasible else None

    lines = [
        "# L2 τ 보정",
        "",
        f"- 골드셋 `{gold_tag}` dev 분할 {len(dev)}문장, 임베더 `{embedder.name}`",
        f"- 목표 정밀도 {args.target} (목표치이지 측정치가 아니다), 최소 결정 수 {args.min_decided}",
        "- 규칙: top-3 앵커 중 τ 이상인 도메인이 정확히 하나일 때만 결정 (`layer2.gate`)",
        "",
        "| τ | 결정 | 정밀도 | 결정률 |",
        "|---|---|---|---|",
    ]
    for tau, n, c in rows[::5] + ([chosen] if chosen and chosen not in rows[::5] else []):
        lines.append(f"| {tau:.2f} | {n} | {_pct(c, n)} | {n / len(dev):.1%} |")
    if chosen:
        tau, n, c = chosen
        lines += ["", f"선택: τ={tau:.2f} — dev 에서 {n}건 결정, 정밀도 {c / n:.1%}. test 로 다시 확인할 것."]
    else:
        lines += ["", "목표 정밀도를 만족하는 τ 가 없다 — 이 임베더로는 L2 를 꺼 두는 편이 낫다(τ=1.0)."]
    report = "\n".join(lines) + "\n"
    print(report)

    if args.write:
        tau = chosen[0] if chosen else 1.0
        old = VersionedFile.load(ROUTING / "thresholds.yaml")
        stamp = datetime.now(UTC).date().isoformat()
        header = (
            "# L2 임계값 τ. 임베더마다 다른 값이다 — embedder 가 지금 임베더와 다르면\n"
            "# L2 는 skipped 가 된다.\n"
            "# eval/run_eval.py calibrate-l2 가 골드셋 dev 분할에서 정했다. 확인은 test 분할로 한다.\n"
        )
        body = {
            "version": int(old.version) + 1,
            "tau": tau,
            "embedder": embedder.name,
            "calibrated_on": f"{gold_tag} dev, target precision {args.target}, {stamp}",
        }
        (ROUTING / "thresholds.yaml").write_text(
            header + yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        out = RUNS / f"calibrate-l2-{stamp}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").write_text(report, encoding="utf-8")
        print(f"→ thresholds.yaml 갱신 (τ={tau}), {out.relative_to(ROOT)}/report.md")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--gold", default="v1")
    run.add_argument("--split", choices=["dev", "test", "all"], default="test")
    run.add_argument("--run-id")
    run.add_argument("--ablate", nargs="*", choices=["L1", "L2", "L3"])
    run.add_argument("--repeat", type=int, default=1)
    cal = sub.add_parser("calibrate-l2")
    cal.add_argument("--gold", default="v1")
    cal.add_argument("--target", type=float, default=0.95)
    cal.add_argument("--min-decided", type=int, default=5)
    cal.add_argument("--write", action="store_true")
    args = parser.parse_args()
    asyncio.run(cmd_run(args) if args.cmd == "run" else cmd_calibrate(args))


if __name__ == "__main__":
    main()
