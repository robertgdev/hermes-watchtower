# Cost & pricing in Hermes Watchtower

## What Hermes stores (the dashboard never invents a price)

- `sessions.estimated_cost_usd` and `session_model_usage.estimated_cost_usd` — computed by Hermes per session/API call from token counts × rates fetched from the provider's models API.
- Provenance columns: `cost_status='estimated'`, `cost_source='provider_models_api'`, `billing_provider` (e.g. `kilocode`), `pricing_version` (usually NULL).
- `actual_cost_usd` is **always 0** — no provider reports actual billed amounts. Always call it "estimated" in UI and prose.

Formula Hermes uses (USD per 1M tokens):

```
cost = input×rate_in + (output + reasoning)×rate_out + cache_read×rate_cache_read + cache_write×rate_cache_write
```

Reasoning is billed at the output rate. Cache reads usually dominate agent workloads (often 90%+ of spend), so a rate error on `cache_read` moves totals far more than one on output.

## Custom rates (recompute mode)

- Config: `config.json` in the dashboard dir (gitignored, user-specific) or `HO_RATES` env (JSON):
  `{"rates": {"deepseek/deepseek-v4-flash": {"input": 0.09, "output": 0.18, "cache_read": 0.018}}, "cost_multiplier": 1.0}`
- When a model has rates, the server recomputes every cost from token columns (`session_cost_values()`), at session level, per-model chart, modal subtitles, per-session stats and expensive-sessions table. Models without rates fall back to the stored estimate.
- `cost_multiplier` scales all displayed costs (calibration). Payload exposes `cost.mode` (`custom`/`stored`) and the frontend footer shows which is active — check that before claiming numbers changed.
- Env overrides win over config.json: `HO_RATES`, `HO_COST_MULTIPLIER`.

## Solving the implied rates from recorded data

When you need to know what rates Hermes actually used (to compare with a provider's published price):

1. Pick ≥3 sessions of the same model with `estimated_cost_usd > 0`, non-zero cache_read and reasoning tokens, `cache_write = 0`.
2. Solve the 3×3 system `tin·ri + (tout+treason)·ro + tread·rc = cost·1e6` with Gaussian elimination (pure Python `fractions` works; no numpy needed).
3. Fit is exact (~1e-16) when rates were constant; a held-out session that deviates slightly means the provider repriced between sessions — expected with live pricing.

Example outcome: kilo deepseek-v4-flash stored estimates implied ~0.0819 / 0.1242 / 0.0183 per 1M, while kilo's published rate is 0.09 / 0.18 / 0.018 — so the stored estimates were *higher* all-time than list price would give ($128.68 vs $111.88). Do not assume "user paid more" means the rates were too low; measure first.

## Scope caveat before "fixing" a cost complaint

A gateway balance (e.g. Kilo) is shared across all its tools — IDE extension, CLI, Cloud Agents, gateway — while this dashboard sees **only Hermes sessions**. Kilo Pass also converts subscription money 1:1 into credits plus bonus credits (and charges a purchase fee), so money paid ≠ usage. When a user says "I've paid a lot more":

1. Check whether the comparison includes non-Hermes usage (most likely).
2. Compare like-for-like: same date window, provider page filtered to Hermes requests.
3. Only then calibrate: `cost_multiplier = actual_hermes_spend / dashboard_estimate` for the window.

Treat cost as a good-enough estimate. After showing the proxy (recorded tokens × the provider's published list rate) and the Hermes-only scope, stop — do not chase exact billing, and do not run a calibration unless asked for one.

## Caching

Rates are read at server start (module-level `_RATES`/`_COST_MULT`). Changing config.json requires a server restart; tests monkeypatch `server._RATES` / `server._COST_MULT` instead (see tests/test_aggregation.py).
