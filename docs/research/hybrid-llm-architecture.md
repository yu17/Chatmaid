# Hybrid local/cloud LLM architecture for chatmaid — landscape research

Research date: 2026-09-27. Author: research agent (web research only; no code written).

Evidence labels used throughout:

- **VERIFIED** — the primary source (paper abstract, official docs, model card, README) was fetched and read during this session.
- **INFERRED** — derived from verified facts (e.g. VRAM = GGUF file size + KV-cache headroom) or from consistent secondary sources.
- **UNCERTAIN** — only seen in blogs/search snippets, or conflicting sources; treat as a lead, not a fact.

---

## 1. Executive summary

- The problem chatmaid poses — read private chat logs locally, draft replies locally, use a frontier cloud model only for revision/heavier reasoning without leaking raw content — is exactly the "privacy-conscious delegation" task defined by PAPILLON (NAACL 2025) and the "local-remote collaboration" setting of Stanford Hazy Research's Minions/MinionS (Feb 2025). Both have MIT-licensed code. Since then (2025–2026) the academic line has grown fast: contextual-integrity query rewriting ("Need to Know", Jun 2026), P2Skill (Aug 2026), PRISM (AAAI 2026), CAMP for multi-turn PII accumulation (Apr 2026), calibrated-uncertainty cascades (UCCI, May 2026), and anonymization-impact studies (Sep 2026). **VERIFIED**
- Industry tooling has converged on three commodity layers we can reuse: (1) local inference servers with OpenAI-compatible APIs (Ollama 0.34.x, llama.cpp `llama-server` router mode, vLLM/SGLang, LM Studio's headless `llmster`); (2) gateways with fallback chains and guardrails (LiteLLM has a built-in Presidio PII-masking guardrail with `output_parse_pii: true`, i.e. re-hydration of placeholders in the response); (3) agent frameworks with fallback models (Pydantic AI `FallbackModel`, OpenClaw `model.fallbacks`). **VERIFIED**
- Every open-source assistant surveyed (Khoj, Jan, Open WebUI, LibreChat, AnythingLLM, Continue, Home Assistant, OpenClaw) chooses local vs cloud by **static configuration, user choice per conversation/role, or error-driven failover** — none does content-aware, privacy-aware routing. The research systems (PAPILLON, Minions, OpenJarvis) are the only places where the local model actively mediates what the cloud sees. That gap is where chatmaid's value lies. **VERIFIED for the listed projects**
- Local models for Chinese+English in the 4B–32B class are strong in Sept 2026: Qwen3.5 (Feb 2026: 0.8B/2B/4B/9B/27B dense, 35B-A3B MoE, Apache-2.0, 201 languages, 256K context), Qwen3.6-27B/35B-A3B (Apr 2026), Qwen3.8-27B (Aug 2026, Apache-2.0, dense, vision, thinking control), Gemma 4 (Apr 2026: E2B/E4B/12B/26B-A4B/31B, Apache-2.0, 140+ languages), GLM-4.7-Flash 30B-A3B (MIT). Q4 GGUF file sizes verified from Unsloth cards: Qwen3.5-4B 2.7 GB, Qwen3.5-9B 5.7 GB, Gemma-4-12B 7.1 GB, Qwen3.8-27B 16.5 GB, Gemma-4-31B 18.3 GB. **VERIFIED sizes; Chinese-quality ranking INFERRED**
- Redaction costs quality, but far less than un-redacted sending costs privacy: PrivateNLP 2025 measured roughly 1 point on a 10-point quality scale at 97–99 % entity masking; a Sep 2026 study across 11 benchmarks found **reversible pseudonymization clearly beats irreversible redaction**, and retrieval-heavy tasks suffer most. Placeholders that are consistent and mapped back locally (Presidio `InstanceCounterAnonymizer` + `deanonymize`, Rescriber's `[NAME1]` mapping, LiteLLM `output_parse_pii`) are the practical pattern. **VERIFIED**
- Recommended start: **Pattern A ("local core, pseudonymized and consent-gated cloud refine")**, built on Ollama or llama-server + LiteLLM (fallbacks + Presidio guardrail) + a local pseudonym map + a small consent UI. Grow toward **Pattern B (Minions/PAPILLON-style abstraction delegation)** for heavy analysis once the local pipeline is stable. Keep **Pattern C (learned cascade)** as a later cost/quality optimizer, not as a privacy mechanism.

---

## 2. Candidate architecture patterns for chatmaid

All three share the same local substrate: an ingest pipeline (WeChat/Discord/email → normalized messages in SQLite), a local embedding index over threads, and a local LLM server. They differ in what crosses the cloud boundary and who decides.

### Pattern A — Local core + pseudonymized, consent-gated cloud "refine" hop (recommended start)

```
 owner's workstation (trusted)                           | cloud (untrusted)
                                                         |
 chat logs ──> ingest/normalize ──> SQLite + vector idx  |
                                        │                |
                       retrieve thread + memory          |
                                        ▼                |
                            LOCAL LLM (Qwen3.x/Gemma 4)  |
                    summarize ▸ draft reply ▸ self-check |
                                        │                |
                       (owner clicks "improve with cloud")
                                        ▼                |
            pseudonymizer (Presidio+zh NER / privacy-filter)
            names→<PERSON_n>, phones→<PHONE_n>, ¥→<AMOUNT_n>
            local map {placeholder ⇄ original} (encrypted)
                                        │                |
                      consent dialog: shows EXACT payload |
                                        ▼                |
                        LiteLLM proxy ── fallbacks ──────┼──> Claude / GPT / Gemini / DeepSeek
                                        ▲                |      (sees: redacted draft +
                        re-hydrate placeholders          |       redacted excerpt/summary)
                                        │                |
                            final suggestion to owner    |
```

- Crosses the boundary: the local draft plus a *short, pseudonymized* excerpt or summary of the thread (owner-selectable: "summary only" / "last N messages redacted" / "nothing, cloud only sees my draft"). Never the raw log, never the placeholder map.
- Privacy: high, and legible — the owner sees the payload. Residual risk: quasi-identifiers and context (job, city, events) can still re-identify; CSA's Mar 2026 note reports LLM agents re-identifying pseudonymous profiles at 67 % recall / 90 % precision for $1–4 per target (VERIFIED as reported; not chatmaid-specific).
- Quality: local draft quality bounds the baseline; cloud refine recovers style/reasoning. Redaction loss is small for rewriting tasks (≈1/10 point, PrivateNLP 2025) but larger if the cloud must reason over facts that were replaced (amounts, dates) — keep amounts/dates unless the owner marks them sensitive.
- Cost: low — cloud is on demand only.
- Complexity: moderate — the hard parts are Chinese NER quality for names/nicknames and a robust re-hydration step (placeholders survive rewriting only if the prompt tells the cloud to keep them verbatim).

### Pattern B — Abstraction delegation (Minions / PAPILLON style)

```
 local (trusted)                                         | cloud (untrusted)
                                                         |
 raw thread ──> LOCAL LLM builds a "brief":              |
   {intent, relationship, constraints, tone, open        |
    questions, candidate reply}  — no verbatim quotes    |
                     │                                   |
                     ├── PAPILLON step: local model      |
                     │   writes a de-identified request  |
                     ▼                                   |
        supervisor loop (MinionS): cloud asks for        |
        specific facts; local answers from the raw log ──┼──> frontier model
        with abstractions only (judge blocks leaks)  <───┼─── returns guidance / rewritten text
                     │                                   |
 local model merges guidance into final reply            |
```

- Crosses the boundary: only local-model-generated abstractions and questions/answers; raw messages never leave. A local LLM judge (PAPILLON's leakage judge, or a small classifier) gates each outbound message.
- Privacy: highest achievable without confidential computing; but leakage is *probabilistic* (PAPILLON best pipeline: quality kept for 85.5 % of queries, leakage 7.5 %). The Secure Minions variant (attested H100 confidential-computing enclave, <1 % latency overhead) removes the cloud operator from the trust set, but for an individual it requires a provider exposing attested endpoints — availability for personal use is UNCERTAIN.
- Quality: MinionS recovered 97.9 % of GPT-4o accuracy at 5.7× lower cost on long-document QA (VERIFIED, Feb 2025 paper); the naive chat-back-and-forth "Minion" recovered 87 % at 30.4× lower cost. For *reply drafting* (not QA) numbers are unknown — UNCERTAIN.
- Cost: multiple cloud round-trips per task; still cheap because payloads are small.
- Complexity: high — needs a protocol, a judge, prompt optimization (DSPy MIPROv2/GEPA as in PAPILLON), and evaluation data (PUPA-style) built from the owner's own logs.

### Pattern C — Learned cascade / router with automatic escalation

```
 request ──> local model answers ──> confidence/uncertainty score (UCCI-style
             isotonic-calibrated token margins, or RouteLLM-style router)
                │ high conf                         │ low conf
                ▼                                   ▼
         return local draft            escalate to cloud (through A's pseudonymizer)
```

- Crosses the boundary: same as A, but *automatically* for the fraction of requests the router deems hard.
- Privacy: neutral-to-negative — automation removes the human gate; must be combined with A's redaction and a "never auto-send from sensitive threads" policy.
- Quality/cost: this is what routers optimize. RouteLLM: up to 85 % cost reduction at 95 % of GPT-4 quality (MT-Bench, VERIFIED). UCCI (May 2026): 31 % cost cut on a 4B→12B cascade with calibrated error probabilities (VERIFIED, code available). Hybrid LLM (ICLR 2024): up to 40 % fewer large-model calls at no quality drop.
- Complexity: needs labeled preference data for the owner's task; RouteLLM's public routers were trained on a 2024 model pair (GPT-4 vs Mixtral) and generalize but are not tuned for Chinese chat replies.

### Recommendation

Start with **Pattern A**. It is buildable in days from off-the-shelf parts, it is auditable (the owner sees the payload), and it produces the data (accept/reject decisions, redaction diffs) that Patterns B and C need later. Add B for "heavy analysis" jobs (e.g. "summarize my last month with this client") where sending even redacted excerpts is unacceptable. Add C only when the owner wants hands-off auto-reply and you have accept/reject data to calibrate confidence.

---

## 3. Research question 1 — Hybrid local/cloud LLM architectures

| System | Core idea | Optimizes | Maturity / code | Evidence |
|---|---|---|---|---|
| FrugalGPT (Chen, Zaharia, Zou, May 2023) | Prompt adaptation, LLM approximation (cache/fine-tune), LLM cascade with a scorer deciding whether to escalate | Cost (98 % reduction at GPT-4 level), quality (+4 % at equal cost) | Paper; reference code exists in the authors' repo (not fetched) | VERIFIED abstract |
| Hybrid LLM (Ding et al., ICLR 2024, Microsoft) | Router predicts query difficulty / quality gap between small and large model; tunable quality target | Cost with quality floor (up to 40 % fewer large calls) | Paper; no official package | VERIFIED abstract |
| RouteLLM (LMSYS) | Preference-data-trained routers: `mf` (matrix factorization, recommended), `sw_ranking`, `bert`, `causal_llm`; threshold calibrated to a target % of strong-model calls; OpenAI-compatible `Controller` | Cost/quality (85 % cheaper, 95 % GPT-4 on MT-Bench) | Apache-2.0, `pip install routellm[serve,eval]`; main repo activity slowed after Aug 2024 but issues still opened Feb 2026 | VERIFIED README; maintenance UNCERTAIN |
| LLMRouter (UIUC, Dec 2025) | Library with 16+ routers (KNN, SVM, MLP, MF, Elo, RouterDC, AutoMix, Hybrid LLM, GraphRouter, Router-R1, personalized routers); supports Ollama/vLLM/SGLang endpoints | Cost/quality research | MIT, `pip install llmrouter-lib`, paper arXiv 2608.06867 | VERIFIED README |
| Not Diamond | Hosted router API + custom router training on your data + prompt optimization | Quality per $ | Commercial; powered OpenRouter's original `openrouter/auto` | VERIFIED docs (feature list only); whether full prompts are sent to their servers — UNCERTAIN, assume yes |
| OpenRouter Auto Router (`openrouter/auto`) | Classifies the prompt in-flight into ~30 task types, ranks models by community spend over a trailing 7-day window, `cost_tier` = low/medium/high/xhigh/max (default ≈ low) | Convenience; not quality-benchmarked | Live; Not Diamond no longer mentioned in current docs | VERIFIED docs |
| Martian, Unify | Commercial routers/gateways | Cost/quality | Commercial; not examined in primary sources | UNCERTAIN |
| Minions / MinionS (Hazy Research, Feb 2025) | Local model holds the long private context; cloud model decomposes tasks into small sub-tasks executed locally in parallel, then aggregates | Cost (5.7×) with 97.9 % of GPT-4o accuracy; naive Minion 30.4× / 87 % | MIT, github.com/HazyResearch/minions, 1.4k stars, 776 commits; backends Ollama, MLX-LM, llama-cpp-python, Lemonade, Tokasaurus; 13 cloud providers | VERIFIED paper abstract + README |
| Secure Minions (May 2025) | Same protocol inside an attested NVIDIA H100 confidential-computing enclave; E2E encryption, replay protection, PFS; "<1 % added latency" | Privacy vs cloud operator | In the same repo (`secure` extra); needs a CC-capable provider | VERIFIED Ollama blog + README |
| OpenJarvis (Hazy, v1.0 May 2026) | "Local-first stack for personal AI": five primitives (Intelligence, Engine, Agents, Tools & Memory, Learning); per-query routing to keep easy queries local; "cloud at search time, local at inference time" (frontier model optimizes local specs offline) | Energy/cost ("Intelligence per Watt"; local models answered 88.7 % of single-turn queries; hybrid routing 60–80 % energy/cost reduction) | Apache-2.0, github.com/open-jarvis/OpenJarvis, 10.3k stars; engines Ollama/vLLM/SGLang/llama.cpp + 5 cloud engines | VERIFIED retrospective blog + README (routing mechanism details not in README) |
| PAPILLON (Columbia, NAACL 2025) | "Privacy-Conscious Delegation": trusted local model writes a de-identified request, untrusted cloud answers, local model aggregates; prompts jointly optimized with DSPy MIPROv2; PUPA benchmark of real PII-bearing chats | Privacy + quality (85.5 % quality kept, 7.5 % leakage) | MIT, github.com/Columbia-NLP-Lab/PAPILLON; DSPy tutorials `tutorials/papillon` and `rl_papillon`; local models Llama-3.2-1B/3B, Llama-3-8B, Mistral; cloud OpenAI | VERIFIED abstract + README |
| Need to Know (Jun 2026) | Contextual-integrity rewriting: forward a span only if the *task* needs it, not by PII type; DelegateCI-Bench (3,167 samples, 11 tasks); RL-trained rewriter +10.1 utility over on-device baselines | Privacy + utility | Paper; code not stated | VERIFIED abstract |
| P2Skill (Aug 2026) | Local SLM learns "skill prompts" for decomposition, PII-aware routing, paraphrase, reconstruction, refined from cloud execution failures; no fine-tuning or detectors | Privacy-preserved quality (1.69×–3.66× over baselines) | Paper; code not stated | VERIFIED abstract |
| PRISM (AAAI 2026) | Entity-level sensitivity profiling on edge, soft gating cloud/edge/collaborative, two-layer local DP, cloud produces a "semantic sketch" the edge model fills with private context | Privacy/utility/energy (40–50 % of baseline energy+latency) | Paper; code not stated | VERIFIED abstract |
| Rescriber (CHI 2025) | Browser extension: small local model flags PII in the prompt, user replaces with placeholders, mapping stored locally, replies re-hydrated | User-led data minimization | MIT; v1.0.1 uses `openai/privacy-filter` via Transformers.js in-browser (paper used Llama-3-8B) | VERIFIED README |
| Prεεmpt (NDSS 2026, Toronto) | Formal "prompt sanitizer": token-level protection (format-preserving encryption / metric-DP for numeric tokens) with provable guarantees | Privacy with utility | Paper; code UNCERTAIN | VERIFIED existence; details from abstract only |
| Apple Intelligence + Private Cloud Compute | On-device first; PCC only "for advanced features that need larger models"; five requirements: stateless computation, enforceable guarantees, no privileged runtime access, non-targetability, verifiable transparency. WWDC26: third-party apps can call `PrivateCloudComputeLanguageModel` (32K context, reasoning levels) and switch per "Dynamic Profile" between `SystemLanguageModel` and PCC; AFM 3 Cloud Pro runs on Google Cloud | Privacy with capability | Shipping; Apple-only | VERIFIED Apple security blog + WWDC26 session |
| Confidence/uncertainty cascades: UCCI (May 2026), Self-REF, CP-Router, "Is Escalation Worth It?" (May 2026), Signed Rescue Routing (Sep 2026) | Escalate when a calibrated local uncertainty exceeds a threshold; UCCI maps token margins to error probability via isotonic regression, picks threshold by cost minimization | Cost with quality floor (UCCI: −31 % cost, ECE 0.12→0.03, 4B/12B models) | UCCI code CC BY 4.0 (github.com/varunkotte6/ucci); others paper-only | VERIFIED UCCI abstract; others from search only |
| Draft-then-refine / speculative | (a) Speculative decoding across edge-cloud (FlexSpec, Jan 2026; early-exit spec decoding, 2025) — token-level, needs shared vocab/target, latency-focused; (b) "cascaded inference" where one model drafts and the other refines (surveyed in arXiv 2510.13890) | Latency (a), quality (b) | Research; (a) not practical across a public API since it needs logits | VERIFIED survey + FlexSpec abstract |
| Surveys | "Collaborating Small and Large LMs for Performance, Cost-effectiveness, Cloud-edge Privacy, Trustworthiness" (Oct 2025, arXiv 2510.13890); "Privacy-Preserving LLM Inference in Practice" (IACR ePrint 2026/105: TEE now, hybrid crypto next, FHE long-term) | — | — | VERIFIED abstracts |

Take-aways for chatmaid:

1. Cost-oriented routers (FrugalGPT, RouteLLM, Hybrid LLM, OpenRouter auto) decide *which* model sees the prompt; they do not change *what* the prompt contains. For a privacy-driven design they are second-order.
2. The privacy-oriented line (PAPILLON → Need to Know → P2Skill → PRISM) has converged on the same shape: the local model rewrites/decomposes, the cloud sees an abstraction, the local model reconstructs. This is Pattern B; the rewriting step alone is Pattern A's pseudonymizer.
3. Only confidential computing (Secure Minions, Apple PCC, TEEs per the IACR survey) removes the cloud operator from the trust set. For an individual with public API keys this is not available today except through Apple's ecosystem — UNCERTAIN whether any public provider offers attested endpoints to individuals.

---

## 4. Research question 2 — Practical building blocks (Sept 2026)

### 4.1 Local inference servers

| Server | State (Sept 2026) | Notes for chatmaid | Evidence |
|---|---|---|---|
| Ollama | v0.34.4 (2026-09-23): structured outputs on thinking models in one pass, thinking-level control advertised via API, MLX default on Apple Silicon, "cloud" model tags (e.g. `glm-5.3:cloud`) that run on Ollama's infrastructure through the same API | Easiest; **disable/avoid `:cloud` tags** — same CLI, but data leaves the machine. Default 5-minute unload | VERIFIED GitHub releases; cloud-tag behaviour from Ollama blog/secondary |
| llama.cpp `llama-server` | Router mode: start with `--model-dir`, load/unload/switch GGUF models on one endpoint without restart; OpenAI-compatible | Best raw control on Linux/NVIDIA; pairs with `llama-swap` if you want to mix engines | VERIFIED ggml-org HF blog (title) + multiple secondary write-ups |
| vLLM ≥ 0.19 / SGLang ≥ 0.5.10 | Recommended by Qwen for production serving of Qwen3.6/3.8; SGLang strongest for structured output and prefix reuse | Overkill for one user; consider only on 24 GB+ with FP8/AWQ weights | VERIFIED Qwen model cards |
| LM Studio 0.4.x | `llmster` headless daemon for Linux servers, OpenAI- and Anthropic-compatible endpoints, idle TTL eviction | Alternative to Ollama on a headless box | Secondary (LM Studio blog cited by search) — UNCERTAIN details |
| mlx-lm 0.31.x | Apple Silicon only | Not applicable to a Linux workstation | Secondary |

### 4.2 Unified gateways

| Gateway | Relevant features | Evidence |
|---|---|---|
| LiteLLM proxy | `router_settings.fallbacks`, `context_window_fallbacks`, `routing_strategy` (simple-shuffle, latency-based, per-group); Ollama (`ollama_chat/...`) and any OpenAI-compatible `api_base`; **Presidio guardrail** with `pii_entities_config` (MASK/BLOCK), per-entity score thresholds, `presidio_language`, and `output_parse_pii: true` to restore originals in the response | VERIFIED via Context7 docs |
| OpenRouter | 300+ models, one key; `openrouter/auto` as described above; convenient for "which cloud" flexibility but adds a third party in the path | VERIFIED docs |
| Portkey | Apache-2.0 gateway + managed platform; reported acquired by Palo Alto Networks (May 29 2026) | UNCERTAIN (secondary) |
| Bifrost (Maxim) | Go, Apache-2.0, automatic fallbacks, low overhead | UNCERTAIN (vendor blogs) |
| Kong AI Gateway | LLM plugins on Kong | Not needed for a single-user app |

For chatmaid a single LiteLLM proxy in front of both local and cloud models is enough; it also gives spend tracking and an audit log of every cloud call.

### 4.3 Routing libraries

- **semantic-router** (Aurelio, MIT): routes by embedding similarity to example utterances; fully local with `HuggingFaceEncoder`; good for the *sensitivity-tier classifier* (family / finance / health / work) rather than model choice. VERIFIED README.
- **RouteLLM**, **LLMRouter**, **UCCI** — see section 3.

### 4.4 Orchestration frameworks

| Framework | Hybrid-relevant capability | Evidence |
|---|---|---|
| Pydantic AI (v2.x) | `FallbackModel(model_a, model_b, ...)`, `fallback_on` accepts exception types and response handlers (semantic failure, truncated output); `OllamaModel`/`OllamaProvider(base_url=...)`; any OpenAI-compatible server via `OpenAIProvider(base_url=...)` | VERIFIED Context7 docs |
| DSPy | PAPILLON tutorials (`/tutorials/papillon/`, `/tutorials/rl_papillon/`); prompt optimizers (MIPROv2) to tune the redaction/aggregation prompts against a leakage+quality metric | VERIFIED existence of tutorials; content not fetched |
| LangGraph, Haystack, Vercel AI SDK, Claude Agent SDK | General agent/graph frameworks; all can call local OpenAI-compatible servers | Not verified this session (general knowledge) |
| OpenClaw | Agent runtime with `agents.defaults.model.primary` + ordered `fallbacks`; failover on auth, rate-limit, overload, timeout, billing, 404; escalating cooldowns (30 s → 1 min → 5 min); local backends via OpenAI-compatible endpoints; "hosted primary and local fallback" or the reverse | VERIFIED docs |

### 4.5 Memory / RAG over chat history

Embedding models (all open weights):

| Model | Size | Chinese/multilingual | Notes | Evidence |
|---|---|---|---|---|
| BAAI bge-m3 | 568M | 100+ languages; dense + sparse + multi-vector in one model | The Chinese open-source baseline; hybrid lexical+dense helps with names/nicknames | Secondary summaries (Qwen blog, BentoML) |
| Qwen3-Embedding 0.6B / 4B / 8B | 0.6–8B | Strong on C-MTEB; 8B ranked #1 MTEB multilingual (70.58, June 2025); instruction-aware | 0.6B is the sweet spot for a workstation; pairs with Qwen3-Reranker | Qwen blog (secondary summary) |
| jina-embeddings-v5-text-small / nano | 677M / 239M | 119+ languages, 32K context; small: 67.7 MMTEB; built on Qwen3-0.6B, distilled from Qwen3-Embedding-4B | Released 2026-02-18; license not verified | Jina announcement (secondary) |
| multilingual-e5 | — | — | Older; not re-verified this session | UNCERTAIN |

Rerankers: bge-reranker-v2-m3 (Apache-2.0, lightweight), Qwen3-Reranker 0.6B/4B/8B (pointwise generative), jina-reranker-v3 (0.6B listwise, 61.94 nDCG@10 BEIR per Jina; license UNCERTAIN). For chat threads, reranking matters less than good chunking by conversation turn and time.

Vector stores for a single-user local app: **sqlite-vec** v0.1.9 (2026-03-31, still 0.x; metadata columns; DiskANN/IVF in alpha) — keeps everything in the same SQLite file as the messages; **LanceDB** (embedded, Python SDK production-ready, multimodal); Qdrant/Chroma (server/embedded, used by Mem0); pgvector (used by Letta). Recommendation: SQLite + sqlite-vec (or FTS5 + sqlite-vec hybrid) unless the corpus exceeds a few million messages. VERIFIED release; recommendation INFERRED.

### 4.6 Local chat models for Chinese + English drafting (4B–32B)

Verified releases (model cards fetched):

| Family | Sizes relevant here | Released | License | Context | Languages | Notes |
|---|---|---|---|---|---|---|
| Qwen3.5 | 0.8B, 2B, 4B, 9B, 27B dense; 35B-A3B, 122B-A10B MoE | Feb 2026 | Apache-2.0 | 262K native | 201 languages | Vision-language, thinking on by default (`enable_thinking: false` to disable), Gated-DeltaNet hybrid |
| Qwen3.6 | 27B dense, 35B-A3B MoE | Apr 2026 | Apache-2.0 | 262K | zh/en emphasised | Successor to 3.5 at the same sizes |
| Qwen3.8 | 27B dense (Apache-2.0); Flash-Next 125B-A6B (Qwen Community 1.0 license) | Aug 2026 | see left | 262K → 1M | zh/en | 27B: `reasoning_effort` xhigh/medium/low, `preserve_thinking`; Flash-Next too large for ≤24 GB VRAM without heavy offload |
| Gemma 4 | E2B (5.1B total), E4B (8B total), 12B, 26B-A4B, 31B | Apr 2 2026 (12B Jun 2026) | Apache-2.0 | 128K (E-models) / 256K | 140+ languages, "35+ out of the box" | Multimodal; audio on E2B/E4B/12B |
| GLM-4.7-Flash | 30B-A3B | Jan 2026 (MarkTechPost) — HF card date read as Aug 2025; conflicting | MIT | 128K | zh/en | "strongest in 30B class" per Z.ai; GGUFs exist |
| GLM-4.6V-Flash | 9B | 2025–26 | — | — | zh/en | Multimodal; UNCERTAIN details |
| GLM-5 / 5.1 / 5.3-Flash | 744B-A40B / — / 320B-A18B | 2026 | MIT | 200–300K | zh/en | Too large for a workstation; cloud/API only |
| Mistral Small 4 | 119B-A6.5B | Mar 16 2026 | Apache-2.0 | 256K | — | Too large at Q4 (~65 GB) for ≤24 GB; UNCERTAIN details |
| DeepSeek V4 (Flash 284B-A13B, Pro 1.6T) | — | Apr 24 2026 | — | 1M | zh/en | API/cloud only; no 2026 small distills found (R1-0528-Qwen3-8B from 2025 is the last verified distill) |
| Hunyuan 0.5B/1.8B/4B/7B | dense | Jul 2025 | — | — | zh/en | Not updated in 2026 per search |
| MiniCPM 4.x / "MiniCPM5" | 8B | 2025 (repo now says MiniCPM5) | — | — | zh | UNCERTAIN; not fetched |
| Llama 4 | Scout 109B-A17B etc. | 2025 | Llama license | — | weak zh | No small dense 2026 model found; not recommended for Chinese |

Which is best for Chinese chat-reply drafting: not benchmarked in this session. INFERRED ranking from provenance and community reports: Qwen3.5/3.6/3.8 first (native Chinese lab, 201-language training, the de-facto default in every 2026 "local LLM" guide surveyed), GLM-4.7-Flash second (also Chinese-native, but MoE 31B total needs 16 GB+ or offload), Gemma 4 third (excellent multilingual, slightly less idiomatic Chinese — UNCERTAIN). Build a 50-thread private eval from the owner's own logs before committing.

VRAM tiers (GGUF file sizes VERIFIED from Unsloth model cards; "fits" INFERRED = file size + ~1.5–4 GB for KV cache/activations at 8–32K context; Qwen3.5+ hybrid attention keeps KV cache small):

| VRAM tier | Draft/reply model (primary) | Alternates | Also on GPU |
|---|---|---|---|
| 8 GB | Qwen3.5-4B Q6_K / Q8_0 (3.5 / 4.5 GB) | Qwen3.5-9B Q4_K_M (5.7 GB, ≤8K ctx, tight); Gemma 4 E4B Q4 (~4.7 GB, secondary) | Embeddings: Qwen3-Embedding-0.6B or bge-m3 on CPU |
| 16 GB | Qwen3.5-9B Q8_0 (9.5 GB) or Gemma 4 12B Q6_K (9.8 GB) | Qwen3.8-27B UD-Q3_K_XL (13.1 GB, quality hit); Gemma 4 26B-A4B UD-Q4_K_M (16.9 GB, experts partly on CPU); GLM-4.7-Flash Q4 (~17–18 GB est., offload) | small 0.6B embedder |
| 24 GB | Qwen3.8-27B UD-Q4_K_M (16.5 GB) or UD-Q5_K_M (19.8 GB) | Gemma 4 31B Q4_K_M (18.3 GB); Qwen3.6-35B-A3B Q4_K_M (22.1 GB, tight, fast tokens) | Embedder + reranker only at Q4 |
| 32–48 GB | Qwen3.8-27B Q8_0 (29 GB) | Gemma 4 31B Q6_K (25.2 GB); Qwen3.6-35B-A3B Q6_K (29.3 GB) | vLLM/SGLang with FP8 becomes reasonable |

Two models is the right shape (as Continue.dev does per role): a ≤4B model for always-on jobs (sensitivity classification, PII tagging, summarizing incoming messages) and the largest reply model the tier allows.

---

## 5. Research question 3 — Privacy patterns for the cloud hop

### 5.1 Entity pseudonymization with re-hydration

- **Microsoft Presidio** (data-privacy-stack/presidio): analyzer with spaCy/Stanza/transformers NLP engines; multi-language via `NlpEngineProvider` config (`{"lang_code": "zh", "model_name": "zh_core_web_trf"}` by analogy to the documented `es` example) and `supported_languages=[...]`. spaCy's `zh_core_web_trf` (bert-base-chinese, OntoNotes 5) tags PERSON, ORG, GPE, LOC, DATE, MONEY, CARDINAL, etc., so Chinese personal names are covered; WeChat nicknames, kinship terms ("老王", "小李", "妈") and pinyin handles are NOT — a custom recognizer with a contact-list deny-list from the owner's address book is required (INFERRED). Phone numbers use the `phonenumbers` library; Chinese regions need `supported_regions` configured (INFERRED from docs on country-specific recognizers).
- Presidio's `pseudonymization.ipynb` sample defines `InstanceCounterAnonymizer` producing `<PERSON_0>`, `<PERSON_1>` consistently per unique value with an `entity_mapping` dict, and a matching deanonymizer; the "openai anonymization and deanonymization best practices" sample keeps `entity_mappings` per session and re-hydrates the LLM output. Note the sample warns it is not thread-safe. VERIFIED Context7.
- **LiteLLM Presidio guardrail** wires this into the proxy: `mode: pre_call`, `pii_entities_config: {PERSON: MASK, PHONE_NUMBER: MASK}`, `presidio_language`, thresholds, and `output_parse_pii: true` to restore originals in the response. VERIFIED. Caveat: `presidio_language` is a single value per guardrail; bilingual messages may need two guardrails or a custom hook (INFERRED).
- **openai/privacy-filter** (Apr 22 2026, Apache-2.0): 1.5B-total/50M-active MoE token classifier, 8 categories (names, addresses, emails, phones, URLs, dates, account numbers, secrets), 128K-token single pass, 96 % F1 claimed by OpenAI; "primarily English, selected multilingual robustness evaluation" — Chinese coverage UNCERTAIN, must be tested. Runs on CPU. Rescriber 1.0.1 uses it in-browser. VERIFIED model card + announcement.
- **Consistency across sessions**: if "张伟" is `<PERSON_3>` today and `<PERSON_9>` tomorrow, the cloud loses continuity but gains unlinkability. For reply drafting, per-thread stable pseudonyms (a persistent local map keyed by contact id) are the better trade (INFERRED; CAMP's session-level PII registry is the research analogue).

### 5.2 How much quality is lost

| Study | Setting | Finding | Evidence |
|---|---|---|---|
| Pasch & Cha, PrivateNLP @ NAACL 2025 | Personal writing tasks (intros, cover letters, emails); masking / contextual masking / pseudonymization | 97–99 % entity masking; response quality dropped ≈1 point on a 10-point scale; Llama-3.3-70B best with plain masking, GPT-4o benefited from contextual cues | VERIFIED via ACL Anthology listing + summary |
| Deußer et al., Sep 2026 (arXiv 2609.11335) | 5 models × 11 benchmarks; pseudonymization vs redaction vs "explicit anonymization prompt" | Anonymization generally degrades performance; stronger models lose more; retrieval-focused tasks (RGB) "catastrophic decline"; **reversible pseudonymization significantly outperforms irreversible redaction**; telling the model it is anonymized does not help | VERIFIED abstract |
| PAPILLON, NAACL 2025 | Real PII-bearing chat prompts (PUPA) | Best pipeline keeps quality on 85.5 % of queries with 7.5 % leakage; DSPy optimization was needed to get there | VERIFIED |
| RAT-Bench, Feb 2026 | Re-identification risk of NER tools (Presidio, Anthropic's PII purifier) vs LLM anonymizers | Even top tools imperfect on non-standard formats and indirect identifiers; LLM-based anonymizers give better privacy-utility at higher cost; strong cross-language performance | VERIFIED abstract |
| AURA, May 2026 | Interview transcripts vs agentic (web-search) re-identification | Mask-then-reconstruct with adversarial checks improves the privacy-utility frontier | VERIFIED abstract |
| CAMP, Apr 2026 | Multi-turn conversations | Per-turn masking misses profiles that accumulate across turns; session PII registry + co-occurrence graph + cumulative exposure score + retroactive masking | VERIFIED abstract (synthetic data only) |
| CSA research note, Mar 2026 | LLM agents de-anonymizing pseudonymous profiles | 67 % recall at 90 % precision, $1–4 per target | VERIFIED as reported by CSA |

Implication: pseudonymize (reversible), do not redact; keep numbers/dates unless flagged; expect some cloud refinements to be worse than local because context was stripped — show both drafts to the owner.

### 5.3 Other patterns

- **Summarization-before-send**: the local model writes a task brief; the cloud never sees verbatim text. This is PAPILLON's "Prompt Creator" and Minions' decomposition. Quality risk: the brief omits what the cloud needed (Need to Know frames this as contextual integrity: forward what the *task* requires). Practical mitigation: let the cloud ask up to N clarifying questions answered by the local model (Minion loop).
- **Per-request consent UI**: Rescriber is the reference UX (highlight detected PII, one-click replace, show the final payload, local mapping, automatic re-hydration). Chatmaid's equivalent: a diff view "what the cloud will see" with per-entity toggles, and a per-thread policy (never / ask / auto-redacted).
- **Tiered sensitivity classification**: classify threads/contacts once (family, finance, health, work, casual) with semantic-router or the small local model; a policy table decides allowed cloud modes per tier. Home Assistant's "prefer handling commands locally" is the simplest deployed form of tiering (local intent matcher first, LLM only for the rest). VERIFIED HA docs.
- **Audit logging**: the LiteLLM proxy logs every cloud call (model, tokens, cost); store alongside the pseudonymized payload hash and the consent decision, locally. Apple PCC's "only pre-specified structured logs leave the node" is the mirror image on the server side. INFERRED design.
- **Keep the cloud from seeing raw logs at all**: Minions-style (cloud sees local abstractions; raw context stays on device) — but note Minions optimizes cost, not privacy; the local model's outputs can still quote raw text. Combine with a leakage judge (PAPILLON) or the pseudonymizer. Secure Minions / TEEs are the only way to hide content from the cloud operator itself; the IACR 2026 survey ranks TEEs as the only immediately deployable option, hybrid crypto next, FHE long-term.
- **Threat to keep in view**: WeChat is mostly Chinese and often uses nicknames/kinship terms and mixed scripts; NER models trained on OntoNotes news text will miss many; plan for a contact-book-driven deny-list plus an LLM second pass (RAT-Bench: LLM anonymizers are more robust on non-standard identifiers).

---

## 6. Research question 4 — Open-source projects with hybrid local+cloud designs

| Project | How local vs cloud is decided | Worth borrowing | Evidence |
|---|---|---|---|
| Khoj | User selects a chat model (Ollama local or cloud) in settings; embeddings can be generated locally; "100 % offline" mode | Self-hosted "second brain" UX; agent/automation model | Docs summary (search); privacy page not fetched — partially VERIFIED |
| Jan | Desktop app; llama.cpp engine for local models plus remote provider connectors; MCP; local API server; selection per model/thread by user | Clean desktop UX; nothing automatic | UNCERTAIN (docs hub only fetched) |
| Open WebUI | "Functions" (Pipes, Filters with inlet/stream/outlet hooks, Actions) run in-process; community filters re-route to vision models or fall back on error; "Pipelines" are now legacy | Filter-hook pattern = where a pseudonymizer or router lives | VERIFIED docs (Functions/Filters pages) |
| PrivateGPT (Zylon) | Relaunched Jun 2026 as PrivateGPT 1.0, an API layer (RAG, tools, MCP, skills) for any OpenAI-compatible server; local by configuration | Ingestion/citation API design | VERIFIED GitHub README description + discussion title |
| Home Assistant Assist | Toggle "Prefer handling commands locally": built-in intent matcher first, LLM agent only for what it cannot answer; external agents only use sentence triggers when the toggle is on | Simplest deployed local-first gate | VERIFIED docs |
| Continue.dev | `config.yaml` assigns models to roles (chat, edit, apply, autocomplete, embed, rerank); local Ollama `AUTODETECT`; typical 2026 setup is a ~1.5B autocomplete model + 7B–32B chat model | Role-based model assignment | Docs (roles list) via search summary |
| OpenClaw | Ordered `model.fallbacks`; auth-profile rotation then model fallback; triggers on auth/rate-limit/overload/timeout/billing/404; not on context overflow or refusals; escalating cooldowns; local backends via OpenAI-compatible endpoints; "hosted primary + local fallback" or reverse | Error taxonomy for failover; cooldown persistence in SQLite | VERIFIED docs |
| LibreChat | `librechat.yaml` custom endpoints (Ollama + clouds); user picks endpoint per conversation; no automatic routing | Multi-endpoint config schema | VERIFIED docs |
| AnythingLLM | Per-workspace LLM/embedder choice; fully local with Ollama/LM Studio | Workspace = sensitivity-tier idea | Secondary only — UNCERTAIN |
| Letta (MemGPT) | Memory blocks pinned in context (editable via tools), shared blocks, archival memory (vector, pgvector); model-agnostic | Tiered memory design for long chat histories | VERIFIED docs (overview page); tier mechanics not fetched |
| Mem0 | `llm`, `embedder`, `vector_store` each configurable; fully local recipe = Ollama LLM + `nomic-embed-text` + Qdrant (`embedding_model_dims: 768`); extracts "facts" with the LLM | Fact-extraction memory over chats; keep the extractor local | VERIFIED cookbook |
| OpenJarvis (Hazy) | Local by default; per-query routing to cloud "only when truly necessary"; frontier model used offline to optimize local agent specs | Closest philosophical match; routing internals not documented in README | VERIFIED README + blog |
| Minions / PAPILLON | Protocol-level: cloud sees only local abstractions / de-identified requests | The actual privacy mechanisms | VERIFIED |

Pattern across the ecosystem: routing is static (config/roles), manual (per-conversation), or failure-driven (fallback chains). Content-aware privacy routing exists only in research code. Chatmaid should reuse the plumbing (LiteLLM/OpenClaw-style fallbacks, Open WebUI-style filter hooks, Continue-style roles) and add the missing content-aware layer.

---

## 7. Questions to ask the owner

Hardware and latency
1. Exact GPU (model, VRAM) and system RAM; is CPU offload of MoE experts acceptable (slower but lets 26B–35B MoE run on 16 GB)?
2. Acceptable latency for a draft suggestion (2 s? 10 s?) and for a cloud refine (30 s?). Should drafting stream?
3. Is the workstation always on, and may chatmaid keep a model resident (VRAM pinned)?

Cloud policy
4. Which cloud providers are acceptable, and in which jurisdictions (US providers vs Chinese providers such as DeepSeek/Qwen API for Chinese text)? Any provider excluded outright?
5. Is a third-party gateway (OpenRouter) acceptable, or must calls go direct to each provider?
6. Monthly cloud budget ceiling?

Privacy model
7. Redaction, consent, or both? Options: (a) always pseudonymize and still ask; (b) pseudonymize and auto-send for low-tier threads; (c) never send excerpts, only local briefs (Pattern B).
8. What counts as sensitive: contact names? nicknames? amounts? locations? health? employer names? Should the same contact keep the same pseudonym across sessions?
9. Are there contacts/threads that must never touch the cloud (family, finance, legal)? Provide the list or the rule.
10. Is an on-screen "what the cloud will see" diff required for every call, or only for new threads / high tiers?
11. Audit requirements: keep a local log of every cloud payload? Retention period? Encryption at rest for the pseudonym map?

Data and quality
12. How are logs obtained (WeChat DB decryption, Discord export, IMAP)? Formats and update frequency? (Out of scope here, but it determines ingestion.)
13. Volume: number of contacts, messages per day, years of history?
14. Language split: share of Chinese vs English threads; any dialect/pinyin/mixed-script habits?
15. Will the owner label ~50–100 threads with "good reply" examples so we can evaluate local models and later calibrate a confidence router?

Product scope
16. Suggestion only, or auto-reply with approval? For auto-reply, per-contact opt-in?
17. Memory: should chatmaid maintain per-contact profiles/facts (Mem0/Letta style)? Where may those be stored?
18. Preferred stack: Python (LiteLLM/Pydantic AI/DSPy) vs TypeScript (Vercel AI SDK); any existing tooling (Open WebUI, OpenClaw) to integrate with?
19. Model licenses: Apache-2.0/MIT only, or are custom licenses (Qwen Community 1.0, Gemma terms) fine?
20. Should Ollama's cloud model tags be blocked at the network level to prevent accidental use?

---

## 8. References (all accessed 2026-09-27)

Hybrid / delegation research
- Minions paper: https://arxiv.org/abs/2502.15964 — VERIFIED
- Minions repo: https://github.com/HazyResearch/minions — VERIFIED
- Hazy Research retrospective "From Minions to OpenJarvis" (2026-05-15): https://hazyresearch.stanford.edu/blog/2026-05-15-minions-to-openjarvis-retrospective — VERIFIED
- Secure Minions (Ollama blog): https://ollama.com/blog/secureminions — VERIFIED
- Hazy "Mind the Trust Gap": https://hazyresearch.stanford.edu/blog/2025-05-12-security — not fetched
- OpenJarvis: https://github.com/open-jarvis/OpenJarvis — VERIFIED
- PAPILLON paper: https://arxiv.org/abs/2410.17127 — VERIFIED; repo: https://github.com/Columbia-NLP-Lab/PAPILLON — VERIFIED; DSPy tutorials: https://dspy.ai/tutorials/papillon/ and https://dspy.ai/tutorials/rl_papillon/ — existence only
- Need to Know (contextual-integrity rewriting): https://arxiv.org/abs/2606.04067 — VERIFIED
- P2Skill: https://arxiv.org/abs/2608.14094 — VERIFIED
- PRISM: https://arxiv.org/abs/2511.22788 — VERIFIED
- FrugalGPT: https://arxiv.org/abs/2305.05176 — VERIFIED
- Hybrid LLM (ICLR 2024): https://arxiv.org/abs/2404.14618 — VERIFIED
- RouteLLM: https://github.com/lm-sys/RouteLLM — VERIFIED
- LLMRouter: https://github.com/ulab-uiuc/LLMRouter — VERIFIED
- UCCI: https://arxiv.org/abs/2605.18796 — VERIFIED; code https://github.com/varunkotte6/ucci
- Cluster, Route, Escalate: https://arxiv.org/html/2606.27457 ; Is Escalation Worth It?: https://arxiv.org/pdf/2605.06350 ; Signed Rescue Routing: https://arxiv.org/pdf/2609.07786 — search-level only
- Survey SLM–LLM collaboration: https://arxiv.org/abs/2510.13890 — VERIFIED
- Survey privacy-preserving inference (IACR 2026/105): https://eprint.iacr.org/2026/105 — VERIFIED
- FlexSpec: https://arxiv.org/abs/2601.00644 — VERIFIED; speculative edge-cloud with early exits: https://arxiv.org/pdf/2505.21594 — not fetched
- OpenRouter Auto Router: https://openrouter.ai/docs/guides/routing/routers/auto-router — VERIFIED
- Not Diamond docs: https://docs.notdiamond.ai/ — VERIFIED (overview only)
- Apple PCC: https://security.apple.com/blog/private-cloud-compute/ — VERIFIED; WWDC26 Foundation Models session: https://developer.apple.com/videos/play/wwdc2026/241/ — VERIFIED

Privacy / redaction
- Presidio docs (via Context7 /data-privacy-stack/presidio): languages, customizing NLP models, `pseudonymization.ipynb`, OpenAI anonymization best-practices sample — VERIFIED
- spaCy zh models: https://huggingface.co/spacy/zh_core_web_trf — labels from search summary
- LiteLLM Presidio guardrail: https://docs.litellm.ai/docs/proxy/guardrails/pii_masking_v2 and https://docs.litellm.ai/docs/tutorials/presidio_pii_masking — VERIFIED via Context7
- OpenAI Privacy Filter: https://huggingface.co/openai/privacy-filter — VERIFIED; announcement https://openai.com/index/introducing-openai-privacy-filter/ — via search
- Rescriber paper: https://arxiv.org/abs/2410.11876 ; repo: https://github.com/PEACH-Research-Lab/Rescriber — VERIFIED
- Prεεmpt (NDSS 2026): https://www.ndss-symposium.org/ndss-paper/pr%CE%B5%CE%B5mpt-sanitizing-sensitive-prompts-for-llms/ — search-level
- Pasch & Cha, PrivateNLP 2025: https://aclanthology.org/2025.privatenlp-main.3/ — VERIFIED listing
- On the Impact of Anonymization on LLM Performance: https://arxiv.org/abs/2609.11335 — VERIFIED
- RAT-Bench: https://arxiv.org/abs/2602.12806 — VERIFIED
- AURA: https://arxiv.org/abs/2605.30848 — VERIFIED
- CAMP: https://arxiv.org/abs/2604.16521 — VERIFIED
- CSA note on LLM-assisted deanonymization: https://labs.cloudsecurityalliance.org/research/csa-research-note-llm-deanonymization-privacy-20260307-csa-s/ — search-level

Models and inference
- Qwen3.5-27B: https://huggingface.co/Qwen/Qwen3.5-27B ; Qwen3.5-9B: https://huggingface.co/Qwen/Qwen3.5-9B ; Qwen3.6-35B-A3B: https://huggingface.co/Qwen/Qwen3.6-35B-A3B ; Qwen3.8-27B: https://huggingface.co/Qwen/Qwen3.8-27B ; Qwen3.8-Flash-Next: https://huggingface.co/Qwen/Qwen3.8-Flash-Next — all VERIFIED
- Unsloth GGUF cards (sizes): https://huggingface.co/unsloth/Qwen3.5-4B-GGUF , https://huggingface.co/unsloth/Qwen3.5-9B-GGUF , https://huggingface.co/unsloth/Qwen3.8-27B-GGUF , https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF , https://huggingface.co/unsloth/gemma-4-12B-it-GGUF , https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF , https://huggingface.co/unsloth/gemma-4-31B-it-GGUF — VERIFIED
- Gemma 4 model card: https://ai.google.dev/gemma/docs/core/model_card_4 ; announcement: https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/ — VERIFIED
- GLM-4.7-Flash: https://huggingface.co/zai-org/GLM-4.7-Flash — VERIFIED (date conflict noted); GLM-5.3-Flash: https://huggingface.co/zai-org/GLM-5.3-Flash — VERIFIED
- Mistral Small 4, DeepSeek V4, Hunyuan, MiniCPM — search-level only (see section 4.6)
- Qwen3-Embedding blog: https://qwen.ai/blog?id=qwen3-embedding ; jina-embeddings-v5-text: https://jina.ai/news/jina-embeddings-v5-text-distilling-4b-quality-into-sub-1b-multilingual-embeddings/ ; bge-reranker-v2-m3: https://huggingface.co/BAAI/bge-reranker-v2-m3 ; Qwen3-Reranker: https://huggingface.co/Qwen/Qwen3-Reranker-0.6B — search-level
- Ollama releases: https://github.com/ollama/ollama/releases — VERIFIED (v0.34.4, 2026-09-23)
- llama.cpp model management / router mode: https://huggingface.co/blog/ggml-org/model-management-in-llamacpp — search-level; write-up https://www.glukhov.org/llm-hosting/llama-cpp/llama-server-router-mode/
- LM Studio 0.4.0 (llmster): https://lmstudio.ai/blog/0.4.0 — search-level
- sqlite-vec releases: https://github.com/asg017/sqlite-vec/releases — VERIFIED (v0.1.9, 2026-03-31)
- LiteLLM docs (via Context7 /websites/litellm_ai): reliability/fallbacks, routing groups, providers/ollama — VERIFIED
- Pydantic AI docs (via Context7 /websites/pydantic_dev_ai): FallbackModel, OllamaProvider — VERIFIED
- semantic-router: https://github.com/aurelio-labs/semantic-router — VERIFIED

Open-source assistants
- Khoj: https://github.com/khoj-ai/khoj , https://docs.khoj.dev/privacy/ — search-level
- Jan: https://jan.ai/docs — hub page only
- Open WebUI Functions/Filters: https://docs.openwebui.com/features/extensibility/plugin/functions/filter/ , Pipelines (legacy): https://docs.openwebui.com/features/extensibility/pipelines/ — docs via search summary
- PrivateGPT: https://github.com/zylon-ai/private-gpt — VERIFIED description
- Home Assistant: https://www.home-assistant.io/integrations/conversation/ and https://www.home-assistant.io/voice_control/assist_create_open_ai_personality/ — VERIFIED
- Continue.dev models/roles: https://docs.continue.dev/customize/models — search-level summary of docs
- OpenClaw model failover: https://docs.openclaw.ai/concepts/model-failover ; local models: https://docs.openclaw.ai/gateway/local-models — VERIFIED (failover page)
- LibreChat endpoints: https://www.librechat.ai/docs/configuration/librechat_yaml/ai_endpoints — VERIFIED
- Letta memory: https://docs.letta.com/guides/agents/memory — VERIFIED (overview)
- Mem0 local cookbook: https://docs.mem0.ai/cookbooks/companions/local-companion-ollama — VERIFIED
