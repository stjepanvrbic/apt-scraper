# Preference learning for listing ranking

Question: which methods and tools should replace the current ranking, so that it learns one user's taste from a few favorites, dismissals, notes and stated preferences, reads every photo of a listing, and explains each rank, on the user's own machine with a Claude subscription through Hermes, Python and no GPU?

It informs the redesign of Spec › Ranking and the ranking ticket ("Ranking that learns from favorites and notes"), which the owner holds unmerged on `feat/learning_ranking` while the design is reworked. The owner's requirements are taken as given: every photo analyzed, a two-stage pipeline (feature extraction once per listing content, then fast scoring in code on a "Score and rerank" button), an LLM review pass where it adds value, every favorite, dismissal and note used as a signal, learning from 5 to 50 favorites, and an explanation per listing.

Each claim cites the source read, listed under [Sources](#sources) by number. Statements marked "estimate" are my arithmetic from cited figures, not measurements. Statements marked "proposal" are design recommendations.

## Summary

- The current design (ranking doc and ticket on `feat/learning_ranking`) is a hand-weighted mix of nearest-neighbor similarity, shared-trait lift and fixed bonuses over a fixed vocabulary of tags read from 3 photos. Its own acceptance experiment shows the two weaknesses a redesign must fix: weights are hand-set rather than learned, and features the vocabulary or the 3 photos missed cannot be recovered. Three of 15 alike garden units stayed low because their profiles lacked the "garden unit" term [1].
- For 5 to 50 labels, the methods that fit are low-parameter models with priors: Bayesian logistic regression on a compact, mostly ordinal feature vector, with prior means set from the user's stated likes and dislikes and weak default priors elsewhere [8], fitted on favorites and dismissals, with unlabeled listings as weakly weighted negatives [9]. Gaussian process preference learning [7] is the nonlinear alternative. Gradient-boosted rankers need far more labels than this (LightGBM's default leaf minimum is 20 samples [12]). Collaborative filtering needs many users and does not apply.
- The expensive and decisive step is feature extraction. Hermes's `vision_analyze` takes one image per call and keeps its pixels in the conversation, re-sent on every later call [2]. Reading 30 full photos that way costs about 47,000 to 61,000 image tokens per listing before re-sends (estimate from [3]). Tiling a listing's photos into labeled contact sheets of 9 and handing them to a per-listing `delegate_task` child (up to 8 images per child [4]) cuts that to about 6,000 to 12,000 image tokens and keeps the images out of the main conversation (estimate from [3], [4]).
- Recommendation: option A below. Stage 1 extracts a fixed schema of ordinal ratings, flags and per-room observations from all photos and the text, plus a short list of free-text traits drawn from a tag list that grows, in one delegated model call per listing. Stage 2 fits a Bayesian logistic preference model in code on each "Score and rerank" press, in milliseconds and with the standard library only. Each listing's reason names its largest feature contributions (exact for a linear model [26]) and its nearest favorite. An optional LLM review pass writes the top listings' reasons and flags conflicts with explicit statements. Local CLIP and sentence embeddings (option B) are the escalation path if leave-one-out checks show favorites missed for traits outside the schema.

## Methods compared

"Fits few examples" means 5 to 50 favorites, a handful of dismissals, and tens to low hundreds of listings.

| Method | Data needed | Fits few examples? | CPU cost | Explainability | Libraries | Risks |
|---|---|---|---|---|---|---|
| Centroid or nearest-neighbor relevance feedback (Rocchio-style, close to the current design) | Feature vectors, positive and negative examples | Yes, no fitting | Negligible | Nearest exemplar, shared features | None needed | Every feature weighs what the metric says, so irrelevant features dilute similarity. No learned weights. The current design's experiment shows this [1] |
| Bradley-Terry on items | Pairwise outcomes between known items | No for this use | Negligible | Per-item score only | `choix` (MIT) [10], [11] | Estimates one parameter per item and takes no item features [11], so it cannot score an unseen listing |
| Pairwise logistic or Ranking SVM on feature differences | Preference pairs, from clicks or from favorite versus non-favorite | Yes with regularization. Joachims learned a retrieval function from about 112 queries [13] | Milliseconds | Linear weights | scikit-learn `LogisticRegression` (BSD-3) [14], or 50 lines of code | Pairs need a model of what the user saw. Joachims' click-over-skipped-above rule assumes ordered impressions [13] |
| Bayesian logistic regression with informative priors | Labels (favorite, dismissal), prior means from stated preferences | Yes. Weakly informative priors give finite answers even under complete separation [8] | Milliseconds for 100 features by 200 listings (estimate) | Exact per-feature contributions `w_j (x_j − mean_j)` [26] | scikit-learn L2 with `sample_weight` [14] (zero-mean prior only), or a short Newton solver for non-zero prior means | Quality is capped by the features. Prior strength needs tuning (leave-one-out) |
| Positive-unlabeled learning | Positives (favorites) and unlabeled listings | Yes | Milliseconds | As the base model | `pulearn` wraps it (not reviewed), or a weight in the model above | Assumes labeled positives are a random sample of all positives [9]. Favorites come from what ranked high and was seen, so the assumption is violated. Use a small unlabeled weight |
| Gradient-boosted rankers (LambdaMART) | Hundreds or more labeled items in query groups | No. Default `min_data_in_leaf` is 20 [12] | Seconds | SHAP values built in (`predict_contrib`) [12] | LightGBM (MIT, 1.4 MB Windows wheel) [12], [27], XGBoost (Apache-2.0, 48.9 MB wheel, ranking objectives not reviewed) [27] | With 5 to 50 positives, trees barely split or overfit. Airbnb's GBDT gains plateaued over time even with marketplace click data [16] |
| Gaussian process preference learning | Pairwise comparisons or labels over feature vectors | Yes. Bayesian, probabilistic predictions [7] | Seconds with dense matrices on CPU [6] | Kernel lengthscales, nearest exemplars, no per-feature additive reasons | BoTorch `PairwiseGP` (MIT, needs PyTorch) [6], [27], GPyTorch (MIT) [27] | PyTorch install, kernel choice, harder explanations |
| Tabular foundation model (TabPFN) | Small labeled tables | Built for small data, but see risks | "Slow to execute on a CPU", CPU cap 5,000 samples [17] | SHAP through a separate extensions package [17] | `tabpfn` (Apache-2.0 code) [17] | Weights for versions 2.5 and later are non-commercial, and downloading them requires a Prior Labs login or token [17]. No way to inject stated preferences as priors |
| Collaborative filtering | User-item matrix across many users | No. One user | Low | Low | `implicit` (MIT) [18], `scikit-surprise` (BSD-style) [27], [28], LightFM (Apache-2.0 on GitHub, MIT on PyPI) [19], [27] | No other users exist. `implicit` takes a sparse user-item matrix and its README names no item features [18]. LightFM handles item features but ships only a source archive for its last release, March 2023 [27], and its Windows build has OpenMP disabled [19] |
| Contextual bandits and active learning | A model with uncertainty, an impression log | Yes, they exist to learn faster from scarce data. LinUCB's advantage over context-free bandits grew as data got scarcer [20] | Negligible | As the base model | None needed (posterior sampling over the Bayesian model) [21] | Showing uncertain listings costs the user attention. Needs labeling in the gallery |
| Embedding similarity (images and text) | Pretrained encoders, no labels to start | Yes as features for nearest-neighbor or GP | CLIP ViT-B-32 image tower 8.82 GFLOPs per image [23] | Exemplars only. Embedding dimensions have no names | `open_clip_torch` (MIT) [22], [23], `sentence-transformers` (Apache-2.0) [24], [27] | PyTorch plus about 700 MB of weights (605 MB CLIP ViT-B-32, 91 MB MiniLM [25]) |
| LLM as feature extractor | Prompt, output schema | Yes, no labels needed | Model call per listing, once | Fields are human-readable | Hermes `delegate_task` with `output_schema` [4], `vision_analyze` [2] | Misses and inconsistency. The current design's model missed "garden unit" on 3 of 15 alike listings [1]. Needs per-field confidence and code-derived checks |
| LLM as judge or ranker | Listing summaries in a prompt | Yes, zero-shot | Model calls on every rerank | Natural-language reasons, not grounded by construction | None needed | Position and popularity bias in LLM rankers [29], position and verbosity bias in LLM judges [30]. Pairwise prompting reduces the burden on the model [31] but multiplies calls |
| Cross-encoder rerankers | A text query and documents | Only if taste is written as a query | ONNX FlashRank models are fast on CPU [32] | Low | `rerankers` (Apache-2.0) [32] | Ranks documents against a query. It does not learn from favorites |

Findings that shape the recommendation:

- The prior is where stated preferences belong. Zillow tried trimming a user's inferred profile to their explicit saved-search filters and rejected it, because narrow filters made recommendations "very similar, less diverse" and users explore outside their filters. Zillow instead added explicit preferences as extra features and saw NDCG lifts, highest at position 1 [33]. For one user, the equivalent is a prior mean on the matching feature weight. A favorite that contradicts it can then overrule it.
- Few labels need few, dense features. Zillow represents a user as weighted histograms over a handful of home features such as price, bedrooms and living area [33]. Its explicit-signal features were four binary and four distance features [33]. A vector of 30 to 60 ordinal ratings and flags per listing gives a linear model with priors something it can fit from 10 favorites. A vocabulary of 150 sparse tags (the current `VOCABULARY` [1]) does not.
- The current design's dismissal behavior hints at confounding. Other exposed-brick listings barely moved after a positive note on exposed brick, because the dismissed Pine Grove listings also had exposed brick [1]. A fitted model separates co-occurring features only as far as the data allow. With few labels, the stated preference prior and per-attribute note readings are what separate them.

## Photo and text feature extraction options and costs

### What the platform allows

- Hermes `vision_analyze` takes one `image_url` per call [2]. On a vision-capable main model it attaches the image to the conversation, where "it is re-sent on every later API call", and it caps embeds at a 1568-pixel long edge and 256 KB by default (`vision.embed_target_bytes`) [2].
- Hermes `delegate_task` accepts up to 8 `images` per child task, which a vision-capable child receives on its first turn. It also accepts an `output_schema` that the parent validates, with one correction retry [4]. `delegation.model` and `delegation.provider` can pin children to another model, with "keep the parent on a frontier model and pin" as the documented cost tip [5]. Only the child's final answer returns to the parent, so the images stay out of the ranking agent's conversation. The child's own system prompt size was not measured.
- Claude's image cost is `⌈width/28⌉ × ⌈height/28⌉` visual tokens. Standard-tier models cap at a 1,568-pixel edge and 1,568 tokens. Claude 4.7 and later cap at 2,576 pixels and 4,784 tokens. A request with more than 20 images requires each image to stay under a stricter dimension limit, met by keeping both sides at or below 2,000 pixels [3].
- The search stores up to 40 photos per Zillow listing, choosing the widest JPEG of each [34]. The ranking ticket found Zillow photo lists often hold the same photo twice in two sizes under one key [1]. The actual pixel sizes of Zillow photos were not measured here. The estimates below assume 1536 by 1024.

### Options per listing

Estimates assume 30 unique photos after removing duplicates, 1536 by 1024 pixels each, on a Claude 4.7 or later model (high-resolution tier) [3]. API-equivalent prices use Claude Opus 5.5 rates, $4 per million input tokens, $0.20 per million cache hits and $20 per million output tokens [35]. On a subscription these are not charges. They show relative use of the subscription's limits. How the subscription counts cache reads against its limits is not documented in the sources read.

| Option | Image tokens per listing (estimate) | Detail per photo | Notes |
|---|---|---|---|
| 1. `vision_analyze` on each full photo in the ranking agent | 30 × 2,035 = 61,000 high-res tier, about 47,000 standard tier | Full, up to 1568 pixels [2] | Every image is re-sent on each later call [2]. Called one at a time, cumulative input reaches about 940,000 tokens per listing, mostly cache reads. Called in one parallel turn, about 120,000 to 180,000. The current design reads 3 photos per listing and caps a batch at 6 listings to keep the conversation small [1] |
| 2. Contact sheets of 9 photos, 3 by 3 tiles of 512 by 384 on a 1536 by 1152 sheet, labeled with photo numbers, sent to one `delegate_task` child per listing | 4 sheets × 2,310 = 9,200 high-res tier, 4 × 1,568 = 6,300 standard tier. 40 photos is 5 sheets, about 11,600 | About a third of the linear resolution | Fits the 8-image child limit up to 72 photos [4]. Room type, light, finishes, floors, condition and outdoor space are visible at 512 pixels. Small text and fine fixture detail may not be |
| 3. Option 2 plus `vision_analyze` zooms on up to 3 full photos the child picks, such as the main kitchen, bathroom and living room | Option 2 plus up to 3 × 2,035 = 6,100 | Full on the rooms that matter most | The child chooses from the sheets, so the important rooms need not come first. Airbnb classified photos by room type for the same reason, so the rooms guests care about most surface first [37] |

Per listing, option 3 comes to about 15,000 image tokens plus about 2,000 tokens of listing text, 2,000 of instructions and schema, and about 1,500 output tokens: roughly $0.11 API-equivalent on Opus 5.5, half that on Sonnet 5.5, and about $0.003 on Haiku 5.5 (estimate from [35]). For 150 listings, about $16 on Opus 5.5 once, plus the child's unmeasured system prompt. For comparison, the current design's 27 agent runs for 129 listings at 3 photos used 3,534,275 cache-read, 1,063,798 cache-write and 134,860 output tokens [1], about $8.70 at Opus 5.5 rates, or $0.07 per listing, excluding uncached input, which the ticket does not report (estimate from [1], [35]). Most of it is agent conversation overhead, not images.

Code prepares the photos before the model call. It drops Zillow's duplicate sizes by photo key [1], drops near-duplicates by a perceptual hash, and tiles the sheets, all with Pillow, which is in Hermes's base dependencies. numpy is only in optional extras [36].

### What stage 1 extracts (proposal)

One schema-validated JSON object per listing content fingerprint:

- Per photo: index, room type (kitchen, bathroom, bedroom, living, dining, exterior, view, outdoor space, laundry, floor plan, building amenity, other), duplicate-of.
- Per listing, as ordinal ratings from 1 to 5 with a confidence and "not shown": natural light, window size, ceiling height, room size impression, layout openness, kitchen finish, kitchen size, bathroom finish, overall condition, renovation recency, storage, view.
- Categorical: dominant floor type (hardwood, carpet, tile or vinyl, mixed), style (modern, vintage, industrial, plain), outdoor space (none, shared, private balcony, private yard), unit level (garden or basement, ground, middle, top) with its evidence, building type.
- Coverage: which room types have photos. A listing with no bathroom photo is informative.
- From the text: in-unit laundry, dishwasher, air conditioning type, parking, pets, utilities included, lease terms, noise cues.
- Up to 8 free-text traits and up to 5 concerns, each chosen from the tag list the code passes in, or a new tag when none fits. Code merges the new tags into the list, so later listings reuse the same words. A summary of at most 200 characters.
- For each stated preference that the schema cannot express: true, false or unknown, with the evidence.

Code adds what code reads reliably: monthly price, price per square foot, bedrooms, bathrooms, log of size, unit level from the unit label (`#G`, `Gn`, "garden"), which the current design's model missed [1], days since first seen, and photo count.

Text and photos go in the same child call, so a trait seen in a photo and contradicted by the description can be resolved once. Per-field confidence lets stage 2 down-weight guesses.

## Real-estate domain findings

- Zillow (2018) ran a content-based recommender that builds a user profile, generates candidates, then ranks them by a predicted user-home match over many features. Location is among the most important features, and a smooth per-user density over clicked homes beat zip-code counting, because zip boundaries split homes a short walk apart [38].
- Zillow (2020) found explicit search filters improve a click model when added as binary in-range features and distance-from-range features. Lat-lon distance and price-in-range had the most impact. Trimming the inferred profile to the filters was rejected because it narrowed recommendations [33].
- Zillow (2018) learned similar-home embeddings with a Siamese network over structured attributes (location, price, size, home type, bedrooms, bathrooms, neighborhood). It uses log transforms and min-max scaling for skewed prices and sizes. Training was supervised by co-clicks, and it handles new listings through their content. Images and descriptions were named as future inputs [39].
- Zillow (2021) added listing-description embeddings (Word2Vec over about 10 million descriptions, among others) to its similar-homes model and saw offline lifts in NDCG, MAP and recall. The lifts were larger for vacant properties, which have less structured data [40].
- Airbnb (2018) classified listing photos into room types (bedroom, bathroom, living room, kitchen, swimming pool, view) with a retrained ResNet50, so photos could be grouped and re-ordered "so that the ones people are most interested in will be surfaced first". Labels came from host captions, filtered by rules [37]. Airbnb's photo tour now sorts new photos into rooms automatically [41].
- Airbnb (KDD 2018) trained listing and user embeddings for real-time personalization in search ranking and similar-listing recommendations, two channels that drive 99% of conversions [15]. Its search ranking's early gains came from a GBDT model and later plateaued [16]. Both rely on marketplace-scale click data, and neither is a few-example method.
- Poursaeed, Matera and Belongie (2018) estimated a property's luxury level from interior and exterior photos with CNNs and combined it with size, price and bedrooms in a value model that beat Zillow's estimates on their dataset [42]. A photo-derived quality rating per room is an established feature.
- Redfin: no engineering publication on its recommendation model was found. A Redfin press release says about 7 in 10 app users have favorited at least one home and nearly 40% have more than 30 [43]. That is consumer data, not a method.
- Idealista: no paper on recommendation or photo features from Idealista was found. One paper used Idealista listing data for Salamanca, Madrid, under a non-disclosure agreement, for price modeling [44].

Common feature sets across these sources: price, bedrooms, bathrooms, size, home type, location as a smooth function of coordinates, neighborhood, room-typed photos with quality ratings, and description text. Industry systems learn weights from millions of users' clicks. With one user, the weights must come from priors and that user's few labels.

## Recommended architecture for this project

### Signals and how each enters the model (applies to all options)

| Signal | Stage 2 use (proposal) |
|---|---|
| Favorite, contact request | Positive label. A contact request weighs more |
| Dismissal | Negative label |
| Shown but unlabeled listing | Negative label with a small weight, about 0.05 to 0.1, tuned by leave-one-out [9] |
| Note | Read once by the model into sentiment plus attribute-level likes and dislikes on the stage 1 schema. Sentiment labels the listing. Attribute readings shift the prior means of those feature weights |
| Stated like or dislike | Read once into prior means on schema features when the schema expresses it faithfully. Otherwise stage 1 judges it per listing (true, false, unknown), and the judgment becomes a feature with a prior from the statement. This keeps the current design's per-listing path for preferences the vocabulary cannot express [1] |
| Listing opened without action | Not recorded by the gallery today, which writes only feedback, notes and contact requests [45]. Joachims' skip-above rule turns such impressions into pairs [13]. See open question 5 |

### Option A: schema features and a Bayesian preference model (recommended)

- Stage 1 (model and code, once per content fingerprint): photo option 3 above, through one `delegate_task` child per listing with the schema as `output_schema` [4]. A background job wakes the agent only when listings wait for extraction, the same gate pattern the current job uses [1]. Cost: about 15,000 image tokens per listing, one child call (estimate above).
- Stage 2 (code, on "Score and rerank"): standardize features (Gelman's scaling to standard deviation 0.5 for non-binary inputs [8]), fit a logistic model by Newton iterations with a Gaussian prior whose means come from stated preferences and note readings and whose default is zero, take the Laplace posterior for uncertainty, and score each listing by posterior mean utility. Prior strength and the unlabeled weight are chosen by leave-one-out rank of each favorite. With about 60 features and 200 listings this is a 60 by 60 linear solve per iteration, milliseconds in pure Python (estimate). The current scripts need only the standard library [1], and this keeps that.
- Exploration (proposal): fill 2 of the top 20 with listings sampled from the posterior, labeled in the gallery as shown to learn the user's taste. Posterior sampling is a strong, simple exploration baseline [21], and contextual bandits gain most when data are scarce [20].
- Explanations: the two or three largest contributions `w_j (x_j − mean_j)` in plain words, such as "bright rooms and an updated kitchen, carpet counts against it", exact for a linear model [26], plus the nearest favorite under the learned weights.
- LLM review pass (one model call per press, text only): the top 30 with their stored features, contributions and reasons, the stated preferences and recent notes. It rewrites the reasons, citing only stored features, and flags listings whose rank contradicts an explicit statement. It does not reorder freely, because LLM rankers show position and popularity bias [29] and judges show position and verbosity bias [30]. A flag the owner accepts can become a bounded score adjustment shown in the reason (open question 4). Ask for verbalized confidence, which was better calibrated than token probabilities for Claude among others [46]. About 7,000 input and 1,500 output tokens, about $0.06 API-equivalent on Opus 5.5 (estimate from [35]).
- Built-in check: `status` reports the leave-one-out rank of each favorite. A favorite the model cannot rank highly from the others points to a missing feature.
- Cost per rerank: zero model calls without the review, one with it.
- Risks: quality is capped by the schema. Free-text traits from a growing tag list cover some of the rest, but synonyms the model words differently stay separate.

### Option B: option A plus local embeddings and a GP preference model

- Stage 1 adds CLIP image embeddings per photo, averaged per room type, and a sentence embedding of description and traits. The CLIP paper showed zero-shot transfer by text prompt [22], which also gives room-type labels for free.
- Stage 2 adds a GP preference model over structured features plus embeddings (BoTorch `PairwiseGP`, Chu and Ghahramani's likelihood [6], [7]) or uses embeddings in the nearest-favorite term.
- Install: PyTorch (124 MB Windows wheel on PyPI [27]), `open_clip_torch` and `sentence-transformers` [27], about 700 MB of weights [25], all on the user's machine through the installer.
- CPU time: 8.82 GFLOPs per image for CLIP ViT-B-32 [23]. 4,500 photos (150 listings by 30) is about 40 TFLOPs, minutes on a laptop CPU (estimate, not measured).
- Gains: similarity on traits outside the schema, such as "soffited ceilings, small high windows", the cue the tester used to judge garden units alike [1].
- Costs: install size, a second Python environment with PyTorch, and explanations limited to exemplars for the embedding part.

### Option C: LLM ranks from stored summaries

- Stage 1 as option A. Stage 2 sends stored summaries to the model, listwise in windows or pairwise [31], and code merges the order.
- Gains: reads notes and odd preferences directly. No feature engineering for edge cases.
- Costs: model calls on every press. With 150 listings in sliding windows of 20 overlapping by 10, about 14 calls of 5,000 tokens each, about 70,000 input tokens per rerank (estimate). Position and popularity bias [29], [30]. Ranks are not reproducible between presses, and reasons are not tied to a model the user can correct.

### Recommendation

Option A. It matches the regime: dozens of labels, explicit statements that belong in a prior, and a need for reasons that name what moved a listing. It keeps every rerank free of model calls except the optional review, keeps the ranking on the standard library, and reports its own fit by leave-one-out. Stage 1 is where quality is won, so it spends the model budget there: every photo, read once, through contact sheets and a few full-resolution zooms in a delegated child, plus the code-derived features the current model missed. Option B's embeddings add the most when the schema misses a trait. A's leave-one-out report shows when that happens, and B can then be added without changing stage 1's model call. Option C belongs in the review pass, not the ranking.

The redesign changes Spec › Ranking: user-triggered scoring replaces the cron rerank, and "every available listing has a rank" needs a rule for listings waiting for extraction. That goes through the spec as an owner decision.

## Open questions for the owner

1. Photo reading. (a) Contact sheets only: cheapest, about 9,000 image tokens per listing, loses fine detail. (b) Every full photo through `vision_analyze`: full detail, about 61,000 image tokens plus re-sends. (c) Contact sheets plus up to 3 full-resolution zooms the child chooses: about 15,000. Recommendation (c), because the child sees every photo at sheet resolution and spends full resolution on the rooms that matter.
2. Extraction model. (a) The profile's main model for the child. (b) A cheaper model pinned through `delegation.model` [5], such as Sonnet 5.5 at half the price or Haiku 5.5 at about a fortieth [35]. Recommendation: run (a) and (b) on the same 20 listings, compare field agreement, then choose. Extraction errors cap the whole ranking.
3. Dependencies. (a) Standard library only, as today. (b) numpy and scikit-learn added by the installer. Recommendation (a) for option A, since its solver is small. Revisit with option B, which needs PyTorch anyway.
4. Review pass authority. (a) Reasons only. (b) Reasons plus flags for conflicts with explicit statements, applied as a bounded adjustment shown in the reason. (c) Free reordering. Recommendation (b). (c) brings LLM ranking bias into the order [29], [30].
5. Implicit signals. Log listing opens and photo views in the gallery (a) now, unused until leave-one-out shows they help, or (b) not at all. Recommendation (a). Joachims' pairs need impressions [13], and logging is local.
6. Exploration slots. Show 2 posterior-sampled listings in the top 20, labeled as shown to learn the user's taste: yes or no. Recommendation yes. Without them, the model sees only listings it already ranks high, which also biases positive-unlabeled learning [9].
7. Cold start. After the interview, (a) rank by stated preferences alone until favorites arrive, or (b) also ask 5 to 8 "which of these two" questions on real listings. Recommendation (b). Pairwise answers are labels the model can use at once, and language-model-led elicitation drew more informative answers than user-written prompts in Li et al. [47].
8. Location. (a) Neighborhood only. (b) Store the listing's coordinates and model a smooth location preference from favorites, as Zillow does [38]. (c) Also commute distance to places the user names, which needs a geocoding service. Recommendation (b). Whether the Zillow pages the search reads expose coordinates was not checked.

## Sources

1. Ranking ticket "Ranking that learns from favorites and notes" (`docs/tickets/0004-ranking.md`), `docs/ranking.md` and `profile/skills/ranking/` (`SKILL.md`, `scripts/scoring.py`), branch `origin/feat/learning_ranking` at `457b0dc`.
2. Hermes Agent source, `tools/vision_tools.py` (`VISION_ANALYZE_SCHEMA`, `_vision_analyze_native`, `_EMBED_MAX_DIMENSION`) and `tools/vision_tools_history_budget.py`, installed checkout at commit `bf0203a794` (2026-10-22).
3. Claude documentation, Vision: https://platform.claude.com/docs/en/build-with-claude/vision
4. Hermes Agent source, `tools/delegate_tool.py` (`images`, `output_schema` task fields) and `tools/delegate_tool_tasks.py` (`_MAX_TASK_IMAGES = 8`), same checkout as [2].
5. Hermes Agent `cli-config.yaml.example`, `delegation.model` and `delegation.provider`, same checkout as [2].
6. BoTorch source, `botorch.models.pairwise_gp`: https://botorch.readthedocs.io/en/stable/_modules/botorch/models/pairwise_gp.html
7. Wei Chu, Zoubin Ghahramani, "Preference Learning with Gaussian Processes", ICML 2005: https://mlg.eng.cam.ac.uk/zoubin/papers/icml05chuwei-pl.pdf
8. Andrew Gelman, Aleks Jakulin, Maria Grazia Pittau, Yu-Sung Su, "A weakly informative default prior distribution for logistic and other regression models", Annals of Applied Statistics 2008: https://arxiv.org/abs/0901.4011
9. Charles Elkan, Keith Noto, "Learning Classifiers from Only Positive and Unlabeled Data", KDD 2008: https://cseweb.ucsd.edu/~elkan/posonly.pdf
10. choix README: https://github.com/lucasmaystre/choix
11. choix API reference: https://choix.lum.li/en/latest/api.html
12. LightGBM parameters: https://lightgbm.readthedocs.io/en/latest/Parameters.html and license https://github.com/microsoft/LightGBM/blob/master/LICENSE
13. Thorsten Joachims, "Optimizing Search Engines using Clickthrough Data", KDD 2002: https://www.cs.cornell.edu/people/tj/publications/joachims_02c.pdf
14. scikit-learn `LogisticRegression`: https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html
15. Mihajlo Grbovic, Haibin Cheng, "Real-time Personalization using Embeddings for Search Ranking at Airbnb", KDD 2018, accepted-paper page (abstract only, full text not retrieved): https://www.kdd.org/kdd2018/accepted-papers/view/real-time-personalization-using-embeddings-for-search-ranking-at-airbnb
16. Malay Haldar et al., "Applying Deep Learning To Airbnb Search", abstract: https://arxiv.org/abs/1810.09591
17. TabPFN README: https://github.com/PriorLabs/TabPFN
18. implicit README: https://github.com/benfred/implicit
19. LightFM README and license: https://github.com/lyst/lightfm
20. Lihong Li, Wei Chu, John Langford, Robert Schapire, "A Contextual-Bandit Approach to Personalized News Article Recommendation", WWW 2010, abstract: https://arxiv.org/abs/1003.0146
21. Olivier Chapelle, Lihong Li, "An Empirical Evaluation of Thompson Sampling", NIPS 2011, abstract: https://papers.nips.cc/paper/2011/hash/e53a0a2978c28872a4505bdb51db06dc-Abstract.html
22. Alec Radford et al., "Learning Transferable Visual Models From Natural Language Supervision" (CLIP), abstract: https://arxiv.org/abs/2103.00020
23. OpenCLIP README, license and `docs/model_profile.csv`: https://github.com/mlfoundations/open_clip
24. all-MiniLM-L6-v2 model card: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
25. Hugging Face file sizes (`X-Linked-Size`): https://huggingface.co/timm/vit_base_patch32_clip_224.openai/resolve/main/open_clip_pytorch_model.bin (605,225,782 bytes), https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/model.safetensors (90,868,376 bytes)
26. SHAP `LinearExplainer`: https://shap.readthedocs.io/en/latest/generated/shap.LinearExplainer.html
27. PyPI JSON metadata (version, upload date, license, wheel size) for lightfm, implicit, scikit-surprise, choix, botorch, gpytorch, lightgbm, xgboost, sentence-transformers, open-clip-torch, rerankers, shap, scikit-learn, tabpfn and torch, read 2026-10-25: https://pypi.org/pypi/<name>/json
28. Surprise license: https://github.com/NicolasHug/Surprise/blob/master/LICENSE.md
29. Yupeng Hou et al., "Large Language Models are Zero-Shot Rankers for Recommender Systems", ECIR 2024, abstract: https://arxiv.org/abs/2305.08845
30. Lianmin Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena", NeurIPS 2023 Datasets and Benchmarks, abstract: https://arxiv.org/abs/2306.05685
31. Zhen Qin et al., "Large Language Models are Effective Text Rankers with Pairwise Ranking Prompting", abstract: https://arxiv.org/abs/2306.17563
32. rerankers README: https://github.com/AnswerDotAI/rerankers
33. Mengnan Zhao, "Utilizing both Explicit & Implicit Signals to Power Home Recommendations", Zillow Tech Hub, 2020-07-08, archived copy: http://web.archive.org/web/20211029095340/https://www.zillow.com/tech/utilizing-both-explicit-implicit-signals/ (zillow.com returned HTTP 403)
34. apt-scraper `profile/skills/search/scripts/sites/zillow.py` (`MAX_PHOTOS`, `best_photo`), `main` at `8fe9e77`.
35. Claude documentation, Pricing: https://platform.claude.com/docs/en/about-claude/pricing
36. Hermes Agent `pyproject.toml` (Pillow in base dependencies, numpy in the `voice` and `wake` extras), same checkout as [2].
37. Shijing Yao, Qiang Zhu, Phillippe Siclait, "Categorizing Listing Photos at Airbnb", Airbnb Engineering, 2018-05-02, archived copy: http://web.archive.org/web/2020/https://medium.com/airbnb-engineering/categorizing-listing-photos-at-airbnb-f9483f3ab7e3 (medium.com returned HTTP 403)
38. Ondrej Linda, "Personalized Location Preference for Home Recommendations", Zillow Tech Hub, 2018-04-10, archived copy: http://web.archive.org/web/2023/https://www.zillow.com/tech/personalized-location-preference/
39. Sangdi Lin, "Home Embeddings for Similar Home Recommendations", Zillow Tech Hub, 2018-10-17, archived copy: http://web.archive.org/web/20241214212034/https://www.zillow.com/tech/embedding-similar-home-recommendation/
40. Anish Khazane, "Improving Recommendation Quality by Tapping into Listing Text", Zillow Tech Hub, 2021-02-24, archived copy: http://web.archive.org/web/2021/https://www.zillow.com/tech/improve-quality-listing-text/
41. Airbnb Help Center, "Setting up a photo tour for your home listing": https://www.airbnb.com/help/article/3509
42. Omid Poursaeed, Tomas Matera, Serge Belongie, "Vision-based Real Estate Price Estimation", Machine Vision and Applications 2018, abstract: https://arxiv.org/abs/1707.05489
43. Redfin, "Redfin Launches Favorites Lists to Help Organize Your Home Search", 2023-01-31, as summarized in search results for https://www.redfin.com/news/?p=75751 (page not opened)
44. "Identifying Real Estate Opportunities using Machine Learning", as summarized in search results for https://arxiv.org/pdf/1809.04933 (paper not opened)
45. apt-scraper `docs/gallery.md` (write routes `/api/listings/<id>/{feedback,notes,contact}`), `main` at `8fe9e77`.
46. Katherine Tian et al., "Just Ask for Calibration", EMNLP 2023, abstract: https://arxiv.org/abs/2305.14975
47. Belinda Z. Li, Alex Tamkin, Noah Goodman, Jacob Andreas, "Eliciting Human Preferences with Language Models", abstract: https://arxiv.org/abs/2310.11589

Searched and not found: a Redfin engineering publication on its recommendation model, an Idealista publication on recommendation or listing photos, and the full text of the Airbnb KDD 2018 embeddings paper (the ACM copy returned HTTP 403).

Also read and left out of the comparison: Hegselmann et al., "TabLLM" (https://arxiv.org/abs/2210.10723). Its abstract reports LLM classification of serialized table rows competitive with gradient-boosted trees in the very-few-shot setting, with the model fine-tuned on the labeled examples. A Claude subscription through Hermes offers no fine-tuning.
