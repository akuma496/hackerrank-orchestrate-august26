# PLAN — Message Notification Router

Agreed design, frozen before implementation. Changes to this document after sign-off require explicit discussion.

Deadline: freeze at ~04:30 local (13h from ~15:30 2026-08-01). Language: Python.

**Models (split by step, confirmed):**
- Transcription (OCR + ASR) → `gemini-2.5-flash` (only option with native audio input)
- Decision (action/type/reason/evidence) → `claude-haiku-4-5`, temp 0
- Conflict-escalation (certainty engine "conflict" state only) → `claude-sonnet-5`, temp 0 — same model family as the base decision call, not Gemini pro
- Tier-2 judge (advisory, pre-freeze) → `claude-haiku-4-5`, temp 0

Env vars: `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` (both only needed when cache is cold).

---

## 1. Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │              CONTEXT STORE                  │
                    │  indexed dicts from dataset/*.csv           │
                    │  (pandas at load time only)                 │
                    └──────────────────┬──────────────────────────┘
                                       │
 media files ──► transcribe (Gemini,   │
                 pure, cached) ──► transcripts + extracted entities
                                       │
                                       ▼
              for each message:  decide(message, context)  [PURE FUNCTION]
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
              feature extraction  evidence retrieval  hard rules
              (signal families)   (ranked, max 5)     (abstain-by-default)
                    │                  │                  │
                    └────────┬─────────┴──────────────────┘
                             ▼
                   hard rule fired? ──yes──► verdict (conf 0.88–0.91)
                             │no
                             ▼
                   LLM decision (flash, temp 0, cached, structured JSON)
                             │
                             ▼
                   certainty engine: signal-family convergence
                     • agree        → 0.88–0.91
                     • mostly agree → 0.83–0.87
                     • conflict     → 0.78–0.82  (escalation seam: pro-tier)
                     • uninformed   → 0.78 floor, safe default
                             ▼
                   policy post-pass: DND demotion, personal-mute guard,
                   notify-tiebreak (never miss important)
                             ▼
                        output.csv
                             ▼
              Tier-1 validator (code, every build)
              Tier-2 LLM judge (cached, advisory, pre-freeze only)
```

### Core principles (agreed)

1. **Rules abstain by default.** Only high-precision rules issue verdicts; everything else falls to the LLM *with deterministic features attached*.
2. **Determinism via caching**, not via avoiding the LLM. Every Gemini response (OCR/ASR/decision/judge) is cached to repo keyed by content hash. Rerun = byte-identical output, zero API calls, grader-runnable without a key.
3. **Confidence is computed, never self-reported.** Deterministic buckets from signal convergence.
4. **Hard line: never miss an important message.** Tiebreaks favor notify over digest (when urgency/trust signals exist) and digest over mute (unless a safety rule fired). Personal senders are never rule-muted on content alone.
5. **Transcription is pure.** No user context into OCR/ASR; generic code-mixing language hint only.
6. **Cost discipline as architecture**: content-hash dedup (cross-user), tiered funnel (rules → cache → flash → rare escalation), token-lean prompts, per-run cost manifest.

---

## 2. Module layout

```
code/
  main.py                    # entry: python code/main.py  (writes dataset/output.csv)
  router/
    config.py                # paths, model ids, env var names, constants, buckets
    loaders.py               # CSV loading (proper csv/pandas, UTF-8), integrity checks, indexes
    features.py              # signal-family feature extraction per message
    rules.py                 # hard rules (verdicts) + soft signal votes
    evidence.py              # deterministic evidence retrieval + ranking (max 5)
    transcribe.py            # Gemini OCR/ASR, content-hash cached; entity extraction
    llm.py                   # Claude decision call (haiku base / sonnet escalation), structured JSON, content-hash cached
    certainty.py             # convergence scoring → confidence bucket; conflict detection
    decide.py                # the pure decision function; policy post-pass
    output.py                # output.csv writer (exact columns, formatting)
    validate.py              # Tier-1 deterministic validator
  evaluation/
    main.py                  # eval harness on sample_messages.csv (10/20 stratified split)
    judge.py                 # Tier-2 LLM judge (advisory, cached)
  cache/                     # committed to repo
    transcripts.json
    llm_decisions.json
    judge_flags.json
  prompts/
    transcribe.md
    decide.md
    judge.md
  README.md                  # setup + run instructions for graders
```

Env: `GEMINI_API_KEY` (transcription), `ANTHROPIC_API_KEY` (decision/judge) — both only needed when cache is cold.

---

## 3. Signal families

| Family | Inputs | Vote semantics |
|---|---|---|
| **Trust** | business verified flag, official vs used domain, domain age, account age, group_type (scam-prone: investment_tips/finance_help/marketplace; trusted: family/school_group/society/coworker), sender role (admin) | mute-ward on trust breaks; notify-ward on high trust |
| **Relationship** | user_business_history (orders/opt-ins/opt-outs, activity_180d), sender↔user reply history, group_members (role, muted, engagement) | notify-ward on active relationship; mute-ward on opt-out/absence |
| **Behavior** | message_events on similar past messages: opened/replied fast vs dismissed/muted/reported | notify-ward if user historically engages this pattern; mute-ward if dismisses |
| **Content** | text or transcript: urgency markers, @mentions (`@u_XXX` regex), OTP/fee/link asks, extracted URLs/amounts/brands, promo language, greetings, forwarded_count | direction per pattern |
| **LLM read** | full composed context | its classification is one vote among five |

Each family emits `notify | digest | mute | abstain` + strength. Certainty = convergence (see §5).

---

## 4. Rule inventory

### Hard rules (verdict + short-circuit; high precision only)

| ID | Condition | Verdict |
|---|---|---|
| HR1 | Business message, `domain_used_by_sender != official_domain` | mute / scam |
| HR2 | Payment/OTP/fee/link ask + (unverified sender OR domain age < 30d OR no user relationship) | mute / scam |
| HR3 | Promo content + user opted out (`allows_promotions=0` with `promotions_opted_out_at` set) | mute / promotion |
| HR4 | User previously **reported** this sender/business (events/history) | mute / (scam or spam by content) |
| HR5 | `@<recipient_user_id>` mention in group message from non-risky sender | notify / (type by content) |
| HR6 | Group admin in trusted group_type + time-critical operational content | notify / urgent-or-event |
| HR7 | Near-duplicate (token-overlap threshold) of ≥2 history messages this user dismissed/ignored, non-personal sender | mute / (promotion or spam) |

### Guards & policy post-pass (applied to every decision, incl. LLM's)

| ID | Policy |
|---|---|
| G1 | **Personal-sender guard**: `conversation_type=personal` → no rule-mute on content alone; mute requires behavioral evidence (prior report/dismiss of this sender). Floor = digest. |
| G2 | **DND demotion**: message inside recipient's DND window → notify demoted to digest, **unless** type is `urgent` or personal+critical. |
| G3 | **Notify tiebreak**: torn notify/digest + any urgency or trust signal → notify. |
| G4 | **Mute tiebreak**: torn digest/mute + no safety-rule fire → digest. |
| G5 | **Type precedence** (first match): scam > spam > urgent > payment > event > business_update > promotion > forward > greeting > personal > unknown. Forwarding is a risk **amplifier** (feeds content family), not a preferred label. |

---

## 5. Certainty engine → confidence

| State | Definition | Confidence | Action |
|---|---|---|---|
| Certain | all non-abstaining families agree | 0.88–0.91 | ship |
| Confident | majority agree, weak dissent | 0.83–0.87 | ship |
| Conflict | ≥2 families pull hard in opposite directions | 0.78–0.82 | **escalation seam** (`claude-sonnet-5`; designed, built only if time allows) |
| Uninformed | all/most abstain | 0.78 | safe default (G1–G4); type often `unknown` |

Confidence within a band is set deterministically (fixed offsets by sub-condition, e.g. hard-rule verdicts sit at the top of their band). No randomness anywhere.

---

## 6. Evidence retrieval (deterministic, max 5)

Candidate pool: `message_history.csv` rows sharing lineage with the message —
same sender > same business/domain-pattern > same group + similar content (token overlap) > cross-user same scam/promo pattern.

Rank score = lineage weight × content similarity × event-outcome weight (replied-fast > opened > dismissed > reported — direction depends on what the evidence is proving). Top 5, strongest first, semicolon-joined; `none` if pool is empty. LLM may only cite from the candidate pool (validator enforces).

---

## 7. Prompts (files in `code/prompts/`)

**transcribe.md** — input: media file only + note "may contain Hindi-English code-mixed content". Output JSON: `{transcript, entities: {urls[], amounts[], brands[], dates[], otp_or_fee_ask: bool}}`. No user context.

**decide.md** — system: role, allowed actions/types, type precedence, policies G1–G5, JSON schema. User content: message fields + transcript/entities (if media) + signal-family features (compact key:value lines, no raw table dumps) + ranked evidence candidates with event outcomes. Output JSON: `{action, message_type, reason (one sentence, sample-style), evidence_ids (subset of candidates), conflict_note}`. Runs on `claude-haiku-4-5` (base) or `claude-sonnet-5` (conflict-escalation), temp 0.

**judge.md** — input: message + final row + cited evidence. Question: does the reason justify the action, and does the evidence support the reason? Output: `{coherent: bool, flag_reason}`. Runs on `claude-haiku-4-5`, temp 0. Advisory only; flags resolved by the user pre-freeze; unresolved flags ship with the deterministic decision.

---

## 8. Evaluation

- **Split**: `sample_messages.csv` (30 rows) → 10 train / 20 test, stratified by action and covering rare types. Test frozen: aggregate score only, no row-level peeking.
- **Metrics**: action accuracy; message_type accuracy; evidence overlap (any-overlap + Jaccard vs sample evidence); confidence-in-band check; per-class breakdown; **headline: false-mute/false-digest of sample-notify rows** (critical-miss count — the hard line).
- **Tier-1 validator** (every build): exact columns/order; one row per messages.csv id; allowed values; confidence ∈ [0,1] with 2-decimal format; evidence ids exist in history AND share lineage; policy invariants (scam/spam ⇒ mute; DND demotion applied; personal never rule-muted).
- **Golden determinism test**: two consecutive runs from cache → byte-identical output.csv.
- **Cost manifest** per run: API calls, tokens, cache hit rate, LLM fall-through rate.
- **Iteration protocol**: change → eval on train → single aggregate test check → accept/reject. 2–4 cycles max, documented in log.

---

## 9. Build order (13h, freeze ~04:30)

| # | Hours | Phase | Exit criterion |
|---|---|---|---|
| 0 | 0.5 | Plan sign-off | user approves this doc |
| 1 | 1.5 | loaders + features | 110 feature bundles print clean |
| 2 | 1.5 | transcribe + cache | 33 media files cached (20 img, 13 audio) |
| 3 | 2.0 | rules + certainty + evidence | rule-decided subset labeled; rest queued |
| 4 | 2.0 | LLM path + decide + output | full output.csv end-to-end |
| 5 | 1.5 | eval harness + validator | baseline score known |
| 6 | 2.0 | measured iteration | plateau or budget out |
| 7 | 1.0 | judge sweep + flag resolution + golden rerun | clean validator, byte-identical |
| 8 | 1.0 | packaging: README, code.zip, output.csv, log check | submittable |
| — | 0.5 | buffer | — |

Skeleton end-to-end by hour ~5.5; after midnight only iteration and packaging.

## 10. Known risks

- **ASR quality** on code-mixed audio → Gemini multimodal is the mitigation; transcripts eyeballed during phase 2.
- **Rate limits** (single key) → sequential calls + exponential backoff; volume is small (≤ ~150 calls cold) — not a concern at hackathon scale.
- **Overfitting to 30 samples** → split discipline + principle-first rules.
- **Time** → escalation seam and judge are the designated cut-lines if behind schedule; core funnel is never cut.

### Scaling note (README-level, not built here)

At vendor scale with a single shared API key across all users/devices, **moving the deterministic engine on-device does not relieve the rate-limit problem** — the limit lives on the key/account tier, not on where compute happens. Decentralizing only removes the cheap 95% (rules, features, evidence retrieval); every LLM/transcription call still funnels through the same shared ceiling, and without coordination, independent devices can't even queue fairly against each other. The correct scaling primitive is a **thin central gateway**: custody of the key, per-user fair-share queueing, backoff, and — critically — the cross-user content-hash cache, which only works centralized (one user's forwarded scam blast can't dedupe against another's from an isolated device). On-device execution buys privacy (full user history never leaves the device; only derived features for ambiguous messages go out) and removes local compute cost — it does not buy API throughput. That comes from the gateway, independent of where decision logic runs.
