# Twelve research bets on prediction-market structure

2026-10-23 · Research proposals, not validated strategies · Branch `research/structural-alpha-20261023`

The strongest opportunity I see is **forecasting the machinery between a real-world event and a dollar of settlement**. A trader may understand the headline but misprice the remaining procedural steps, the probability that evidence would already be visible, or the difference between a physical measurement and its published value. Those mistakes can last long enough for our constraints: free public data, a laptop, roughly an hour of live collection per day, no HFT, and tiny capacity being acceptable.

A second opportunity is to infer *why another trader moved a price*: new information, a mistaken model of the contract, or a cash constraint. Public wallet histories could make that distinction unusually observable here.

These are mechanism hypotheses. All numerical examples below are invented illustrations, not observations or performance forecasts. Novelty means a specific proposed variation beyond our existing plan; it does not mean nobody has privately tried it. A targeted literature check already finds published combinatorial arbitrage and semantic relationship discovery [S4, S5]. Neither an LLM matching similar questions nor checking whether probabilities sum to one is our novel contribution.

## Where this fits the actual repository

I read the research plan, roadmap, findings and lookahead post-mortem, data schemas and loaders, research/evaluation code, recorder selection logic, backtester and fee assumptions, validation/gotchas, and learning conventions. The important constraints are:

- The first BTC digital model failed to beat the market after assumed costs. Similar questions on different venues had different outcomes. A large general-market anomaly mostly disappeared when horizons stopped using realized close times. These findings favor precise definitions and observation processes over adding another volatility estimator.
- The current agenda already contains deadline calibration, weather forecasting, wallet skill, cross-venue rules, ladder consistency, and slow crypto. The proposals below must add a distinguishable mechanism to those families.
- A read-only catalog inspection showed 53,778 `general-sched` markets, of which 28,628 have samples; 993,600 one-minute candles per BTC/ETH symbol; seven weather series, but six with **zero sampled markets** and New York with 100 at inspection time. Large crypto ladder catalogs have much smaller sampled subsets. Counts describe the local snapshot and may change while other agents work.
- `MARKETS_SCHEMA` includes titles, rules, scheduled end, strike and event IDs. It does **not** establish that the stored rules or scheduled end are the versions available at a past decision time. Current metadata cannot silently become historical knowledge.
- Historical `price_samples` are last trades. Live discovery selects top-volume markets, which is a poor default universe for these orphan opportunities. The recorder has useful raw catalog capture; targeted enrollment and source histories would still be needed.
- The engine has conservative taker fills but no complete settlement-driven prediction-market simulation. This memo does not pretend the proposed baskets can already be backtested faithfully.
- An existing uncommitted change to `src/drc/research/studies.py` was left alone. This worktree starts from committed `e5f2458`; no holdout study was run or inspected.

**Costs change the ranking.** The repo's Polymarket zero-fee assumption is a placeholder. Current official documentation lists taker fees across multiple categories and zero fees for geopolitical/world-event markets [S1]. Kalshi also has market-specific fee exceptions [S2]. Use the applicable market and date, with order-size rounding; don't transplant today's rates into old backtests.

For a binary contract with expected payoff in `[q_L,q_U]`, YES ask `a_Y`, NO ask `a_N`, actual fee `f`, and a dollar allowance `u` for unmodeled execution and capital costs, a conservative entry screen is:

```
buy YES only if q_L - a_Y - f_Y - u > 0
buy NO  only if (1 - q_U) - a_N - f_N - u > 0
```

Here the spread is already in the ask: do not subtract half the spread again. Fractional/void settlements require the actual payoff function, not the binary shortcut. Most proposals use one purchased side held to settlement; baskets explicitly pay for every leg. A convergence exit must clear both entry and exit costs. A probability disagreement is not automatically a trade.

**A shared constraint on all twelve ideas:** under a correctly specified information model,
the conditional expectation of a fixed terminal payoff is a martingale. Known calendar decay,
a scheduled report, or an outgoing rolling-window observation cannot by itself generate
expected profit. Survival-conditioned prices can decay while the unconditional expectation
remains unchanged because event arrivals create offsetting jumps. Every proposal therefore
needs evidence of a specific market approximation error or a priced liquidity service; the
mathematics describes where to look, not a free return from the passage of time.

## Priorities

H/M/L are qualitative judgments, not estimated success probabilities. Feasibility includes missing data; novelty refers to the proposed mechanism relative to our agenda and ordinary variants.

| ID | Proposed mechanism | Novelty | Feasibility | Typical horizon | Priority |
|---|---|---|---|---|---|
| 1 | Remaining procedural gates change the power of deadline decay | H | H | Days–weeks | Start |
| 2 | Price silence using the probability that evidence would have appeared | H | M–H | Hours–weeks | Start |
| 3 | Trade branch-specific news that a headline model propagates incorrectly | H | M | Hours–weeks | Start |
| 4 | Isolate the small set of worlds where similar rules disagree | M–H | H | Days–months | Start |
| 5 | Infer official weather outcomes through the measurement/rounding pipeline | M–H | H | Hours–day | Start |
| 6 | Infer the *type* of new information from the shape of a threshold ladder | H | M | Hours–days | Next |
| 7 | Couple first-release, revised, rounded and derived economic quantities | M–H | M | Days–weeks | Next |
| 8 | Predict unrelated selling from wallets' external cash shocks | H | L–M | Hours–days | Small pilot |
| 9 | Price the deterministic observations leaving a rolling settlement window | M | H if listed | Days–weeks | Cheap scan |
| 10 | Detect systematic use of the wrong contract model from its residual fingerprint | H | M | Hours–days | Next |
| 11 | Infer hidden readiness from an actor's choice of announcement timing | H | L–M | Days–weeks | Speculative |
| 12 | Use a specialist's *selective absence*, conditional on observable activity | H | L | Hours–days | Speculative |

Do not implement twelve systems. My first allocation would be 1, 4 and 5, with 2 attached to 1. They have the clearest paths to a small hand-audited decision set. Ideas 3 and 6 are the strongest mathematical expansions. Ideas 8 and 12 are the more unusual microstructure bets but require substantially better data.

## 1. Count the remaining gates, not just the remaining days

**Hypothesis.** A deadline market can be badly priced even if traders have approximately correct beliefs about ultimate success. They may not distinguish “one thing must happen” from “four things must happen in order.”

Suppose an event needs `k` sequential unfinished stages, each with waiting time `X_j`, and the remaining time is `τ`. The relevant probability is

```
q(τ) = P(X_1 + ... + X_k <= τ).
```

For independent exponential waiting times with common rate `λ`,

```
q(τ) = 1 - exp(-λτ) Σ_{j=0}^{k-1} (λτ)^j / j!.
q(τ) ~ (λτ)^k / k! as τ -> 0.
```

That exponent is the interesting part. With three gates left, success probability near the deadline can decay cubically, while a one-step narrative model decays linearly. At `λτ = 1`, the respective probabilities are about 8.0% and 63.2%. This is a structural illustration, not a defensible estimate for a real project.

**Algorithm.** Manually compile one narrow family of rules into a graph of prerequisite states: filing → review → vote → signature, for example. Attach public evidence and an earliest possible date to each transition. Use empirical waiting-time ranges rather than assuming exponential times. Represent parallel tasks by maxima, sequential tasks by sums, optional routes by alternative paths. Simulate over plausible transition laws; trade only if even an optimistic completion model leaves YES overpriced. A newly completed bottleneck can create the opposite trade because it removes an entire waiting stage.

**Trade and payer.** Buy NO on a deadline with several genuinely sequential gates remaining; buy YES after a bottleneck disappears if the crowd merely applies its usual small news bump. The hypothesized payer focuses on desirability or eventual success and underprices calendar mechanics. Institutional workflow interpretation is labor-intensive and capacity per question is small.

**Cheap discrimination.** Hand-audit 15–20 historical or current questions, blinded to prices initially. Can the stages and their ordering actually be established from contemporaneous public documents? Match questions with similar price and time remaining but different unresolved gate counts. The mechanism predicts a gate-count interaction, not generic deadline decay. No live sizing inference from that small sample.

**Kill it if** supposedly sequential stages overlap, can be waived, or are already privately completed. A single plausible shortcut can dominate the tail. “Not publicly announced” must not be equated to “not completed”; that is idea 2's job.

**Increment beyond the plan:** deadline *topology* and changing state dimension, rather than fitting one hazard curve by category.

## 2. Silence has a likelihood, and the likelihood has an owner

**Hypothesis.** Traders confuse “no public report” with “nothing happened,” or ignore silence even where an event would almost certainly have left public traces.

Let `H` mean the event has already happened, with prior `p`. A monitored source emits relevant evidence at rate `λ_1` under `H` and `λ_0` under `not H`. Under a simple conditional Poisson model, no evidence for time `Δ` gives

```
logit P(H | silence) = logit(p) - (λ_1 - λ_0) Δ.
```

More realistically, model event time `T` and reporting delay `D`. Given no report by `t`, the deadline probability is

```
P(T <= deadline | T + D > t),
```

integrating over both `T` and `D`, plus missed reports and false reports. This can be much greater than `P(T <= deadline | T > t)`. An event can have occurred without being visible.

**Algorithm.** Build a small “evidence map” for each question: mandatory registry, routine meeting minutes, named official source, independent press, satellite bulletin, etc. Estimate or bound source sensitivity and delays from ordinary positive events, not just traded ones. Add an explicit source-health state. A source that stopped publishing everything contributes little negative evidence.

**Trade and payer.** Buy NO when a reliable, functioning public trace should have appeared and did not. Buy YES when the market has overreacted to absence during an established publication lag. A one-week reporting delay is compatible with our collection limits; a seconds-long delay is not.

**Cheap discrimination.** Freeze a source and its normal update schedule. Separate “missing target report while unrelated reports continue” from a general outage. The silence effect should track source sensitivity and health. If it tracks only elapsed time, this adds little to ordinary deadline decay.

**Kill it if** reports are selectively suppressed, publication policies changed, or evidence already enters prices before we can observe it. Ten newspapers syndicating one wire are one source, not ten independent negative likelihoods.

**Novel variation:** trade the *detection process* with explicit outage controls, rather than sentiment or absence-of-news heuristics.

## 3. A headline can be true in the wrong branch of the world

**Hypothesis.** Traders propagate information through a familiar narrative even when that information matters only under a branch that has become unlikely.

Suppose `A` is “a bill passes” and `B` is “the policy is implemented by December.” Let

```
a = P(A),    r = P(B | A),    s = P(B | not A).
P(B) = ar + (1-a)s.
```

News about implementation readiness might change `r`, leave `s` unchanged, and have little effect on `a`. Then `ΔP(B) = a Δr`. A headline-driven updater might behave as though `ΔP(B) = Δr`, particularly if it forgets the gating event.

For example, with `a=.20`, `r` moving `.60→.90`, and `s=.05`, B moves `.16→.22`, not `.16→.46`. If a plausible posterior range for B remains below its executable price, buy NO on B. This needs no ability to short A.

**Algorithm.** Maintain a small manually verified event graph and branch-specific likelihoods. Classify each public development by which conditional it changes. Recompute the full mixture with uncertainty in `a,r,s`; never infer all three from a single B price. Use related markets to constrain `a`, and process records to constrain `r,s`. If those bounds are too wide, abstain.

**Why an edge could persist.** The information is easy to read but cognitively expensive to propagate through the full event tree. Most stories discuss eventual implementation conditional on success without repeating the conditional clause. Thin downstream contracts may receive a narrative price jump without a full probability update.

**Cheap discrimination.** Preselect one recurring gate/child family. Compare similarly sized readiness announcements when the gate is likely versus unlikely. The proposed effect scales with gate probability. A model that simply fades every large headline move should fail this test.

**Kill it if** the same news changes A substantially, if implementation has unmodeled alternative routes, or if the branch probabilities are not identifiable from independent information. This is structural Bayesian forecasting, not arbitrage.

**Novel variation:** identify a specific error in conditional propagation, rather than generic correlated-market lead/lag.

## 4. Buy the difference in the rulebooks, stripped of the headline

**Hypothesis.** Similar questions can hide a cheap exposure to a narrow administrative event. We can isolate that exposure without having a superior forecast of the main story.

Let two contracts have payoffs `Y_A` and `Y_B`, with a proved nesting `Y_B <= Y_A` in every admissible settlement state. Example: A pays for a qualifying announcement by D; B pays only if that same announced action has also legally taken effect by D. The relationship must follow the actual rules, not the titles.

Buy YES A and NO B. The terminal payoff is

```
Y_A + (1-Y_B) = 1 + 1{A happens, B does not}.
```

At an all-in cost of `1.04`, the extra four cents buys exposure to the “announced but not effective” worlds. If their probability is credibly above 4%, the basket has positive expectation. At a hypothesized 12% probability, expected profit is 8 cents, but return is on **$1.04 locked**, not four cents. That capital distinction matters enormously.

**Algorithm.** Translate 2–5 related contracts into a payoff table over explicit worlds, including deadline, source, exception, fractional settlement, and fallback states. Seek nonnegative quantities of purchasable YES/NO legs whose aggregate payoff is nearly constant across headline worlds and varies mainly across the narrow procedural distinction. A small linear program can minimize maximum deviation from the target payoff subject to cost, capital and displayed-size limits. Allow “no solution.”

**Trade and payer.** Buy the basket only when a pessimistic estimate of the residual state's probability clears both legs' costs and capital burden. The potential payer prices the common headline closely but approximates the rule difference as negligible. Holding both legs to settlement avoids depending on convergence.

**Cheap discrimination.** Find ten pairs with a human-checkable world table and one-sentence residual event. First ask whether the extra dollar of collateral makes the expected return uninteresting. Then collect synchronized executable quotes on just those pairs.

**Kill it if** nesting fails in even one plausible state, sources differ enough to create a reverse outcome, or clarification can alter the mapping. Polymarket documents both specific resolution rules and later clarification machinery [S3]. A fractional fallback also breaks the binary identity unless included explicitly.

**Novelty limit:** the payoff algebra is standard. The contribution is selecting narrow *disagreement sets* as forecastable assets and pricing those sets directly, not declaring all similar questions equivalent. This goes beyond the repo's cross-venue gap scan but should not be marketed as newly invented arbitrage.

## 5. Forecast the thermometer's publishing pipeline

**Hypothesis.** A crowd can have the right weather forecast and still misprice the contract because it treats a displayed temperature as the official daily high.

The NWS explains that public observations can differ from climate-product extrema because of sampling, averaging, Celsius conversion and rounding [S6]. This is a concrete reason to model the measurement channel, not simply add a weather feature.

Let latent temperature be `X`, the visible display be `Z=Q(X+ε)`, and the settlement value be a possibly different functional `R=G(X_path)`. Then price bracket `b` with

```
P(R in b | displayed observations, forecast, station metadata).
```

A display rounded to whole Celsius does not identify a single Fahrenheit temperature. In a deliberately simplified illustration, a display of 27°C implies a pre-rounded value in `[26.5,27.5)`°C, or `[79.7,81.5)`°F. Under a locally uniform prior, the probability of exceeding 81°F is about 28%, not certainty. Actual station processing is richer; this toy is not the NWS settlement algorithm.

**Algorithm.** For one station, model observation likelihoods by product and precision; combine them with a prior over the remaining temperature path. Carry the observed-so-far maximum as a latent quantity when observations are incomplete. Map the result into the exact climate report, reporting day and bracket boundaries named by the contract. Update on the hourly higher-precision observation and other publicly available extrema information, recording when each became available.

**Trade and payer.** Buy the bracket or its NO side where naïve display-to-settlement conversion assigns mass to the wrong integer. Hold to settlement, using one leg if possible. The target is a recurring measurement misunderstanding, not fastest reaction to a new forecast.

**Cheap discrimination.** Reconstruct 20 station-days where a rounding boundary lies near the observed high. Does a naïve display-based model make signed errors at the predicted boundaries? Only then look at quotes. With seven series but sparse prices locally, source-mechanism validation is available sooner than tradability evidence.

**Kill it if** the correct source/precision is already universally used, boundaries show no distinctive errors, or the correction is smaller than spread and fees. Unobserved intraday maxima can overwhelm a neat rounding argument.

**Increment beyond weather forecasting:** infer the observation channel and exact published integer, rather than improve tomorrow's physical-temperature forecast. This is a high-feasibility variation, not an assertion that weather traders have never noticed rounding.

## 6. Decode news from the shape it leaves across a ladder

**Hypothesis.** Prices may incorporate a news item as “higher” when it actually means “less uncertain,” or vice versa. Different threshold contracts then receive the wrong relative changes even while the ladder remains monotone.

For a toy latent quantity `X ~ N(μ,σ²)`, a threshold contract is

```
p(K)=P(X>K)=Φ((μ-K)/σ),       z=(μ-K)/σ.
∂p/∂μ = φ(z)/σ.
∂p/∂σ = -z φ(z)/σ.
```

A mean increase pushes all thresholds upward. An uncertainty increase pushes thresholds below the mean downward and thresholds above it upward. A skew change has another pattern. These response vectors are more informative than the movement of one favorite contract.

**Algorithm.** Fit a small distribution family to synchronized quotes before a scheduled information event, respecting quote intervals rather than treating mids as exact. Use the most liquid subset to infer a posterior over changes in mean, width and asymmetry. Predict changes in held-out thresholds. Target residuals in less attended legs, preferably after enough time that speed is irrelevant. A more flexible version transports probability mass between adjacent outcome bins, constrained by what the news could change.

**Trade and payer.** Buy an underadjusted tail YES or an overadjusted tail NO. The payer applies a one-dimensional headline response to a multi-dimensional distribution. Do not buy a multi-leg curvature basket unless its risk reduction justifies extra spreads.

**Cheap discrimination.** Restrict to one source of explicitly uncertainty-reducing news, such as completion of a previously uncertain component. Fit on central thresholds and predict tail direction before examining tail prices. Compare against a generic “slow legs follow fast legs” rule; a distinct sign pattern is the mechanism's signature.

**Kill it if** threshold prices are too stale to fit a contemporaneous distribution, the family misses bimodality, or width/skew cannot be separated with available strikes. A well-fitting distribution is not evidence that it is the physical distribution: demand and compensation for risk can also shape prices.

**Novel variation:** classify a news update by its distributional response pattern, rather than scan monotonicity or trade the same directional forecast at multiple strikes.

## 7. Couple publication vintages instead of predicting the economy twice

**Hypothesis.** Related contracts can refer to different releases, rounding operations or derived quantities. Traders may treat these as independent forecasts or as identical, missing the joint publication mechanism.

Let `X_0` be a first published estimate and `X_1=X_0+U` a subsequent revision. If A pays on `X_0>k` and B on `X_1>k`,

```
E[Y_B-Y_A]
 = P(X_0 <= k, X_0+U > k)
 - P(X_0 > k, X_0+U <= k).
```

Only threshold-crossing revision states matter. Revision variance alone does **not** imply a directional edge; without asymmetry or a threshold-relative location effect, upward and downward crossings can cancel.

**Algorithm.** Compile each contract into `source × series × vintage × transformation × rounding × cutoff`. Where two publications derive from a shared underlying index, use the arithmetic identity to transport probability intervals. Where revisions intervene, estimate a joint vintage model from original releases and later revisions, with change-point sensitivity. BLS explicitly documents revisions to seasonally adjusted CPI series [S7]; that establishes the need for versioning, not an available arbitrage.

**Trade and payer.** If B−A is underpriced, purchase YES B plus NO A, whose expected payoff is `1+E[Y_B-Y_A]`. Unlike idea 4, no nesting is assumed: the basket can pay 0, 1 or 2. Alternatively buy the single mispriced contract if a basket's collateral and fees dominate. The payer confuses the economic latent variable with a particular published representation.

**Cheap discrimination.** Start with one release family, not a generic macro predictor. Can original releases be reconstructed freely, and are there simultaneously tradable contracts referring to distinct but linked values? The local 48 CPI contracts are far fewer independent releases; don't mistake strikes for sample size.

**Kill it if** all listed contracts use the same first release, exact transformations leave no disagreement, or revision crossings have no reliable sign. Annual revisions learned from finalized data cannot be fed into earlier trades.

**Novel variation:** trade a joint *release-version* distribution or transformation error rather than compete on the best macro nowcast.

## 8. Follow a wallet's need for cash, not its opinion

**Hypothesis.** A loss in one market can induce selling in an unrelated market held by the same cash-constrained wallet. The price move in the second market can look like information but actually reflect portfolio financing.

For a resolution shock in event e, define a pre-position-based wealth surprise

```
W_w = Σ_{i in e} holdings_{w,i,pre} (payoff_i - pre_event_value_i).
```

Define a pressure score for unrelated market j using *pre-shock* holdings and an estimated sensitivity to cash shocks:

```
S_j = Σ_w β_w holdings_{w,j,pre} max(-W_w,0).
```

Normalize for accessible liquidity. Importantly, a losing token becoming worthless does not mechanically force liquidation: this is a behavioral cash-demand hypothesis. Frozen collateral and cash returned on settlement are separate channels and must be separated from unexpected PnL.

**Algorithm.** Pick a small public-wallet cohort with observable positions and activity. Use past unrelated resolutions to estimate whether losses predict broad proportional liquidation rather than targeted information trades. Look for sales across several unrelated holdings and stable prices in comparable markets with different wallet ownership. Predict pressure only using positions known before the shock.

**Trade and payer.** Buy a depressed YES (or NO if that is the token being liquidated) after an unrelated shock, conditional on a financing signature and no new event-specific evidence. Exit on recovery with two-sided costs, or hold to settlement if an independently bounded payoff expectation justifies it. The payer values immediate cash more than a few cents of contract value.

**Cheap discrimination.** A 20-wallet, one-month event study can ask whether the behavior exists at all. It cannot establish deployable alpha. Examine event-time ordering: pressure must persist after a realistic observation and decision delay. Compare ownership overlap, not only market-topic similarity.

**Kill it if** wallet funds and positions are too incomplete, common news explains both markets, losses are too anticipated, or funding arrives from unobserved wallets. Wallet count is not independent-investor count. Public history retention and transfer coverage still need a capability check.

**Novel variation:** a predicted cash-demand network across unrelated events, explicitly contrasting with the existing “copy historically skilled wallets” study. Evidence that wallet flow is informative would not establish this mechanism.

## 9. Trade the data point that is about to leave the window

**Hypothesis.** Traders extrapolate the newest observation while ignoring deterministic aging in a rolling average or cumulative settlement statistic.

For a rolling mean of n observations,

```
M_{t+1} = M_t + (X_{t+1} - X_{t-n+1})/n.
```

The outgoing observation is already known. Conditional on history, uncertainty in the next mean comes from the new observation, with its loading reduced by `1/n`. For a fixed-horizon average with m observations already realized,

```
P(mean > K) = P(Σ_future X_i > nK - Σ_observed X_i).
```

That converts a headline threshold into the exact remaining hurdle. If the first six daily values of a seven-day average sum to 12.0 and the required average is above 2.0, the last value must exceed 2.0, regardless of whether the latest daily reading looked bullish.

**Algorithm.** Search rules for explicitly defined averaging or cumulative windows. Recompute the remaining threshold using only confirmed source observations. Model the future block jointly, preserving serial dependence and possible revision. Screen for an outgoing outlier whose removal changes the distribution materially without any new fundamental story.

**Trade and payer.** Buy the side favored by deterministic aging when the quote appears anchored to the latest reading or an older window. Target slow multi-day windows; final-minute BTC averages return us to the latency problem we deliberately avoided.

**Cheap discrimination.** Rules-first inventory scan: do suitable, liquid contracts exist? Weekly gas markets in the repo are a place to inspect the exact rules, **not evidence that those contracts settle on a weekly average**. “Weekly” can describe listing cadence only. Reject candidates without a true rolling/cumulative formula.

**Kill it if** no relevant contracts exist, observations revise substantially, or outgoing-value effects are fully priced. The algebra is elementary and known; the orphan opportunity would be the repeated failure to apply it to a neglected settlement formula.

## 10. Identify the wrong model from its characteristic mistakes

**Hypothesis.** Some price setters reuse a convenient model across contracts whose rules differ. Instead of fitting an unrestricted predictor, detect the residual pattern that a particular wrong model must produce.

Examples of wrong models: treating an average as an endpoint; treating “ever above K” as “above K at D”; treating “officially announced” as “reported by any source”; using one clock convention across different cutoff rules.

For models m with predicted contract values `g_m(x_t, rules_j)`, infer which model best explains *past quote changes* across related contracts. Then compute the signed difference from a rule-correct model, keeping both models' uncertainty. This is a mixture over possible pricing models, not a license to call our favorite model correct.

A concrete mathematical fingerprint: for a driftless Brownian process starting at 0 and barrier b>0,

```
P(max_{s<=T} X_s >= b) = 2[1-Φ(b/(σ√T))],
P(X_T >= b)           = 1-Φ(b/(σ√T)).
```

The factor of two is specific to this idealized model. With jumps, drift, discrete monitoring or log-price transformations, derive the appropriate relationship. The proposal is to find systematic pricing as if the *wrong payoff functional* were in force, not trade that factor blindly.

**Trade and payer.** Buy a touch YES if it is persistently priced like a terminal YES and uncertainty bounds still give edge; trade the opposite side when a rule mismatch reverses the error. Hold to settlement. A reusable pricing shortcut can generate many small, persistent mistakes in low-attention contracts.

**Cheap discrimination.** Choose one model confusion before viewing outcomes. Require its distinctive residual pattern across thresholds, times and rule variants, with controls where the two models agree. Fit the fingerprint on one set of markets and predict its sign on another; don't infer a model anew from every losing trade.

**Kill it if** the pattern is equally explained by stale quotes, missing path observations, different risk compensation, or our misspecified dynamics. We cannot identify a particular bot from prices alone; only a candidate pricing behavior.

**Novel variation:** structural diagnosis of the crowd's computational shortcut. The component formulas are established mathematics; the research bet is persistent, rule-specific model reuse.

## 11. The choice to wait can reveal hidden readiness

**Hypothesis.** When an actor controls when to announce or file, a missed attractive opportunity may reveal private readiness more strongly than ordinary elapsed time would suggest.

Let `R` denote private readiness. Suppose an actor chooses action time by comparing an immediate reward with the value of waiting:

```
V(state,t) = max{ announce_value(state,t), E[V(next_state,t+1)] }.
```

The action policy induces `P(wait | R, public opportunity)`. Observing deliberate waiting changes the posterior odds of readiness by

```
posterior odds(R) = prior odds(R) × P(wait|R,opportunity) / P(wait|not R,opportunity).
```

Missing a ceremonial date with a large public payoff could be stronger negative evidence than missing an ordinary Tuesday. Conversely, an actor known to hold completed work for a later announcement may make silence nearly uninformative.

**Algorithm.** Restrict to one repeated actor/process with public calendars, filing choices or release windows. Specify the public benefit of each opportunity before the event. Infer broad classes of timing policy and hidden readiness, then pass the readiness posterior into the deadline model in idea 1. Include cancellation and outside-option states.

**Trade and payer.** Buy NO after an unusually attractive opportunity is passed up, only where the actor actually had discretion and the calendar choice is observed. Buy YES after a costly public commitment that would be unattractive if readiness were low. The payer models announcements as exogenous Poisson arrivals and misses strategic selection.

**Cheap discrimination.** Does a missed high-value opportunity forecast later delay more strongly than a missed low-value opportunity at the same remaining horizon? Use publicly scheduled opportunities, never retrospectively chosen “obvious announcement dates.”

**Kill it if** readiness and publicity strategy cannot be separated, the actor changes policy, or the calendar is too subjective to label without hindsight. This is inverse decision-making under partial observation, which is an identification problem before it is a trading strategy.

**Novel variation:** actor incentives determine the likelihood of silence. More speculative than idea 2 because observation reliability alone is insufficient; we must model strategic choice.

## 12. The specialist who trades everything except this

**Hypothesis.** An expert wallet's selective failure to participate may be informative, but only when we have evidence it was active, funded, and likely to have noticed the opportunity.

Let `Y` be a market's outcome, `A=1` indicate a specialist is observably active in comparable markets, and `N=1` indicate no observed participation in the target after a predeclared opportunity. The potentially useful quantity is

```
LR = P(N=1 | Y=1,A=1,price,access,cash)
   / P(N=1 | Y=0,A=1,price,access,cash).
```

If `LR<1`, selective absence is negative evidence for YES. But its sign is learned, not assumed: the specialist might prefer other opportunities, have exposure elsewhere, or avoid an ambiguous market regardless of outcome.

**Algorithm.** Establish specialization on past decisions and outcomes only. Define opportunity episodes independently of that wallet's future behavior. Require contemporaneous activity in comparable contracts and evidence of available buying power. Model participation jointly with trade direction, capital pressure and current price. Test whether absence improves a forecast already conditioned on market price and ordinary wallet flow.

**Trade and payer.** Buy NO against a crowd move that historically knowledgeable, currently active participants systematically decline to endorse, if the resulting posterior clears costs. Symmetrically use nonparticipation after a crowd selloff if the learned sign supports YES. This is not “experts didn't buy, therefore sell.”

**Cheap discrimination.** A small manually audited cohort should first establish whether nonparticipation can even be measured. An unfilled resting order is intention without a trade; a hidden wallet is participation without the observed address. If public data cannot distinguish these well enough, stop before modeling outcomes.

**Kill it if** selective absence adds nothing conditional on price, cash and activity, or if the relevant choice set is unobservable. Controls from idea 8 are needed to avoid calling cash constraints private information.

**Novel variation:** informative abstention with an activity control, as opposed to conventional copy trading. Highest novelty ambition here, but lowest confidence in identifiability.

## What I would hand to execution agents

These are bounded research assignments, not permission to trade money or a new infrastructure roadmap.

| Assignment | Smallest useful output | Stop before building if… |
|---|---|---|
| Gates + evidence (1,2) | 15–20 frozen contract/source cards, gate graph, report-delay ranges, price-blind probability bounds | Stage ordering or evidence visibility cannot be verified |
| Rule differences (4) | Ten paired rulebooks, explicit payoff tables, all-in basket costs and locked capital | Residual event is ambiguous or extra capital destroys attractiveness |
| Weather channel (5) | One station, 20 boundary days, source-to-official-value likelihood diagram | Correct processing eliminates predicted boundary error |
| Conditional propagation (3) | One gate/child family; a few news updates classified before child-price inspection | News cannot be localized to a conditional branch |
| Ladder shape (6) | One event type, synchronized quote intervals, held-out threshold predictions | Parameters cannot be identified within spread uncertainty |
| Publication/window/model scans (7,9,10) | A rules inventory identifying actual eligible contracts and one exact transformation per candidate | The alleged distinction is absent from the actual rules |
| Wallet mechanisms (8,12) | Public-data capability audit and a small event/choice-set reconstruction | Position, cash or intention visibility is inadequate |
| Timing policy (11) | One recurring actor, ex-ante opportunity calendar and competing timing explanations | Opportunity labels require hindsight |

For every candidate, record: rule version and capture time; event, publication and receipt clocks separately; observed state; competing explanations; payoff function; quoted YES and NO asks and sizes; every fee; maximum capital at risk; entry/exit or settlement rule; the qualitative signature and kill observation. Use lower/upper probability ranges from substantive alternative models, not confidence intervals from a tiny pilot disguised as certainty.

A practical review sequence:

1. **Does the mechanism exist?** An exact payoff identity or demonstrable publishing artifact is stronger early evidence than a favorable backtest.
2. **Does it change an observable decision?** If every plausible model recommends the same trade as the crowd, no edge is being proposed.
3. **Does the disagreement survive an hour?** Evaluate several predeclared decision delays; an effect requiring seconds is outside our mandate even if real.
4. **Can we buy enough at the ask to matter?** Quote availability and capital occupation can kill a statistically impressive signal. Record tiny capacity honestly.
5. **What would distinguish this mechanism from stale prices, ordinary calibration or generic news fading?** Each idea above has a predicted interaction or residual pattern.

The current scheduled-calibration holdout is reserved for its existing hypothesis. Do not mine it for these twelve ideas and then describe it as untouched confirmation. A new prospective enrollment or genuinely separate event-family/time partition is needed for later confirmation. Current metadata, final volume, amended end dates, revised source data, and the decision to study a memorable outcome all need point-in-time handling. Cluster by the underlying event; shared source failures and macro releases may require larger clusters than the exchange's `event_id`.

I would spend the first research time trying to establish **one repeated process mistake**, especially gate count, disagreement-state pricing or weather measurement. The valuable output is a short set of cases where we can explain exactly why a quote is wrong and what would make us change our mind. A dozen weakly parameterized backtests would be less informative.

## Sources and novelty boundaries

Sources checked 2026-10-23. They support institutional facts or establish nearby prior art; they do not validate our proposed signals. This was a targeted check, not an exhaustive novelty search.

- **[S1]** [Polymarket trading fees](https://help.polymarket.com/en/articles/13364478-trading-fees), also [developer fee documentation](https://docs.polymarket.com/trading/fees): category-dependent taker fees; do not reuse the repo's zero-fee placeholder indiscriminately.
- **[S2]** [Kalshi fee guidance](https://help.kalshi.com/en/articles/13823805-fees): some markets have different fees, including maker fees. Obtain the applicable schedule for a concrete trade.
- **[S3]** [Polymarket resolution documentation](https://docs.polymarket.com/concepts/resolution): rules define settlement, clarification is possible, and fractional resolution is a relevant exceptional state. This motivates explicit payoff tables.
- **[S4]** [Saguillo et al., *Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets*](https://arxiv.org/abs/2508.03474): within-market and combinatorial arbitrage are already studied. We do not claim their invention or rely on the paper's profit estimates.
- **[S5]** [Capponi et al., *Agentic AI for Clustering, Relationship Discovery, and Semantic Trading in Prediction Markets*](https://arxiv.org/abs/2512.02436): automated semantic relationship discovery is already studied. Our proposal needs a narrower payoff/process distinction than “use an LLM to connect markets.”
- **[S6]** [NWS Chicago weather observations and climate products FAQ](https://www.weather.gov/lot/weather_observations_faq): measurement, averaging and rounding can make displayed observations differ from official extrema. Station-specific verification remains necessary.
- **[S7]** [BLS CPI technical notes](https://www.bls.gov/cpi/technical-notes/): seasonally adjusted indexes can be revised. This does not imply that any given listed contract uses a revised value.

The original research work here is the proposed decomposition, signatures, selection rules and falsifiers. Erlang waiting times, Bayes factors, payoff replication, quantized measurement models, distribution sensitivities and optimal stopping are established tools. Novel alpha, if any, must come from locating a neglected mechanism where the market's approximation is predictably wrong and the error survives real execution costs.
