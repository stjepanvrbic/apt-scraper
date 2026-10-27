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

- [ ] The 5 to 8 cold-start questions are chosen by the same selector on the prior covariance, so they differ most on the features the stated preferences do not pin down.
- [ ] The gallery logs card impressions (a card visible for at least one second) next to the other implicit signals, and the harness forms skip-above pairs from them as a diagnostic.
- [ ] Favorite and dismiss offer a one-line "why", read by the note reading into the prior.
- [ ] Measured: in simulation, labels needed to reach 0.75 pairwise accuracy fall by at least 25% against the selector on `main`; on the owner's store, once 40 Compare answers exist, prequential pairwise accuracy at least 0.65 with the lower 90% bound above 0.5. If the selector does not cut the label count in simulation, the selector on `main` stays and Result says why.

Phase 3, hypotheses:

- [ ] After a rerank with new labels, the review pass proposes up to three named hypotheses (phrase, kind visual or textual, evidence) about what separates the liked listings that no current column expresses, and no longer moves listings. Reasons, flags and findings stay.
- [ ] A textual hypothesis is judged for every listing by the judgment pipeline and becomes a `hypothesis:<phrase>` column with prior mean 0. A visual hypothesis without a local encoder re-reads the photos of the labeled listings and the top 50 only, under a budget the Result records.
- [ ] A hypothesis stays while the prequential check with it is no worse than without and its weight stays away from zero; otherwise it retires after a fixed number of labels and is not proposed again. At most six live. The Rankings tab shows each as the model's guess; confirm makes it a stated preference, reject retires it.
- [ ] Measured: on the 129-listing garden store, the pass proposes the finish or renovation split within two reranks, and the unseen renovated garden units' mean rank improves with the column; on the owner's store, at least one hypothesis survives over 20 labels; one model call per rerank with new labels. If no hypothesis survives on any store after 40 labels, the pass goes back to reasons and flags only.

Phase 4, local encoder, authorized regardless of Phase 3 outcome:

- [ ] A background job embeds every unique photo once per content with one int8 ONNX image tower (MobileCLIP2-S2 or SigLIP 2 B/16, chosen by the gate), cached by fingerprint and photo URL, centered per room type over the store, in an environment of its own with ONNX Runtime and numpy, pinned by hash.
- [ ] Three kinds of named column, nothing else: a text probe per visual hypothesis and per visual stated preference; per-room best-match similarity to the most similar favorite for kitchen, bathroom, living and bedroom, explained as "kitchen looks like your favorite X"; and a room-type check that flags an extraction disagreeing with the encoder on most photos.
- [ ] Gate, measured before any column enters the model: downloads under 150 MB and memory under 1 GB; 3,000 photos in under 10 minutes at 4 threads; room-type agreement with extraction at least 85% on 1,000 photos; AUC at least 0.85 for five probes against record fields (carpet, exposed brick, balcony, bathtub, open kitchen).
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


- Phase 1: the candidate stays in `tests/ranking_priors.py`; the installed `fit_model` and its priors are unchanged because both the simulation and CPU gates failed. Composite display and action-date capture are shared with the experiment, but the installed design creates no composite columns.
- Evidence optimization uses up to 20 monotonic fixed-point steps with backtracking, not a claimed global optimum. Tags and hypotheses use per-column scales directly, with a 0.1 standard-deviation floor; a separate multiplicative group scale would be unidentifiable without another constraint. Composite components have a tighter default scale of 0.25.
- Half-life selection uses log loss on the last ten already-observed Compare answers, choosing among no decay, 90, 30 and 7 days. Before ten answers it keeps no decay; the weak-negative weight stays 0.05. Synthetic answers arrive one per day with stationary tastes. Real action dates are captured where known; old favorite dates are not invented. No real recency benefit is claimed.

- Owner decision, 2026-10-27: attempt Phase 4 regardless of the hypothesis result. Downloads are allowed only for that isolated encoder experiment, within its size gate. Real-photo gates without a cached dataset and the blind test remain owner-run, unchecked items. Phase 5 is not authorized.

- Phase 0: exact prequential replay needs action-time records and candidate sets. Existing feedback has neither timestamps nor historical values. A new transactional `learning_events` journal stores pre-action inputs; old history is explicitly unavailable rather than reconstructed from future records.
- The documented earlier experiment roots are absent. The authorized workspace copy contains 63 listings, 31 current feature records and 28 eligible records, but no user labels and none of the five original garden favorites. It is not the 129-listing experiment. Actual user-label gates remain unmet; simulated users are reported separately.
- The isolated Hermes runtime fails before chat with "no dependency environment is committed for this install; run `hermes pm repair`". The same symptom is tracked in the [upstream fresh-home report](https://github.com/NousResearch/hermes-agent/issues/143476). The 20-listing retest has 286 cached photos and 40 prepared tasks, but no model outputs. Its agreement check remains not run; no owner installation or credentials were borrowed to work around the missing environment.
- Snapshot payloads use zlib BLOBs; a migration compresses existing text rows. Combined restore/favorite calls journal both transitions with intermediate pre-action states. Known-positive actions are separated from genuine held-out positives; observed Compare ties are reported outside the Wilson denominator, while latent/model ties in simulations receive chance credit. Bootstrap tails use symmetric order-statistic indices. Actual displayed ranks have their own row field and median/interval; refitted ranks remain the model-comparison metric, since substituting stored ranks would make every model variant return the same result.
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

Verification before the review fixes: full suite 665 tests and 317 subtests passed, zero skipped; Compare headless 11 passed. The review fixes have 19 targeted measurement tests passing, including compression migration, combined transitions, tie handling, prior-positive exclusion and cache selection. After the fixes: full Git Bash Python suite 674 passed and 317 subtests passed, zero skipped (327.02 s); full headless Playwright 40 passed, zero skipped (1.9 min); build, gallery lint and `ruff check .` pass. CodeRabbit re-review is pending for the updated head.

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
| 0 | PR #50, review fixes verified locally; re-review pending | Measurement infrastructure; 1.00 s rerank; real labels 0; retest runtime prerequisite open |
| 1 | PR #51, awaits rebase and corrected measurements | Prior candidate not promoted |
| 2 | PR #52, awaits rebase and corrected measurements | Impressions/reasons; cold-start candidate not promoted |
| 3 | Local candidate, not published | Hypothesis lifecycle and controls under verification |
| 4 | Authorized, not started | Encoder experiment and owner-run blind test |
| 5 | Not authorized | Owner decision and longitudinal data required |

Re-review: the frozen-feedback rescorer now rejects inconsistent run counts, truncated curves/trajectories and non-finite accuracies before fitting. A regression test distinguishes displayed rank 1 from refitted ranks 1 and 3 under two alternative models. Both actual exposure and counterfactual model quality are reported.

Re-review verification: 676 Python tests and 317 subtests passed, zero skipped (299.77 s); full headless Playwright 40 passed, zero skipped (1.7 min); gallery lint and Ruff pass. The synthetic chronological fixture has displayed median 16 [2, 21] and refitted median 14 [2, 19], showing the exploration-order difference explicitly.

### Phase 1: rejected prior candidate

`python tests/ranking_candidate_experiment.py docs/research/ranking_measurements_phase0.json docs/research/ranking_measurements_phase1.json` evaluates the exact same 200 users, first 20 answers and 100 held-out pairs per user from the checksummed feedback artifact.

- Baseline accuracy 0.64145 [0.63070, 0.65175]. Candidate 0.62215 [0.61100, 0.63320]. Paired change -0.01930 [-0.02610, -0.01280], against the required +0.05. Intervals are 95% bootstrap over users, 2,000 resamples.
- Candidate full rerank on 300 listings and 50 favorites, including store read/write and leave-one-out: 3.140625 CPU seconds, least of three, against under 2 seconds. The installed baseline measured 1.00 s in Phase 0.
- Real-store median-rank comparison: not run, authorized copies are absent. No claim of better ranks on a real store.
- Stop condition fired: the simulation shows no gain. Priors, composites and decay stay out of the installed fit, as on `main`. The experiment remains reproducible for a real-data reassessment. The independent random rating fixture does not model the correlations in real apartment finishes, so this rejects promotion on available evidence, not every possible grouped prior.

Seven targeted prior/date tests pass: exact preservation of stated-prior scores through the composite transform, a tighter default deviation scale, nondecreasing Laplace evidence, scale floors, defaults below ten labels, recency floors without aging unlabeled rows, missing components, component explanations, and known versus unknown action dates. The measurement module's twelve tests pass. Final Git Bash full suite: 674 tests and 317 subtests passed, zero skipped (289.93 s); `ruff check .` clean. An earlier full run had 673 passes and one diagnostic timing failure at 2.015625 s; the isolated check and the final full run passed with the 2-second limit unchanged. No owner store, Hermes root, credentials or listing site was used.
