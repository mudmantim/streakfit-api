# StreakFit Information Layer — Content Audit

**Scope:** `INSIGHT_LIBRARY` (270 = 90 original in `app.py` + 180 expansion in `streakfit_content.py`) and `BRAIN_BOOST_LIBRARY` (190 = 40 original + 150 expansion).
**Method:** full read of every entry; lexical near-duplicate scan (Jaccard over content words); option-length analysis; distractor-pattern analysis; cross-check against `tests/test_content_library.py`.
**Date:** 2026-09-18. No repo file was modified.

**ID scheme used throughout:** `I000`–`I089` = original 90 insights, `I090`–`I269` = expansion. `B000`–`B039` = original 40 Brain Boost, `B040`–`B189` = expansion.

---

## 0. Headline findings

1. **The correct Brain Boost option is the longest option in 143 of 190 questions (75%).** "Always pick the longest" scores 75% with zero knowledge — the exact same class of bug as the position bias the test suite already fixed (`test_the_correct_answer_is_not_always_in_the_same_slot` documents that "always pick the second one" used to score 75%). Nothing tests for this.
2. **Seven factual claims are wrong, outdated, or myths stated as fact** — including two classic debunked myths (`I084` sneeze speed, `I265` sneezing with eyes open), one obsolete physiology model (`I135` oxygen debt), and one claim that inverts the mechanism it describes (`I020` DOMS).
3. **The 25-question "Mind & Wellness" block (`B015`–`B039`, all original) is not a knowledge quiz.** Every one is "pick the obviously kind option." That is 13% of the entire Brain Boost library.
4. **The expansion is written in British English with metric units; the original is American English with imperial.** Same library, two dialects, and one fact stated in *miles* as an insight and *kilometres* as a quiz answer.
5. **Meal-skipping appears as a named wrong answer three times** and "Eating a whole bag of chips" once, in content read by nine-year-olds. The banned-substring test covers "calorie"/"diet"/"dieting" but not these.

---

## 1. Inventory by actual subject matter

The `category` field is not a subject taxonomy — it is a fixed nine-bucket structure with **exactly 30 entries each**, an artifact of `test_every_insight_uses_a_known_category` forcing the expansion to reuse the original 90's labels. Re-classified by what each entry is actually *about*:

| Actual subject | Insights | % | Notes |
|---|---:|---:|---|
| Anatomy & body trivia (non-actionable) | ~42 | 16% | All 30 `FUN FACTS` + ~12 scattered through `HEALTH`/`MOVEMENT` |
| Exercise science: training, recovery, adaptation | ~35 | 13% | Nearly all of `RECOVERY` |
| Sleep & circadian rhythm | ~32 | 12% | |
| Balance & the vestibular system | ~31 | 11% | |
| Flexibility & stretching | ~31 | 11% | |
| Family, parenting & social | ~31 | 11% | |
| Habit psychology / behaviour change | 30 | 11% | |
| Biomechanics (how a movement works) | ~11 | 4% | |
| Nutrition, hydration & digestion | ~12 | 4% | |
| "This already counts as movement" framing | ~8 | 3% | |
| Thermoregulation & environment | ~6 | 2% | |
| Mood & mental wellbeing | ~4 | 1.5% | `I000`, `I009`, `I016`, partially `I069` |
| Hygiene | 1 | 0.4% | `I014` |

Brain Boost by actual subject (the `category` field here is closer to honest):

| Subject | Count | Notes |
|---|---:|---|
| Mind & Wellness (soft skills) | 25 | **All original.** The 150-item expansion added zero. |
| Anatomy / body facts | ~39 | `Muscles & Bones` 13, `Body Facts` 13, parts of `Heart & Lungs` |
| Heart, lungs, circulation | 13 | |
| Everyday movement & biomechanics | 13 | |
| Strength & flexibility | 13 | |
| Sleep & recovery | 13 | |
| Hydration & nutrition | 12 | |
| Warm-up & soreness | 12 | |
| Brain & movement | 12 | |
| Habits & consistency | 12 | |
| Balance & agility | 12 | |
| Outdoors | 12 | |
| **Family / social** | **0** | |

### Over-represented

- **Balance (31 insights + 12 quiz items = 43 entries, ~9% of the whole library).** Balance is one narrow subsystem and it gets more space than nutrition, mood, thermoregulation, biomechanics and hygiene *combined*. Six items say a version of "your eyes, inner ear and feet vote on balance" (`I042`, `I172`, `B076`, `B166`, plus `I044`/`B167` on the eyes-closed corollary).
- **Flexibility & stretching (31 + 13 = 44).** Same cause: the `FLEXIBILITY` bucket had to be filled to 30.
- **Anatomy trivia (~42 insights + ~39 quiz).** The single largest subject and the least connected to moving. `FUN FACTS` is 30 items of "your body has N of X."
- **Habit psychology (30 + 12).** Seven separate entries make the habit-cue point (`I060`, `I064`, `I212`, `I221`, `B006`, `B154`, `B159`).

### Under-represented

- **Mood and mental wellbeing: four insights out of 270** (`I000`, `I009`, `I016`, `I069`) — despite 25 Brain Boost questions on it. The two halves of the information layer disagree completely about how important this subject is.
- **Aerobic fitness as a trainable thing.** `B058` (resting heart rate settles lower) is essentially the only entry about *getting fitter* aerobically. Nothing on pacing, breathlessness improving, endurance building, or what a month of walking actually changes.
- **Strength training.** ~6 entries (`B079`, `B080`, `B083`, `B086`, `B087`, `B088`), all adult-gym framed.
- **Family in Brain Boost: zero of 190.** The app's stated differentiator has 31 insights and no quiz coverage at all.
- **Nutrition and hydration: 12 insights.** Deliberately thin given the safety constraints, but `B107`/`B108`/`B111`/`B113` are the only real content and three of the four are guessable.

---

## 2. Duplication and near-duplication

Lexical similarity found little (highest insight-to-insight Jaccard was 0.36) because the two sets were written in different registers. The real duplication is **semantic**, and it is heavily **cross-set** as predicted.

### 2a. Insight ↔ Brain Boost: the quiz re-states the insight almost verbatim

Twelve cases where a user gets the same fact twice in one product. The worst:

| | |
|---|---|
| `I053` [ORIG] | "Holding a stretch for about 20 to 30 seconds gives your muscles time to actually relax." |
| `B000` [ORIG] | Q: "About how long should you hold a stretch for it to actually help?" → **"20-30 seconds"** |

| | |
|---|---|
| `I012` [ORIG] | "Your heart is roughly the size of your fist and beats about 100,000 times a day." |
| `B007` [ORIG] | Q: "Which is closest to the size of your heart?" → **"About the size of your fist"**. Explanation: "Your heart is roughly the size of your own closed fist — small, but it beats around 100,000 times a day." (near word-for-word) |

| | |
|---|---|
| `I004` [ORIG] | "Climbing stairs works more muscles at once than almost any other everyday movement." |
| `B009` [ORIG] | → **"Works more muscles at once than almost any other everyday movement"** (the correct option is the insight, copied) |

| | |
|---|---|
| `I007` [ORIG] | "A short walk after a meal can help your body handle that food better." |
| `B004` [ORIG] | → **"Helps your body handle the food better"** |

Others in the same shape: `I080`/`B043` (baby bones), `I087`/`B012` (bones vs steel), `I113`/`B147` (goosebumps), `I115`/`B056` (lungs have no muscle), `I142`/`B123` (joints lubricated by movement), `I164`/`B103` (sleep inertia), `I175`/`B174` (fixed point), `I177`/`B175` (spotting), `I178`/`B075` (proprioception), `I190`/`B085` (nervous system brakes), `I193`/`B090` (hip flexors), `I199`/`B082` (dynamic warm-up), `I206`/`B089` (flexibility is joint-specific), `I257`/`B060` (left lung smaller), `I258`/`B057` (blood lap in a minute), `I259`/`B062` (blood vessel length), `I266`/`B132` (brain uses a fifth of energy), `I104`/`B173` (standing still is active), `I170`/`B168`, `I171`/`B169`.

That is roughly **22 of 190 quiz questions whose answer is already a printed insight.**

### 2b. Original ↔ expansion insight collisions

| | |
|---|---|
| `I022` [ORIG] | "A short walk on a rest day can help sore muscles feel better than sitting still all day." |
| `I133` [EXP] | "Light movement raises blood flow to a sore muscle, which is why a gentle walk often feels better than lying down." |
| *plus* `B119` [EXP] | "What do most people find helps mild soreness feel better?" → "Gentle movement" |
| *plus* `B101` [EXP] | "What is 'active recovery'?" → "Easy movement, like a gentle walk, on a lighter day" |

Four entries, one idea.

| | |
|---|---|
| `I042` [ORIG] | "Your eyes, ears, and feet all work together to help you stay balanced." |
| `I172` [EXP] | "Balance is a vote, not a single sense: your brain compares your eyes, your inner ears, and the pressure under your feet." |
| *plus* `B076` | "What does your body use to work out which way is up?" → "Your inner ear, eyes and feet working together" |
| *plus* `B166` | "Which three systems combine to keep you balanced?" → "Eyes, inner ear, and sensors in muscles and joints" |

| | |
|---|---|
| `I021` [ORIG] | "Resting after a hard workout is when your body actually gets stronger." |
| `I027` [ORIG] | "Your body repairs muscle while you rest, not while you're still exercising." |
| `I132` [EXP] | "Muscles don't grow during exercise. Exercise is only the signal — the building happens afterward." |
| `I148` [EXP] | "Hard effort leaves you temporarily weaker. The strength arrives during the rest that follows, slightly above where you started." |
| *plus* `B079`, `B091` | "Between sessions, not during them" / "They repair after being worked and come back a little stronger" |

**Six entries** for "you get stronger during rest." Two are duplicates *within* the original 90 alone.

| | |
|---|---|
| `I035` [ORIG] | "A cooler room tends to help most people fall asleep faster." |
| `I165` [EXP] | "Falling asleep depends on your body shedding heat, which is part of why a slightly cool room helps most people." |
| *plus* `B002` [ORIG] | "Which helps most people fall asleep faster?" → "A cooler room" |

| | |
|---|---|
| `I019` [ORIG] | "Spending time outside in daylight helps your body know when it's day and when it's night." |
| `I155` [EXP] | "Morning daylight is one of the strongest signals your body uses to set its internal clock for the whole day." |
| *plus* `B094`, `B182` | "It helps set your internal body clock" / "Helps keep it in rhythm" |

| | |
|---|---|
| `I070` [ORIG] | "Kids are more likely to be active when they see the adults around them moving too." |
| `I076` [ORIG] | "Kids often copy the habits they see at home more than the ones they're told to follow." |
| `I249` [EXP] | "Adults tend to underestimate how closely kids watch what they do, and overestimate how much they hear what they're told." |
| `I248` [EXP] | "The habit a child watches an adult keep on a bad day is the one that sticks." |
| *plus* `B010` [ORIG] | → "Mostly by copying the adults around them" |

| | |
|---|---|
| `I060` [ORIG] | "Doing something at the same time every day makes it much easier to remember." |
| `I064` [ORIG] | "Habits form faster when they're tied to something you already do every day." |
| `I212` [EXP] | "Every habit needs a cue, and the most reliable cue is something already nailed down in your day, like brushing your teeth." |
| `I221` [EXP] | "A habit tied to a time of day holds up better than one tied to a mood." |
| *plus* `B006`, `B154`, `B159` | |

| | |
|---|---|
| `I063` [ORIG] | "Putting your shoes by the door can make it easier to actually go for that walk." |
| `I219` [EXP] | "The friction between you and a habit is usually physical: where the shoes are, whether the bag is packed, how far the mat is." |
| `I226` [EXP] | "Changing your surroundings changes your behavior faster than changing your mind does." |
| *plus* `B154` | → "Setting up cues, like leaving your shoes by the door" |

| | |
|---|---|
| `I061` [ORIG] | "Missing one day rarely breaks a habit — missing many days in a row is what does." |
| `I216` [EXP] | "Getting back to it on day two matters far more than being perfect on day one." |
| `I139` [EXP] | "A single rest day undoes nothing. Fitness is built and lost on a scale of weeks, not hours." |
| *plus* `B008`, `B164`, `B165` | |

Further pairs, more briefly:

- `I106` [EXP] "Your grip strength comes mostly from muscles in your forearm, not from your hand." / `I250` [EXP] "There are no muscles inside your fingers. They're pulled by tendons running from your forearm, like puppet strings." — same fact, both in the expansion.
- `I006` [ORIG] "Swinging your arms while you walk helps you move faster with less effort." / `I091` [EXP] "Your arms swing opposite to your legs when you walk, which cancels out the twist your hips would otherwise put into your spine." / `B067`.
- `I030` [ORIG] "Most of your body's repair work happens while you're asleep." / `I168` [EXP] "Your skin, muscle, and bone all repair faster while you're asleep than while you're up and about." / `I141` [EXP] / `B092`.
- `I032` [ORIG] "Bright screens before bed can trick your brain into thinking it's still daytime." / `I161` [EXP] / `I166` [EXP] / `B095`.
- `I047` [ORIG] "Carrying something in one hand can test your balance more than you'd expect." / `I186` [EXP] "The same weight is easier to balance split between two hands than carried in one." / `I094` [EXP] / `B177`.
- `I055` [ORIG] cold muscles stretch less / `I057` [ORIG] warm shower / `B117` / `B126` — four on warm-vs-cold tissue.
- `I059` [ORIG] "Yawning and stretching together is your body's natural way of waking itself up." / `I203` [EXP] pandiculation / `I208` [EXP] "The stretch you do without thinking when you wake up is one your body asked for on its own."
- `I075` [ORIG] chores / `I241` [EXP] chores / `I237` [EXP] groceries and strollers / `I105` [EXP] gardening / `I096` [EXP] doors and couches — five "this already counts."
- `I104` [EXP] "Standing perfectly still is impossible. Your body is constantly making tiny corrections you never feel." / `I256` [EXP] "Your eyes never truly hold still. Even when you're staring at one spot, they're making tiny jumps you can't feel." — same sentence structure and same reveal, twice, in the same set.
- `I179` [EXP] feet close together harder / `B176` wider stance steadier — same fact stated in both directions.
- `I049` [ORIG] "Toddlers and grandparents can both benefit from the exact same simple balance practice." / `I073` [ORIG] / `I243` [EXP] / `I244` [EXP] "Movement is one of the few things a nine-year-old and a sixty-year-old can genuinely do side by side."
- `B179` / `B187`: "Why does walking on grass or a trail use more muscles than flat pavement?" and "What does regularly walking on uneven ground do for your ankles?" — same fact, same set, ~8 questions apart.
- `B092` "What is your body busy doing while you sleep?" → "Repairing and rebuilding tissue" and `B091` "When does most muscle repair happen?" → "Between sessions, not during them" — adjacent questions, overlapping answers.

---

## 3. Questionable factual claims

Ordered by severity.

### 3.1 `I084` — sneeze speed is a debunked myth

> "A sneeze can shoot out of your nose faster than a car drives down most neighborhood streets."

A neighbourhood street is ~25 mph. The popular "sneezes travel at 100 mph" figure has no measurement behind it; the actual high-speed-imaging literature (Tang et al. 2013; Bourouiba et al. 2014) puts sneeze exit velocities in the **4.5–10 m/s range, i.e. 10–22 mph**. The claim as phrased sits at or above the top of every measured value and rests on the very folk figure that research contradicted. **Replace or cut.**

### 3.2 `I265` — "You can't sneeze with your eyes open" is a myth

> "You can't sneeze with your eyes open. It's an automatic reflex, and nobody has entirely explained the point of it."

Eyelid closure during a sneeze is a reflex, not a physical impossibility. People have been documented sneezing with their eyes open; the reflex can be overridden. Stating "you can't" as fact — in a library whose whole selling point is that its facts are true — is the single most checkable error here, because any child will try it.

### 3.3 `I020` — DOMS: the claim inverts its own mechanism

> "Sore muscles after exercise are usually a sign of repair, not damage."

Delayed-onset muscle soreness *is* caused by microscopic mechanical damage to muscle fibres and connective tissue from unaccustomed (especially eccentric) work, plus the inflammatory response to it. Repair follows the damage; the soreness is the damage signal, not the repair signal. The library's own `I130`/`B074` describe the eccentric-damage mechanism correctly, so this is an internal contradiction as well as an error. `B001`'s correct option ("Your body repairing and adapting") has the same problem but is partly saved by the distractor being "Muscle damage that's getting **worse**." A truthful version: *soreness means your muscles were challenged in a new way and are now rebuilding — it isn't injury.*

### 3.4 `I135` — "oxygen debt" is an obsolete model

> "Breathing hard for a few minutes after you stop is your body paying back the oxygen it borrowed while you were going."

This is the oxygen-debt/lactic-acid model, dismantled by Gaesser & Brooks (1984) and replaced by EPOC. Elevated post-exercise oxygen consumption is not repayment of a borrowed quantity; it reflects restoring body temperature, ventilation, circulation, hormone levels and phosphocreatine stores. A textbook example of a plausible-sounding explanation that physiology abandoned forty years ago.

### 3.5 `B102` — glymphatic clearance is actively contested, not settled

> Q: "What does your brain do during deep sleep?" → **"Clears out waste that built up during the day"**
> Explanation: "Fluid washes through brain tissue during deep sleep, flushing out the day's metabolic leftovers. It's a rinse cycle you can't run while awake."

The sleep-clearance/glymphatic story (Xie et al. 2013) was directly contradicted in 2024 by work reporting that brain clearance is *reduced* during sleep and anaesthesia (Miao et al., *Nature Neuroscience*). This is a live dispute in the field, and the entry presents one side as fact plus an invented flourish ("a rinse cycle you can't run while awake") that the disputed study specifically denies. Of everything in the library, this is the claim most likely to be flatly wrong.

### 3.6 `I081` — "strongest muscle" is an unresolvable superlative

> "The strongest muscle in your body, for its size, is the one in your jaw."

There is no agreed definition of "strongest" (force? force per cross-section? work? pressure?), and the masseter claim rests on a single 1986 bite-force record. Competing claims for heart, uterus, soleus, and the external eye muscles are equally common. Stating it flat is stating a trivia-book answer, not a fact. The "for its size" hedge does not rescue it because the metric is still undefined.

### 3.7 `I087` / `B012` — "stronger than steel," with an explanation that doesn't follow

> `I087`: "Pound for pound, your bones are stronger than steel."
> `B012` explanation: "Pound for pound, bone is actually stronger than steel — it's just lighter, so a steel beam the same size would weigh far more."

The *specific compressive* strength claim is arguably defensible (cortical bone ~170 MPa at ~1.9 g/cm³ vs structural steel ~250 MPa at ~7.8 g/cm³). But bone is **much weaker than steel in tension and in absolute terms**, which the unqualified "stronger than steel" obscures, and the explanation's reasoning is a non sequitur: "a steel beam the same size would weigh far more" is a statement about *density*, not strength. If a child repeats the reasoning, they've learned nothing true.

### 3.8 `I066` — publicly stating a goal is contested, not settled

> "Telling a friend about a goal can make you more likely to actually follow through on it."

There is a well-known counter-literature (Gollwitzer et al. 2009, *When intentions go public*) finding that announcing identity-relevant goals can *reduce* subsequent action, by substituting a sense of completeness. Public-commitment research points the other way for some goal types. Presented here as a settled behavioural fact. `I220` ("Most people find it easier to keep a promise made to someone else than one made only to themselves") is on firmer ground and duplicates the intent.

### 3.9 `I227` — an aphorism presented as a research finding

> "People reliably overestimate what they'll get done in a week and underestimate what they'll get done in a year."

The first half is the planning fallacy (well supported). The second half is folklore — a Bill Gates/Amara's-Law aphorism with no research behind it. The word "reliably" asserts an empirical regularity that does not exist for the long-horizon half.

### 3.10 `I230` — a fabricated statistic

> "Kids move most when nobody calls it exercise. Call it a game and the same movement happens for twice as long."

"Twice as long" is a specific quantitative claim with no source. The underlying qualitative point is fine; the number is invented precision, and it is the kind of thing a parent will repeat.

### 3.11 `I228` — false, and mildly unsafe in a movement app

> "A habit done badly still counts. Repetition builds the pattern, and quality shows up later on its own."

The first sentence is on-brand and fine. The rest is wrong for motor learning: repetition entrenches whatever pattern is repeated, which is precisely why coaching exists. In a library read by nine-year-olds doing squats and push-ups, "quality shows up later on its own" is the one piece of advice here that could plausibly lead to someone getting hurt.

### 3.12 `I000` — mood effect over-stated

> "A ten-minute walk can lift your mood for the rest of the day."

Acute affective response to a short walk is measured over minutes-to-a-couple-of-hours, not a day. The library's own `B129` gets this right ("Lift it, often within minutes"), so this is a self-inconsistency. This is also the **first insight in the library** and therefore a strong candidate for what a new user sees.

### 3.13 `I009` — an unsupported head-to-head comparison

> "Moving around in the morning can wake you up faster than a cup of coffee."

No evidence supports movement beating caffeine for speed of alertness onset; caffeine's alerting effect is one of the most replicated findings in the field. "Can" does not carry the comparison.

### 3.14 `B059` — exercise hyperpnoea is an unsolved problem, presented as solved

> Explanation: "Your body is actually more sensitive to rising carbon dioxide than to falling oxygen — that build-up is the main thing driving the faster breathing."

True at rest; not established for exercise. Arterial CO₂ does not meaningfully rise during moderate exercise, and the control of exercise hyperpnoea is a long-standing open question in respiratory physiology. The question's correct option ("Muscles need more oxygen and make more carbon dioxide") is fine — it's the confident mechanistic explanation that overreaches.

### 3.15 `B131` — two defensible answers, and the mechanism is speculation

> Q: "Why do people so often solve problems while walking?" → **"The body runs on autopilot, freeing the mind to wander"**
> Explanation: "Studies comparing sitting and walking find people come up with noticeably more ideas while walking."

The cited effect (Oppezzo & Schwartz 2014) is a small lab study on *divergent thinking* specifically, not problem-solving generally. The study established the effect, not the mechanism; "body on autopilot frees the mind" is a hypothesis. Meanwhile the distractor **"Walking pushes extra oxygen to the brain"** is a plausible-sounding mechanism a reasonable person could pick, and nothing in the question rules it out. Asking "why" about an unexplained effect makes this unanswerable on knowledge.

### 3.16 `B151` — precise error on the cornea

> Explanation: "It takes oxygen straight from the air and nutrients from your tears instead."

Oxygen from the air via the tear film is correct. Nutrients come principally from the **aqueous humour** behind the cornea and the limbal vessels at its edge, not from tears.

### 3.17 `I031` — contested, and risky framing for a child reader

> "A consistent bedtime can matter more than the exact number of hours you sleep."

Sleep regularity is genuinely important and there is recent cohort evidence that regularity indices predict outcomes well. But "can matter more than the exact number of hours" is a contested comparative, and in a library aimed at nine-year-olds it reads as permission to sleep less as long as it's on schedule. The library's own `I036` says children need more sleep — the two sit awkwardly.

### 3.18 `I052` / `B005` — small effect, loose mechanism, presented as settled

> `I052`: "Your flexibility can change throughout the day — most people are looser in the evening."
> `B005` explanation: "Your muscles warm up over the course of the day."

Diurnal ROM variation is real but small and study-dependent, and where a peak is found it is usually **afternoon/early evening**, tracking core temperature, which then *falls* through the evening. "Most people are looser in the evening" over-states a modest and variable finding, and the mechanism given is a folk version of it.

### 3.19 `I053` / `B000` — "barely does anything" over-states the stretch-duration evidence

> `B000` explanation: "Holding a stretch for 20 to 30 seconds gives your muscles enough time to actually relax and lengthen — **shorter than that barely does anything**."

ACSM guidance is 10–30 seconds, and shorter holds repeated multiple times produce ROM gains. "Barely does anything" is not supported and contradicts the app's own "small amounts count" philosophy.

### 3.20 `I196` — a folk claim about ballistic stretching

> "Bouncing at the end of a stretch tends to make the muscle tighten up — it reads the bounce as something to resist."

The stretch-reflex story is the standard gym explanation, but evidence that ballistic stretching is counterproductive is weak; controlled studies generally find ballistic stretching improves ROM comparably to static. Stated as mechanism-plus-fact.

### 3.21 Smaller overstatements (correct-ish but loose)

| ID | Text | Issue |
|---|---|---|
| `I089` | "Over your lifetime, your heart pumps enough blood to fill a small lake." | ~5 L/min over 80 years ≈ 200 million litres ≈ 80 Olympic pools. A small *lake* is orders of magnitude larger. The standard version of this factoid is "about 100 swimming pools." |
| `I123` | "You carry trillions of bacteria in your gut, and the vast majority of them are helpful." | Most gut bacteria are commensal — neither helpful nor harmful. "Vast majority helpful" is a value claim, not a finding. |
| `I141` | "Most of your body's **repair hormones** are released during deep sleep." | Growth hormone pulses in slow-wave sleep — true. "Most of your repair hormones" is a made-up category over-generalised from one hormone. |
| `I116` | "Blood is red because of iron." | The colour comes from the haem complex (porphyrin ring + iron), not iron alone; iron filings are not red. |
| `I166` | "Reading on paper before bed affects your sleep differently than reading on a bright screen. **The difference is the light, not the reading.**" | Content-driven arousal and notification interruptions also contribute, and the screen-light effect size is modest. The exclusive "not the reading" over-claims. |
| `I092` | "Going down stairs is **harder on your muscles** than going up, even though it's easier on your lungs." | Ambiguous, and arguably backwards: ascending requires more total muscular work and force. Descending causes more *soreness* (eccentric damage). `B074` phrases the same fact correctly — copy that phrasing. |
| `I156` | "Some people are genuinely sharper at night and others in the morning. **That's built in**, not a character flaw." | Chronotype is partly heritable but shifts substantially with age and light exposure. "Built in" over-states fixedness. |
| `I101` | "Riding a bike lets you keep going far longer than running does, because your legs never take the landing impact." | Impact is one factor; endurance duration is mostly metabolic and mechanical-efficiency driven. Over-simplified causal "because." |
| `I211` | "Most people feel better **three minutes in** than they did at the door." | Invented precision. |
| `B051` | "The stapes, deep in your middle ear, is about the size of a grain of rice." | Stapes ≈ 3 mm; a rice grain ≈ 6–7 mm. Roughly 2× over-stated. |
| `B107` | Options: "Vitamins / Water / **Fibre** / Carbohydrates" | **Fibre is a carbohydrate.** Listing it as an alternative to carbohydrates is a category error inside the options. |
| `I267` | "Astronauts grow slightly taller in orbit, because there's no gravity pressing down…" | Defensible as written (freefall = no compressive spinal load) but one word from the common "there's no gravity in space" misconception. Worth tightening. |

### 3.22 Unit and dialect inconsistency (whole-library)

The expansion is British English; the original is American. Confirmed occurrences: `colour` (`B061`), `centre` (`B060`, `B113`), `fibre` (`B107`, `B111`, `B113`), `fibres` (`B088`), `practise`/`practised`/`practising` (`B058`, `B070`, `B093`, `B128`), `recognising` (`B171`), `stabilising` (`B179`, `B187`), `flavoured` (`B155`), `metres`/`kilometres` (`B062`, `B138`), `20°C` (`B186`).

And the same fact is stated in two unit systems:

| | |
|---|---|
| `I259` [EXP] | "The blood vessels in one adult body, laid end to end, would stretch tens of thousands of **miles**." |
| `B062` [EXP] | → "Tens of thousands of **kilometres**." |

Both are compatible with the ~100,000 km textbook estimate, but a reader who sees both gets two different numbers for one fact. `B138` ("Well over 100 metres per second") and `B186` ("20°C") are the only quantitative items in the whole library a US family cannot picture.

---

## 4. Weak Brain Boost questions

### 4.1 The length tell — systemic, 75% of the library

**The correct option is the longest option in 143 of 190 questions.** It is longest by more than six characters in 112 of 190. A reader who never learns a single fact and always picks the longest option scores 75%.

This is the same failure the test suite already caught and fixed for *position* — `test_the_correct_answer_is_not_always_in_the_same_slot` exists precisely because "always pick the second one" used to score 75%. Length is the uncaught twin. The most extreme cases:

| Δ chars | ID | Correct option |
|---:|---|---|
| +59 | `B081` | "Flexibility is how far a muscle stretches; mobility is controlling a joint through its range" (vs "They mean exactly the same thing") |
| +45 | `B071` | "Getting different body parts working together in the right order" (vs "Being very strong") |
| +38 | `B088` | "The nervous system gets better at switching muscle fibres on" (vs "Bones lengthen") |
| +38 | `B079` | "They repair after being worked and come back a little stronger" (vs "They spin") |
| +36 | `B123` | "Joint fluid spreads across the surfaces, helping them glide" (vs "Cartilage melts") |

The pattern is structural: the correct answer is a real explanation and the distractors are two-to-four-word dismissals.

### 4.2 The Mind & Wellness block is not a knowledge quiz — 25 questions

`B015`–`B039` (all original, 13% of the library) all have the same shape: one kind/sensible option and three that are obviously bad. No knowledge is tested; the answer is available to anyone who can read.

- `B016`: "Naming what you're feeling" vs "Pretending you don't feel it" / "Yelling at whoever is nearby" / "Eating a whole bag of chips"
- `B026`: "Listen without immediately trying to fix everything" vs "Tell them to just get over it" / "Change the subject quickly" / "Compare it to a bigger problem you once had"
- `B028`: "Saying 'I can't take that on right now'" vs "Saying yes to everything to avoid conflict" / "Never telling anyone what you need" / "Doing things you resent to keep the peace"
- `B036`, `B038`, `B033`, `B031`, `B024`, `B021`, `B019`, `B018`, `B023`, `B025`, `B029`, `B035`, `B037`, `B039` — all the same.

`B022` is the extreme case and is not a question at all:

> "True or false: it's okay to ask for help when you're struggling."
> Options: **True** / False / "Only for emergencies" / "Only if no one else needs help"

### 4.3 Absurd-distractor questions (guessable with zero knowledge)

- `B012`: "True or false: pound for pound, bones are stronger than steel." Options: True / False / **"Only baby bones"** / **"Only in animals, not humans"** — two options are nonsense, so it's a coin flip on a contested claim (see §3.7).
- `B045`: "Are your bones living tissue?" → distractor **"No, they're more like rocks"**.
- `B099`: "Do children generally need more sleep than adults?" → "No" / "Exactly the same amount" / "Children need less" / **"Yes"**. Everyone knows this.
- `B003`: "What's a good way to test your balance at home?" → "Standing on one foot" vs "Running in place" / **"Holding your breath"** / "Closing both eyes while walking fast".
- `B113`: "Which of these is a mineral your body needs?" → "Iron" vs **"Gluten"** / "Fibre" / "Starch".
- `B129`: "What does a bout of activity tend to do to mood?" → distractors "Lower it reliably" / "Nothing — mood and movement are unrelated".
- `B092`, `B111`, `B130`, `B136`, `B182`, `B111` — all include "Nothing at all" or "Nothing measurable" as a distractor.

**Filler distractor counts across the library:** "never" ×10, "nothing at all" ×7, "nothing measurable" ×6, "no effect" ×4, "holding/hold your breath" ×4, "only children" ×3, "only athletes" ×1, "no real benefit" ×1. These options are never the answer and a regular player will learn to eliminate them on sight.

### 4.4 Answer given away by the question's own wording

- `B161`: "How long does it take to form a habit?" → options are **"Exactly 21 days for everyone" / "Exactly 30 days" / "Exactly one week"** vs "It varies a lot by person and by habit." Three "Exactly"s point at the fourth. (The debunk itself is excellent content — it's the option-writing that gives it away.)
- `B120`: "**Is** soreness a **good measure** of how useful a workout was?" — a question framed as a doubt answers itself.
- `B124`: "What's the 'no pain, no gain' idea **missing**?" with option "Nothing — it's accurate." The question already asserts something is missing.
- `B040`: "Which is the largest muscle in your body?" → "Your heart" / "Your bicep" / "Your calf" / **"The muscle you sit on"**. Three are named muscles; the answer is the only descriptive phrase.

### 4.5 Explanations that restate rather than explain

- `B069`: Q "Why do people naturally take shorter steps on ice?" → "Shorter steps keep your weight over your feet." Explanation: *"Nobody teaches you this — your balance system works it out on its own within a step or two of hitting a slippery patch."* The explanation never says why a shorter step helps (it reduces the horizontal force you need from the ground, so less friction is required). It just admires the phenomenon.
- `B003`: "A simple one-foot stand is one of the easiest ways to check — and train — your balance, no equipment needed." Pure restatement.
- `B011`: "Balance improves fastest with little, regular practice — a minute here and there beats one long session." Restates the correct option.
- `B092`: "Sleep is the body's maintenance window." Restates.
- `B023`, `B031`, `B035`, `B037`, `B130`, `B136`, `B162`, `B182` — all restate the correct option in different words.

For contrast, the good ones genuinely add: `B152` ("Scans of joints as they crack show a bubble appearing in the joint fluid at the exact moment of the pop. It takes a while to reset — which is why you can't do it twice in a row"), `B148`, `B181`, `B161`, `B133`.

### 4.6 Two defensible answers / ambiguity

- `B131` — see §3.15. "Walking pushes extra oxygen to the brain" is defensible if you don't know the specific study.
- `B044` — "Roughly what share of your bones are in your hands and feet?" → "More than half" (106/206). But `I251` tells the same user "**Roughly a quarter** of all the bones in your body are in your feet," and "About a quarter" is one of the four options. A reader who half-remembers `I251` will pick the trap.
- `B084` — "What does grip strength tend to reflect?" → "Overall body strength, fairly well" vs distractor "Only forearm size." Grip strength *does* also substantially reflect forearm musculature; "only" saves the item, but it's close.
- `B120` — "No — plenty of effective sessions leave none" vs "Only for beginners." Beginners genuinely do get more DOMS, so the rejected option is partly true.

---

## 5. Reading level

### 5.1 Too hard for a nine-year-old

| ID | Text | Problem |
|---|---|---|
| `I178` | "The sense that tells you where your arm is without looking has a name — proprioception — and it's why you can touch your nose with your eyes shut." | 29 words, double em-dash aside, five-syllable technical term. |
| `I140` | "There's a simple way to tell how hard you're working: if you can talk in sentences you're going steady, and if you can sing you're going easy." | 27 words, colon plus two parallel conditionals; "going steady"/"going easy" are adult training idioms. |
| `I121` | "Vitamin D is unusual: your skin can make it from sunlight, which makes it less like a vitamin and more like something you produce." | Requires holding an abstract category ("what counts as a vitamin") and then revising it. |
| `I148` | "Hard effort leaves you temporarily weaker. The strength arrives during the rest that follows, slightly above where you started." | "Slightly above where you started" has no concrete referent — it's supercompensation described without a noun. |
| `I188` | "Balance takes more practice to hold on to as the years go by, which is exactly what makes a minute of it worth spending." | Two abstractions ("hold on to" a capacity; "worth spending" a minute) chained by "which is exactly what makes." |
| `I227` | "People reliably overestimate what they'll get done in a week and underestimate what they'll get done in a year." | Two counterfactual time-horizon comparisons in one sentence. |
| `I215` | "Your brain remembers the end of an experience more strongly than the middle, so finishing on a good note is what pulls you back." | Metacognitive; also factually loose (§3). |
| `I222` | "Deciding in advance is how you avoid having the argument with yourself later." | The whole sentence is a metaphor a child has to unpack. |
| `I226` | "Changing your surroundings changes your behavior faster than changing your mind does." | Three abstract nouns and a comparative in twelve words. |
| `I109` | "Left alone, people drift toward the walking speed that costs them the least energy. Your body works that out without asking you." | "Left alone" as an adverbial opener; "costs them the least energy" is an optimisation concept. |
| `I190` | "Most of the tightness you feel in a stretch is your nervous system putting the brakes on, not the muscle running out of length." | Good content, but two competing mechanisms contrasted in one sentence. |
| `I205` | "One of your hip muscles starts at your lower spine and crosses all the way to your thigh — a long way for a single muscle to travel." | Fine vocabulary, but the muscle is never named, so there's nothing to hold on to. |
| `B081` | "Flexibility is how far a muscle stretches; mobility is controlling a joint through its range" | A 91-character semicolon-joined definition as a tappable multiple-choice option on a phone. |
| `B080`, `B087`, `B101`, `B128` | "progressive overload", "isometric exercise", "active recovery", "warm-up set" | Four gym-jargon definition questions. These are vocabulary tests for adults who lift, dropped into a library shared with nine-year-olds. |

### 5.2 Condescending for an adult

| ID | Text |
|---|---|
| `B022` | "True or false: it's okay to ask for help when you're struggling." → True |
| `B018` | "Which is a healthy self-care habit?" → "Getting enough sleep" (vs "Staying online all night") |
| `B039` | "What's a good reason to take breaks during a busy day?" → "They help you focus better afterward" (vs "Breaks are a waste of time") |
| `B025` | "What's one sign that you might need a break?" → "Feeling consistently tired or irritable" (vs "Feeling excited about your day") |
| `B037` | "What's one benefit of spending time outdoors?" → "It can lift mood and lower stress" (vs "No real benefit") |
| `B029` | "What's a simple way to reset during a stressful day?" → "Step outside for a few minutes" (vs "Scroll on your phone faster") |
| `I014` | "Washing your hands well is one of the simplest ways to stay healthy." |
| `I015` | "Eating fruits and vegetables of different colors usually means you're getting different vitamins." |
| `I063` | "Putting your shoes by the door can make it easier to actually go for that walk." |
| `I048` | "Good balance helps with everyday things, like getting dressed while standing up." |

The library has a **register split**, not a reading-level problem per se: the original 90/40 talks down, the expansion talks up. `I014` and `I178` cannot both be aimed at the same reader.

---

## 6. The weakest ~20 — no "huh, I didn't know that"

Ranked roughly worst first. These produce nothing the reader didn't already believe.

1. `B022` — "True or false: it's okay to ask for help when you're struggling." Not a fact.
2. `I014` — "Washing your hands well is one of the simplest ways to stay healthy."
3. `B039` — "What's a good reason to take breaks during a busy day?" → they help you focus.
4. `B018` — "Which is a healthy self-care habit?" → getting enough sleep.
5. `B037` — "What's one benefit of spending time outdoors?" → lifts mood, lowers stress.
6. `I015` — "Eating fruits and vegetables of different colors usually means you're getting different vitamins."
7. `B031` — "What's a healthy way to celebrate a small win?" → "Acknowledge it, even briefly."
8. `B025` — "What's one sign that you might need a break?" → tiredness and irritability.
9. `I051` — "A little gentle stretching most days works better than one long stretch once a week."
10. `I043` — "Practicing balance gets easier the more often you do it, at any age." (Tautological.)
11. `I046` — "Balance tends to improve fastest when you practice it in small amounts, often." (Duplicate of 10, plus `B011`.)
12. `I025` — "Taking a full day off once in a while helps your body keep up with everything else you do." (Vague to the point of meaninglessness — keep up with *what*?)
13. `I029` — "Giving your body a break after a busy week helps it come back stronger for the next one."
14. `I062` — "Small habits repeated often tend to stick better than big changes done occasionally."
15. `I060` — "Doing something at the same time every day makes it much easier to remember."
16. `I072` — "Playing outside together counts as exercise for everyone, no matter their age."
17. `I074` — "Family game nights that involve standing and moving can be just as fun as the sit-down kind."
18. `I079` — "Shared routines, like a Sunday walk, tend to last longer than ones you do alone."
19. `B130` — "What can a short burst of movement do for concentration?" → sharpens attention. (Distractors: "Nothing at all", "Make focus worse".)
20. `B136` — "What's the link between regular activity and memory?" → "Regular activity is linked to better memory." Question and answer are the same sentence.
21. `B182` — "What does daylight do for your body clock?" → "Helps keep it in rhythm." (Third entry on this fact.)
22. `I021` — "Resting after a hard workout is when your body actually gets stronger." (Said six times across the library; see §2b.)

Common thread: **almost all of the filler is in the original 90/40.** The expansion's weak items (`B130`, `B136`, `B182`) are weak because they duplicate a fact the same set already delivered better elsewhere, not because the fact is boring.

---

## 7. Content gaps

Subjects a family movement app should cover, that are missing or thin:

1. **Family, in Brain Boost: zero questions of 190.** The app's core differentiator has 31 insights and no quiz coverage. Anything about moving together, encouraging without comparing, or what kids notice would fit.
2. **Getting fitter aerobically.** `B058` (resting heart rate settles lower) is the only entry about aerobic adaptation. Nothing on: why the same walk gets easier, breathlessness improving, pacing, what "cardio fitness" actually means to change over a month.
3. **Growing bodies — the 9-year-old's own subject.** `I036` and `I157` mention sleep needs and teen chronotype. Nothing on growth spurts, temporary coordination loss during growth, growing pains, why kids can play all day, why a child's stamina profile differs from an adult's. This is the most conspicuous gap given the stated audience.
4. **The "weights stunt growth" myth.** A well-known, well-debunked myth directly about children and movement, and the library has 190 quiz questions and doesn't address it. It is the single best unclaimed Brain Boost in this space.
5. **Safety and when to stop.** `I028` ("feeling sharp pain is not [normal]") and `I200` are the only two. Nothing on: warming up before rough play, hydration when it's hot, sun, dressing for cold, what to do if something hurts tomorrow, when a grown-up should look at it.
6. **Older adults and falls.** For an app spanning grandparents: `I041` is the only mention of falls, and `I188` the only mention of age-related change. Four family items reference grandparents but none are about them.
7. **Adaptation — what if you can't do the movement.** Zero entries. Nothing about injury, illness, a bad knee, a wheelchair, or doing a smaller version. This sits badly against the project's "never punish who showed up" rule: the information layer has no language for the person who *can't* do today's mission as written.
8. **Sitting and screens beyond the bedroom.** `I001`, `B077` on movement breaks; `I032`/`I161`/`I166` on evening light. Nothing about sitting at school or at a desk all day, which is the actual sedentary problem for both audiences.
9. **Heat and cold safety.** Good physiology (`I110`, `I111`, `B184`, `B186`, `B188`) with no practical edge — `B186`'s "a 20°C lake is a shock" is cold-water shock, a real drowning mechanism, presented purely as a curiosity next to `I099` recommending swimming.
10. **Skill and play content.** `B071` (coordination) and `B140` (music) are alone. Nothing on throwing, catching, rhythm, learning a new physical skill — the things a family actually does together.
11. **Hydration for children specifically.** `I010` and `B116` are adult-framed.
12. **Movement and neurodiversity / fidgeting as self-regulation.** `I093` covers fidgeting energetically only.
13. **Indoor and bad-weather options.** The `Outdoors` Brain Boost category is 12 items with no counterpart for the rainy Tuesday in February.

---

## 8. Tone and safety spot-check

`tests/test_content_library.py` covers weight/shape/appearance vocabulary, diagnosis/medication language, shame words ("lazy", "excuses", "guilty"), and direct accusations ("you failed"). The following are inside those guardrails and still worth a decision.

### 8.1 Meal-skipping as a repeated named option — four occurrences

| ID | Text |
|---|---|
| `B018` | distractor: **"Skipping meals when busy"** |
| `B023` | distractor: **"Skipping meals"** |
| `B029` | distractor: **"Skip your next meal"** |
| `B016` | distractor: **"Eating a whole bag of chips"** |

The banned list catches "calorie", "calories", "diet plan", "dieting" — it does not catch these. In content read daily by nine-year-olds, three separate questions put meal-skipping on screen as a named behaviour, and `B016` frames a specific eating behaviour as the pathological response to emotion. This is the exact axis the banned-substring list exists to protect and the specific phrasings slip under it. `B016` is the worst of the four: the correct answer is "Naming what you're feeling" and the punchline distractor is a food behaviour.

### 8.2 Unsafe actions written as plausible-looking options

| ID | Text |
|---|---|
| `B003` | distractor: **"Closing both eyes while walking fast"** — in a question about what to *do at home* to test balance. |
| `B015` | distractor: **"Hold your breath as long as possible"** — as a stress-management option. |
| `B082`, `B121` | distractor: "Holding your breath" |

`B003` and `B015` are written in the imperative, as instructions. Breath-holding challenges are a documented child-safety hazard, and "close both eyes and walk fast" is a fall waiting to happen. Even as wrong answers, they are the only two items in the library that describe an action a child could copy and get hurt doing. The existing tests permit harmful *beliefs* as distractors when the explanation defuses them — neither of these explanations mentions the distractor at all.

### 8.3 Concentrated negative self-talk in one item

`B024` puts three of these on screen at once:

> "I always mess everything up" / "I'm the worst at everything" / "I should just give up"

The test file explicitly and correctly permits a harmful belief as a distractor so the explanation can defuse it (citing `B164`). `B164` states one belief and the explanation takes it apart. `B024` states three and the explanation addresses none of them individually. Worth a second look specifically because it is the one item that reproduces a child's inner critic verbatim.

### 8.4 Caffeine as the reference point

| `I009` | "Moving around in the morning can wake you up faster than a cup of coffee." |
| `I018` | "Feeling tired sometimes just means your body needs water, not caffeine." |
| `I150` | "Caffeine doesn't add energy. It blocks the chemical that tells your brain you're tired…" |

`I150` is genuinely good content. `I009` and `I018` assume the reader's baseline is a coffee drinker. Nothing is unsafe; the register is simply adult and three entries normalise caffeine to a nine-year-old reader.

### 8.5 Near-medical content the banned list doesn't reach

| `I137` | "Ice feels good on a sore spot largely because cold quiets the nerves carrying the signal." |
| `I228` | "A habit done badly still counts. Repetition builds the pattern, and quality shows up later on its own." |
| `B139` | explanation: "The name is a squashed-together version of 'endogenous **morphine**'…" |
| `B113` | distractor: **"Gluten"** offered as a candidate mineral |

`I137` is the closest the library comes to a treatment recommendation. It's hedged ("largely", "feels good") and probably fine, but it is the one to watch. `I228` is the only content in the library that could contribute to an injury (see §3.11). `B139` naming morphine and `B113` putting "gluten" in front of children with no context are minor but are both avoidable.

### 8.6 Aging framing

`I188` — "Balance takes more practice to hold on to as the years go by, which is exactly what makes a minute of it worth spending." This is the library's only statement about age-related decline, and it's aimed squarely at the grandparent using a family app. The second clause does the repair work, but the first is the one thing in 270 insights that tells a user something about their body is getting worse.

### 8.7 What is clean

No appearance, shape or weight language anywhere — the banned list is doing its job and no content skirts it. No negative framing of a missed day; `B008`, `B164`, `B165`, `I061`, `I216`, `I139` are consistently and genuinely kind about it. No comparison or leaderboard framing; `I240` ("Cheering for someone works better than comparing them to someone") is exactly on-brand. No "you should" imperatives from the product's own voice.

---

## 9. Suggested test additions

These would close the gaps found here in the style of the existing file:

1. `test_the_correct_answer_is_not_always_the_longest_option` — assert the correct option is the longest in fewer than 40% of questions (mirrors the existing position test).
2. `test_no_filler_distractors` — bound how many distractors may be "Nothing at all" / "Nothing measurable" / "No real benefit" / "It doesn't matter".
3. `test_the_library_speaks_one_dialect` — reject British spellings, or reject American ones, but not both; reject bare metric units.
4. `test_no_quiz_answer_is_a_printed_insight` — near-duplicate check between `BRAIN_BOOST_LIBRARY[i]["options"][correct_index]` and `INSIGHT_LIBRARY` texts.
5. Extend `BANNED_SUBSTRINGS` with `"skipping meals"`, `"skip your next meal"`, `"hold your breath"`, `"holding your breath"`.
