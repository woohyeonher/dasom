# Dasom — AI Relationship Conflict Mediator
## Master Context Document for Claude Code

---

## CRITICAL INSTRUCTION FOR CLAUDE CODE

Build and execute **one step at a time**. After completing each step, stop and wait for confirmation before proceeding to the next. Do not batch steps together. Do not proceed if a step fails — surface the error clearly and wait for instruction.

---

## 1. Product Overview

**Name:** Dasom (다솜) — indigenous Korean word for "love"

**What it is:** A multi-agent AI platform that mediates relationship conflicts between couples. Each person submits their side privately. AI agents simulate the conflict. A neutral mediator produces a structured resolution.

**Core insight:** "Dasom isn't for couples who can't talk. It's for couples who've already tried." People who have fought and gone nowhere need a structured way to be heard without the other person reacting in real time. Externalizing conflict to a neutral AI is easier than direct confrontation.

**Target user:** Couples in romantic relationships experiencing unresolved conflict.

**Business model:** Monthly subscription ~$15–20/couple. Competes with couples therapy ($150–300/hour, 3–6 week waitlist).

---

## 2. Full Agent Architecture

### Agent Roles

| Agent | Model | Role |
|---|---|---|
| Emotion Agent A | Gemini 2.5 Flash | Intake conversation with Person A |
| Emotion Agent B | Gemini 2.5 Flash | Intake conversation with Person B |
| Persona Agent A | Gemini 2.5 Pro | Argues Person A's perspective in simulation |
| Persona Agent B | Gemini 2.5 Pro | Argues Person B's perspective in simulation |
| Mediator Agent | Llama 4 Scout (MaaS) | Observes simulation, decides when to intervene, produces synthesis | 

### All Models via Vertex AI (GCP credits — no external billing)

```
GCP_PROJECT_ID=ieor-4576-agents-487001
GCP_LOCATION=us-central1
GEMINI_FLASH_MODEL=gemini-2.5-flash
GEMINI_PRO_MODEL=gemini-2.5-pro
LLAMA_SCOUT_MODEL=meta/llama-4-scout-17b-16e-instruct-maas
```

### Complete Flow

```
Session created → short code + shared link
        ↓                           ↓
   Person A joins              Person B joins
        ↓                           ↓
Tone preference set         Tone preference set
(Warm / Neutral / Direct)   (Warm / Neutral / Direct)
        ↓                           ↓
Emotion Agent A             Emotion Agent B
(Gemini 2.5 Flash)          (Gemini 2.5 Flash)
- Dynamic intake conversation    - Dynamic intake conversation
- Gathers 5W1H + emotional state - Gathers 5W1H + emotional state
- Reddit RAG few-shot examples   - Reddit RAG few-shot examples
- Max 3 correction loops/exchange- Max 3 correction loops/exchange
- Reasoning: full→agents,        - Reasoning: full→agents,
  summarized→user panel            summarized→user panel
- Confidence levels shown        - Confidence levels shown
  (Low / Low-Medium / Medium /     (Low / Low-Medium / Medium /
   Medium-High / High)              Medium-High / High)
- HARD BLOCK below Medium-High   - HARD BLOCK below Medium-High
        ↓                           ↓
Structured Output A         Structured Output B
- 5W1H facts                - 5W1H facts
- Emotional labels + needs  - Emotional labels + needs
- Confidence per field      - Confidence per field
        ↓                           ↓
Persona Agent A             Persona Agent B
(Gemini 2.5 Pro)            (Gemini 2.5 Pro)
- Different system prompts  - Different system prompts
- Never share state until   - Never share state until
  simulation begins           simulation begins
        ↓                           ↓
              SIMULATION ARENA
         Persona A argues ↔ Persona B argues
         Mediator observes every turn
         Mediator decides when to intervene
                    ↓
             MEDIATOR AGENT
             (Llama 4 Scout)
         6-section structured synthesis
                ↓         ↓
         Output A      Output B
      (framed for A) (framed for B)
```

---

## 3. Key Product Decisions

### Emotion Agent Behavior
- Dynamically follows up — never feels like a checklist
- Must gather all 6 fields of 5W1H: Who, When, Where, What, Why, How
- Must gather emotional state — user may provide wall-of-text stream of consciousness, agent must connect and analyze all of it
- After each user input: agent reflects understanding, user confirms or corrects (max 3 correction loops per exchange)
- Confidence updates ONLY after user confirmation, not during reflection
- Agent must explain when more information is needed: "I need more detail here for a better result"
- Agent must communicate that more information = better output quality

### Confidence Level System (5 levels)
- **Low** — major gaps, vague answers → keep asking, explain why
- **Low-Medium** — some gaps, unclear emotions → keep asking
- **Medium** — reasonable picture, some uncertainty → keep asking
- **Medium-High** — good picture, minor gaps → **PROCEED**
- **High** — complete, clear, well-confirmed → **PROCEED**

Hard block if below Medium-High. Do not let user proceed.

### Tone Preference (set at very start of emotion agent)
Very first thing the emotion agent asks. Options:
- Warm and gentle
- Calm and neutral
- Direct and straightforward

This tone propagates to ALL downstream agents for that user — emotion agent conversation style, persona agent framing, mediator output framing.

### Reasoning Exposure
- **Full reasoning** → stored internally, passed to downstream agents
- **Summarized reasoning** → shown to user in real time on ReasoningPanel
- Summarization: lightweight prompt pass — "given this reasoning, produce a 1-2 sentence empathetic summary"
- Exposed during BOTH emotion agent intake AND simulation

### Simulation
- Persona A and Persona B argue in turns
- Mediator runs as parallel observer on EVERY simulation turn
- Mediator decides when it has seen enough and intervenes — no fixed round limit
- Both users watch the simulation stream in real time (SSE fan-out)

### Mediator Output — 6 Sections
1. **What Happened** — neutral factual summary, no blame
2. **What Each Person Felt** — draws from emotion agent output
3. **Where You Actually Agree** — surfaces common ground explicitly
4. **The Core Tension** — 1-2 sentences, the real underlying need
5. **A Path Forward** — specific and actionable, not generic advice
6. **A Message to Each Person** — same synthesis, framed per user's tone preference

### Session Management
- In-memory only — no database
- Session code generated on creation (short readable code)
- Shared as a link: `dasom.app/join/{code}`
- Both users must complete intake in same session — no 24-hour async (roadmap item)
- State held in Python dict keyed by session code
- SSE fan-out: session manager broadcasts simulation stream to all registered SSE connections for that session

### Error States
- If session breaks (timeout, browser close, API failure): clean error page
- Message: "Your session has ended."
- Show "Start Over" button — generates new session
- No technical error messages shown to user
- `SessionNotice.jsx`: persistent quiet line on every page — "Please keep this tab open. Closing or refreshing will end your session."

---

## 4. RAG Design

### Two ChromaDB Collections

**Collection 1: `psychology_kb`** — Primary knowledge base
- Feeds: Mediator (primary), Emotion Agents (secondary)
- Sources (scraped/downloaded into `data/psychology/`):
  - Gottman Institute articles (gottman.com — free library)
  - NVC framework (CNVC downloadable materials — cnvc.org)
  - CNVC Feelings Inventory + Needs Inventory
  - Attachment theory summaries (simplypsychology.org)
  - Positive Psychology articles — active listening, emotion regulation, empathy, assertive communication (positivepsychology.com)

**Collection 2: `reddit_examples`** — Few-shot examples only
- Feeds: Emotion Agents, Persona Agents
- Source: pre-collected `.txt` files already saved in `data/reddit/`
- **DO NOT scrape Reddit** — API access is closed. Read from existing files only.
- File structure:
  ```
  data/reddit/
  ├── relationship_advice/
  │   ├── post_001.txt
  │   ├── post_002.txt
  │   └── ... (10 posts)
  └── aitah/
      ├── post_001.txt
      ├── post_002.txt
      └── ... (10 posts)
  ```
- Each `.txt` file contains the body text of one Reddit post (no title, no metadata)

### Retrieval Functions in `retriever.py`
```python
retrieve_psychology(query)  # pulls from psychology_kb
retrieve_examples(query)    # pulls from reddit_examples
```

### Scripts
- `scripts/load_reddit.py` — reads all `.txt` files from `data/reddit/`, loads into `reddit_examples` ChromaDB collection
- `scripts/load_psychology.py` — reads all files from `data/psychology/`, loads into `psychology_kb` ChromaDB collection
- `scripts/build_vectorstore.py` — runner that calls both loaders in sequence

### Data Directory Structure
```
data/
├── psychology/
│   ├── gottman_articles/
│   ├── nvc_framework/
│   ├── attachment_theory/
│   └── apa_conflict/
└── reddit/
    ├── relationship_advice/
    │   ├── post_001.txt ... post_010.txt
    └── aitah/
        ├── post_001.txt ... post_010.txt
```

`data/` is gitignored — never committed to repo.

---

## 5. Tech Stack

| Layer | Choice |
|---|---|
| Agent framework | LangGraph |
| Backend | FastAPI + SSE streaming |
| Frontend | React + Tailwind CSS |
| RAG | ChromaDB + LangChain |
| All LLMs | Vertex AI (GCP credits) |
| Deployment | Cloud Run (backend) + Firebase Hosting (frontend) |
| State | In-memory Python dict (no database) |

### Why LangGraph
Architecture is literally a graph — nodes (agents), conditional edges (confidence gate routing), human-in-the-loop interrupts (user confirmation steps), supervisor pattern (mediator observing simulation).

### Why SSE over WebSockets
Two distinct stream types needed:
1. Intake stream (emotion agent conversation + reasoning panel)
2. Simulation stream (persona agent arguments + mediator reasoning)

SSE is simpler, lighter, and sufficient for unidirectional server→client streaming.

---

## 6. File Structure

```
dasom/
├── CLAUDE.md                      ← this file
├── README.md
├── .env                           ← gitignored
├── .env.example
├── .gitignore
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                    # FastAPI app, SSE endpoints, session mgmt
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── emotion_agent.py       # Emotion agent, 5W1H tracking, confidence
│   │   ├── persona_agent.py       # Persona agent logic
│   │   └── mediator_agent.py      # Mediator observer + synthesis
│   │
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── dasom_graph.py         # LangGraph graph definition
│   │   └── state.py               # Shared state schema
│   │
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── embeddings.py          # Chunking, embedding, ChromaDB ingestion
│   │   └── retriever.py           # retrieve_psychology() + retrieve_examples()
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   └── router.py              # Vertex AI client init for all 3 models
│   │
│   ├── session/
│   │   ├── __init__.py
│   │   └── manager.py             # In-memory session store, SSE fan-out
│   │
│   └── utils/
│       ├── __init__.py
│       ├── streaming.py           # SSE helpers, reasoning summarization
│       └── prompts.py             # ALL system prompts in one place
│
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── index.html
│   │
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       │
│       ├── pages/
│       │   ├── Landing.jsx        # Create session / join with code
│       │   ├── Intake.jsx         # Tone setting + emotion agent conversation
│       │   ├── Waiting.jsx        # Simple loading state while partner completes
│       │   └── Result.jsx         # Final synthesis output
│       │
│       ├── components/
│       │   ├── ChatPanel.jsx      # Conversation with emotion agent
│       │   ├── ReasoningPanel.jsx # Summarized reasoning stream
│       │   ├── AnalysisPanel.jsx  # Live emotional analysis + confidence levels
│       │   ├── SimulationView.jsx # Persona agent argument stream (live)
│       │   ├── SynthesisCard.jsx  # 6-section mediator output
│       │   ├── SessionNotice.jsx  # Persistent "keep tab open" warning
│       │   └── ErrorState.jsx     # Error + Start Over button
│       │
│       └── hooks/
│           ├── useSSE.js          # SSE connection management
│           └── useSession.js      # Session code, join/create logic
│
├── data/                          ← gitignored
│   ├── psychology/
│   └── reddit/
│
└── scripts/
    ├── load_reddit.py             # Reads data/reddit/ txt files → ChromaDB
    ├── load_psychology.py         # Reads data/psychology/ files → ChromaDB
    └── build_vectorstore.py       # Runner — calls both loaders in sequence
```

---

## 7. Frontend Design

### Design Philosophy
Calm, warm, trustworthy. Not clinical, not cold, not overly technical. The UI should feel like a safe space — something worth slowing down for. Every design decision should reduce emotional friction.

### Color Palette
```css
--color-primary:     #7C9E87;   /* sage green — calming, grounding */
--color-background:  #FAF7F2;   /* warm cream — soft, non-clinical */
--color-accent:      #C4A882;   /* warm sand — warmth, humanity */
--color-text:        #2C2C2C;   /* near black — never pure #000000 */
--color-surface:     #FFFFFF;   /* white panels */
--color-surface-alt: #F0EDE8;   /* subtle warm gray for secondary panels */
--color-message-bg:  #7C9E8712; /* sage tint for personal message section */
--color-border:      #E8E4DF;   /* soft warm border */
```

### Typography
- **Font:** Inter (Google Fonts) — clean, modern, highly readable
- **Line height:** 1.7 for body text — generous, calm
- **Font sizes:** 14px body, 16px chat, 20px headings, 13px labels
- **Weight:** 400 regular, 500 medium for emphasis, 600 for headings

### Layout — Intake Page (three columns)
```
┌─────────────────────────────────────────────────────────┐
│  SessionNotice (top bar, quiet, persistent)              │
├──────────────────┬──────────────────┬───────────────────┤
│                  │                  │                   │
│   ChatPanel      │  ReasoningPanel  │  AnalysisPanel    │
│   (left, 45%)    │  (middle, 25%)   │  (right, 30%)     │
│                  │                  │                   │
│  Conversation    │  Summarized      │  5W1H completion  │
│  with emotion    │  reasoning       │  Emotional labels │
│  agent           │  stream          │  Confidence level │
│                  │                  │  (updates after   │
│                  │                  │   confirmation)   │
│                  │                  │                   │
│  [text input]    │                  │                   │
└──────────────────┴──────────────────┴───────────────────┘
```

### Layout — Simulation Page
```
┌─────────────────────────────────────────────────────────┐
│  SessionNotice                                           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   SimulationView — chat-style, two labeled speakers     │
│                                                         │
│   [A]  message bubble left-aligned, sage tint           │
│                  message bubble right-aligned, sand [B] │
│   [A]  message bubble left-aligned, sage tint           │
│                                                         │
│   ─────────────────────────────────────────────────     │
│   ReasoningPanel — mediator thinking, italic, quiet     │
│   "The mediator is observing..."                        │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Layout — Result Page
Flowing document style — not cards, not accordion. Reads like a thoughtful letter. Each section flows into the next with a soft divider line.

- Section 1–5: warm cream background, prose paragraphs, section headers in sage
- Section 6 (Message to You): visually distinct — sage tint background, slightly inset, speaks directly to the user

### Landing Page
- Centered layout, generous whitespace, warm cream background
- Dasom wordmark at top center
- Tagline: **"For when talking isn't enough."**
- Two actions:
  - "Start a session" — primary button, sage green, full rounded
  - "Join with a code" — secondary, outlined, sand accent
- Session code input appears inline when "Join with a code" is clicked

### Waiting Page
- Centered, warm cream background
- Gentle pulsing circle animation in sage green
- Rotating sentences every 4 seconds (no progress bar)

### Component Style Rules
- **Buttons:** `border-radius: 8px`, sage primary (`#7C9E87`), sand secondary (`#C4A882`)
- **Chat bubbles:** `border-radius: 16px`, white background, `box-shadow: 0 1px 4px rgba(0,0,0,0.06)`
- **Panels:** white surface, `1px solid var(--color-border)`, subtle shadow
- **Reasoning stream:** italic, 70% opacity text, 13px font
- **Confidence level badges — color coded pills:**
  - Low: `#E8A598` (muted red)
  - Low-Medium: `#E8C498` (muted orange)
  - Medium: `#E8D898` (muted yellow)
  - Medium-High: `#A8C4A2` (muted green)
  - High: `#7C9E87` (full sage)
- **Simulation speakers:**
  - Person A: left-aligned bubble, sage tint background, labeled "A"
  - Person B: right-aligned bubble, sand tint background, labeled "B"
  - Mediator intervention: centered, italic, slightly larger — *"The mediator has seen enough."*
- **Result page section dividers:** `1px solid var(--color-border)`, generous vertical padding

---

## 8. Environment Variables

`.env` file (gitignored):
```
GCP_PROJECT_ID=ieor-4576-agents-487001
GCP_LOCATION=us-central1
GEMINI_FLASH_MODEL=gemini-2.5-flash
GEMINI_PRO_MODEL=gemini-2.5-pro
LLAMA_SCOUT_MODEL=llama-4-scout-17b-16e-instruct-maas
LLAMA_REGION=us-east5
LLAMA_ENDPOINT=https://us-east5-aiplatform.googleapis.com
```

---

## 9. Local Development Setup

- **OS:** Windows 11, native (Git Bash + PowerShell)
- **Terminal:** PowerShell preferred
- **Python:** 3.12.5 (use `py -3.12` to invoke)
- **Node.js:** v24.12.0
- **Docker:** Installed but NOT needed for local dev — only for deployment
- **Virtual environment:** `backend\.venv` (already created with py -3.12)
- **Activate venv (PowerShell):** `backend\.venv\Scripts\Activate.ps1`

---

## 10. Build Order — ONE STEP AT A TIME

### Step 1: Environment Setup ✓ (complete)
- GCP project configured
- Vertex AI enabled
- All models enabled in Model Garden
- Repo directory created
- Python venv created

### Step 2: RAG Pipeline (START HERE)
1. `scripts/load_reddit.py` — reads all `.txt` files from `data/reddit/relationship_advice/` and `data/reddit/aitah/`, loads into `reddit_examples` ChromaDB collection
2. `scripts/load_psychology.py` — scrapes/downloads psychology sources into `data/psychology/`, loads into `psychology_kb` ChromaDB collection. Sources: gottman.com, cnvc.org, simplypsychology.org, apa.org
3. `backend/rag/embeddings.py` — chunking and embedding logic used by both loaders
4. `scripts/build_vectorstore.py` — runner that calls both loaders in sequence
5. `backend/rag/retriever.py` — `retrieve_psychology()` and `retrieve_examples()`
6. Test both retrieval functions with sample queries before proceeding

### Step 3: Backend Core
1. `backend/requirements.txt`
2. `backend/main.py` — FastAPI skeleton, CORS, health check
3. `backend/session/manager.py` — session creation, code gen, SSE fan-out
4. `backend/models/router.py` — Vertex AI clients for all 3 models, test each endpoint
5. `backend/utils/streaming.py` — SSE helpers, reasoning summarizer
6. `backend/utils/prompts.py` — ALL system prompts drafted here

### Step 4: Agents (in this order)
1. `backend/agents/emotion_agent.py` — test heavily before moving on
2. `backend/agents/persona_agent.py`
3. Simulation loop (persona A ↔ persona B, mediator observing)
4. `backend/agents/mediator_agent.py`

### Step 5: LangGraph Graph
1. `backend/graph/state.py` — state schema
2. `backend/graph/dasom_graph.py` — full graph, nodes, edges, conditional routing
3. Test full pipeline end to end with dummy inputs

### Step 6: FastAPI Endpoints
- `POST /session/create` → returns session code
- `POST /session/join/{code}` → joins session
- `GET /stream/intake/{session_code}/{user_id}` → SSE: emotion agent stream
- `POST /intake/message` → send user message to emotion agent
- `GET /stream/simulation/{session_code}` → SSE: simulation stream (fan-out)
- `GET /result/{session_code}/{user_id}` → final synthesis

### Step 7: Frontend
1. Vite + React + Tailwind setup
2. `useSSE.js` and `useSession.js` hooks
3. Pages: Landing → Intake → Waiting → Result
4. Components: ChatPanel → ReasoningPanel → AnalysisPanel → SimulationView → SynthesisCard
5. `SessionNotice.jsx` and `ErrorState.jsx`
6. Connect SSE hooks after static rendering confirmed

### Step 8: Integration
- Connect frontend to backend end to end
- Test full session flow with two browser tabs simultaneously
- Verify SSE fan-out works across both tabs

### Step 9: Deployment
- Dockerize backend → deploy to Cloud Run
- Deploy frontend → Firebase Hosting
- Verify live public URL works end to end

### Step 10: Documentation
- `README.md` — run instructions, live URL, class concepts mapped to files
- Business one-pager (separate document)

---

## 11. Class Concepts to Call Out in README

| Concept | Where in code |
|---|---|
| Multi-agent patterns | `backend/graph/dasom_graph.py`, `backend/agents/` |
| RAG | `backend/rag/`, ChromaDB collections |
| Hosted models | `backend/models/router.py` — Vertex AI |
| Structured output / tool calling | `backend/agents/mediator_agent.py` |
| Reasoning tokens | `backend/utils/streaming.py`, `backend/agents/emotion_agent.py` |
| Agent framework (LangGraph) | `backend/graph/dasom_graph.py` |

---

## 12. Constraints and Rules

- **Never use a database** — all state is in-memory
- **Never expose full reasoning to user** — only summarized version
- **Never let persona agents share state before simulation**
- **Never proceed below Medium-High confidence** — hard block
- **Never show technical error messages to user**
- **All models via Vertex AI** — no direct Anthropic API calls
- **Python 3.12 only** — do not use 3.14
- **PowerShell for terminal commands**
- **`data/` is always gitignored** — never commit scraped data
- **All system prompts live in `utils/prompts.py`** — never scattered
- **Do NOT scrape Reddit** — API access is closed, use pre-existing txt files only

---

## 13. Waiting Page Copy

Rotating sentences for `Waiting.jsx`:
- "Your partner is sharing their side of the story..."
- "Both perspectives are being carefully prepared..."
- "The agents are getting ready to understand your conflict..."
- "Almost there — waiting for both sides to be ready..."

---

## 14. Roadmap Items (NOT for current build)

- 24-hour async session with database persistence
- Room code system in mobile app (App Store)
- Premium feature: see partner's emotional analysis
- Model selection UI for users
- Reasoning budget slider (low/medium/high)
