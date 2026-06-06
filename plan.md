# Hackathon Win Plan — Trade Surveillance & Alert Triage Engine
> Wissen Hackathon 2026 | June 4–6 | PS2

---

## Scoring Rubric (100 pts)

| Criterion             | Weight   | Current Est. | Target      |
| --------------------- | -------- | ------------ | ----------- |
| AI Triage Quality     | 25%      | ~18/25       | 23/25       |
| Pattern Detection     | 20%      | ~13/20       | 18/20       |
| Automation & Workflow | 20%      | ~16/20       | 19/20       |
| Working Demo          | 20%      | ~15/20       | 19/20       |
| API Efficiency        | 10%      | ~7/10        | 9/10        |
| Docs / README         | 5%       | ~2/5         | 5/5         |
| **Total**             | **100%** | **~71/100**  | **~93/100** |

---

## 🔴 Critical Gaps (fix first — blocking or heavily weighted)

### ~~1. Missing `.env.example` file~~ ✅ DONE
- `.env.example` and `.env` already exist — **skip this item**

---

### 2. MARKING_CLOSE missing from live SSE demo
- **Impact:** Working Demo (20%) — only 4 of 5 detectors are demonstrated
- **What:** `simulation.py` has 7 phases but skips MARKING_CLOSE entirely; Phase 6 is a generic FP catch-all
- **Fix:** Add a dedicated Phase 5.5 in `_generate_stream()` for trader `T-5599` (close session), showing either DISMISS (fund rebalance FP) or ESCALATE
- **Effort:** 1 hour

---

### 3. FP suppression rate not reported anywhere
- **Impact:** Pattern Detection (20%) — the rubric explicitly says "false positive suppression rate"
- **What:** `/metrics` returns counts by severity/pattern but never computes FP suppression rate (`DISMISS / total_triaged`)
- **Fix:** Add `fp_suppression_rate_pct` to `/metrics` response
- **Effort:** 10 min

---

### 4. False Positive demo phase is non-deterministic
- **Impact:** Working Demo (20%) — Phase 6 of the SSE simulation depends on accidental borderline alerts
- **What:** `other_alerts` in simulation relies on borderline detections; if none exist, Phase 6 is empty
- **Fix:** Inject a deterministic market-maker FP scenario in `data/generate_data.py` — a `market_maker_registered=True` trader with 80% cancel rate that Claude correctly DISMISSes
- **Effort:** 45 min

---

### 5. Architecture diagram is ASCII-only
- **Impact:** Docs (5%) — rubric says "architecture diagram"; ASCII boxes in README don't qualify
- **Fix:** Add a Mermaid diagram block in README or export a PNG architecture diagram
- **Effort:** 20 min

---

## 🟡 Quality Gaps (fix second — directly target high-weight criteria)

### 6. No analyst feedback loop
- **Impact:** AI Triage Quality (25%) — no mechanism for human-in-the-loop correction
- **What:** No `POST /alerts/{id}/feedback` endpoint; Claude never improves within session; cannot demonstrate FP reduction
- **Fix:**
  - Add `POST /alerts/{alert_id}/feedback` accepting `{ "correct": bool, "analyst_note": "..." }`
  - Store confirmed TPs as few-shot examples in session memory
  - Prepend 1–2 confirmed examples to Claude system prompt on subsequent calls
- **Effort:** 2–3 hours
- **Differentiator:** No other team will have this

---

### 7. Claude has no cross-alert trader context
- **Impact:** AI Triage Quality (25%) — single-alert triage misses coordinated manipulation signals
- **What:** Each alert is triaged in isolation; if T-4821 has 3 alerts, Claude doesn't know about the others
- **Fix:** Add `POST /traders/{trader_id}/investigate` — sends all trader alerts in one prompt; Claude assesses whether they represent a coordinated scheme
- **Effort:** 2 hours

---

### 8. Token cost not translated to USD
- **Impact:** API Efficiency (10%) — "cost awareness" means showing dollar savings, not just token counts
- **What:** `/metrics` reports token counts and cache hit rate but no USD estimate
- **Fix:** Add to `/metrics`:
  ```json
  "estimated_cost_usd": 0.0142,
  "savings_from_caching_usd": 0.0089,
  "cache_hit_rate_pct": 78.3
  ```
  Using Claude Sonnet pricing: $3/M input tokens, $15/M output tokens, $0.30/M cache-read tokens
- **Effort:** 15 min

---

### 8a. Per-Claude-call token + cost logging *(new requirement)*
- **Impact:** API Efficiency (10%) — makes cost visible per-call in console output and API responses
- **What:** Every Claude API call (single and batch) should print tokens consumed and USD cost; TriageResult should carry `call_cost_usd`
- **Fix:**
  - Add `_compute_cost(input, output, cache_read)` helper to `ClaudeTriageClient`
  - Add `_log_call(label, ...)` that prints a formatted cost line after every API call
  - Add `call_cost_usd: float = 0.0` to `TriageResult` schema
  - Update `get_metrics()` to include `estimated_cost_usd` and `savings_from_caching_usd`
- **Effort:** 30 min

---

### 9. MARKING_CLOSE scenario not injected in data
- **Impact:** Pattern Detection (20%) — `MarkingCloseDetector` never fires on default scenario data
- **What:** `data/generate_data.py` injects anomalies for 4 traders (T-4821, T-9033/34, T-6610, T-8802); unclear if a CLOSE-session manipulation scenario exists
- **Fix:** Verify and if missing, inject a 5th anomaly trader (e.g. `T-5599`) with dominant close-window activity
- **Effort:** 30 min

---

## 🟢 Above & Beyond (differentiate from all other teams)

### 10. Analyst Feedback + Few-Shot Learning
- **Builds on Gap #6**
- After an analyst marks a verdict correct/incorrect, the corrected example becomes a few-shot example in the next Claude prompt
- Show in the demo: Claude makes a borderline call → analyst corrects → Claude makes a better call on a similar alert
- **Demo script:** `POST /alerts/TRD-XXX/feedback {"correct": false, "analyst_note": "This is a market maker — DISMISS"}` then retriage another similar alert

---

### 11. Daily Surveillance Report Generation
- **New endpoint:** `GET /report/daily`
- Claude synthesizes all triage results into a 1-page compliance officer narrative:
  - Key risks of the day
  - Priority escalations with rationale
  - FP rate and suppression summary
  - Traders under enhanced monitoring
- **One Claude API call, massive demo impact** — judges see Claude writing a professional compliance report
- **Effort:** 2 hours

---

### 12. Natural Language Alert Query
- **New endpoint:** `POST /query` accepting `{ "question": "show all wash trading alerts with confidence above 80%" }`
- Claude extracts filter parameters → system runs the query → returns structured results
- **Effort:** 1.5 hours
- **Demo line:** "You can ask the system in plain English"

---

### 13. Evidence Visualization in Dashboard
- For each alert card in `static/index.html`, render a mini price sparkline (Chart.js) showing:
  - Pre-manipulation baseline price
  - The manipulation window (highlighted)
  - Post-manipulation price
- Makes the demo visually compelling when projected
- **Effort:** 2–3 hours

---

### 14. Third Automated Workflow Action
- **Impact:** Automation & Workflow (20%) — PS says "at least two"; three is better
- **What:** Currently: Jira ticket + Slack alert + Watchlist. Add email or webhook notification for CRITICAL alerts, or a `DISMISS` auto-close workflow
- **Options:**
  - Email summary via SMTP (or simulated) for CRITICAL verdicts
  - Auto-generate a PDF compliance case summary (using `reportlab`)
  - Webhook POST to a generic endpoint (configurable in `.env`)
- **Effort:** 1 hour

---

## 📋 Implementation Order (time-boxed)

| #    | Task                                                                  | Time   | Criterion                  |
| ---- | --------------------------------------------------------------------- | ------ | -------------------------- |
| ~~1~~| ~~Create `.env.example`~~                                             | ✅ Done | Docs                       |
| 2    | Add Mermaid architecture diagram to README                            | 20 min | Docs                       |
| 3    | Add `fp_suppression_rate_pct` + USD cost to `/metrics`                | 15 min | Detection + API Efficiency |
| 3a   | Per-call token + cost logging in `ClaudeTriageClient`                 | 30 min | API Efficiency             |
| 4    | Inject MARKING_CLOSE + FP market-maker scenario in `generate_data.py` | 1 hr   | Detection + Demo           |
| 5    | Add MARKING_CLOSE phase to SSE simulation                             | 1 hr   | Demo                       |
| 6    | Add `POST /alerts/{id}/feedback` + few-shot injection                 | 2.5 hr | AI Triage Quality          |
| 7    | Add `POST /traders/{id}/investigate` (cross-alert analysis)           | 2 hr   | AI Triage Quality          |
| 8    | Add `GET /report/daily` compliance report                             | 2 hr   | Automation + Demo          |
| 9    | Add 3rd workflow action (PDF compliance case)                         | 1 hr   | Automation                 |
| 10   | Natural language query endpoint                                       | 1.5 hr | Differentiator             |
| 11   | Evidence sparkline in dashboard                                       | 2 hr   | Demo                       |

**Total estimated effort: ~13 hours** — achievable in the remaining hackathon window.

---

## Demo Script (what judges should see)

1. `python demo/run_demo.py` → 5 alerts detected, 2 ESCALATE, 2 REVIEW, 1 DISMISS (market maker FP)
2. Open browser → `http://localhost:8000` → SSE simulation plays all 5 pattern phases
3. Show FP phase: market maker correctly dismissed by Claude
4. Show `GET /metrics` → FP suppression rate, cache hit rate, USD cost and savings
5. `POST /traders/T-4821/investigate` → Claude cross-alert coordinated scheme analysis
6. `GET /report/daily` → Claude-generated compliance officer narrative
7. `POST /query {"question": "..."}` → natural language filter
8. Show Jira tickets, Slack messages, Watchlist entries in response payloads

---

## Key Files to Touch

| File                             | Changes                                                                             |
| -------------------------------- | ----------------------------------------------------------------------------------- |
| `.env.example`                   | **CREATE** — all config keys with comments                                          |
| `README.md`                      | Add Mermaid diagram, update quick-start                                             |
| `data/generate_data.py`          | Add MARKING_CLOSE + market-maker FP injection                                       |
| `src/api/routes.py`              | Add `/alerts/{id}/feedback`, `/traders/{id}/investigate`, `/report/daily`, `/query` |
| `src/api/simulation.py`          | Add MARKING_CLOSE phase (Phase 5.5)                                                 |
| `src/triage/claude_client.py`    | Add few-shot injection from feedback store, cross-alert method, report method       |
| `src/triage/prompt_templates.py` | Add prompts for investigate, report, and query endpoints                            |
| `src/ingestion/schemas.py`       | Add `FeedbackRecord` model                                                          |
| `src/workflows/`                 | Add 3rd workflow action                                                             |
| `static/index.html`              | Add Chart.js sparklines                                                             |
