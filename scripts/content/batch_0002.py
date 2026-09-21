#!/usr/bin/env python3
"""Batch 0002 — the content types the store supports and the library had none of.

Riddles, mini-experiments and Rickie originals, plus facts and trivia in the
areas the September audit found thin. Written to be read by a fourteen-year-old
without being written down to, and by an adult without being childish.

Deliberately modest. The target is thousands, and the way to miss it badly is to
produce a thousand in one go that nobody can review. This is one batch, sized so
a person can actually read every line of it.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "content" / "items"
BATCH = "0002-discovery"
TODAY = date.today().isoformat()

# ── Riddles ─────────────────────────────────────────────────────────────────
# Answer in the text, after a line break — the delivery layer reveals on tap.
# No trick questions whose answer is a pun on a word the reader has to already
# know; a riddle a nine-year-old cannot get is just a way of telling them they
# are not clever.
RIDDLES = [
    # Original, and about the reader's own body — which is both the point of the
    # product and the reason they work. The first twenty here were classic
    # public-domain riddles (piano, candle, towel, stamp) and every one was
    # rejected on review: recycled, two answer-duplicates, and one about
    # coffins. See scripts/content/review_0002.py.
    ("I work hardest when you go downstairs, and I complain about it tomorrow.",
     "Your thigh muscles."),
    ("I beat without ever being hit, and I would rather you did not notice me.",
     "Your heart."),
    ("The harder you work, the more of me you make — and my entire job is to leave.",
     "Sweat."),
    ("I am taller in the morning than at night, and nobody ever catches me shrinking.",
     "You are. The discs in your spine compress over a day."),
    ("I come in pairs, I can only pull, and I need a partner to undo my work.",
     "A muscle."),
    ("You practiced me for years, you cannot remember learning me, and robots "
     "still cannot copy me properly.", "Walking."),
    ("Three of us vote on which way is up. When we disagree, you feel sick.",
     "Your eyes, your inner ear, and the sensors in your feet."),
    ("I am the biggest thing you own and you wear me on the outside.",
     "Your skin."),
    ("I never stop moving, even while you are standing perfectly still.",
     "You are — your balance corrects itself dozens of times a minute."),
    ("You cannot do me to yourself on purpose, however hard you concentrate, "
     "and nobody is entirely sure why not.", "Tickle yourself."),
    ("I am what is left of a reflex for fluffing up fur you no longer have.",
     "Goosebumps."),
    ("Break me and I knit. Load me and I thicken. Ignore me and I quietly give up.",
     "A bone."),
    ("I arrive at your wrist a moment after the beat that made me.",
     "Your pulse."),
    ("I happen before you have decided anything, which is the entire point "
     "of me.", "A reflex."),
    ("You cannot hear me, I run all day, and I am the only muscle you would "
     "rather never be in charge of.", "Your heart."),
    ("I am hollow, I am lighter than you would guess, and I am a factory for "
     "the blood you are using right now.", "A bone."),
    ("Warm me up and I reach further. Rush me and I push back.",
     "A muscle you are stretching."),
    ("I am the first thing to get tired when you read, and the fix is to look "
     "at something far away.", "The focusing muscle in your eye."),
    ("Your ancestors used me to run down animals in the heat. You mostly use me "
     "to be embarrassed.", "Sweating."),
    ("I am the gap between noticing and moving, and almost all of me happens "
     "in your head.", "Reaction time."),
]

# ── Mini-experiments ────────────────────────────────────────────────────────
# Safe, needs nothing, takes under a minute. Every one is something you find
# out about YOUR OWN body — never a comparison with anyone else's.
EXPERIMENTS = [
    ("Stand near a wall or a worktop, close enough to touch it. Stand on one "
     "foot and count. Now try it with your eyes closed. Most people last a "
     "fraction as long — that is how much of your balance was coming from your "
     "eyes without you knowing.", 9),
    ("Hold your arm out and touch your nose with your eyes shut. You didn't "
     "look and you didn't miss. That's a whole sense most people never notice "
     "they have.", 9),
    ("Rub your hands together fast for ten seconds. That warmth is friction "
     "becoming heat — the same reason moving on a cold day works better than "
     "another jumper.", 9),
    ("Find your pulse at your wrist, count for fifteen seconds, multiply by "
     "four. Now stand up, do twenty marches, and count again. You just watched "
     "your heart respond to a demand.", 9),
    ("Press your finger and watch the color come back. That's your blood "
     "refilling capillaries you just squeezed empty, and it takes about a "
     "second.", 9),
    ("Reach down toward your toes — only as far as is comfortable, and stop "
     "well before anything pulls. Note where you got to, walk for two minutes, "
     "and try again. Warm muscle reaches further than cold muscle, and one walk "
     "is enough to feel it.", 9),
    ("Stand with your feet together, then a shoulder-width apart. The second "
     "one feels obviously steadier. That's your base of support, and it's the "
     "same reason a stepladder's legs splay out.", 9),
    ("Walk across a clear bit of floor. Now do it again with your arms folded. "
     "It feels wrong because your arms were doing work you never asked them to "
     "do.", 9),
    ("Yawn on purpose and notice you can't do it convincingly. Then watch "
     "somebody else yawn. One of those is a reflex and one is acting.", 9),
    ("Look at something far away for twenty seconds after reading this. Your "
     "focusing muscle has been holding one position — that's most of why "
     "screens make eyes tired.", 9),
    ("Clench your jaw and put your fingers just in front of your ears. That "
     "movement under your fingertips is the muscle that bites — and it is doing "
     "it every time you eat without you ever noticing.", 9),
    ("Shift your weight onto one foot — hopping if you like, near something to "
     "hold if you would rather, or just leaning in a chair. Feel the ankle "
     "making corrections you never decided to make, dozens of them, none of "
     "which reached your attention.", 9),
    ("Breathe out slowly for twice as long as you breathe in, three times. "
     "Your heart rate drops slightly on every exhale, which is the whole trick "
     "behind slow breathing.", 9),
    ("Shrug your shoulders as high as they go, hold for five seconds, then let "
     "go. The drop afterwards is lower than where you started — muscles relax "
     "further after being asked to tighten.", 9),
    ("Stand up and reach for the ceiling. You are measurably taller now than "
     "you'll be tonight: the discs in your spine compress about a centimetre "
     "over a day and plump back up while you sleep.", 9),
]

# ── Rickie originals ────────────────────────────────────────────────────────
# His voice, not the narrator's. Short, dry, self-deprecating, never a lesson
# with a joke stapled on. The character bible's rule applies: he is a raccoon
# who happens to be a good coach, and he never performs being a raccoon.
RICKIE = [
    "I tried to demonstrate a burpee once. The bin lid won.",
    "People ask what I do on rest days. Mostly I think about snacks and look "
    "thoughtful. It's a full schedule.",
    "My personal best at anything is 'showed up twice in a row'. I'm very proud "
    "of it.",
    "Raccoon fact: we have extremely good hands and absolutely no self-control "
    "around a bin. I'm working on one of those.",
    "I don't do 'no pain no gain'. I do 'mild inconvenience, some gain, "
    "biscuit'.",
    "Somebody asked if I'm a personal trainer. I'm a raccoon with opinions. "
    "There's overlap.",
    "The best exercise is the one you'll actually do. The second best is the "
    "one you'll do while complaining. Both count.",
    "I once did twelve push-ups. I've been mentioning it for two years.",
    "My warm-up is a stretch and a sigh. The sigh is load-bearing.",
    "I'm told the mask isn't a disguise, it's just my face. I remain "
    "unconvinced and slightly disappointed.",
    "If you're reading this instead of moving, that's fine. I'm reading it too "
    "and I wrote it.",
    "Five minutes is not a small workout. It's a workout. The five is just how "
    "long it took.",
    "I have never once regretted a walk. I've regretted plenty of other things "
    "at speed.",
    "Rest days aren't days off. They're the bit where the work turns into "
    "something.",
    "Somebody called me a mascot. I prefer 'colleague with an unusual face'.",
]

# ── Facts, in the areas the September audit found thin ──────────────────────
FACTS = [
    ("Your body makes about two pints of saliva a day, and most of the work it "
     "does is digestion that starts before you've swallowed.", "HEALTH",
     "simplified", []),
    ("Cold hands are usually your body being efficient rather than unwell — it "
     "narrows the vessels near the skin to keep your core warm, and fingers are "
     "the first thing it gives up.", "HEALTH", "simplified", []),
    ("A hiccup is your diaphragm misfiring. Nobody has a convincing account of "
     "what hiccups are for, which is unusual for something every human does.",
     "FUN FACTS", "contested", []),
    ("A piano player's hands are almost entirely operated from the forearm. "
     "Watch a fast passage and the fingers are the end of the machine, not the "
     "machine.", "FUN FACTS", "simplified", []),
    ("Walking uphill uses more energy than walking downhill, but walking "
     "downhill leaves you sorer. Two different systems, two different bills.",
     "MOVEMENT", "simplified", []),
    ("Your sense of balance gets worse in the dark not because your legs "
     "change, but because one of the three systems reporting to your brain has "
     "gone quiet.", "BALANCE", "simplified", []),
    ("Muscles do not turn into fat when you stop training, any more than a car "
     "turns into a garage. They get smaller; that is all that happens.",
     "RECOVERY", "simplified", []),
    ("A stitch in your side is still unexplained. The leading guess involves "
     "the membrane lining your abdomen rubbing, but it remains a guess.",
     "MOVEMENT", "contested", []),
    ("Your tongue is not the strongest muscle in your body, for its size or "
     "otherwise. It is unusually tireless, which is probably where the story "
     "started.", "FUN FACTS", "simplified", []),
    ("Reaction time is slowest first thing in the morning and fastest in the "
     "late afternoon, tracking body temperature — which is also when most "
     "sporting records fall.", "FUN FACTS", "simplified", []),
    ("You lose water just by breathing. On a cold day you can watch it leave.",
     "HEALTH", "simplified", []),
    ("Goosebumps and standing hair are the same reflex a cat uses to look "
     "bigger. On us it achieves nothing at all, which is quite funny.",
     "FUN FACTS", "simplified", []),
    ("Children's bones bend further before they break than adults' do, which is "
     "why a child's fracture is often a crack rather than a clean snap.",
     "HEALTH", "simplified", []),
    ("Your heart does not beat faster because you decided to run. It starts "
     "climbing before the first step, from the intention alone.", "MOVEMENT",
     "simplified", []),
    ("Standing up quickly can make your vision gray at the edges for a second. "
     "That's blood pressure catching up with where your head has gone.",
     "HEALTH", "simplified", []),
]

# ── Trivia, same areas ──────────────────────────────────────────────────────
TRIVIA = [
    ("Where are the muscles that give your fingers their strength?",
     ["Mostly in your forearm", "Inside the fingers themselves",
      "In the palm of your hand", "Spread evenly through the whole arm"], 0,
     "Make a fist and feel just below your elbow — that is your grip working. "
     "The strong finger movements come from muscles in the forearm pulling on "
     "tendons, which is why fingers can be narrow and still powerful. The hand "
     "has small muscles of its own, and they do the fine control rather than "
     "the force.", "Muscles & Bones", "established",
     ["https://www.ncbi.nlm.nih.gov/books/NBK546607/",
      "https://www.ncbi.nlm.nih.gov/books/NBK537229/"]),
    ("What happens to a muscle when somebody stops training?",
     ["It gets smaller", "It turns into fat", "It stays the same but weakens",
      "It stiffens permanently"], 0,
     "It gets smaller, and that is all. Muscle cannot become fat any more than "
     "a car can become a garage — they are different tissues. The story "
     "survives because both changes often happen around the same time.",
     "Strength & Flexibility", "simplified", []),
    ("Why does walking downhill leave you sorer than walking uphill?",
     ["The muscles are working while being lengthened",
      "Downhill uses muscles that are not normally used at all",
      "The impact travels further up the leg",
      "You walk downhill faster, so there are more steps"], 0,
     "Going down, your legs work as brakes — contracting while being stretched, "
     "which is the kind of work that shows up the next day. Uphill costs more "
     "energy at the time and complains less afterwards.", "Everyday Movement",
     "simplified", []),
    ("What is a stitch in your side?",
     ["Nobody is certain", "Trapped air in the intestine",
      "A cramp in the diaphragm", "Lactic acid pooling in the abdomen"], 0,
     "Genuinely unresolved. The leading guess is the membrane lining the "
     "abdomen rubbing against itself, but it is a guess — which is worth "
     "knowing, because everything you have been told about stitches was said "
     "with more confidence than anyone has.", "Everyday Movement", "contested",
     []),
    ("Which is the strongest muscle in the human body?",
     ["The question has no settled answer",
      "The tongue, for its size", "The jaw, which bites hardest",
      "The heart, which never stops"], 0,
     "There is no agreed definition — force, force for its size, pressure, or "
     "work done over a lifetime each give a different winner. The jaw, the "
     "heart, the uterus and the calf all get claimed. The tongue almost never "
     "wins on any measure, despite being the popular answer.", "Muscles & Bones",
     "contested", []),
    ("When is reaction time usually quickest?",
     ["Late afternoon", "First thing in the morning", "Immediately after eating",
      "It does not change through the day"], 0,
     "It tracks body temperature, which peaks in the late afternoon — the same "
     "window in which a disproportionate number of sporting records have "
     "fallen. The difference is small, and nowhere near a reason to reschedule "
     "anything.", "Brain & Movement", "simplified", []),
    ("Why do your fingers get cold before the rest of you?",
     ["Your body narrows the vessels there to protect your core",
      "Fingers have no blood vessels of their own",
      "The skin there is thinner than anywhere else",
      "They are furthest from the heart, so blood arrives cooler"], 0,
     "It is a decision, not a failure. Your body pulls blood back from the "
     "extremities to keep the core warm, and fingers are first on the list. "
     "They are also furthest from the heart, but the narrowing is what you are "
     "feeling.", "Body Facts", "simplified", []),
    ("What is a hiccup?",
     ["Your diaphragm contracting when it was not asked to",
      "Air escaping the stomach too quickly",
      "The vocal cords closing on an in-breath",
      "A spasm in the muscles between the ribs"], 0,
     "The diaphragm fires without being asked and the vocal cords snap shut a "
     "fraction later, which is the sound. What hiccups are FOR is still "
     "unexplained — unusual for something every human does.", "Body Facts",
     "contested", []),
    ("What makes a child's broken bone different from an adult's?",
     ["Children's bones bend further before they break",
      "Children's bones contain no calcium yet",
      "Children's bones heal without any blood supply",
      "Children's bones are hollow until the teenage years"], 0,
     "A child's bone is more flexible, so it often cracks on one side like a "
     "green twig rather than snapping through — which is exactly what that kind "
     "of fracture is called.", "Growing & Ageing", "simplified", []),
    ("Why can standing up quickly make your vision gray at the edges?",
     ["Blood pressure takes a moment to catch up with your head",
      "The inner ear is briefly confused by the movement",
      "The eyes cannot refocus fast enough at that speed",
      "Blood rushes to the legs and stays there"], 0,
     "Your head has moved faster than your circulation could adjust, so for a "
     "second the top of you is slightly short. It settles by itself, and "
     "standing up slowly is the entire fix.", "Heart & Lungs", "simplified", []),
]


def main() -> int:
    counters = {"RID": 0, "EXP": 0, "RCK": 0, "FCT": 300, "TRV": 300}

    def next_id(kind: str) -> str:
        counters[kind] += 1
        return f"SF-{kind}-{counters[kind]:06d}"

    def base(kind: str, category: str, confidence: str, sources: list,
             min_age: int = 9) -> dict:
        return {"id": next_id(kind), "category": category, "min_age": min_age,
                "confidence": confidence, "sources": sources,
                # Generated. Nothing has read it yet, and the author fixing
                # their own gate failures is not a review.
                "stage": "generated",
                "review": {"pass": None, "depth": None, "notes": None},
                "added": TODAY, "batch": BATCH}

    riddles = [dict(base("RID", "Riddles", "editorial", []), type="riddle",
                    text=f"{q}\n\n{a}") for q, a in RIDDLES]
    experiments = [dict(base("EXP", "Try This", "simplified", [], age), type="experiment",
                        text=t) for t, age in EXPERIMENTS]
    rickie = [dict(base("RCK", "Rickie", "editorial", []), type="rickie", text=t)
              for t in RICKIE]
    facts = [dict(base("FCT", cat, conf, src), type="fact", text=t)
             for t, cat, conf, src in FACTS]
    trivia = [dict(base("TRV", cat, conf, src), type="trivia", question=q,
                   options=list(opts), answer_index=idx, explanation=exp)
              for q, opts, idx, exp, cat, conf, src in TRIVIA]

    for name, rows in (("riddles", riddles), ("experiments", experiments),
                       ("rickie", rickie), ("facts", facts), ("trivia", trivia)):
        path = OUT / f"{BATCH}-{name}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{len(rows):4} {name}")
    total = len(riddles) + len(experiments) + len(rickie) + len(facts) + len(trivia)
    print(f"{total:4} written to content/items/{BATCH}-*.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
