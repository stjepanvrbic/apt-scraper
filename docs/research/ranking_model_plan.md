# How the ranking should learn the user's taste

Question: what is the best way, from first principles and current practice, to turn a listing's photos, text and structured record into signal, and to learn one user's taste from the labels this product produces, so that the gallery's order gets better with every label and stays explainable, local and fast?

It follows the [preference learning research](preference_learning_for_listing_ranking.md), the [embedding spike](embedding_preference_model.md) and an independent review of that spike. It takes the review as one input, not the answer. Statements marked "estimate" are arithmetic from cited figures, not measurements. Sources are listed by number under [Sources](#sources).

## Summary for the owner

- The architecture is right and the data are the limit. A model that reads every photo once into named features and fits a small Bayesian model on them is the correct shape for one user with tens of labels: it is a concept bottleneck [8], which gives exact explanations by construction and lets priors carry what the labels cannot. With 5 favorites, no model learns much. Held-out ranks of 80 and 115 for two of the five garden favorites are the data's verdict, not a method failure. The garden experiment also asked the wrong question: a one-feature rule the schema already had, judged by one tester on 16 units, where 6 against 7 is noise.
- Three things move the needle, in this order. First, get more out of each label: choose pairwise questions by information gain rather than nearest to a coin flip, which today can pick two near-identical listings and learn nothing; weigh labels by recency; evaluate in time order. Second, make the feature space learnable from few labels: group related columns under shared prior scales so 5 labels can move "finish" as a whole and 50 can tell kitchen from bathroom. Third, let the model name what it is missing: after a rerank, a model pass proposes up to three named hypotheses about what separates the favorites, code turns each into a feature scored for every listing, and the held-out check decides whether it stays. That is where the review pass earns its cost, instead of moving listings, which it has never done in four live runs.
- Local encoders come later, if at all, and smaller than the spike built. A named probe scored by a modern image encoder (SigLIP 2 [1] or MobileCLIP2 [2]) over cached per-photo embeddings is the only way a visual hypothesis such as "white kitchen cabinets" can be scored across all listings without re-reading the photos, and the only way a preference stated later can be judged from photos, which today it is not. On ONNX Runtime that is a 6 MB runtime [7] and a 40 to 200 MB model, not the 1.7 GB the spike cost. It is gated on the hypothesis phase showing that visual hypotheses exist that judgments cannot score, and on a pre-registered blind test.
- Do not do: a neural network trained on the user's labels, a Gaussian process in the rerank, fine-tuning, collaborative filtering, or a model that ranks the listings. None fits tens of labels, the 2-second rerank, or the explanation requirement.
- Measure first. Phase 0 builds a replay harness that scores any model on the stored real listings in time order, with bootstrap intervals, plus simulated users for power, and measures how repeatable the extraction is. Every later phase has a number it must beat and a condition under which it stops.

## What the system is and where it stands

Today's pipeline, from [the ranking guide](../ranking.md):

- Extraction, once per listing content, on Sonnet: a room type per photo, 12 ratings from 1 to 5 with confidence, 4 categories, unit level, 10 flags, up to 8 traits and 5 concerns from a tag list that grows, a summary, and a verdict per stated preference the schema does not express.
- Code features: price, size, price per square foot, rooms, unit level from the unit label, coordinates, distance to the nearest station and to each place, the criteria areas, nearness to the favorites.
- A Bayesian logistic regression over about 170 columns, Gelman-scaled [11], Gaussian prior whose means come from stated preferences and note readings, favorites and contact requests positive, dismissals and negative notes negative, every other ranked listing a weak negative at 0.05 or 0.1, pairwise answers as difference rows. Prior scale and weak-negative weight chosen by an approximate leave-one-out check on the favorites. Laplace posterior for exploration (Thompson sampling at ranks 7 and 14) and for pairwise questions.
- Exact per-feature explanations, a most similar favorite, a model review pass that rewrites reasons and may move listings when the held-out check allows, implicit signals logged and reported but unused.

Where it stands, from the [scoring model ticket](../tickets/0020-scoring_model.md), the [verification](../tickets/0027-verify_ranking_redesign.md) and the [end-to-end run](e2e_run_2026-10-26.md):

- 129 real listings, 5 garden favorites: 6 of 16 unseen garden units above every similar-price listing, mean unseen rank 29.5, held-out ranks 3, 18, 25, 80, 115. Before the favorites, 0 of 16 and mean rank 107.7. The model learned; it learned `ceiling_height` at −1.34 alongside `unit_level:garden` at +1.00, because garden units have low ceilings, and it ranked renovated garden units low because the favorites were vintage.
- The review pass made no moves in four live runs and rewrote reasons. The embeddings did not help as built.
- Reranks take 0.3 s live and 1.1 s on the 300-listing, 50-favorite fixture.

## What "accurate" means here

One user. Tens of labels for the first weeks, hundreds after months. Labels arrive in time order, from the listings the user saw, which are the ones the model ranked high. The user's criteria and taste drift, and sometimes change outright. The question the user is asking the gallery is: of the listings I have not labeled yet, which should I look at first?

So accuracy is prequential [13]: for each label in time order, how well did the model, fitted on everything before it, rank the listing the user then favorited, or predict the pair the user then answered? That is the number to optimize and report. It has four properties the current check lacks:

- It never lets a later label explain an earlier one. Leave-one-out does, and it rewards a model that memorizes clusters of favorites that arrived together.
- It measures the ranking the user actually saw at each moment, exposure bias included.
- It shows drift: a model that was right in March and wrong in May shows it.
- It applies to pairwise answers as naturally as to favorites.

Three metrics, all from stored data, all with intervals:

1. Prequential held-out rank of each favorite and contact request among the listings ranked at that time: median, and the fraction in the top 20. Interval by bootstrap over favorites.
2. Prequential pairwise accuracy on Compare answers: the fraction of answers the model fitted on earlier labels predicts. Chance is 0.5. Interval by Wilson score [14].
3. Leave-one-out held-out rank, kept for continuity with the stored reranks and as the within-rerank tuning signal.

What the numbers can and cannot say. With 30 pairwise answers, an accuracy of 0.70 has a 95% interval of about ±0.16 (estimate); with 100, about ±0.09. With 5 favorites, no model comparison on held-out rank is more than a hint. So real stores are the sanity check, and simulated users give the power: a simulated user is a sparse taste function over the real feature records plus noise, labeling listings as the gallery would show them, hundreds of runs with different seeds and taste functions. Label-efficiency curves from simulation (accuracy against number of labels) are what decide between two model variants; real stores decide whether the simulation was honest. The garden experiment stays as one regression test, not as the bar.

A blind judgment by the owner belongs in the protocol once, at the end of the encoder phase, where the question is whether photos carry taste the record misses: at least 30 unseen listings, the owner picks the 10 they would view, blind to rank, criterion written down beforehand.

## The signal

### What the labels carry

| Label | What it says | How much |
| --- | --- | --- |
| Favorite | This listing is in the user's set, given what they saw | About one bit against the shown set, more as a positive among many unlabeled |
| Contact request | A favorite the user acted on | Same, stronger |
| Dismissal | Not this, for a reason the user did not state | One bit, often about one disqualifying fact |
| Pairwise answer | A over B, both plausible | Near one bit when the model was unsure, near zero when it was sure |
| Note | Why, in words, about named features | The most information per action: it names features and a direction. Already read into the prior |
| Stated preference | A direction on a named feature, or a phrase to judge | A prior mean, overruled by labels |
| Implicit: dwell, opens, photo views | Attention, not preference | Weak, confounded by photo count and rank position |

Two consequences. Notes and pairwise answers are the cheapest bits to get more of, so the product should ask for them well: pairwise questions chosen for information, and a one-line "why" offered on favorite and dismiss, read by the same note reading. And the weak negatives are not negatives: a listing the user never opened says nothing. The positive-unlabeled assumption [12] is violated by exposure, and the prequential check is the one that keeps the model honest about it.

### What the features carry

The record is a concept bottleneck [8]: a model maps photos and text to named concepts, a small model maps concepts to taste. The literature on these models is clear about their two failure modes, and both show up here.

- Concept noise. A 1-to-5 rating from a language model is a judgment, and judgments vary between runs. Repeat-run reliability of model ratings is high for well-defined fields and lower for nuanced ones [9], and vision input adds variance [10]. The scoring model shrinks ratings by the stated confidence, which helps only if the confidence tracks the variance. This has never been measured here. It is cheap to measure: extract 20 listings twice and compare field by field. A feature with low test-retest agreement caps what any model can learn from it, and no amount of modeling fixes that.
- Concept incompleteness. Whatever the user's taste rests on that the schema does not name is invisible. The garden experiment's residual misses were explained by the record (renovation, finish, style), so there it was not incompleteness. But the thing the tester used to recognize garden units, "soffited ceilings, small high windows", has no column, and neither does "white cabinets", "dark floors", "original wood trim" or "that 1990s beige". Traits are the schema's escape hatch, but they are binary, sparse, worded by the model, and limited to 8.

### Photos, specifically

The spike averaged CLIP ViT-B/32 unit vectors over all photos and over rooms, uncentered, and put them in a kernel. Averaging over a listing's photos moves every listing toward "a photo of an apartment interior", so cosine similarities between listings sat at 0.87 to 0.88 whether or not they were alike. That is a known property of CLIP space (a small cone), not a finding about apartments. Three changes make photo embeddings informative, and all three come for free with per-photo caching:

- Center. Subtract the mean embedding over the store's photos (or over the room type) before any similarity. Similarities then spread over the full range.
- Compare like with like. Kitchen to kitchen, bathroom to bathroom, best match rather than mean: the similarity of a listing to a favorite is the maximum over the favorite's photos of that room, not the average over everything.
- Name it. A text probe ("a kitchen with white cabinets") scored against each photo is a feature with a name, usable in the linear model and in a reason. This is the label-free concept bottleneck construction [15]: concepts scored by an image-text encoder, weights learned by a linear model. It is the only way to add a visual concept after the fact without reading the photos again.

Encoder candidates, if a local encoder is built:

| Encoder | Image tower | Zero-shot ImageNet | Size on disk (estimate) | Notes |
| --- | --- | --- | --- | --- |
| CLIP ViT-B/32, 2021 (the spike's) | 88M params, 224 px, 49 tokens | 63% | 605 MB fp32 as shipped | 63% room-type agreement with extraction in the spike. Dated |
| MobileCLIP2-S2 [2] | 35.7M, 256 px | 77.2% | 140 MB fp32, about 40 MB int8 | 3.6 ms per image on an iPhone neural engine; tens of ms on a laptop CPU (estimate). Apple license, check before shipping |
| MobileCLIP2-B [2] | 86M, 224 px | 79.4% | 350 MB fp32, about 90 MB int8 | |
| SigLIP 2 B/16 at 256 [1] | 86M vision, 256 tokens | 78.2% at 224; better localization and dense features than SigLIP | 370 MB fp32 vision tower, about 95 MB int8 | Apache-2.0. The review's suggestion. About 5× the compute of B/32 per image (estimate: 256 against 49 tokens) |
| DINOv3 ViT-S/16 [3] | 21M | No text tower | 85 MB fp32 | Best visual similarity per parameter; no probes by text, so no names. Only as a similarity channel |

The runtime matters more than the model. The spike's 1,064 MB environment was PyTorch. ONNX Runtime's CPU wheel is about 6 MB [7], numpy about 15 MB, and an int8 image tower 40 to 100 MB. Total install under 150 MB and memory under 1 GB are reasonable targets (estimate). Converting the chosen encoder to ONNX once is a build step for the implementer, with the exported file pinned by hash as the spike pinned weights.

Time budget, estimate from the spike's measurement: CLIP B/32 ran 32 photos per second on 4 threads. SigLIP 2 B/16 at 256 is about 5× the compute, so about 6 photos per second, 3,000 photos in about 8 minutes at 4 threads. MobileCLIP2-S2 is about 2× B/32's compute, so about 3 minutes. Both are fine for a background job that runs once per listing content, as extraction does.

A small local vision-language model (SmolVLM 256M to 2B [4], Moondream, FastVLM) could score concepts with a text question instead of an embedding, but on a laptop CPU it is seconds per photo, so hours per 3,000 photos, and its judgments are below Sonnet's. Not now.

### Text, specifically

The text channel is already served better by the language model than by any sentence embedding: extraction reads the description into flags, traits, concerns and judgments, and a later preference is judged from the record and text. The spike's text kernel improved the held-out check while it ranked the unseen garden units worse, and the one near-duplicate it found was the same landlord's boilerplate. Text similarity captures who wrote the listing. Leave text embeddings out. What text still lacks is cheap and code-derived: description length, a renovation year or "renovated" mention, the building's age when stated, and a lease term. Add them as code features.

## The model

### Keep the linear model, fix its priors

With n in the tens and p near 200, the prior is the model. The current prior is one Gaussian scale for every column, chosen among 1, 0.5 and 2 by the held-out check, with means from readings. Three problems, each with a stdlib-sized fix:

- One scale for everything. A prior that treats "kitchen finish" and "tag: package room" alike spreads five favorites' worth of evidence over 170 columns, and the garden experiment showed it: the garden weight shared its evidence with ceiling height, window size and style. Group the columns and give each group its own scale, learned by maximizing the Laplace evidence (automatic relevance determination [17], the empirical Bayes that the relevance vector machine made standard). Groups: quality ratings, size and layout ratings, kitchen, bathroom, flags, categories, tags, price and size, location, judgments and hypotheses. A group whose columns the labels do not use gets a small scale and stops absorbing evidence. This is a Newton loop around the existing Newton fit, with the same Hessian, so it stays in the standard library and within the budget if the number of groups stays near 10.
- Composite before component. For the quality ratings, add a composite column (the mean of kitchen finish, bathroom finish, condition and renovation recency) with its own prior, and let the components deviate from it under a tighter scale. Five labels move the composite. Fifty labels tell kitchen from bathroom. The same for light (natural light, window size) and for space (room size, ceiling height, layout openness, storage). The explanation shows the composite and the component that deviates most, which reads better too: "finish 4.5 of 5, kitchen above the rest".
- Sparsity where it belongs. Tags and hypotheses are many and mostly irrelevant. A horseshoe-type prior [16] is the right shape but needs sampling. The practical substitute within the evidence loop is a per-column scale for the tag and hypothesis groups only, with the regularized horseshoe's floor on shrinkage, so a tag that five favorites share can take a large weight while the other hundred tags stay near zero.

Beyond that, the model does not change. Logistic is right for binary labels and pairwise answers (the Bradley-Terry form every preference model from Christiano et al. [18] onward uses). The Laplace posterior is right for exploration and question selection. Newton in pure Python is fast enough.

### Recency and criteria changes

Weight each label by its age with a half-life, one more hyperparameter chosen prequentially, with a floor so an old favorite never falls to zero. A criteria change that removes an area or changes the price band leaves labels on listings outside the new criteria: they stay in the fit, because taste about kitchens does not change with the price band. Stated preferences are re-read when reworded, as today. The user can also say "start over on my taste", which drops the labels from the fit and keeps them in the store.

### Labels worth more: question selection, impressions, cold start

Pairwise questions today pair the two listings among the top 40 whose order the posterior is least sure of: the probability of one over the other closest to 0.5. Two identical listings satisfy that exactly, and the answer teaches nothing. The information a pair carries about the weights is the posterior variance of their difference, d = x_a − x_b, which is dᵀ Σ d under the Laplace posterior: the pairs to ask are those with the largest dᵀ Σ d among plausible candidates, which is the BALD criterion [19] for a logistic model and matched or beat random, entropy and query-by-committee selection in the original preference experiments. It prefers pairs that differ on features the model is unsure of, and it never asks about near-duplicates. The candidate set stays the top 40 so both listings are ones the user might actually want. Each question still goes to at most one listing, and skips still cool down.

Cold start is the same selector run on the prior: before any label, Σ is the prior covariance, and the 5 to 8 questions after the interview are the pairs that differ most on the features the stated preferences do not pin down.

Impressions. The gallery logs opens, dwell and photo views, but not which cards the user scrolled past. Without that, Joachims' skip-above pairs [20] (a favorite ranked 30 was preferred over the listings ranked above it that the user saw and passed over) cannot be formed, and that is the one implicit signal with a clear semantics. Log card impressions (a card visible for at least a second), then form skip-above pairs at weight 0.1 to 0.25 and let the prequential check decide whether they enter the fit. Dwell stays a diagnostic: it is confounded by photo count and by position.

### Hypotheses: the model names what the schema lacks

The piece that is missing is a path for taste that the schema does not name, with an explanation that still names it. The review pass is the place for it, and its current job, moving listings, has not earned its cost: no moves in four live runs, and moves are the one thing a language-model ranker is biased at [21].

The hypothesis pass. After a rerank with new labels, one model call reads the favorites' and dismissals' records and summaries, the current weights, and the held-out report, and proposes up to three named hypotheses about what separates the liked listings that no current column expresses, each as a short phrase, a kind (visual or textual) and the evidence. Code turns each into a feature:

- A textual hypothesis ("original wood trim", "a landlord who lists rent inclusive of heat") is judged for every listing by the judgment pipeline that already judges later stated preferences, from the record and the text, and becomes a `hypothesis:<phrase>` column with prior mean 0.
- A visual hypothesis ("white kitchen cabinets", "dark hardwood") needs the photos. Without a local encoder it goes through re-extraction of the labeled listings and the top 50 only, which is a budgeted model cost. With one, it is a text probe over cached embeddings, scored for every listing in milliseconds.

The next rerank weighs the columns like any other, under the sparse group prior. A hypothesis stays while the prequential check with it is no worse than without it and its weight stays away from zero; otherwise it is retired after a fixed number of labels, and the pass may not propose it again. At most six live hypotheses. Each is shown in the Rankings tab as "the model thinks you may like: …", which the user can confirm, which turns it into a stated preference, or reject, which retires it. That is the elicitation that Li et al. found draws more informative answers than user-written preferences [22], applied to the model's own residuals.

This is the honest version of "LLM in the loop": the model proposes, the data decide, and the reason can cite the hypothesis by name.

### Local encoder, gated

If the hypothesis phase shows visual hypotheses that judgments cannot score, or if later stated preferences judged without photos turn out to matter, build the encoder path as a background job in its own small environment:

- One int8 ONNX image tower (MobileCLIP2-S2 or SigLIP 2 B/16, chosen by the gate below), per-photo embeddings cached by content fingerprint and photo URL, centered per room type over the store.
- Three uses, all as named columns in the linear model: a text probe per visual hypothesis and per visual stated preference; per-room best-match similarity to the most similar favorite, one column per room type among kitchen, bathroom, living and bedroom, with the explanation "kitchen looks like your favorite X"; and a check of extraction's room types, so an extraction whose room types disagree with the encoder on most photos is flagged for a second read.
- No kernel, no Gaussian process, no listing-level mean vectors.

The gate is measured before any of it enters the model: room-type agreement with extraction on at least 1,000 photos (the spike's CLIP B/32 got 63%; below 85% the encoder's probes cannot be trusted), and five probe sanity checks against record fields the encoder should recover (carpet against `floor:carpet`, exposed brick, a balcony, a bathtub, an open kitchen), each an AUC over the listings. Then the blind test above.

### Reinforcement learning and bandits, honestly

The problem is a contextual dueling bandit [23]: arms are listings with features, feedback is preferences and clicks, the policy is the order shown. The practice that transfers is what the system already does, Thompson sampling for exploration [24] and a posterior for uncertainty, plus the information-gain query selection above. Regret-minimizing algorithms beyond that assume thousands of rounds; the user gives tens. A learned policy, off-policy evaluation and reward models trained on the user's data all need orders of magnitude more interaction. So "online learning" here means: the fit is milliseconds, every rerank is a full refit on all labels, exploration is Thompson sampling with two slots, and questions are chosen by information gain. Nothing more is warranted by the data.

### What not to build, and why

- A neural network on the user's labels: p ≫ n, no explanation, nothing to pretrain on.
- A Gaussian process in the rerank: 8 to 13 s at 300 listings in the spike, exemplar-only explanations, and its one real advantage (nonlinear similarity) is better served by per-room best-match columns.
- Gradient-boosted rankers: leaf minimums of 20 samples against tens of labels.
- Collaborative filtering: one user.
- The language model as ranker: position and popularity bias [21], not reproducible, a model call per rerank.
- Fine-tuning any model: no path on a subscription, and no data.

## Explanations

The requirement, a reason per listing naming the values for and against it, is met by the linear model exactly and should stay. Two amendments to the spec are worth making:

- A similarity item may name a favorite and a room: "kitchen looks like your favorite 2650 N Kedzie Ave". It is a named column with a value, so it is still citing the record and the store.
- The reason should say how much the model knows: "learning from 5 labels" in the Rankings tab, and "the model is unsure about this one" where the posterior standard deviation of the score is large. A user who sees "ranked 3rd" after one favorite is being told more than the model knows.

Hypotheses and composites both improve reasons: "finish 4.5 of 5" and "original wood trim (the model's guess, confirm or reject)" read better than three correlated ratings.

## Constraints worth challenging

- The garden-unit acceptance item. Replace Spec › Acceptance "Ranking" with the prequential and pairwise criteria below. A single-trait test judged by one tester on 16 units cannot tell 6 from 7 and never tests taste outside the schema.
- The review pass may move listings. Stop the moves. Keep the rewritten reasons, the flags against stated preferences and the findings, and give the pass the hypothesis job. Moves were the one model output the spec had to guard with a reversal rule, and they have never survived it with a gain.
- Standard library only. Keep it for the scoring core through the priors phase; the evidence loop fits in it. The encoder phase brings numpy and ONNX Runtime in its own environment anyway. When it does, allowing numpy in the ranking environment is a reasonable trade for a simpler solver, but it is a second code path until then, and not worth it on its own.
- The leave-one-out check tunes the hyperparameters. With 3 to 5 favorites, choosing among 6 candidates on 3 to 5 numbers is noise, and it chose prior scale 2 in the garden run, the loosest. Tune prequentially with a floor of 10 labels; below that, the defaults.
- Pairwise questions pair the listings "whose order the scoring model is least sure of". Reword to "whose answer would tell the model most".
- Implicit signals are unused. Keep that until the prequential check says otherwise, but log impressions so the one well-defined implicit label can exist.
- A preference stated later is judged without photos. That is a gap the encoder closes; until then, the judgment should say "not judged from photos" where it matters, and the hypothesis pass can budget a re-read of the top 50.
- Extraction's confidence is taken as the shrinkage. Measure test-retest agreement first; replace the confidence with the measured reliability per field if they disagree.

## Costs

| Item | Today | After the priors and labels phases | After the encoder phase (estimate) |
| --- | --- | --- | --- |
| Extraction per listing | One Sonnet subagent, about 15,000 image tokens | Same | Same, plus 3 to 8 minutes of CPU per 3,000 photos once, in the background |
| Rerank, 300 listings and 50 favorites | 1.1 s CPU | Under 2 s with the group evidence loop (to be measured; about 10 extra Newton solves) | Same plus up to 40 dense columns; the Hessian solve grows with the square of the dense count, so measure before adding probes |
| Model calls per rerank | One review | One hypothesis and review pass, only after new labels | Same |
| Judgments per hypothesis | | One judgment batch per 25 listings, as for later preferences | Visual hypotheses: none, a probe |
| Install | None beyond Hermes | None | About 150 MB (ONNX Runtime, numpy, one int8 tower), under 1 GB of memory |
| Disk per listing | Record and photos | Same | Plus 512 floats per photo, about 2 KB in float32; 60 KB per listing of 30 photos |
| Privacy | Photos and text reach Anthropic in extraction; labels stay local | The hypothesis pass sends the favorites' summaries and records to the model, which the agent's conversations already do | Embeddings stay local |

## Failure modes

- Extraction noise caps everything. Measured in phase 0; a field that does not repeat gets a tighter prior, or is reworked, or is dropped.
- Confounding in the labels. Garden units are dim with low ceilings; the model cannot know which the user wants until a label separates them. The information-gain selector asks exactly that pair. The composite prior spreads the evidence less badly meanwhile.
- Exposure bias. Favorites come from the top of the ranking. Exploration slots and the prequential check are the mitigations; weak negatives at 0.05 keep the unseen from counting as disliked.
- Hypotheses that fit the noise. The sparse prior, the prequential gate, the six-hypothesis cap and retirement after a fixed number of labels. A hypothesis the user confirms becomes a stated preference and leaves the cap.
- Probes that fire on the wrong thing. The gate's agreement and AUC checks, and a probe is retired like a hypothesis.
- Drift. The half-life, and the prequential plot, which shows it.
- The 2-second rerank. The evidence loop and the dense probe columns both cost; both are measured on the 300-listing fixture before they ship, and the loop can run every fifth rerank if it must.
- The owner as the only judge. Simulated users give power; the blind test is pre-registered; the real stores are three and growing.

## The phases

Each phase has a measurement, a number to beat and a stop condition. The ticket [0055](../tickets/0055-ranking_model_plan.md) carries them as acceptance items.

### Phase 0: measure

Build the replay harness over a store copy: prequential held-out rank and pairwise accuracy with intervals, leave-one-out as today, the garden regression, and simulated users over the real feature records. Measure extraction test-retest on 20 listings read twice. Fix the pairwise selector. The stores: the embedding spike's 129 listings, the end-to-end run's 100 and the owner's own once there is one, copied read-only.

Numbers to beat: the harness reproduces the stored leave-one-out numbers of the 129-listing store exactly (3, 18, 25, 80, 115). Stop: nothing stops here; it is measurement.

### Phase 1: priors and recency

Group scales by evidence, composites, sparse tag and hypothesis scales, recency half-life, prequential tuning with a floor.

Numbers to beat: on simulated users with 20 labels, pairwise accuracy on held-out pairs at least 0.05 above today's model, averaged over 200 runs; on each real store, prequential median held-out rank no worse by more than 2, and better on at least one; the 300-listing rerank under 2 s CPU. Stop: if simulation shows no gain, the priors stay as they are and the phase records why.

### Phase 2: labels worth more

Information-gain pairs, cold-start questions from the prior, card impressions, skip-above pairs as a diagnostic, a one-line "why" on favorite and dismiss.

Numbers to beat: in simulation, labels needed to reach 0.75 pairwise accuracy fall by at least 25% against today's selector; on the owner's store, once 40 Compare answers exist, prequential pairwise accuracy at least 0.65 with the lower 90% bound above 0.5. Stop: if the selector does not cut the label count in simulation, keep today's and record why.

### Phase 3: hypotheses

The review pass proposes; judgments score textual hypotheses; the sparse prior and the prequential gate decide; the gallery shows and takes confirm or reject; moves are removed from the review.

Numbers to beat: on the 129-listing garden store, the pass proposes the finish or renovation split within two reranks, and with the hypothesis column the unseen renovated garden units' mean rank improves; on the owner's store, at least one hypothesis survives the gate over 20 labels; one model call per rerank with new labels. Stop: if no hypothesis survives on any store after 40 labels, the pass goes back to reasons and flags only.

### Phase 4: local encoder, gated

Only if phase 3 produces visual hypotheses the judgments cannot score, or photos matter for later preferences. Build the ONNX job, the per-photo cache, centering, probes, per-room best match, the room-type check.

Numbers to beat: install under 150 MB of downloads and under 1 GB of memory; 3,000 photos in under 10 minutes at 4 threads; room-type agreement with extraction at least 85% on 1,000 photos; five probe AUCs at least 0.85; blind test on at least 30 unseen listings, criterion written before the run: precision at 10 against the owner's blind picks at least 0.5 and above the model without the encoder. Stop: any gate failing ends the phase with the encoder out, as the spike ended.

### Phase 5: implicit signals and drift

Decide from the prequential check on the owner's store over four weeks whether skip-above pairs or dwell labels enter the fit, and tune the half-life.

Numbers to beat: prequential median held-out rank with the signals better than without, lower bootstrap bound above zero improvement. Stop: otherwise they stay diagnostics.

## Sources

1. Michael Tschannen et al., "SigLIP 2: Multilingual Vision-Language Encoders with Improved Semantic Understanding, Localization, and Dense Features", 2025: https://arxiv.org/abs/2502.14786. Checkpoints at ViT-B (86M), L (303M), So400m (400M) and g (1B); B/16 at 224 scores 78.2% on ImageNet-1k zero-shot.
2. Apple, MobileCLIP2 model cards and checkpoint table (TMLR 2025): https://huggingface.co/apple/MobileCLIP2-S2 and https://huggingface.co/timm/MobileCLIP2-S0-OpenCLIP. S0 71.5% at 1.5 ms, S2 77.2% at 3.6 ms, B 79.4% at 10.4 ms, S3 80.7% at 8.0 ms image latency on an iPhone 12 Pro Max.
3. Oriane Siméoni et al., "DINOv3", 2025: https://arxiv.org/abs/2508.10104. Distilled ViT-S (21M) and ViT-B (86M) from a 7B teacher; no text tower.
4. Andrés Marafioti et al., "SmolVLM: Redefining small and efficient multimodal models", 2025: https://arxiv.org/abs/2504.05299.
5. Hermes Agent source, `tools/delegate_tool.py` and `tools/vision_tools.py`, as cited in the [preference learning research](preference_learning_for_listing_ranking.md).
6. The embedding spike's measurements: [embedding research](embedding_preference_model.md), on 129 real listings and 1,596 photos.
7. PyPI, onnxruntime 1.17.0 file list, Windows x86-64 CPU wheel 5.6 MB: https://pypi.org/project/onnxruntime/1.17.0/#files.
8. Pang Wei Koh et al., "Concept Bottleneck Models", ICML 2020: https://arxiv.org/abs/2007.04612.
9. Test-retest reliability of language-model rubric scoring, five repeated runs, ICC at least 0.89, lower on nuanced dimensions: https://preprints.jmir.org/preprint/94666.
10. Dongping Chen et al., "MLLM-as-a-Judge", 2024: https://arxiv.org/abs/2402.04788. Repeated-judgment consistency varies by model with vision input.
11. Andrew Gelman et al., "A weakly informative default prior distribution for logistic and other regression models", 2008: https://arxiv.org/abs/0901.4011.
12. Charles Elkan and Keith Noto, "Learning Classifiers from Only Positive and Unlabeled Data", KDD 2008: https://cseweb.ucsd.edu/~elkan/posonly.pdf.
13. João Gama, Raquel Sebastião and Pedro Pereira Rodrigues, "On evaluating stream learning algorithms", Machine Learning 90, 2013: https://doi.org/10.1007/s10994-012-5320-9. Prequential evaluation, after Dawid 1984.
14. Lawrence Brown, Tony Cai and Anirban DasGupta, "Interval Estimation for a Binomial Proportion", Statistical Science 2001: https://doi.org/10.1214/ss/1009213286.
15. Tuomas Oikarinen et al., "Label-Free Concept Bottleneck Models", ICLR 2023: https://arxiv.org/abs/2304.06129. Concepts scored by an image-text encoder, a sparse linear layer learned on top.
16. Juho Piironen and Aki Vehtari, "Sparsity information and regularization in the horseshoe and other shrinkage priors", Electronic Journal of Statistics 2017: https://arxiv.org/abs/1707.01694. The regularized horseshoe, and the effective number of nonzero coefficients as the way to set global shrinkage.
17. Michael Tipping, "Sparse Bayesian Learning and the Relevance Vector Machine", JMLR 1, 2001: https://www.jmlr.org/papers/v1/tipping01a.html. Per-group or per-weight scales by evidence maximization with a Laplace approximation.
18. Paul Christiano et al., "Deep Reinforcement Learning from Human Preferences", 2017: https://arxiv.org/abs/1706.03741. The Bradley-Terry form of preference likelihoods and an ensemble over resampled comparisons.
19. Neil Houlsby et al., "Bayesian Active Learning for Classification and Preference Learning", 2011: https://arxiv.org/abs/1112.5745. BALD; matched or beat random, entropy, query-by-committee and decision-theoretic selection on preference tasks.
20. Thorsten Joachims, "Optimizing Search Engines using Clickthrough Data", KDD 2002: https://www.cs.cornell.edu/people/tj/publications/joachims_02c.pdf.
21. Yupeng Hou et al., "Large Language Models are Zero-Shot Rankers for Recommender Systems", ECIR 2024: https://arxiv.org/abs/2305.08845. Position and popularity bias.
22. Belinda Z. Li, Alex Tamkin, Noah Goodman and Jacob Andreas, "Eliciting Human Preferences with Language Models", 2023: https://arxiv.org/abs/2310.11589.
23. Viktor Bengs, Róbert Busa-Fekete, Adil El Mesaoudi-Paul and Eyke Hüllermeier, "Preference-based Online Learning with Dueling Bandits: A Survey", JMLR 22, 2021: https://jmlr.org/papers/v22/18-546.html.
24. Olivier Chapelle and Lihong Li, "An Empirical Evaluation of Thompson Sampling", NIPS 2011: https://papers.nips.cc/paper/2011/hash/e53a0a2978c28872a4505bdb51db06dc-Abstract.html.
25. The production ranking code at `main`: `profile/skills/gallery/scripts/scoring.py` (`design`, `prior_means`, `fit_model`, `questions`), `rerank.py`, `implicit_signals.py`, `schema.py` (`pairwise_answers`, `implicit_signals`), and `docs/ranking.md`.
26. The spike branch `origin/spike/embeddings_gp` at `bff3632`: `embeddings.py` (listing-level and per-room mean vectors, uncentered), `embedding_models.py` (squared-exponential kernel over the means), read-only.
