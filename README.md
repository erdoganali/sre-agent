# SRE Agent

A read-only, multi-agent SRE assistant for a multi-tenant Milvus (vector database) platform on Kubernetes. When an alert fires, it collects evidence, lets specialist agents investigate with MCP tools, and writes a root-cause report that a human can verify.

The core knows no product. The Milvus platform is the first **pack**; you can plug in your own product the same way (see [Use it for your own product](#use-it-for-your-own-product)).

## Problem

One infrastructure event (a lost node, an etcd quorum loss) restarts many components at once and floods the on-call engineer with alerts. Finding the first cause means jumping between Prometheus, Loki, Tempo and the Kubernetes API. Most of these failures have been seen before, but that knowledge lives in people's heads.

The agent answers the question an on-call engineer asks first: *what started this, and how sure are we?* It never changes the system. It only reads and reports.

## Use it for your own product

You do not change the core. You give it five things:

| What | Where | What it is for |
| --- | --- | --- |
| **Pack** | `packs/<yours>/` (copy `packs/example/`) | `pack.yaml`: which alerts run which checks; `<module>/__init__.py`: your checks (PromQL, no model); `catalog.yaml`: known failures, indexed into the knowledge base; `topology.yaml`: components and dependencies; `specialists/product.md`: hints for the product specialist |
| **Taxonomy** | `src/sre_agent/taxonomy/taxonomy.yaml` (shared, no change needed) | Root-cause categories every report ends with (application release, hardware problem, database, network…). Your pack maps onto them: each catalog entry names a category, `category_notes` says what it means for your product |
| **Environment** | `config/<env>.yaml` | Enabled packs, Prometheus/Loki/Tempo/Grafana URLs, MCP server URLs, knowledge base URI (any Milvus 2.6), investigation mode |
| **Models** | `config/models.yaml` | Any OpenAI-compatible endpoint (vLLM, a gateway, OpenAI). Two roles: `specialist` (tool loop) and `synthesize`. Keys come from environment variables, never the repo |
| **Alerts** | Alertmanager | Route your alerts (`alert_matchers` in `pack.yaml`) to the agent's `POST /alerts` webhook |

```yaml
# config/<env>.yaml
packs:
  - path: packs/<yours>
prometheus_url: http://prometheus.monitoring:9090
knowledge:
  uri: http://milvus.<ns>:19530          # or a local file: knowledge.db (Milvus Lite)
investigation:
  mode: "on"
  mcp_servers:
    metrics: http://sre-agent-mcp-metrics:8080/mcp
    # logs, traces, k8s, product …
```

```yaml
# config/models.yaml
roles:
  specialist: {model: main, max_tokens: 4096}
  synthesize: {model: main, max_tokens: 8192}
models:
  main:
    name: "${MAIN_MODEL:gpt-4.1-mini}"
    url: "${MAIN_URL:https://api.openai.com/v1/chat/completions}"
    max_context_tokens: 128000
    headers: {Authorization: "Bearer ${MAIN_KEY:}"}
```

Then build the knowledge base (`python -m sre_agent.knowledge index`) and fire one of your alerts. Step-by-step guide and pack tests: [docs/packs.md](docs/packs.md).

## How it works

```mermaid
flowchart TD
  P[1. Prometheus alert rule] ==> AM[2. Alertmanager]
  AM ==>|webhook| CH[3. Checks<br/>~30 checks, report]
  CH ==> INC[4. Incident grouping<br/>waits 3 min of quiet]
  INC ==> GA[5. gather<br/>brief + pick specialists]
  KB[(Knowledge base<br/>Milvus BM25)] -.->|known failures| GA
  GA ==> SP
  subgraph SP[6. Specialists in parallel: LLM + MCP tools]
    M[metrics]
    L[logs]
    T[traces]
    K[k8s]
    PR[product]
  end
  M <-.->|MCP| PROM[(Prometheus)]
  L <-.->|MCP| LOKI[(Loki)]
  T <-.->|MCP| TEMPO[(Tempo)]
  K <-.->|MCP| K8S[(Kubernetes API)]
  PR <-.->|MCP| MV[(Milvus, checks,<br/>knowledge base)]
  SP ==> CO[7. correlate<br/>compare findings, pick root cause]
  CO ==> EV{8. evaluate<br/>confident?}
  EV ==>|NO| DD[deep_dive<br/>ask one specialist<br/>a targeted question]
  DD ==>|LOOP, max 2 rounds| SP
  EV ==>|YES| SY[9. synthesize<br/>final report]
  SY ==> REP[10. Report page<br/>+ Grafana annotation]
  SY -.-> PG[(Postgres memory)]
  linkStyle 13,14 stroke:#e8590c,stroke-width:4px
  classDef code fill:#e6ebee,stroke:#6b7c86,color:#17232b
  classDef agent fill:#4fb3b8,stroke:#0b6e74,color:#0b2e30
  classDef llm fill:#0b6e74,stroke:#064c50,color:#ffffff
  classDef loop fill:#fff0e6,stroke:#e8590c,color:#7a2a06
  class P,AM,CH,INC,GA code
  class M,L,T,K,PR agent
  class CO,EV,SY llm
  class DD loop
```

Grey steps are plain code. Light teal steps are agents (LLM + MCP tools). Dark teal steps are LLM-only reasoning. The orange edge is the loop.

1. **Checks** run without a model: ~30 deterministic checks (pods, nodes, capacity, network, etcd, Milvus coordinators…) produce a first report within seconds.
2. **Incident grouping** joins reports that belong to the same event and waits for 3 quiet minutes, so one node loss is investigated once, not ten times.
3. **gather** writes a brief, adds the closest known failures from the knowledge base (RAG), and picks specialists in code from the evidence.
4. **Specialists** run in parallel. Each is an LLM with its own read-only MCP server and calls tools in a loop (max 8 calls) until it can state a finding with evidence.
5. **correlate** compares the findings; **evaluate** decides whether the result holds. Code requires at least two specialists to support the same category (cross-validation).
6. If not, **deep_dive** sends one specialist a targeted question and the graph loops back (max 2 rounds).
7. **synthesize** writes the final report: root cause, evidence chain, rejected hypotheses, recommendations.

## Agentic parts

| Concept | In this project | Code |
| --- | --- | --- |
| LLM | Any OpenAI-compatible endpoint. Developed with an LLM gateway serving open-source models and Azure OpenAI gpt-4.1-mini. | `src/sre_agent/adapters/openai_compat.py`, `config/models.yaml` |
| Tool calling | 5 MCP servers, one per capability, read-only | `src/sre_agent/mcp/`, `src/sre_agent/graph/model_specialist.py` |
| Multi-agent | metrics, logs, traces, k8s and product specialists in parallel | `src/sre_agent/graph/routing.py`, `specialists.py` |
| Orchestration | LangGraph; routing is deterministic code, not a model decision | `src/sre_agent/graph/build.py`, `routing.py` |
| RAG | Failure catalog, specialist guides and taxonomy in Milvus, BM25 full-text search (no embedding model). Used in `gather` and as the `knowledge_search` tool. | `src/sre_agent/knowledge/`, `src/sre_agent/adapters/vector_store.py` |
| Memory | Postgres: reports, incidents, human labels, LangGraph checkpoints | `src/sre_agent/store/` |
| Safety | Read-only service accounts, no secret access, log content treated as untrusted data, token budget (1M per run, 120K per specialist) | `src/sre_agent/llm/budget.py`, `src/sre_agent/mcp/client.py` |
| Error handling | A failed model, tool or knowledge base never stops the graph: each step has a rule-based fallback and the report says so | `src/sre_agent/graph/reasoner.py` |

## Quick start (no cluster needed)

The demo replays a recorded real incident: an etcd quorum loss after a node was lost. Tool answers come from the recording and the model runs live.

In production the knowledge base is a collection in the Milvus platform the agent watches (one of its own tenants). The demo has no cluster, so it builds the same collection in [Milvus Lite](https://milvus.io/docs/milvus_lite.md), Milvus's embedded mode that keeps the data in one local file. The code is the same; only `knowledge.uri` changes (a URL or a file path).

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone <this repo> && cd sre-agent
uv sync
cp .env.example .env      # set DEMO_MODEL_KEY (any OpenAI-compatible endpoint)
make demo                 # writes demo/out/report.md
```

Expected output:

```
knowledge base: 61 doc → demo/out/knowledge.db
incident 9b71b3e1a26c: 1 rapor, investigation koşuyor…
specialist'ler: metrics, k8s, product (11 tool çağrısı)
sonuç (model): hardware_problem, güven high
token: 59660
rapor: demo/out/report.md
```

Without a key the demo still runs: no model is called and the report states that the result came from rules.

Tests and lint:

```bash
make test     # 338 unit tests, no network
make lint     # ruff + mypy
```

## Running on Kubernetes

The agent, its MCP servers and Postgres are installed with Helm (Argo CD in production). Nothing calls a managed cloud service.

```bash
make image-build image-push IMAGE_TAG=<tag>
make db-secrets db-install ENV=<env> KUBE_CONTEXT=<ctx>
make agent-install ENV=<env> KUBE_CONTEXT=<ctx> IMAGE_TAG=<tag>
kubectl exec deploy/sre-agent -- python -m sre_agent.knowledge index   # build the knowledge base
```

- `config/<env>.yaml` holds the Prometheus/Loki/Tempo/Grafana URLs, MCP servers, knowledge base URI and investigation mode (`off`, `shadow`, `on`).
- Model keys come from a Kubernetes Secret, never from the repo.
- Alertmanager sends alerts to `POST /alerts`. Reports are served at `/reports/<id>`.

## Technologies

Python 3.12, uv, LangGraph, MCP (Python SDK), Pydantic structured output, FastAPI, PostgreSQL (with LangGraph checkpoints), Milvus 2.6 (knowledge base, BM25; Milvus Lite in the demo), OpenTelemetry GenAI traces, Prometheus, Alertmanager, Grafana, Loki, Tempo, Kafka, LitmusChaos (fault scenarios), Helm, Argo CD.

## Repository layout

| Path | What |
| --- | --- |
| `src/sre_agent/checks/` | deterministic checks (no model) |
| `src/sre_agent/graph/` | LangGraph investigation: routing, specialists, correlate/evaluate/synthesize, replay |
| `src/sre_agent/mcp/` | read-only MCP servers |
| `src/sre_agent/knowledge/` | knowledge base (RAG) |
| `packs/` | product packs: checks, failure catalog, specialist guides (`example/` is the template) |
| `scenarios/` | fault-injection scenarios with expected outcomes |
| `deploy/` | Helm charts and per-environment values |
| `demo/` | recorded incident and demo config |
| `docs/adr/` | architecture decisions |

## Screenshots

Report for a real etcd quorum loss after a node was lost. The agent's section is on top: three specialists agree on the cause, the k8s specialist's "application bug" hypothesis is rejected, and the known failures from the knowledge base and the tools each specialist used are listed. The check results follow below.

![Report page](docs/screenshots/report.png)

One investigation is one OpenTelemetry trace in Tempo: a root span, a span per graph step (specialists in parallel, then correlate, evaluate, synthesize), and every LLM call (`chat gpt-4.1-mini`) and MCP tool call under the step that made it.

![Investigation trace](docs/screenshots/trace.png)

Model cost and budget (Grafana): tokens per run, budget rejections, early synthesis and exhausted runs, tokens per minute by model, role and type (input, output, cached input), and model call latency. The budget is enforced in code, not in the prompt: 600K soft / 1M hard per run, 120K per specialist, ~8K per tool result. A real investigation with three specialists uses about 50–60K tokens.

![Model cost and budget](docs/screenshots/cost.png)

## Limitations

- Accuracy is not yet measured on a golden set; the evaluation harness exists (`eval/`, `scenarios/`), but the full comparison is post-MVP.
- BM25 search has no semantic similarity; hybrid search waits for an on-prem embedding model.
- Model output and report text are currently in Turkish.
