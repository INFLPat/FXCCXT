<!-- version: 261008 -->
# Research Notes

Read after `CONTEXT_HANDOFF.md` (esp. Section 4f) and `ROADMAP.md`. Purpose: keep what chat 14 learned from the literature, the web and the data so the cartesian chats (14p1, 17p1, 17p2, 18, 19) start with context instead of from scratch and do not re-litigate settled points. Built chat 14, 261003. Add new research as dated sections; fold resolved items into CONTEXT_HANDOFF as decision + evidence.

**Trust labels used everywhere:** MEASURED (computed on the sandbox; reproduce with `audit_sandbox_alignment.py`), READ (from code or a document actually read), INFERRED (reasoned, not tested), MEMORY (Claude's recollection, unfetched: verify before citing), UNREAD (supplied by the user, not yet read by Claude).

## 1. Sources and how far to trust them

| Source | Status | What we took from it |
|---|---|---|
| ECB Statistics Paper 49 (effective exchange rate methodology), URL: https://www.ecb.europa.eu/pub/pdf/scpsps/ecb.sps49~655da0a6cb.en.pdf (user: "gold standard") | READ in chat 14; exact title not recorded - re-read for exact definitions before relying on them | Effective exchange rates are GEOMETRIC weighted averages of bilateral rates (log-linear, so a basket numeraire is just a weighted view of currency log-values); weights are updated infrequently and the index is CHAIN-LINKED at changes to avoid jumps; a data-provenance hierarchy (observed/reconciled > single reported > estimated); implausible zero/negative values treated as missing; two reports of the same quantity are combined with reliability weights (the dual-source QC pattern). Not about triangular consistency. |
| bukittimahtutor.com, 2026-08-29, "How FX cross-rate algorithms enforce triangular consistency..." | READ in chat 14. SECONDARY tutoring-site article; its BIS figures were not verified - treat as pointers | Mid-rates explain but are not executable; construct from bid/ask with directed edges, not memorised bid*bid shortcuts; validation is useful even when nobody trades the loop; pipeline and failure modes; keep timestamp, venue and staleness per quote; keep full precision, round only at output; a second independent path is the falsifier; backtest for false alerts in volatile windows; asynchronous-quote and vehicle-currency-stress risks. |
| FxPro "cross rates" article (text pasted by user) | READ (pasted excerpt) | Worst-of rule for client pricing; asynchronous quote risk; vehicle-currency stress; synthetic crosses from two dealers carry wider spreads than a direct quote; cross pairs have larger spreads and lower liquidity (esp. nights/weekends). |
| BIS WP 1073 (constrained liquidity provision, dealers), BIS FX execution algorithms, BIS Quarterly Review Dec 2025 (triennial survey trade-execution landscape) | UNREAD | Planned: R1 (17p1) and R4 (29p1). Dealer-behaviour paper may identify dealers breaching consistency - needs multi-dealer data we do not have. |
| Web searches chat 14 (third-party descriptions; URLs not retained - re-search): Dukascopy download libraries, HistData library notes, broker holiday notices, a forum thread on FX holidays, Epps / Hayashi-Yoshida abstracts | READ as snippets only | See Sections 5, 6, 7. Provider terms themselves were NOT read. |

## 2. Definitions and hierarchies

- **Data objects (never mix):** reference rate (informational fix), mid (analysis), executable bid/ask (tradeable), BAR-CLOSE bid/ask (what the sandbox holds: last quote of each hour per pair, not a simultaneous snapshot). Label every dataset with its object type.
- **Executable directed edge:** for pair BASE_QUOTE, selling base earns ln(bid); buying base costs -ln(ask). A cycle's executable gain is the sum of its edge weights; profitable if > 0 either way round. Do not rely on bid*bid shortcuts.
- **Mid residual (bps):** sum of signed ln(mid) around a cycle; ~0 when consistent. **Cost ratio:** |residual| / summed leg spread. **Quantisation floor:** price rounding noise (5 dp, 3 dp for JPY) - expected a few hundredths of a bp per leg; hypothesis only, INV-5.
- **Currency strength:** latent ln-value c per currency with ln(pair) = c_base - c_quote. Least-squares over all pairs gives c up to a constant; **any numeraire (USD, a basket, zero-sum) is a constant shift per bar**, so cross-sectional ranks are numeraire-invariant. Basket view = geometric weighted mean (ECB), chain-linked when weights change.
- **Edge types for the cartesian graph:** QUOTED (a direct pair on a venue), BASIS (unquoted equivalence, e.g. USDT~USD: estimated, never assumed zero), VENUE (same pair on different venues: transfer friction, not one executable loop unless inventory is pre-positioned). A loop is executable only inside one venue account or with pre-positioned inventory.
- **Provenance hierarchy for any bar/value (carry as a flag):** observed in primary source > reconciled across sources > single secondary source > forward-filled > estimated. Never forward-fill silently (Kraken has no bar when no trade happened; a real feed will not either).
- **Gap classes (implemented in the audit):** SCHEDULED_WEEKEND (matches the New York 17:00 Fri->Sun rule), HOLIDAY_CANDIDATE (FX-wide gap starting 20 Dec - 3 Jan; unverified), FX_WIDE_UNSCHEDULED, PAIR_SPECIFIC; crypto: venue-wide (missing in all instruments of the venue) vs instrument-quiet. Classes still to add: STALE (bars exist but frozen), plus external-calendar confirmation.

## 3. Cross-rate pipeline we adopt (steps; falsifiers)

1. Ingest with per-quote metadata (timestamp, venue/source, data-object type, staleness, size if known). 2. Canonicalise orientation (base/quote from the name; a wrongly inverted leg shows as a huge residual - test each inversion). 3. Build the directed executable graph (log-bid / -log-ask per direction). 4. Enumerate cycles (all triangles now; longer loops via Bellman-Ford on -log rates later) - this is also the second-independent-path falsifier. 5. Compute mid residual and executable gain both directions. 6. Tolerance = summed spread + quantisation floor. 7. Tier (fixed data-validity tiers, Section 4f.4). 8. Attribute the leg: leave-one-pair-out across overlapping cycles (shared legs identify the culprit; JPY/CHF pairs have only one cycle each, so tie-break with staleness / own-history). 9. Record flags with provenance. 10. Diagnostics: stress-window false-alert review, shuffled-timestamp control (synchronisation should vanish), reversed-orientation control, residual autocorrelation/half-life (null benchmark for spread mean-reversion work, chat 24).
Reality check (MEASURED): residual p99 is 0.17-0.44 of summed spread, so triangular arbitrage is NOT a strategy on H1 bar closes from one broker; it is a data-integrity and strength-extraction tool. Post-MVP edge only with tick data, latency and depth (likely not Python).

## 4. Design brief for the cartesian chats

- **Universe will be large:** cartesian of as many FX and crypto instruments as possible, grown in stages (sandbox as-is -> augmented sandbox incl. stablecoin pairs (25p1) -> MVP suite (P11b) -> later additions). Registry must be data-driven with point-in-time membership (listings, delistings, ticker renames); `instrument_config.py` currently asserts exactly 16 (19p1 replaces that). Graph must not depend on a USD triangle; USD is just a node.
- **Crypto nodes and stablecoins:** USDT~USD is an alias edge in the audit (basis measured, not assumed). Kraken may quote USDT/USD and USDT/GBP (user to confirm bulk-CSV availability, INV-16) which would make the basis directly observable.
- **Strength estimation = two speeds:** slow path (daily/weekly): cycle set, orientation map, weights, thresholds, calendars, basket weights. Fast path (every bar): least-squares strengths, residuals, flags (cheap: small matrix-vector). A candidate edge is confirmed from executable bid/ask at decision time. Backtest and live must run the identical two-stage logic.
- **Keep several strength definitions as VIEWS** of one stored canonical estimate + residuals + weights (vs USD, zero-sum, geometric basket, spread-weighted, robust/leave-one-out).
- **Confidence inputs:** data completeness / asynchrony (Epps guard, Section 7) and cost ratio feed the confidence discount; the risk-appetite slider adjusts only the discount, never data validity.
- **Reproducibility metadata per run:** git commit, data manifest hash, all config versions (params, CostModel, PPY constant, universe, calendar, thresholds), library versions, seeds, the quotes and data-quality flags used at decision time (33p1).
- **Dependencies:** stdlib reference implementation first (oracle); optional numpy path cross-checked at 1e-9 (numpy 2.5.3 cp314 wheel verified installable on the user's Mac); coverage.py and hypothesis dev-only (verified installable). Property tests to write: pair inversion leaves the residual unchanged after orientation fix; strength differences invariant to numeraire; cycle result independent of edge order; consistent synthetic world gives ~0 residual; injected fault of k bps is found at k bps.
- **Weekend-proxy hypotheses (21p1):** H1 crypto-implied weekend GBPUSD predicts the Monday FX gap (about 220 weekends: low power); H2 crypto/stablecoin as a fiat "parking venue" while FX is closed (needs USDT/USD history, depeg episodes, counterparty and UK-access (L12) information; price data alone cannot settle it).

## 5. Data sources: two separate threads

| | Sandbox / research | Live / production |
|---|---|---|
| Purpose | Truth-check, outlier adjudication, reproducibility | Redundancy, freshness, failover, outage detection |
| Constraint | Free/research licences are fine during R&D | SLA, cost, redistribution licence for subscribers |
| Owner chat | 16p1 | 30p1 |

Candidates (descriptions from third-party pages; provider terms UNREAD): Dukascopy (free tick history with bid/ask; a different dealer from OANDA, so an independent FX source; downloads reach up to the previous hour), HistData (free M1/tick, personal/educational use with redistribution restrictions; a library note says timestamp resolution changed in mid-2026 with many rows sharing whole-second stamps - check before use), OANDA M1/S5 (already accessible), other exchanges via ccxt (crypto), ECB/BoE daily fixes (level checks only, not executable). Other free tiers were NOT searched. Policy: access as many as helpful during R&D on free licences; start the redistribution-licence process at MVP / single-user live (P15b); licensing cost is a risk-register row. **Storage:** `candles` PRIMARY KEY is (instrument, granularity, timestamp) - a second source would overwrite the first. Decide a `source` column vs separate tables vs separate DB files only after the source inventory (16p1 -> 30p2). Stage 2: a canonical integer `ts_utc` for ordering/joins with raw timestamps kept as provenance and an explicit per-source bar-stamp convention (open vs close); needs a migration and a new manifest hash.

## 6. Holidays vs outages (settled learning)

User's framing: a holiday affects everyone, an outage may be local. Refinement: OTC FX has no market-wide closure except the weekend. Brokers publish their own holiday/early-close schedules (different times by broker and instrument group) and Asian trading continues thinly, so a holiday at our broker does NOT pause the market - prices move unseen exactly as in an outage. The difference is predictability:

| | Scheduled closure | Outage |
|---|---|---|
| Duration | Known | Unknown |
| Handling | Pre-position, flatten or widen risk, expect a gap on reopen | Failover to second source, alert, reconcile (L3), kill switch (L5) |
| Detection | Calendar says closed | Calendar says open but there is silence |

Rule for live: "expected-open hour but silence" = outage; so calendar and classifier are one artefact. Holiday ground truth should come from an external source, not inference: the broker's own published schedules (OANDA), Internet Archive snapshots of past notices (user suggestion; unverified), and a second independent feed (ticks present at Dukascopy during an OANDA gap show the gap was OANDA-local). Fall back to data-and-calendar inference if no source is found. Live: broker pricing status/heartbeat (OANDA tradeable flag and stream heartbeat: MEMORY, verify in 31). Multiple sources are mandatory for the live feed. The gap catalogue doubles as outage-rehearsal ground truth for the replay feed (L1): add fault injection (missing bars, stale quotes, duplicates, inverted quotes, DST shifts, out-of-order) in P7.

## 7. Asynchrony and the Epps effect

Correlation measured at high sampling frequency is biased towards zero when series are asynchronous (Epps 1979: correlations diminish as sampling frequency rises). The Hayashi-Yoshida estimator corrects the asynchrony part for tick data but cannot examine other time scales, and asynchrony is only one cause. One study of stocks finds no causal structure in lagged cross-correlations once asynchrony is removed: be sceptical of lead-lag edge claims (a lag between liquid and illiquid legs may be stale quotes, not tradeable price discovery).
**Where it bites us:** cross-instrument correlation in `portfolio.py` (esp. Kraken GBP pairs with missing hours), hedge ratios / cointegration, the "do not double-allocate correlated instruments" rule. **Where it does not:** strategy-vs-strategy clustering (same instrument's bars: synchronous).
**Guards:** compute correlation at H1/H4/D1 and use the plateau value (signature plot); inner-join on common timestamps, never forward-fill; a large H1-vs-D1 gap is itself a data-quality signal for that pair; Hayashi-Yoshida when tick data exist. **Confidence input (user request):** data completeness / asynchrony enters the confidence-weighting calculation - if gaps are present, discount accordingly (CONFIDENCE_SIZING_DESIGN Section 6).

## 8. What we cannot do yet (and when to plug it)

| Gap | Why | Plug | When |
|---|---|---|---|
| Financing / swap / rollover costs | No history in sandbox | OANDA financing fields (MEMORY), policy-rate carry as approximation | 33 (L9) |
| Forward points / cross-currency basis | Needs forward data (paid) | Spread widening as proxy; paid data post-MVP | Post-MVP |
| Depth, last look, latency | Bars carry none | Paper trading measures them | P12 (explicit aims) |
| Tick timestamps (async skew, true close time) | Bars only | Dukascopy ticks | 16p1 / R1 |
| USDT/USD basis directly | No quote in sandbox | Kraken stablecoin pairs (INV-16) | 25p1 |
| Independent QC | Single source | Second sources | 16p1 |
| Real crypto spreads | bid = ask in all crypto bars | Order-book/fee data; Kraken taker fee 0.40% base vs model 0.10%+0.05% | 30 / 33 |
| Same-bar-close fill realism | Engine fills at the signal bar's close | Next-bar-open option, latency model | 33 (risk register) |

## 9. Literature leads (MEMORY - unfetched; verify existence and details before citing)

Dacorogna, Gencay, Muller, Olsen, Pictet, *An Introduction to High-Frequency Finance* (2001) - tick filtering; Brownlees & Gallo (2006) on ultra-high-frequency data handling; Barndorff-Nielsen, Hansen, Lunde, Shephard (2009) on trade/quote cleaning; Makarov & Schoar (2020, JFE) on crypto arbitrage across exchanges; Du, Tepper, Verdelhan (2018) on CIP deviations; Hayashi & Yoshida (2005); Epps (1979); Bailey & Lopez de Prado (2014) deflated Sharpe, White (2000) reality check, Harvey-Liu-Zhu (2016) on multiple testing (relevant to chat 20 and MC-1). Research chats: 17p1 (R1: cross-rate, data cleaning, Epps, microstructure), 17p2 (R2: cartesian, relative strength, crypto cross-venue), 19p2 (R3: multiple testing, Sharpe, annualisation), 29p1 (R4: execution, venues, UK regulation). Rule: a "what did we miss" research chat before each major phase (scoring/sizing, live infrastructure, risk setting, investor pack).

## 10. Open investigations register

Causes of the residual tails and stress days are NOT yet established. "Evidence" is MEASURED unless stated; every cause below is hypothesis.

| ID | Question | Evidence so far | Method | Needs | Chat |
|---|---|---|---|---|---|
| INV-1 | Source of the 2022-05-04 USD_CHF frozen closes (0.98512 for 3 bars while GBP_USD moved ~1.3%; GBP_CHF residual 130.8 bps) and the 2022-05-08 EUR_GBP Sunday-open bar (implied 0.8538 vs stored 0.8442; EUR_USD triangle 114.1 bps) | Worst events; USD_CHF-only 6h gap 2022-05-04 21:00 UTC, EUR_GBP-only 4h gap 2022-05-08 21:00 UTC | Re-fetch those windows from OANDA at M1/S5 and from Dukascopy; compare closes and tick counts | User OANDA token, local run | 16p1 |
| INV-2 | Are residual tails thin-bar effects? | Residuals cluster at 20-22 UTC; bars have `volume` (tick count) | Residual vs min-leg volume and bar range; sandbox-only | none | 14p1 |
| INV-3 | Which clock anchors the hour concentration (20-22 UTC all triangles; GBP_JPY also 17 UTC)? | Hour profile | Split by UK-DST vs US-DST state: the hour that moves with US DST is NY-anchored | none | 14p1 |
| INV-4 | Stress days: 2022-05-31 / 2022-06-01 (GBP_CHF, EUR_USD triangles); 2023-04-28, 2024-03-27, 2023-04-05 (GBP_JPY triangle); 2024-12-24 21:00 UTC (JPY 11.7 bps), 2025-04-13 21:00 UTC (JPY 10.9 bps, a Sunday open) | stress-day lists | Leg attribution (leave-one-out; GBP_USD is shared by all three triangles), then an external event calendar (central-bank dates, month-end fixings) | Event calendar | 14p1 + 17p1 |
| INV-5 | Is the small signed cycle mean a bias or noise? Sign pattern is a labelling artefact (the EUR sign flips if EUR_GBP is called direct); magnitude near the quantisation floor (hypothesis) | signed means +0.051 / +0.063 / -0.076 bps in golden-label orientation (NOT established as systematic) | Compare with the quantisation floor; regress on spread. 14p1 note: arithmetic-mid asymmetry under pair inversion is ~half-spread^2 (~2.5e-5 bps at 1 bp spread, MEASURED on a synthetic world), far too small to explain it | none | 14p1 |
| INV-6 | Why does the GBP_JPY triangle have heavier tails (90 suspect bars; executable-band violation in 0.89% of bars vs 0.04% and 0.01%)? | tier counts | Price precision, spreads, tick data | Ticks | 16p1 |
| INV-7 | Basis tails: 2022-05-12 08-15 UTC (all four assets, BTC -234 bps), 2022-06-13 (LTC), 2022-04-06 (LTC), 2023-03-13 (XRP), 2023-03-24 11:00 (ETH, two hours before Binance's missing hour), 2025-04-07 (ETH -290 bps): USDT dislocation or Kraken thin/stale bars? | worst events | USDT/USD (Kraken), a third venue's USD pair, bar volumes | Stablecoin data | 25p1 / 16p1 |
| INV-8 | Cost realism for thin survivors (LTC pairs; bid = ask bars hide spread) | Kraken base taker 0.40% vs model | Order-book snapshots, fee tiers | Exchange data | 30 / 33 |
| INV-9 | Late reopens and gaps: OANDA-local or market-wide? 2022-05-12 05:00-08:00 (all FX), 2022-06-24 (EUR_USD, GBP_USD, USD_JPY reopen 1h late), 2022-08-28 (all FX, 2h late) | gap catalogue | Second FX feed | Dukascopy | 16p1 |
| INV-10 | Holiday ground truth for the 8 candidates | catalogue | OANDA published schedules, Internet Archive | External | 16p1 |
| INV-11 | Absolute bar-open vs bar-close convention (Binance, Kraken bulk CSV) | agree with each other (lag 0) | Read Binance/Kraken docs; third source | User can confirm from docs | 16p1 |
| INV-12 | Sub-hour skew | FX-vs-crypto loading 0.05-0.08 at lag 0 (consistent with up to a few minutes if timing were the only cause) | M1 data | M1 | 16p1 |
| INV-13 | Kraken-wide runs: ~2024-04-14 03:00 (5-6 h, all four), ~2025-11-01 15:00 (5-6 h), 2023-05-07 20:00, 2024-01-08 09-10 | venue summary | Kraken status history / Internet Archive | External | 16p1 |
| INV-14 | Binance 2023-03-24 13:00 UTC missing hour (all four pairs) | catalogue | Binance announcements | External | 16p1 |
| INV-15 | Weekend-proxy H1/H2 | none yet | Section 4 | alignment layer | 21p1 |
| INV-16 | Do Kraken bulk CSVs include stablecoin pairs? | PARTLY ANSWERED 261005: the 23Q2 hourly file NAMES (user's directory listing, 654 files) include USDT vs USD/GBP/EUR/AUD/CAD/CHF/JPY, USDC, DAI, TUSD, EURT, UST pairs, and Kraken FX pairs (GBPUSD, EURUSD, EURGBP, USDJPY, USDCHF, USDCAD, AUDUSD...) - a possible second FX source for 16p1. Row counts, date coverage, `historical/` and other quarters NOT yet checked | User runs a `find` over the Kraken folders (path, row count, first/last epoch for USDTUSD, USDTGBP, GBPUSD) | User | 16p1 (basis edge QUOTED upgrade then 25p1) |
| INV-17 | CLOSED 261005 (14p1): wheels on Python 3.14 | psycopg2 2.9.13, ccxt 4.5.78, requests 2.34.2, numpy 2.5.3, matplotlib 3.11.2 import and construct on 3.14.7 (psycopg2 never connected to a DB, ccxt never reached an exchange) | `pip install --dry-run` each | User | any |
| INV-18 | OANDA pricing tradeable flag, stream heartbeat, financing fields | MEMORY only | Practice-account test | OANDA token | 31 |
| INV-19 | LTC/GBP has 406 missing hours, 36% on weekends (BTC/GBP 86%): weekday illiquidity? | per-instrument summary | Volume, run structure | none | 16p1 |
| INV-20 | Internet Archive coverage of broker/exchange notices | unknown | Try it | User/Claude | 16p1 |

## 11. Coverage ledger (chat 14)

See the end of `CONTEXT_HANDOFF.md` Section 4f ("Chat 14 coverage ledger") - maintained there so it travels with the decisions.
