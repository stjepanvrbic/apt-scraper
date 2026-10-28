---
title: The ranking learns the user's taste in measured phases
type: Feature
state: In Progress
priority: 2
assignee:
milestone:
parent: 0015
blocked_by: []
related: [0020, 0023, 0024, 0025, 0027]
---

## Outcome

The ranking gets better with every label the user gives, measured in time order on real stores and on simulated users with intervals, and each step of the [ranking model plan](../research/ranking_model_plan.md) ships only when it beats its number. The explanations stay exact and name what moved each listing, reranks stay under 2 seconds without a model call, and everything but the model reads stays on the user's machine.

## Acceptance

Phase 0, measure:

- [x] A replay harness scores a model on a read-only copy of a store: the prequential held-out rank of each favorite and contact request (fitted on the labels before it, ranked among the listings ranked at the time), the prequential accuracy on Compare answers, the leave-one-out check as the rerank reports it, and the garden regression of 0027. Held-out ranks come with bootstrap intervals, pairwise accuracy with Wilson intervals.
- [ ] The harness reproduces the stored leave-one-out ranks of the 129-listing store of 0025 exactly: 3, 18, 25, 80, 115.
- [x] Simulated users: a sparse taste function over the real feature records plus noise labels listings as the gallery would show them, over at least 200 runs with different seeds and taste functions, and the harness reports accuracy against number of labels for any model variant.
- [ ] Extraction test-retest: 20 real listings read twice on Sonnet, agreement reported per field (exact match for categories and flags, within one for ratings, Jaccard for tags), and the fields whose agreement is below 0.7 listed.
- [x] Pairwise questions pair the listings with the largest posterior variance of their feature difference among the top 40, not the closest to a probability of one half; a test shows two listings with identical records are never paired.

Phase 1, priors and recency:

- [x] Columns are grouped (quality ratings, size and layout ratings, kitchen, bathroom, flags, categories, tags, price and size, location, judgments and hypotheses), each group's prior scale is set by maximizing the Laplace evidence, and tags and hypotheses have a per-column scale with a floor.
- [x] Composite columns for finish, light and space, with the components as deviations under a tighter scale, and the explanation names the composite and the component that deviates most.
- [x] Labels are weighted by age with a half-life chosen prequentially, with a floor, and hyperparameters are tuned prequentially only from 10 labels on; below that, the defaults.
- [x] Measured: on simulated users with 20 labels, pairwise accuracy on held-out pairs at least 0.05 above the model on `main`, averaged over 200 runs; on each real store, prequential median held-out rank no worse by more than 2 and better on at least one; the 300-listing, 50-favorite rerank under 2 s CPU. If simulation shows no gain, the priors stay and Result says why.

Phase 2, labels worth more:

- [x] The 5 to 8 cold-start questions are chosen by the same selector on the prior covariance, so they differ most on the features the stated preferences do not pin down.
- [x] The gallery logs card impressions (a card visible for at least one second) next to the other implicit signals, and the harness forms skip-above pairs from them as a diagnostic.
- [x] Favorite and dismiss offer a one-line "why", read by the note reading into the prior.
- [x] Measured: in simulation, labels needed to reach 0.75 pairwise accuracy fall by at least 25% against the selector on `main`; on the owner's store, once 40 Compare answers exist, prequential pairwise accuracy at least 0.65 with the lower 90% bound above 0.5. If the selector does not cut the label count in simulation, the selector on `main` stays and Result says why.

Phase 3, hypotheses:

- [x] After a rerank with new labels, the review pass proposes up to three named hypotheses (phrase, kind visual or textual, evidence) about what separates the liked listings that no current column expresses, and no longer moves listings. Reasons, flags and findings stay.
- [x] A textual hypothesis is judged for every listing by the judgment pipeline and becomes a `hypothesis:<phrase>` column with prior mean 0. A visual hypothesis without a local encoder re-reads the photos of the labeled listings and the top 50 only, under a budget the Result records.
- [x] A hypothesis stays while the prequential check with it is no worse than without and its weight stays away from zero; otherwise it retires after a fixed number of labels and is not proposed again. At most six live. The Rankings tab shows each as the model's guess; confirm makes it a stated preference, reject retires it.
- [ ] Measured: on the 129-listing garden store, the pass proposes the finish or renovation split within two reranks, and the unseen renovated garden units' mean rank improves with the column; on the owner's store, at least one hypothesis survives over 20 labels; one model call per rerank with new labels. If no hypothesis survives on any store after 40 labels, the pass goes back to reasons and flags only.

Phase 4, local encoder, authorized regardless of Phase 3 outcome:

- [ ] A background job embeds every unique photo once per content with an image tower chosen for measured quality, cached by fingerprint and photo URL, centered per room type over the store, in its own ONNX Runtime/numpy environment and pinned by hash. Compare SigLIP 2 B/16 and larger variants, MobileCLIP2 S2 and larger variants, and stronger candidates found, at fp32/fp16 as well as int8; quantize only without measurable quality loss. Encoding stays off the rerank path, whose existing 2 s CPU check remains.
- [ ] Three kinds of named column, nothing else: a text probe per visual hypothesis and per visual stated preference; per-room best-match similarity to the most similar favorite for kitchen, bathroom, living and bedroom, explained as "kitchen looks like your favorite X"; and a room-type check that flags an extraction disagreeing with the encoder on most photos.
- [ ] Quality gates, measured before any column enters the model: room-type agreement with extraction at least 85% on 1,000 photos; AUC at least 0.85 for five probes against record fields (carpet, exposed brick, balcony, bathtub, open kitchen). Report user-side download size, peak memory and the 3,000-photo encoding time at 4 threads as information, not gates.
- [ ] Blind test, criterion written down before the run: at least 30 unseen listings, the owner picks the 10 they would view blind to rank, precision at 10 of the model with the encoder at least 0.5 and above the model without it. Any gate or the blind test failing ends the phase with the encoder out.

Phase 5, implicit signals and drift:

- [ ] Decided from the prequential check on the owner's store over four weeks whether skip-above pairs or dwell labels enter the fit: only if the median held-out rank with them is better and the lower bootstrap bound of the improvement is above zero. Otherwise they stay diagnostics.
- [ ] The half-life is tuned on the same data and reported.

Throughout:

- [ ] Spec › Ranking, Acceptance and Decisions are updated through the owner: the garden-unit item replaced by the prequential and pairwise criteria, the review pass without moves, questions "whose answer would tell the model most", a similarity item allowed in a reason, and "learning from N labels" in the Rankings tab.
- [ ] Each phase is merged to `main` through a reviewed pull request with its measurements in Result, and can be the last.

## Spec

[Spec › Ranking](../spec.md#ranking), [Spec › Listing features](../spec.md#listing-features), [Spec › Gallery](../spec.md#gallery), [Spec › Contracts and invariants](../spec.md#contracts-and-invariants), [Spec › Acceptance](../spec.md#acceptance) (Ranking, Rerank, Pairwise questions, Review pass), [Spec › Decisions](../spec.md#decisions)

## Design

The [ranking model plan](../research/ranking_model_plan.md) has the reasoning, the model, the evaluation protocol, the costs, the failure modes and the phases. In short: keep the linear model and the concept-bottleneck shape, fix the priors (group scales by evidence, composites, sparse tags), evaluate prequentially with intervals and simulated users, make labels worth more (information-gain questions, impressions, a "why" line), let the review pass propose named hypotheses that code scores and the held-out check gates, and evaluate a small ONNX image encoder behind a measured gate and a pre-registered blind test, regardless of the hypothesis outcome as the owner authorized. No Gaussian process, no neural network on the labels, no model as ranker.

The scoring core stays on the standard library through phase 3. Phase 4 brings numpy and ONNX Runtime in its own environment, built and checked by the installers as the search environment is. Stores for measurement are read-only copies: the embedding spike's 129 listings at `%TEMP%\apt-embedding-root`, the end-to-end run's 100 at `%TEMP%\apt-e2e-root`, and the authorized profile workspace copy. The gallery directory was copied once with plain file copies into `%TEMP%/apt-0055-store`; only copies are opened through SQLite. The original profile is never written or used as HERMES_HOME.

Phases 0 to 2 change no visible UI but the Compare questions and the "why" line; the "why" line, the hypothesis prompts in Rankings and "learning from N labels" have their frontend done by a separate design-focused agent, as the umbrella says.

## Deviations

- Owner decision: the 150 MB download target, 1 GB memory limit and 10-minute encoding-time limit are withdrawn. Encoder choice is by room agreement, probe AUCs and the blind test, with float and quantized candidates compared for quality. Sizes, memory and encoding time remain reported measurements. The 2-second gallery rerank bar remains.
- The text tower is a build-time tool only. It produces hash-pinned vectors for the named probes, room types and known visual hypotheses; those vectors ship with the image tower. Runtime handling of later user-authored probes is an owner decision and is not designed here.

- Owner clarification: optimize when a measurement shows a real problem for the user or the result; arbitrary size, storage and speed numbers are not requirements. The journal compression revert is explicit. The atomic journal and its chronological semantics remain.

- The automatic review job stays as on main because the real hypothesis gates are unmet. The explicit copy-based trial prepares the no-move review and judgment tasks; the existing scheduled review is not silently changed. The gallery controls and named columns operate on hypotheses supplied through that trial.
- Grouped evidence priors were rejected earlier, so hypothesis columns use the existing sparse solver with a fixed Gaussian standard deviation at most 0.5 and prior mean zero. Survival requires at least 20 new labels, absolute fitted weight at least 0.1, and no worse paired prequential median rank or pair accuracy. The weight floor is a declared shrinkage threshold, not a significance test; the Laplace interval is reported separately.
- Removals and neutralized notes do not advance the 20/40-label clocks or trigger another proposal pass. The last meaningful event identifies a pass; legacy labels receive one initial pass at event zero. Known-positive actions are excluded from held-out hypothesis ranks. Score ties receive chance credit for the paired hypothesis comparison; the main Compare Wilson report still excludes ties from its binomial denominator.
- An unscored hypothesis has no inferred direction. The gallery says it is still learning and disables confirmation until an assessment supplies a direction; reject remains available. Confirming a scored hypothesis copies its judgments into the stated preference, without moving the current listing order.
- Review tasks are stored as plain JSON text without a duplicate full-input snapshot. The migration regression constructs the historical schema directly instead of rewinding a current schema and guessing which newer tables to drop.


- Phase 2: the prior-only selector is an experimental candidate, not the installed policy, because its label-efficiency bar was not established. Its baseline is the variance selector delivered in Phase 0, fixed by the saved feedback artifact rather than changing as PRs merge. The candidate asks up to eight disjoint prior questions, uses one quarter of the variance for columns with stated prior means, then returns to the posterior selector.
- A card impression requires at least half the card in the viewport for one continuous second, with a visible document and no open details drawer. It records content fingerprint, seen rank and page visit, once per mounted content/rank; failed replies retry with the same event id while the card remains mounted. Skip-above pairs use only prior impressions from the selected card's visit and exclude stale content, future events and explicitly labeled alternatives. They remain diagnostics at weight 0.1, never fit rows. Favorite snapshots retain only the latest impression per listing in the selected visit; other action snapshots omit impressions. Raw events remain intact. Timestamp comparisons handle timezone offsets and exclude future events, and partial indexes bound visit lookup work.
- The small frontend changes reuse the current Mantine components in this implementation. No separate design worker was launched from the delegated implementation task. Reasons are optional inline fields, saved through the existing user-note route; existing note reading handles them without another model path.


- Phase 1: the candidate stays in `tests/ranking_priors.py`; the installed `fit_model` and its priors are unchanged because both the simulation and CPU gates failed. Composite display and action-date capture are shared with the experiment, but the installed design creates no composite columns.
- Evidence optimization uses up to 20 monotonic fixed-point steps with backtracking, not a claimed global optimum. Tags and hypotheses use per-column scales directly, with a 0.1 standard-deviation floor; a separate multiplicative group scale would be unidentifiable without another constraint. Composite components have a tighter default scale of 0.25.
- Half-life selection uses log loss on the last ten journaled Compare answers and their original pre-action snapshots, choosing among no decay, 90, 30 and 7 days. Before ten usable historical snapshots it keeps no decay; the weak-negative weight stays 0.05. Synthetic answers arrive one per day with stationary tastes. Real action dates are captured where known; old favorite dates are not invented. No real recency benefit is claimed.

- Owner decision, 2026-10-27: attempt Phase 4 regardless of the hypothesis result. Downloads are allowed for the isolated encoder experiment; the owner later withdrew its size and memory limits. Real-photo gates without a cached dataset and the blind test remain owner-run, unchecked items. Phase 5 is not authorized.

- Phase 0: exact prequential replay needs action-time records and candidate sets. Existing feedback has neither timestamps nor historical values. A new transactional `learning_events` journal stores pre-action inputs; old history is explicitly unavailable rather than reconstructed from future records.
- The documented earlier experiment roots are absent. The authorized workspace copy contains 63 listings, 31 current feature records and 28 eligible records, but no user labels and none of the five original garden favorites. It is not the 129-listing experiment. Actual user-label gates remain unmet; simulated users are reported separately.
- The isolated Hermes runtime fails before chat with "no dependency environment is committed for this install; run `hermes pm repair`". The same symptom is tracked in the [upstream fresh-home report](https://github.com/NousResearch/hermes-agent/issues/143476). The 20-listing retest has 286 cached photos and 40 prepared tasks, but no model outputs. Its agreement check remains not run; no owner installation or credentials were borrowed to work around the missing environment.
- Owner decision: journal compression is withdrawn. Snapshots are plain JSON; one migration converts previously compressed rows back to text. The earlier compressed-size measurements below are historical, not a storage requirement. Combined restore/favorite calls journal both transitions with intermediate pre-action states. Known-positive actions are separated from genuine held-out positives; observed Compare ties are reported outside the Wilson denominator, while latent/model ties in simulations receive chance credit. Bootstrap tails use symmetric order-statistic indices. Actual displayed ranks have their own row field and median/interval; refitted ranks remain the model-comparison metric, since substituting stored ranks would make every model variant return the same result.
- Proposed owner spec change: describe Compare as selecting answers that tell the model most, using posterior difference variance. The implementation follows the assigned ticket; `docs/spec.md` is unchanged.

## Result

### Phase 0: measurement and comparison selection

On `feat/ranking_phase0_measure`, based on `main` at `324cb52`. The installed fit and leave-one-out calculation are unchanged; Compare maximizes posterior difference variance among the top 40 and excludes identical records. The event journal makes new actions replayable without later records or labels leaking backward.

`python tests/ranking_experiments.py --runs 200 --labels 40 --workers 4 --output docs/research/ranking_measurements_phase0.json` records the complete metrics. The accompanying checksummed compressed feedback artifact preserves the synthetic corpus, answers, latent tastes and held-out pairs for paired comparisons in later experiments. Seeds 0 through 199, 80 synthetic listings, five randomly selected taste columns, Gaussian answer noise with standard deviation 0.3, 100 fixed held-out pairs per user, excluded from query selection. Intervals are 95% percentile bootstrap over users, 2,000 resamples.

- Corrected held-out pair accuracy on synthetic records: zero labels 0.499575 [0.491425, 0.507550]; 10 labels 0.591825 [0.582675, 0.600950]; 20 labels 0.641675 [0.631225, 0.651625]; 40 labels 0.691025 [0.680800, 0.701825]. The fixed trajectories reproduce every earlier score before applying the corrected tie policy. Of 20,000 held pairs, 3,549 have tied latent taste.
- The 300-listing, 50-favorite store reranked in 1.00 CPU second, least of three, below 2 seconds. Its 50 setup favorites precede any stored rank and are correctly excluded from the chronological rank metric; no historical rank is invented.
- Synthetic garden regression: the one unseen garden unit moves from rank 3 [3, 3] to rank 6 [6, 6] as the five favorites occupy the top ranks. It beats all 11 eligible similar-price nonfavorites both before and after. This is a code regression, not evidence about the missing real garden units.
- Chronological synthetic fixture: four favorites and one contact scored at ranks 19, 2, 10, 14, 19 among 24; median 14 [2, 19], top-20 fraction 1.0 [1.0, 1.0]. Its one Compare answer is incorrect, accuracy 0.0 with Wilson 95% interval [0.0, 0.7935]. This checks the replay path, not a quality bar.
- Real-store replay and leave-one-out were executed on a disposable copy: zero favorites, dismissals, contacts, user notes, Compare answers and journal events. Held-out lists are empty; medians, accuracy and intervals are null. There is no real-label evidence to meet a rank or pairwise bar. None of the five historical garden favorites is present, so exact 3, 18, 25, 80, 115 reproduction and that garden regression remain unmet.
- A separate 200-user simulation over the 28 eligible real feature records reaches 0.754725 [0.745950, 0.763125] at 20 simulated labels and 0.814125 [0.805750, 0.821675] at 40. These are virtual preferences, not owner labels. All 28 candidates fit within the selector's top-40 pool. The frozen real records stay locally with the measurement copy.
- Snapshot payload on the 300-listing fixture: mean 746,096.5 bytes before compression, 63,859.56 after; compressed range 63,844 to 63,866 bytes over 50 events. On the actual 63-listing copy, one snapshot is 173,739 raw bytes and 30,573 compressed bytes. Over 20 samples, snapshot read median is 2.197 ms [2.145, 2.223]; read plus compression inside a write transaction is 4.933 ms [4.865, 4.961]. No user feedback was fabricated or saved for this measurement. Existing SQLite free pages are reusable after migration; no immediate file-size shrink is claimed.
- Stop condition: none for Phase 0. The missing real-data checks remain open. The ticket remains In Progress and the merge acceptance remains unchecked.

Verification before the review fixes: full suite 665 tests and 317 subtests passed, zero skipped; Compare headless 11 passed. The review fixes have 19 targeted measurement tests passing, including compression migration, combined transitions, tie handling, prior-positive exclusion and cache selection. After the fixes: full Git Bash Python suite 674 passed and 317 subtests passed, zero skipped (327.02 s); full headless Playwright 40 passed, zero skipped (1.9 min); build, gallery lint and `ruff check .` pass. The final CodeRabbit review approved the updated head before merge.

Tech debt fixed: the migration fixture now reconstructs the old schema completely; the Compare accessibility assertion accepts all three seeded listing prices; the guide no longer calls a fit with weak negatives the untouched prior.

### Measurement coverage

The source read is SQLite `mode=ro`; backup, migration and fitting run on a disposable copy. Unit tests check source hashes, pre-action snapshots, retry idempotency, future-label exclusion, empty-sample intervals, exact Wilson values, seeded simulation, garden peers, repeat extraction agreement and duplicate-record exclusion. No skipped tests are added.

The extraction retest is blocked on the isolated Hermes dependency environment. The real content and photos are prepared: 20 distinct listings, 286 retained photos, 287 URL downloads cached once, 40 independent tasks. After provisioning a working isolated runtime with the Claude subscription provider and Sonnet delegation, run:

```powershell
$retest = Join-Path $env:TEMP 'apt-0055-photos/retest-current'
$env:HERMES_HOME = Join-Path $env:TEMP 'apt-0055-hermes'
hermes chat --oneshot -Q -q (Get-Content (Join-Path $retest 'prompt.txt') -Raw)
python profile/skills/ranking/scripts/extraction_retest.py $retest
```

The preparer requires 20 current listings with cached photos and sheets and raises if fewer exist. No network fetch is in this path. Forty independent child reads produce the two samples; the collector reports room/category/flag agreement, ratings within one point, tag Jaccard and fields below 0.7. These commands do not start a gateway or dashboard.


Real-store evidence: [inventory and replay](../research/ranking_real_store_phase0.json), [simulated users on real records](../research/ranking_real_feature_simulation_phase0.json). Reproduce the latter with `python tests/measure_real_ranking.py <copied-store.sqlite3> <local-report.json>`; the companion feedback file contains the local records and is not published. The first operator copy included `listings.sqlite3`, `search.sqlite3`, `features.json` and the empty extraction directories. No original database was opened through SQLite or written.

| Phase | Delivery | Measured outcome |
| --- | --- | --- |
| 0 | Merged PR #50 as `432fabd` after CodeRabbit approval | Measurement infrastructure; 1.00 s rerank; real labels 0; retest runtime prerequisite open |
| 1 | Merged PR #51 as `7002c7b` after CodeRabbit approval | Prior candidate not promoted |
| 2 | Merged PR #52 as `bf03fbb` after CodeRabbit approval | Impressions/reasons; cold-start candidate not promoted |
| 3 | PR #53 rebased onto merged #54; all local checks green | Real labels 0; automatic pass not promoted |
| 4 | Authorized, not started | Encoder experiment and owner-run blind test |
| 5 | Not authorized | Owner decision and longitudinal data required |

Re-review: the frozen-feedback rescorer now rejects inconsistent run counts, truncated curves/trajectories and non-finite accuracies before fitting. A regression test distinguishes displayed rank 1 from refitted ranks 1 and 3 under two alternative models. Both actual exposure and counterfactual model quality are reported.

Re-review verification: 676 Python tests and 317 subtests passed, zero skipped (299.77 s); full headless Playwright 40 passed, zero skipped (1.7 min); gallery lint and Ruff pass. The synthetic chronological fixture has displayed median 16 [2, 21] and refitted median 14 [2, 19], showing the exploration-order difference explicitly.

### Phase 1: rejected prior candidate

`python tests/ranking_candidate_experiment.py docs/research/ranking_measurements_phase0.json docs/research/ranking_measurements_phase1.json` evaluates the exact same 200 users, first 20 answers and 100 held-out pairs per user from the checksummed feedback artifact.

- Baseline accuracy 0.641675 [0.631225, 0.651625]. Candidate 0.622525 [0.611625, 0.632875]. Paired change -0.019150 [-0.025400, -0.012700], against the required +0.05. Intervals are 95% bootstrap over users, 2,000 resamples.
- Candidate full rerank on 300 listings and 50 favorites, including store read/write and leave-one-out: 2.234375 CPU seconds, least of three, against under 2 seconds. The installed baseline measured 1.00 s in Phase 0.
- On the 28 eligible real feature records with 200 simulated users, baseline accuracy is 0.754725 [0.745950, 0.763125], candidate 0.739975 [0.730050, 0.749650], paired change -0.014750 [-0.020950, -0.008300]. This also fails +0.05. These are virtual preferences, not user labels. See [real-feature prior experiment](../research/ranking_real_feature_priors_phase1.json).
- Real-store replay and leave-one-out executed for both models on the authorized copy: zero user labels, no held-out ranks, and null medians and intervals. The real rank-improvement bar remains unmet. See [the baseline and candidate replay report](../research/ranking_real_store_phase1.json).
- Stop condition fired: the simulation shows no gain. Priors, composites and decay stay out of the installed fit, as on `main`. The experiment remains reproducible for a real-data reassessment. The independent random rating fixture does not model the correlations in real apartment finishes, so this rejects promotion on available evidence, not every possible grouped prior.

The targeted measurement and prior suite passes: 32 tests, zero skips. It covers original historical states, known and unknown action dates, evidence improvement and floors, composites, ties, and immutable simulated histories. After the rebase: full Git Bash Python suite 687 tests and 317 subtests passed (283.20 s); full headless Playwright 40 passed (1.7 min); gallery lint, build and Ruff pass. Zero skips. CodeRabbit approved final head `a4ea149` before the squash merge.


Review correction for Phase 1: the tuner no longer reconstructs past states from current inputs. Replay supplies the actual earlier journal snapshots, preserving past dismissals, feature records, readings and preferences, and excluding later labels. The frozen pair-only simulator constructs its history from its original corpus and answer prefixes. A regression test fails under the old reconstruction and passes with the historical snapshots. Undated legacy favorites retain full weight; legacy contact requests use their existing requested-at timestamp. Equal composite components are no longer falsely described as above the rest.

The refreshed synthetic prior report is run `f580db55f8362e73`; the real-feature simulation is run `e072d6f741d8ac0f`, both dated 2026-10-27 UTC. Each report records its command and the least-of-three CPU timing rule. All 400 frozen trajectories have scorable historical pairs, so excluding unscorable journal events does not change these measured accuracies. A regression now covers nine scorable answers plus a missing-record answer (default retained), and ten scorable answers followed by a missing-record answer (only the ten usable snapshots compared).

Final verification after these review fixes: full Git Bash Python suite 689 tests and 317 subtests passed (289.32 s), zero skips; Ruff passes. The production and gallery files are unchanged from the passing 40-test headless run, gallery lint and build.

### Phase 2: impressions and reasons, cold-start candidate not promoted

`python tests/ranking_selector_experiment.py docs/research/ranking_measurements_phase0.json docs/research/ranking_measurements_phase2.json` evaluates 200 users with the same corpus, seeds, tastes, noise and held-out pairs as the baseline. The horizon was fixed at 40 answers.

- Synthetic records, 200 users: at 20 labels, cold start 0.633925 [0.624925, 0.643175], baseline 0.641675 [0.631225, 0.651625]. At 40: candidate 0.690875 [0.680800, 0.701850], baseline 0.691025 [0.680800, 0.701825]. Paired change at 40 is -0.000150 [-0.008100, 0.007850]. Intervals are 95% bootstrap over users.
- Neither synthetic mean curve reaches 0.75. Both threshold interval endpoints exceed 40 labels; all 2,000 bootstrap draws are censored. No reduction percentage is inferred from those censored curves.
- Real feature records, 28 eligible listings and 200 virtual tastes: candidate accuracy is 0.752075 [0.742825, 0.760775] at 20 labels and 0.803725 [0.796150, 0.810900] at 40. Its paired change at 40 is -0.010400 [-0.018350, -0.003050]. These are simulated preferences, not owner answers.
- Both selectors reach 0.75 at 20 labels on the real records, with 95% paired bootstrap threshold intervals [20, 25] for each. Label reduction is 0.0 [-0.25, 0.20], against at least 0.25. No threshold draw is censored. The no-reduction stop condition fires on this simulation; the prior-only candidate stays out and the installed variance selector remains. See [the real-feature selector report](../research/ranking_real_feature_selector_phase2.json).
- Real-store replay executed on the authorized 63-listing workspace copy: zero Compare answers against the required 40, zero held-out positives, null accuracy and intervals. The real pairwise bar remains unmet. See [the replay report](../research/ranking_real_store_phase2.json).
- Impressions and their skip-above diagnostic, optional favorite/dismiss reasons, content identity in card summaries, and the data migration are implemented. The existing implicit-dwell diagnostic and scoring fit are unchanged.

The snapshot-cost harness runs as `python tests/measure_snapshot_cost.py <copied-store.sqlite3> report.json`, backs up the supplied copy, and rolls back every measured journal insertion. The source hash is unchanged in its regression, including the command-line run. On the real copy, 20 samples give a median payload of 30,617 bytes [30,616, 30,617], snapshot read 2.359 ms [2.3445, 2.4258], and journal write inside the transaction 4.5535 ms [4.5149, 4.5972]. With 6,300 synthetic impressions added only to the disposable backup, snapshots retain 63 impressions: 33,794 bytes [33,793, 33,794], read 2.5718 ms [2.5295, 2.7122], journal write 4.7998 ms [4.7315, 4.9258]. Retaining every impression would make that snapshot 1,521,094 raw bytes or 78,490 compressed bytes. The new snapshot has 187,303 raw bytes. These stress events are not user activity. See [snapshot costs](../research/ranking_snapshot_cost_phase2.json).

The seven label/impression tests cover scoping to the selected visit, latest seen ranks, future timestamps, timezone offsets, preserved raw signals, retry idempotency, threshold censoring and source-copy integrity. Full headless Playwright passes 43 tests (1.8 min), zero skips; gallery lint and build pass. Full Git Bash Python verification passes 696 tests and 317 subtests (305.90 s), zero skips; Ruff passes. Production fitting still ignores impressions and skip-above pairs.

Review follow-up: if the baseline reaches 0.75 but the candidate does not, the selector stop condition now fires; a censored baseline remains explicitly unestablished. Neither recorded run changes outcome. A headless regression commits an impression through the server, loses its response, forwards the retry and verifies identical event IDs and one stored row. The eight targeted label tests and four learning browser tests pass, zero skips.

### Plain JSON journal

Merged as PR #54, `1d48c16`, after CodeRabbit approval and complete green checks.

The journal writes JSON text directly and replay reads JSON without a compression branch. The compression migration is removed; its schema-version slot is reserved so existing version-16 databases retain their numbering. One new migration rewrites BLOB snapshots to UTF-8 JSON text, leaving event IDs, ordering and already-plain rows intact. A corrupt compressed row aborts the whole migration. Tests cover mixed rows, Unicode, repeated opening and rollback. No owner database is opened or modified; real measurements use the authorized copy.

On the authorized 63-listing copy, 20 samples measure a plain snapshot at 173,826 bytes [173,825, 173,826], read time 3.0865 ms [2.7927, 3.3330], and journal insertion 3.3865 ms [3.2300, 3.5432]. With 6,300 synthetic impressions only in the disposable backup, the 63-row relevant-visit snapshot is 187,303 bytes [187,302, 187,303]; insertion is 3.8300 ms [3.6713, 4.2248]. These sizes and times are information, not limits. Actual replay remains empty: zero journal events and held-out observations, with null intervals. See [plain-JSON measurements](../research/ranking_snapshot_plain_json.json). The 43 targeted tests pass. Complete Git Bash Python suite: 698 tests and 317 subtests passed (340.91 s); full headless gallery suite: 44 passed (2.3 min); gallery lint, build and Ruff pass. Zero skips.

### Phase 3: explicit hypothesis trial, automatic pass not promoted

The candidate prepares at most three novel named proposals per pass, at most six live, validates evidence against the supplied records, and rejects retired phrases and move output. Textual judgments cover every listing in batches of 25. Visual judgments cover labeled listings and the top 50, use cached sheets only, and instruct the reader to open at most six full-resolution photos per listing. Missing cached photos remain pending.

`python tests/ranking_hypothesis_experiment.py docs/research/ranking_measurements_phase3.json --benchmark` produced run `228427da4395ba88`, dated 2026-10-28 UTC. This is a scripted synthetic lifecycle with known judgments, not a model discovering the cue:

- After 20 new labels, the planted original-wood-trim column survives: weight 0.329081, Laplace 95% interval [-0.635005, 1.293168]. Its median prequential rank is 35.5 [23.5, 44.0], versus 42.0 [28.0, 50.5] without it. Mean paired rank improvement is 6.0 [2.85, 10.8], using 2,000 bootstrap resamples of the 20 held-out actions.
- The constant textual cue and unscored visual cue retire after 20 labels. A separate regression proves that 40 new Compare answers without a survivor stop later proposals; favorite removals cannot manufacture those 40 labels.
- There are 21 requested passes for 21 reranks with new labels, and zero remote model calls. This verifies request deduplication and the budget, not the quality or cost of a live model response. The judgment budget is three text batches and 50 visual listing reads, at most six full-resolution opens each.
- The shared 300-listing, 50-favorite, one-Compare-answer, six-hypothesis fixture reranks in 1.453125 CPU seconds, least of three, with observed range [1.453125, 1.531250], below the 2-second bar. The complete store read/write and leave-one-out are timed, and the strict test uses the same fixture.
- Across the 21 task rows, plain-JSON mean payload is 182,127.52 bytes [180,816.71, 182,898.67]. The interval is bootstrap over the stored synthetic passes.
- On a fresh disposable backup of the authorized real copy, `prepare`, `assess`, `status`, chronological replay and leave-one-out all ran. The source hash is unchanged. There are 63 listings and zero labels: zero proposal requests, zero hypotheses, zero remote calls, empty held-out samples and null intervals. The required real 20-label survivor does not exist; that bar remains unmet. None of the five historical garden favorites is present, so the two-rerank garden-discovery and mean-rank bars also remain unmet. See [real-store hypothesis evidence](../research/ranking_real_store_phase3.json).
- The real 40-label stop condition has not fired because there are zero labels. Automatic hypothesis generation stays out on the unmet real-data bar; the owner-authorized encoder experiment follows independently.

For an explicit trial, use `hypothesis_trial.py init <copied-store.sqlite3> <new-trial-directory>`, then `prepare <trial-directory>/listings.sqlite3 <tasks-directory>`. Run its prepared review task in the same isolated Sonnet runtime required by the extraction retest, apply it with `apply-review ... --file <review-output.json>`, prepare `judgments ... --cache <cached-extraction-directory>`, apply their outputs with `apply-judgments`, and run `assess` after judgments and subsequent labels. The current real copy cannot produce a task because it has no labels; the isolated Hermes dependency prerequisite remains open. These commands never require the owner's live profile as HERMES_HOME.

Final verification: full Git Bash Python suite 721 tests and 317 subtests passed (401.52 s); complete headless Playwright 45 passed (2.5 min); gallery lint, build and Ruff pass. Zero skips. Desktop and mobile screenshots were inspected, including an unknown direction with confirmation disabled. The 300-listing/50-favorite/six-hypothesis CPU test retains its strict two-second limit.

The final review follow-up for PR #52 passed 697 Python tests and 317 subtests (263.78 s), 44 headless browser tests (2.0 min), gallery lint, build and Ruff, with zero skips. Its recorded numerical outcomes are unchanged.

Hypothesis review corrections: a legacy rerank without a review produces no task; survival comparisons require the hypothesis column to exist in the original pre-action fit; missing cached files leave that listing pending while unsafe paths still fail; judgment tasks reuse the listing-field formatter with address, price, rooms, size and the complete description. Hypothesis tasks are plain JSON as well. The proposed per-event prediction cache is not applied: predictions now also depend on which hypothesis column must exist, so caching an absent-column result by event alone would suppress other valid comparisons. An underlying-fit cache would be a separate optimization requiring a measured problem.

Concurrent assessments now check the store revision and update only active hypotheses. A confirm, reject or new label arriving during computation prevents stale writes; the result reports the current state with `applied: false`. The interleaving regression passes for all three cases. Numerical trial results are unchanged because this guard affects only concurrent mutations.
