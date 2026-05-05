# Dasom (다솜)

**AI-powered relationship conflict mediator.** Dasom is indigenous Korean word for "love."

Each person submits their side of a conflict privately. Five AI agents — two intake agents, two persona agents, and a neutral mediator — work together to simulate the conflict and produce a structured, empathetic resolution that neither person could reach on their own.

---

## Live URLs

| Service | URL |
|---|---|
| **Frontend** | https://dasom-frontend-275751645735.us-central1.run.app |
| **Backend API** | https://dasom-backend-275751645735.us-central1.run.app |
| **Health check** | https://dasom-backend-275751645735.us-central1.run.app/health |

Both services run on Google Cloud Run in `us-central1`.

---

## How It Works — Full System Breakdown

### Phase 1: Session Creation

One partner visits the frontend and clicks **Start a session**. The backend generates a short, readable session code and returns a shareable link (`/join/{code}`). The other partner joins using that link. Both partners are now in the same session.

### Phase 2: Emotion Agent Intake (parallel)

Both partners independently complete a private intake conversation with an **Emotion Agent** (Gemini 2.5 Flash with extended thinking).

**What the Emotion Agent does:**
- Sets tone preference first — Warm and Gentle / Calm and Neutral / Direct and Straightforward. This tone propagates to all downstream agents for that user.
- Conducts a dynamic intake conversation gathering all six 5W1H fields: Who, When, Where, What, Why, How — plus emotional state.
- After each user message, the agent reflects its understanding back. The user confirms or corrects (up to 3 correction loops per exchange). Confidence updates **only after confirmation**, not during reflection.
- Uses Gemini's thinking tokens — the full reasoning is stored internally and passed to downstream agents; a 1–2 sentence empathetic summary is shown to the user in the **ReasoningPanel** in real time.
- RAG-augments responses using two ChromaDB collections: `psychology_kb` (Gottman Institute, NVC framework, attachment theory, positive psychology) and `reddit_examples` (20 pre-collected relationship conflict posts as few-shot context).

**Confidence gate (hard block):**

| Level | Meaning | Behavior |
|---|---|---|
| Low | Major gaps, vague answers | Keep asking, explain why |
| Low-Medium | Some gaps, unclear emotions | Keep asking |
| Medium | Reasonable picture, some uncertainty | Keep asking |
| **Medium-High** | **Good picture, minor gaps** | **Proceed** |
| High | Complete, clear, well-confirmed | Proceed |

The `[INTAKE_COMPLETE]` marker is only emitted when confidence reaches Medium-High or High. The LangGraph `check_readiness` node gates simulation entry; neither user can proceed until both have cleared this threshold.

### Phase 3: LangGraph Orchestration

The session lifecycle runs as a **LangGraph StateGraph** with five nodes:

```
START → [intake_a, intake_b] (parallel fan-out)
              ↓ (join)
        check_readiness
              ↓ (conditional route)
        run_simulation
              ↓
           finalize
              ↓
             END
```

- `intake_a` / `intake_b` sync each user's emotion-agent state into the graph after every conversation turn.
- `check_readiness` reads both readiness flags. If both are ready, routes to simulation. If one or both are not ready, routes back to the corresponding intake node(s).
- A **MemorySaver** checkpointer persists graph state across HTTP requests within the process (no database required).

### Phase 4: Simulation

Once both users clear intake, the simulation begins. Both users watch it stream live in their browsers via SSE.

**Persona Agent A and B** (Gemini 2.5 Pro) argue in alternating turns, each advocating for their respective person's perspective. They have separate system prompts and never share state until simulation begins.

**Mediator Agent** (Llama 4 Scout via Vertex AI MaaS) observes every turn as a parallel stream. It does not have a fixed intervention schedule — it decides when it has seen enough. When the mediator determines it has witnessed the core conflict, it issues a structured synthesis.

### Phase 5: Mediator Synthesis

The mediator produces a six-section structured document:

1. **What Happened** — neutral factual summary, no blame
2. **What Each Person Felt** — draws from emotion agent structured output
3. **Where You Actually Agree** — surfaces common ground explicitly
4. **The Core Tension** — 1–2 sentences, the real underlying need
5. **A Path Forward** — specific and actionable, not generic advice
6. **A Message to Each Person** — same synthesis, framed per user's tone preference

Each user sees the full document; the frontend highlights their personal message section.

### Streaming Architecture

All agent output streams to the frontend via **Server-Sent Events (SSE)**:
- `/stream/intake/{session_code}/{user_id}` — intake stream, per user
- `/stream/simulation/{session_code}` — simulation stream, **fan-out** to all registered SSE connections for the session (both users watch simultaneously)

Event types carried inside the JSON body: `token`, `thinking`, `analysis_update`, `intake_complete`, `simulation_turn`, `mediator_intervention`, `synthesis`, `done`.

A `connected` keep-alive event fires every 20 seconds to prevent idle chunked connections from being cut by Cloud Run's load balancer.

---

## Data Flow

### 1. Session Creation and Data Storage

One partner creates a session and receives a 6-character code. They share it with their partner, who joins using that code. Both are now linked to the same session — but completely isolated from each other throughout intake.

All session data lives in server memory: a Python dictionary keyed by the session code. There is no database. This is intentional — data exists only for the duration of the session and disappears when the server restarts, which is a deliberate privacy choice. Everything stored in RAM: each person's conversation history, their emotional analysis, the simulation transcript, and the final synthesis.

### 2. Emotion Agent Intake

Each person talks privately to their own emotion agent, running independently on Gemini 2.5 Flash via Vertex AI. The two agents have no knowledge of each other or what the other person is sharing.

The agent does two things simultaneously: it holds a natural, flowing conversation, and it builds a structured profile behind the scenes — the 5W1H fields (Who, When, Where, What, Why, How), emotional labels, and underlying needs. The agent's reasoning streams to the screen in real time, but is automatically summarized into plain, empathetic language before the user sees it — the raw analytical reasoning stays hidden.

A confidence score (Low → Low-Medium → Medium → Medium-High → High) updates after each confirmed exchange. The agent cannot proceed until it reaches Medium-High or High. Below that threshold, it keeps asking — gently, conversationally, never like a form.

### 3. RAG — Retrieval Augmented Generation

Before generating each response, the agents search two ChromaDB vector stores to find relevant material:

- **Psychology KB** — content from the Gottman Institute, the Nonviolent Communication (NVC) framework, attachment theory research, and active listening and emotion regulation literature. This grounds the agents in real relationship psychology.
- **Reddit examples** — real posts from r/relationship_advice and r/AITAH, used as few-shot examples. This teaches the agents how real people actually talk when they're hurt, frustrated, or feeling unheard — not how a textbook describes it.

Retrieved content is injected into the agent's system prompt before each response. The result is output grounded in real psychology and real human language, rather than generic AI advice.

### 4. Streaming — How Responses Appear in Real Time

All agent output streams token by token using Server-Sent Events (SSE) — the server pushes a continuous stream to the browser rather than waiting for a complete response. There are two separate streams:

- **Intake stream** — one per user, private. Carries conversation tokens, reasoning summaries, and analysis panel updates (confidence level, 5W1H fields filling in).
- **Simulation stream** — shared between both users. When a persona agent produces a turn, the session manager fans it out to every registered SSE connection for that session simultaneously. Both people watch the simulation unfold on their own screens in real time.

### 5. The Simulation

Once both users reach Medium-High or High confidence, the LangGraph pipeline advances automatically — no manual trigger needed.

Two persona agents are initialized, each given only their own person's emotional profile. They have no access to the other person's intake data. Both run on Gemini 2.5 Pro with opposing system prompts — they argue back and forth in turns, speaking as the people they represent.

The Mediator Agent (Llama 4 Scout, Meta's open-source model served via Vertex AI MaaS) observes silently after every exchange. It does not intervene on a fixed schedule — it reads the transcript and decides for itself when it has seen enough: when the core tension is visible, when the conversation has gone circular, when a genuine softening has occurred, or when escalation has reached a point of diminishing returns.

### 6. LangGraph Orchestration

LangGraph connects all of the above into an automated pipeline with explicit conditional logic:

- **START** → both emotion agents are activated in parallel (fan-out)
- **Confidence gate** → after each intake turn, the graph checks: are both users ready? If no — loop back to whichever intake node(s) still need work. If yes — advance to simulation.
- **Simulation ends** → mediator synthesizes → results stored → **END**

LangGraph handles the parallel execution of two independent intake conversations, the conditional routing based on confidence, and the persistence of state across multiple HTTP requests — all within a single Python process, with no database.

### 7. Mediator Synthesis

When the mediator intervenes, it receives everything at once: both emotional profiles from intake, both conversation histories, and the full simulation transcript. It also receives each person's tone preference.

It produces a single 6-section document: **What Happened**, **What Each Person Felt**, **Where You Actually Agree**, **The Core Tension**, **A Path Forward**, and **A Message to Each Person**. The last section is written twice — once addressed directly to Person A in their preferred tone, once to Person B in theirs. The same synthesis is stored for both users; the frontend surfaces each person's personal message prominently.

### 8. Deployment

The backend (FastAPI) runs as a Docker container on Google Cloud Run — serverless, meaning it scales automatically and costs nothing when idle. The frontend (React + nginx) runs as a separate Cloud Run service serving the compiled static bundle.

All AI models run on Vertex AI within a single GCP project — no external API keys, no per-request billing outside GCP. The pre-built ChromaDB vector store is baked into the backend Docker image, so there is no separate database or vector store service to manage at runtime.

---

## Architecture Diagram

```
                        ┌─────────────────────────────────────┐
                        │           Browser (Person A)         │
                        │  ChatPanel │ ReasoningPanel │ Analysis│
                        └────────────────────┬────────────────┘
                                             │ SSE + POST
                        ┌────────────────────▼────────────────┐
                        │            FastAPI Backend           │
                        │                                      │
   ┌──────────────┐     │  ┌──────────────┐  ┌─────────────┐  │
   │  Person A    │─────┼─►│ Emotion      │  │  Session    │  │
   │  intake SSE  │     │  │ Agent A      │  │  Manager    │  │
   └──────────────┘     │  │ (Flash+think)│  │ (in-memory) │  │
                        │  └──────┬───────┘  └──────┬──────┘  │
   ┌──────────────┐     │         │   LangGraph      │         │
   │  Person B    │─────┼─►┌──────▼──────────────────▼──────┐  │
   │  intake SSE  │     │  │  dasom_graph (StateGraph)      │  │
   └──────────────┘     │  │  check_readiness → confidence  │  │
                        │  │  gate → run_simulation         │  │
                        │  └──────┬──────────────────┬──────┘  │
                        │         │                  │         │
                        │  ┌──────▼──────┐   ┌───────▼──────┐  │
                        │  │ Persona A   │   │ Persona B    │  │
                        │  │ (Pro)       │◄──►│ (Pro)        │  │
                        │  └──────┬──────┘   └──────┬───────┘  │
                        │         └────────┬─────────┘         │
                        │                  ▼                   │
                        │          ┌───────────────┐           │
                        │          │ Mediator Agent│           │
                        │          │ (Llama Scout) │           │
                        │          └───────┬───────┘           │
                        │                  │ SSE fan-out       │
                        └──────────────────┼───────────────────┘
                                           │
                   ┌───────────────────────▼─────────────────────┐
                   │  Both browsers receive simulation + synthesis │
                   └─────────────────────────────────────────────┘

RAG (ChromaDB, baked into Docker image):
  psychology_kb  → Emotion Agents + Mediator
  reddit_examples → Emotion Agents + Persona Agents
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent framework | LangGraph 1.1.10 |
| Backend | FastAPI 0.136 + uvicorn + sse-starlette |
| Frontend | React 18 + Vite + Tailwind CSS |
| LLM SDK | google-genai 1.73 (Vertex AI mode) |
| Persona / Mediator calls | httpx (OpenAI-compatible SSE endpoint) |
| RAG | ChromaDB 1.5 + LangChain text splitters |
| All models | Vertex AI (GCP credits — no external billing) |
| Deployment | Cloud Run (backend + frontend), Artifact Registry |
| State | In-memory Python dict — no database |
| Auth | Application Default Credentials (ADC) — works locally and on Cloud Run |

**Models:**

| Agent | Model | API |
|---|---|---|
| Emotion Agent A & B | `gemini-2.5-flash` | google-genai SDK, streaming + thinking tokens |
| Persona Agent A & B | `gemini-2.5-pro` | google-genai SDK, streaming |
| Mediator Agent | `meta/llama-4-scout-17b-16e-instruct-maas` | Vertex AI OpenAI-compatible endpoint, `us-east5` |

---

## Class Concepts

| Concept | Location | Notes |
|---|---|---|
| **Multi-agent patterns** | `backend/graph/dasom_graph.py`, `backend/agents/` | Fan-out/join, supervisor pattern (mediator observing simulation), human-in-the-loop (user confirmation after each emotion-agent reflection) |
| **Agent framework (LangGraph)** | `backend/graph/dasom_graph.py` | StateGraph with 5 nodes, conditional routing via `route_after_readiness`, MemorySaver checkpointer |
| **RAG** | `backend/rag/`, `backend/agents/emotion_agent.py`, `backend/agents/mediator_agent.py` | Two ChromaDB collections; `retrieve_psychology()` and `retrieve_examples()` injected into agent system prompts |
| **Hosted models** | `backend/models/router.py` | Three Vertex AI models via two different SDKs; ADC token refresh; 429 rate-limit retry with back-off |
| **Structured output** | `backend/agents/mediator_agent.py`, `backend/graph/state.py` | Mediator produces 6-section JSON; emotion agent produces typed `FiveW1HOutput` dataclass with per-field confidence |
| **Reasoning tokens** | `backend/models/router.py` (`call_gemini_flash_thinking`), `backend/agents/emotion_agent.py` | Gemini thinking tokens separated from text tokens; full reasoning passed to downstream agents, summarized version shown to user |
| **Streaming (SSE)** | `backend/main.py`, `backend/session/manager.py` | SSE fan-out using `asyncio.Queue` per connection; `connected` keep-alive events; two distinct stream types (intake per-user, simulation broadcast) |

---

## Local Development Setup

### Prerequisites

- Python 3.12 (`py -3.12`)
- Node.js v24+
- Google Cloud SDK (`gcloud`) authenticated with ADC
- GCP project `ieor-4576-agents-487001` with Vertex AI API enabled

### Backend

```powershell
# Activate virtual environment
backend\.venv\Scripts\Activate.ps1

# Install dependencies (already done if venv exists)
pip install -r backend/requirements.txt

# Set environment variables (copy from .env.example)
# .env must exist at repo root with GCP_PROJECT_ID etc.

# Authenticate ADC
gcloud auth application-default login

# Run backend
uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload
```

The backend will be available at `http://localhost:8080`.

### Frontend

```powershell
cd frontend

# Install dependencies
npm install

# Run dev server (proxies API calls to localhost:8080)
npm run dev
```

The frontend will be available at `http://localhost:5173`.

### Environment Variables

Create `.env` at the repo root:

```
GCP_PROJECT_ID=ieor-4576-agents-487001
GCP_LOCATION=us-central1
GEMINI_FLASH_MODEL=gemini-2.5-flash
GEMINI_PRO_MODEL=gemini-2.5-pro
LLAMA_SCOUT_MODEL=llama-4-scout-17b-16e-instruct-maas
LLAMA_REGION=us-east5
```

---

## Rebuild Vector Store

The pre-built ChromaDB vector store is already included in the Docker image at `backend/chroma_db/`. You only need to rebuild if you add new documents to `data/`.

> `data/` is gitignored — never committed to the repo.

### Step 1: Populate data directories

```
data/
├── psychology/
│   ├── gottman_articles/   ← Gottman Institute articles (gottman.com free library)
│   ├── nvc_framework/      ← CNVC NVC materials + Feelings/Needs Inventories
│   ├── attachment_theory/  ← Attachment theory summaries
│   └── apa_conflict/       ← Positive Psychology articles (positivepsychology.com) — APA.org was bot-protected
└── reddit/
    ├── relationship_advice/
    │   ├── post_001.txt ... post_010.txt
    └── aitah/
        ├── post_001.txt ... post_010.txt
```

Reddit files must be plain text (post body only, no title or metadata). Do not attempt to scrape Reddit — use pre-collected files only.

### Step 2: Run the build script

```powershell
backend\.venv\Scripts\Activate.ps1
py -3.12 scripts/build_vectorstore.py
```

This calls `load_reddit.py` and `load_psychology.py` in sequence, embedding all documents into the ChromaDB collections at `backend/chroma_db/`.

### Step 3: Test retrieval

```python
from backend.rag.retriever import retrieve_psychology, retrieve_examples

results = retrieve_psychology("Gottman four horsemen conflict")
results = retrieve_examples("partner ignored me during argument")
```

---

## Deployment

Both services are deployed to Google Cloud Run. All commands use `gcloud` from the repo root.

### Backend

```powershell
# Build and push image
docker build -t us-central1-docker.pkg.dev/ieor-4576-agents-487001/dasom/backend:latest .
docker push us-central1-docker.pkg.dev/ieor-4576-agents-487001/dasom/backend:latest

# Deploy to Cloud Run
gcloud run deploy dasom-backend `
  --image us-central1-docker.pkg.dev/ieor-4576-agents-487001/dasom/backend:latest `
  --region us-central1 `
  --platform managed `
  --allow-unauthenticated `
  --memory 2Gi `
  --timeout 3600
```

The Dockerfile (at repo root) builds from the project root so `backend/chroma_db/` is included in the image. The backend has no startup scripts — ChromaDB opens the pre-built store on first query.

### Frontend

```powershell
# Build production bundle
cd frontend
npm run build   # outputs to frontend/dist/

# Build and push nginx image (from frontend/)
docker build -t us-central1-docker.pkg.dev/ieor-4576-agents-487001/dasom/frontend:latest .
docker push us-central1-docker.pkg.dev/ieor-4576-agents-487001/dasom/frontend:latest

# Deploy to Cloud Run
gcloud run deploy dasom-frontend `
  --image us-central1-docker.pkg.dev/ieor-4576-agents-487001/dasom/frontend:latest `
  --region us-central1 `
  --platform managed `
  --allow-unauthenticated `
  --memory 256Mi
```

The frontend container is nginx:alpine. It serves the Vite production bundle with SPA routing (`try_files $uri $uri/ /index.html`) and long-lived cache headers on static assets.

> **Note:** Firebase Hosting is blocked by a Columbia University org-level policy (`constraints/iam.allowedPolicyMemberDomains`). nginx on Cloud Run is functionally equivalent for this project.

---

## Project Structure

```
dasom/
├── CLAUDE.md                        ← master build context
├── README.md
├── .env                             ← gitignored
├── .env.example
├── .gitignore
├── .dockerignore
│
├── backend/
│   ├── Dockerfile                   ← build context is repo root
│   ├── requirements.txt
│   ├── main.py                      ← FastAPI app, all endpoints, SSE
│   ├── chroma_db/                   ← pre-built vector store (gitignored)
│   │
│   ├── agents/
│   │   ├── emotion_agent.py         ← intake conversation, 5W1H, confidence gate
│   │   ├── persona_agent.py         ← persona simulation turns
│   │   └── mediator_agent.py        ← mediator observer + 6-section synthesis
│   │
│   ├── graph/
│   │   ├── dasom_graph.py           ← LangGraph StateGraph, nodes, routing
│   │   └── state.py                 ← DasomState TypedDict schema
│   │
│   ├── models/
│   │   └── router.py                ← Vertex AI clients, 429 retry, ADC token
│   │
│   ├── rag/
│   │   ├── embeddings.py            ← chunking, embedding, ChromaDB ingestion
│   │   └── retriever.py             ← retrieve_psychology() + retrieve_examples()
│   │
│   ├── session/
│   │   └── manager.py               ← in-memory session store, SSE fan-out queues
│   │
│   └── utils/
│       ├── prompts.py               ← ALL system prompts (single source of truth)
│       └── streaming.py             ← SSE helpers, reasoning summarizer
│
├── frontend/
│   ├── Dockerfile                   ← nginx:alpine, serves dist/
│   ├── nginx.conf                   ← SPA routing + cache headers
│   ├── package.json
│   ├── .env.production              ← VITE_API_URL pointing to Cloud Run backend
│   ├── index.html
│   │
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       │
│       ├── pages/
│       │   ├── Landing.jsx          ← create session / join with code
│       │   ├── Intake.jsx           ← tone setting + emotion agent conversation
│       │   ├── Waiting.jsx          ← loading state while partner completes intake
│       │   └── Result.jsx           ← 6-section mediator synthesis
│       │
│       ├── components/
│       │   ├── ChatPanel.jsx        ← conversation with emotion agent
│       │   ├── ReasoningPanel.jsx   ← summarized reasoning stream
│       │   ├── AnalysisPanel.jsx    ← 5W1H + emotional labels + confidence badges
│       │   ├── SimulationView.jsx   ← persona argument stream, live
│       │   ├── SynthesisCard.jsx    ← 6-section mediator output
│       │   ├── SessionNotice.jsx    ← persistent "keep tab open" banner
│       │   └── ErrorState.jsx       ← clean error + Start Over button
│       │
│       └── hooks/
│           ├── useSSE.js            ← SSE connection management
│           └── useSession.js        ← session code, join/create logic
│
├── data/                            ← gitignored — never committed
│   ├── psychology/
│   └── reddit/
│
└── scripts/
    ├── load_reddit.py               ← reads data/reddit/ txt files → ChromaDB
    ├── load_psychology.py           ← reads data/psychology/ files → ChromaDB
    ├── build_vectorstore.py         ← runner: calls both loaders in sequence
    └── test_e2e.py                  ← 48-step end-to-end integration test
```
