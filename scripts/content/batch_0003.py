#!/usr/bin/env python3
"""Batch 0003 — Brain Boost, into the categories the store is thinnest in.

The September inventory had 202 trivia items very unevenly spread: 17 on Heart
& Lungs and 4 on Family & Together. A library that is broad on paper but four
items deep in a category repeats itself inside a week for anyone who picks that
category, which is the failure a reader actually notices.

So this batch is not "100 more questions." It is the four thinnest categories
brought up to roughly the size of the others, plus the next three after them.

Written against the tells `scripts/content/validate.py` checks for, because
those are the ways a question can be answered without knowing anything:
  - answer positions deliberately weighted toward 2 and 3, which the existing
    corpus under-uses (59/52/47/44);
  - every option in a question kept to a similar length, so the correct one is
    never the long specific one among three short vague ones;
  - no bare dismissals ("nothing", "it's a myth") as distractors — a reader
    learns to never pick those;
  - distractors that are plausible wrong beliefs somebody actually holds,
    not obvious jokes.

STAGE. Every item here is `generated`. Not `accepted`, and not `validated`
either — validation is what `make content` does to it, and per content/SCHEMA.md
an author clearing the gates on their own batch is explicitly NOT a review.
Nothing in this file reaches a reader until somebody reads it.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "content" / "items"
BATCH = "0003-brainboost"
TODAY = date.today().isoformat()

# (category, question, options, answer_index, explanation, confidence)
TRIVIA = [
    # ── Family & Together (4 in the store) ──────────────────────────────────
    ("Family & Together",
     "Why is a walk with someone else often easier than the same walk alone?",
     ["Two people always walk faster than one",
      "Talking makes your muscles work less hard",
      "Company makes the effort feel smaller than it is",
      "Walking together uses a different set of muscles"],
     2,
     "Effort has a felt size as well as a real one, and company shrinks the felt "
     "one. The walk is the same distance; it just costs you less attention.",
     "simplified"),
    ("Family & Together",
     "What usually happens when a household starts moving at the same time each day?",
     ["Everyone gets the same amount of benefit from it",
      "The time itself starts doing the remembering for you",
      "It only works if everyone does the same activity",
      "The youngest person sets the pace for everybody"],
     1,
     "A habit attached to a time of day stops needing a decision. Nobody has to "
     "propose it, which is the part that usually fails.",
     "simplified"),
    ("Family & Together",
     "A grandparent and a child go for the same walk. Who is getting a real workout?",
     ["Only the child, because they move faster",
      "Only the grandparent, because it is harder for them",
      "Neither — a walk is too gentle to count for either",
      "Both, because effort is measured against your own body"],
     3,
     "Effort is relative to the person doing it. The same pace can be easy for "
     "one body and genuinely demanding for another, and both count.",
     "simplified"),
    ("Family & Together",
     "Why do people often keep a habit longer when somebody else knows about it?",
     ["Being watched makes your body work more efficiently",
      "The habit becomes something shared rather than private",
      "Other people remind you more often than you remember",
      "It turns the habit into a competition you want to win"],
     1,
     "Once somebody else knows, skipping it is a small thing you would be doing "
     "to them too. That is different from a promise you made only to yourself.",
     "simplified"),
    ("Family & Together",
     "What is the most useful thing to say to someone coming back after a long gap?",
     ["Ask what stopped them so it does not happen again",
      "Remind them how well they were doing before",
      "Say nothing about the gap and get straight on with it",
      "Tell them the gap barely matters now that they are back"],
     3,
     "The gap is the thing they are already thinking about. Naming it lightly and "
     "moving on beats both silence and a post-mortem.",
     "editorial"),
    ("Family & Together",
     "Two people start the same routine on the same day. A month later one has "
     "gone further. What does that most likely mean?",
     ["One of them tried considerably harder",
      "Bodies respond to the same work at different rates",
      "The other one was doing the movements incorrectly",
      "The first one has better natural ability for it"],
     1,
     "Response to the same training varies a lot between people and always has. "
     "It says very little about how hard either one worked.",
     "simplified"),
    ("Family & Together",
     "Why does doing something active with a child tend to work better than telling them to?",
     ["Children copy what they see far more than what they hear",
      "Adults are better at explaining while they move",
      "It guarantees the child is doing it correctly",
      "Children need supervision for most kinds of movement"],
     0,
     "Watching somebody do a thing ordinarily is the most reliable teaching there "
     "is. It also removes the part where it sounds like an instruction.",
     "simplified"),
    ("Family & Together",
     "What tends to happen to a shared activity when one person is much better at it?",
     ["It motivates the other person to catch up quickly",
      "It has no effect as long as nobody mentions it",
      "The gap makes it feel like a test",
      "The stronger person gets a poorer workout from it"],
     2,
     "Difficulty that is obviously mismatched stops being company and starts "
     "being assessment. Picking something you are both mediocre at works better.",
     "editorial"),
    ("Family & Together",
     "Why is walking a dog unusually good at keeping people consistent?",
     ["Dogs need more exercise than people do",
      "It makes the walk longer than you would choose",
      "The obligation cannot be rescheduled",
      "Walking a dog uses more effort than walking alone"],
     2,
     "The dog will not accept 'tomorrow'. An external obligation you cannot "
     "renegotiate is a far stronger commitment than an internal one.",
     "editorial"),
    ("Family & Together",
     "What makes a household routine survive one person losing interest?",
     ["Having a rule about what happens if somebody stops",
      "Making sure nobody notices that they stopped",
      "The routine belonging to the day, not a person",
      "Choosing an activity that person was never keen on"],
     2,
     "If the routine only exists because one person drives it, it ends when they "
     "do. Attached to a time instead, it survives anyone's off week.",
     "editorial"),
    ("Family & Together",
     "Why is it worth letting a younger person choose the activity sometimes?",
     ["Their choice will usually be more physically demanding",
      "It is the only way to know what they enjoy",
      "Choosing it makes them far more likely to turn up for it",
      "Adults tend to pick activities that are too difficult"],
     2,
     "Having chosen a thing changes your relationship to it. The activity matters "
     "much less than whose idea it was.",
     "simplified"),
    ("Family & Together",
     "What is the effect of turning a shared walk into a daily step target?",
     ["It reliably makes everybody walk further",
      "It can turn company into something you can fail at",
      "It helps most when the people walk at different speeds",
      "Targets work better for groups than for individuals"],
     1,
     "A number introduces the possibility of falling short. That is sometimes "
     "useful and sometimes the thing that ends a walk people were enjoying.",
     "editorial"),
    ("Family & Together",
     "Somebody in the house says they are too tired to join. What generally helps most?",
     ["Encouraging them to push through it anyway",
      "Offering a much shorter version of the same thing",
      "Leaving them out and not mentioning it again",
      "Asking them to commit to tomorrow instead"],
     1,
     "A shorter version keeps the habit and the company intact without arguing "
     "about whether the tiredness is real. It usually is.",
     "editorial"),
    ("Family & Together",
     "Why do people often move more on holiday without noticing?",
     ["Warmer places make movement feel easier",
      "Holidays generally involve more planned exercise",
      "The movement is attached to something you want to do",
      "People have more energy when they are rested"],
     2,
     "Walking to see something is not experienced as exercise at all. The same "
     "distance on a treadmill is a task; here it is the way you got there.",
     "simplified"),

    # ── Growing & Ageing (5 in the store) ───────────────────────────────────
    ("Growing & Ageing",
     "What happens to your sense of balance as you get older, if nothing is done about it?",
     ["It stays the same but your legs get weaker",
      "It improves as you get more practice at it",
      "It gradually fades, and practicing it slows that down",
      "It changes only after a fall has already happened"],
     2,
     "Balance behaves like a skill rather than a fixed trait: unused it fades, "
     "practiced it holds up remarkably well.",
     "simplified"),
    ("Growing & Ageing",
     "Can somebody in their eighties still build strength?",
     ["No, muscle stops responding after about sixty",
      "Only if they were strong when they were younger",
      "Only with equipment designed for older people",
      "Yes, muscle responds to being used at any age"],
     3,
     "Studies in people well into their nineties have found real strength gains. "
     "The rate is slower; the response itself does not switch off.",
     "established",
     ),
    ("Growing & Ageing",
     "Why do teenagers sometimes get clumsier for a while?",
     ["They are growing tired faster than they realize",
      "Limbs change length faster than coordination updates",
      "Their balance organs are still finishing developing",
      "Growth makes muscles temporarily weaker"],
     1,
     "Your brain holds a map of how long your arms and legs are. During a growth "
     "spurt the map is briefly out of date, and coordination catches up after.",
     "simplified"),
    ("Growing & Ageing",
     "What is the main reason older adults are encouraged to keep doing strength work?",
     ["It is the fastest way to improve heart health",
      "It keeps everyday independence",
      "It is gentler on joints than walking is",
      "It replaces the need for balance practice"],
     1,
     "The point is rarely the muscle itself. It is getting out of a chair unaided "
     "and carrying your own shopping, which is what strength buys you later.",
     "simplified"),
    ("Growing & Ageing",
     "Do children need to train the way adults do to get benefit from moving?",
     ["Yes, structure matters more for children than adults",
      "Children only benefit from organized sport",
      "No — varied play covers most of what they need",
      "Children need shorter but much harder sessions"],
     2,
     "Climbing, chasing and messing about covers strength, balance, coordination "
     "and stamina at once, which is roughly what a structured program is for.",
     "simplified"),
    ("Growing & Ageing",
     "What happens to reaction time as people age?",
     ["It is fixed from birth and never changes",
      "It slows a little, less so in active people",
      "It slows sharply and suddenly in your sixties",
      "It improves with age because of experience"],
     1,
     "Some slowing is ordinary. How much is heavily influenced by whether you "
     "keep doing things that demand a quick response.",
     "simplified"),
    ("Growing & Ageing",
     "Why does recovery from a hard effort tend to take longer as you get older?",
     ["Muscles become permanently more fragile",
      "Older people work harder for the same result",
      "Several repair processes each run a little slower",
      "The body stops repairing muscle after middle age"],
     2,
     "No single system fails; a number of them just run at a slightly reduced "
     "pace. The practical answer is more time between hard efforts, not fewer.",
     "simplified"),
    ("Growing & Ageing",
     "At what age is it too late to start being active?",
     ["Around seventy, when injury risk becomes too high",
      "Once you have been inactive for over a decade",
      "It depends entirely on your family history",
      "There is no age at which starting stops helping"],
     3,
     "People who begin late still measurably improve. Starting is worth more than "
     "the age at which you start.",
     "simplified"),
    ("Growing & Ageing",
     "Why do bones need to be loaded to stay strong?",
     ["Loading warms the bone and helps it grow",
      "Bone rebuilds itself in response to being used",
      "Pressure forces minerals into the bone from the blood",
      "Loading stretches bone slightly, making it longer"],
     1,
     "Bone is living tissue that constantly rebuilds. Regular load is the signal "
     "telling it how strong it needs to be.",
     "simplified"),
    ("Growing & Ageing",
     "What does 'use it or lose it' actually describe in the body?",
     ["A rule that applies only to muscle tissue",
      "The idea that rest is always harmful",
      "Tissue kept in proportion to its demand",
      "A gradual loss that begins at a fixed age"],
     2,
     "Muscle, bone and balance are all maintained at roughly the level demanded "
     "of them. Reduce the demand and the body economizes.",
     "simplified"),
    ("Growing & Ageing",
     "Is stiffness in the morning a normal part of getting older?",
     ["No, it always indicates a joint problem",
      "Yes, and moving gently usually eases it",
      "Yes, and the only remedy is rest",
      "No, it only affects people who exercise"],
     1,
     "Some morning stiffness is ordinary. It typically loosens with gentle "
     "movement. Stiffness that is severe or persistent is worth asking about.",
     "simplified"),
    ("Growing & Ageing",
     "Why is falling treated so seriously in older adults?",
     ["Bones in older adults cannot heal at all",
      "The fear afterward reduces movement",
      "Falls are much more common than in younger people",
      "Older adults fall from greater heights on average"],
     1,
     "The fall itself is often survivable. The loop that follows — less walking, "
     "weaker legs, worse balance — is what does the lasting damage.",
     "simplified"),
    ("Growing & Ageing",
     "Do you stop growing taller and then simply stay that height?",
     ["Yes, your height is fixed once growth finishes",
      "No, most people continue growing slowly for life",
      "No, height slowly decreases over decades",
      "Yes, unless you do a lot of heavy lifting"],
     2,
     "The discs between the vertebrae lose a little height over many years, so "
     "most people end up slightly shorter than their peak.",
     "simplified"),
    ("Growing & Ageing",
     "What is the best predictor of how well somebody moves at eighty?",
     ["How athletic they were as a teenager",
      "Their height and build",
      "Whether they played sport at school",
      "How much they moved in recent years"],
     3,
     "Recent decades matter far more than distant ones. Teenage athleticism "
     "you stopped using thirty years ago is not doing much for you now.",
     "simplified"),

    # ── Safety & Sense (6 in the store) ─────────────────────────────────────
    ("Safety & Sense",
     "You feel a sharp pain partway through a movement. What is the sensible response?",
     ["Finish the set and see if it settles",
      "Switch to the other side and carry on",
      "Push through slowly with lighter effort",
      "Stop that movement and find out why later"],
     3,
     "Sharp is the word that matters. Dull effort is ordinary; sharp is your body "
     "naming a specific place, and it is worth listening to.",
     "simplified"),
    ("Safety & Sense",
     "What is the difference between ordinary effort and a warning sign?",
     ["Effort builds gradually; a warning is sudden",
      "Effort is felt in muscle, warnings only in joints",
      "A warning always hurts more than effort does",
      "There is no reliable way to tell them apart"],
     0,
     "Effort arrives slowly and spreads out. A warning is usually abrupt and you "
     "can point at it. Not a perfect rule, but a useful one.",
     "simplified"),
    ("Safety & Sense",
     "Why is holding your breath during a hard effort discouraged?",
     ["It makes the movement harder than it needs to be",
      "It can spike pressure and leave you lightheaded",
      "It uses up oxygen you will need afterward",
      "It prevents the muscle from working fully"],
     1,
     "Breath-holding under strain raises pressure sharply and some people go "
     "dizzy. Breathe out on the effort — that is the whole technique.",
     "simplified"),
    ("Safety & Sense",
     "You feel dizzy during a workout. What should happen next?",
     ["Sit down, and mention it to a doctor if it repeats",
      "Drink water quickly and continue",
      "Lie flat on the floor until it passes completely",
      "Slow the pace but keep the session going"],
     0,
     "Sitting down handles the immediate risk. Dizziness that keeps happening is "
     "a question for a professional, not something to work around.",
     "simplified"),
    ("Safety & Sense",
     "What does a good warm-up actually do?",
     ["Removes any chance of injury occurring",
      "Stretches muscles to their full length",
      "Warms tissue and rehearses the movement",
      "Uses up energy that would otherwise cause cramp"],
     2,
     "Warmer tissue moves more easily and rehearsing the pattern wakes up the "
     "coordination for it. It reduces risk; it does not abolish it.",
     "simplified"),
    ("Safety & Sense",
     "Is it sensible to exercise through a heavy cold?",
     ["Yes, sweating helps clear the illness faster",
      "Only if you take something for the symptoms first",
      "Gentle movement is fine; hard efforts can wait",
      "No, any movement while ill is harmful"],
     2,
     "A short walk is usually fine. A hard session while genuinely unwell mostly "
     "buys you a longer illness, and anything chest-related deserves caution.",
     "simplified"),
    ("Safety & Sense",
     "Why does footing matter more than people expect?",
     ["Poor footing makes muscles work inefficiently",
      "Most movement injuries happen on soft ground",
      "Shoes affect how much benefit you get from walking",
      "The surface usually did something unexpected"],
     3,
     "Wet tile, a loose rug, an unseen step. The movement is rarely the problem; "
     "the ground doing something you did not predict usually is.",
     "simplified"),
    ("Safety & Sense",
     "What is the right amount of water to drink during ordinary activity?",
     ["A fixed amount measured before you start",
      "As much as you can manage, to stay ahead of thirst",
      "Roughly what thirst asks for",
      "Nothing until the session is finished"],
     2,
     "For ordinary activity thirst is a decent guide. Drinking far beyond it is "
     "not safer, and in rare cases it is its own problem.",
     "simplified"),
    ("Safety & Sense",
     "Somebody offers you advice about your knee. When is it worth acting on?",
     ["When several people have said the same thing",
      "When it comes from somebody who has examined it",
      "When it comes from an experienced athlete",
      "When it matches what you read online"],
     1,
     "A joint is specific to its owner. Advice that has not met your knee is a "
     "guess, however confident or well-meant it is.",
     "editorial"),
    ("Safety & Sense",
     "What is the safest way to begin after a long time away from moving?",
     ["At the level you managed before the gap",
      "With the hardest session you can complete",
      "Below what you think you can do, for a few weeks",
      "With stretching only, until flexibility returns"],
     2,
     "Starting under your capacity is how you get to do it again tomorrow. Most "
     "restart injuries come from resuming at the old level.",
     "simplified"),
    ("Safety & Sense",
     "Why is exercising in heat treated differently?",
     ["Heat makes muscles more likely to tear",
      "Cooling is competing with the effort for blood flow",
      "Sweat removes minerals the muscles need to contract",
      "Warm air carries less oxygen than cool air"],
     1,
     "Your body sends blood to the skin to shed heat and to the muscles to do the "
     "work. In real heat those demands compete, so the same effort costs more.",
     "simplified"),
    ("Safety & Sense",
     "What is a reasonable way to judge whether a new movement is too much?",
     ["Whether it leaves you sore the next day",
      "Whether you can still talk during it",
      "Whether it feels harder than last time",
      "Whether other people find it easy"],
     1,
     "The talk test is crude and surprisingly good. If you cannot get a sentence "
     "out, you are working harder than a first session needs.",
     "simplified"),
    ("Safety & Sense",
     "Your knee clicks but does not hurt. What does that usually mean?",
     ["It is a warning that pain is coming",
      "It means the joint needs more rest",
      "Painless joint noise is usually ordinary",
      "It indicates the movement is being done wrong"],
     2,
     "Joints make noise. Painless clicking is very common and usually means "
     "little. Noise that comes with pain or swelling is a different question.",
     "simplified"),
    ("Safety & Sense",
     "Why do instructions often say to move slowly when learning something new?",
     ["Slow movement uses more muscle",
      "Speed hides mistakes you would otherwise feel",
      "It is the only way to avoid getting out of breath",
      "Slow movements are easier on the heart"],
     1,
     "At speed, momentum does part of the work and covers up a rough pattern. "
     "Slowing it down is how the mistake becomes noticeable.",
     "simplified"),

    # ── Mind & Wellness (7 in the store) ────────────────────────────────────
    ("Mind & Wellness",
     "How soon after starting to move do most people notice a change in mood?",
     ["Only after several weeks of consistency",
      "Often within the first few minutes",
      "Usually the following day",
      "Only after the activity becomes easy"],
     1,
     "The mood change tends to arrive far sooner than any physical change, which "
     "is why it is the more reliable reason to start.",
     "simplified"),
    ("Mind & Wellness",
     "Why does a walk often help when you are stuck on a problem?",
     ["Walking sends more oxygen to the brain",
      "It gives you time to think without interruption",
      "Loosening attention lets connections surface",
      "Physical effort resets your concentration"],
     2,
     "Solutions tend to arrive when you stop gripping the problem. Walking is "
     "good at loosening the grip without emptying your head entirely.",
     "simplified"),
    ("Mind & Wellness",
     "What tends to happen to motivation when you wait for it before starting?",
     ["It builds steadily until you act",
      "It arrives more reliably in the morning",
      "It usually follows the action",
      "It returns once you have rested enough"],
     2,
     "Starting is usually what produces the motivation, not the other way round. "
     "Waiting to feel like it is waiting for the wrong thing.",
     "editorial"),
    ("Mind & Wellness",
     "Why is a very small daily amount often better than an occasional large one?",
     ["Small amounts are better for the heart",
      "Large sessions do not count toward a habit",
      "It is easier to recover from small sessions",
      "The habit survives the days you have no time"],
     3,
     "A two-minute version you can do on a terrible day keeps the thread intact. "
     "An hour you can only manage sometimes breaks every time you cannot.",
     "editorial"),
    ("Mind & Wellness",
     "What does rumination — going over something repeatedly — respond to?",
     ["Trying harder to stop thinking about it",
      "Analyzing it until it resolves",
      "Waiting for the feeling to pass on its own",
      "Doing something that occupies attention gently"],
     3,
     "Rumination needs somewhere else to go, and it does not go on command. A "
     "walk gives attention a mild job, which is often enough.",
     "simplified"),
    ("Mind & Wellness",
     "Is it normal to enjoy an activity less on some days than others?",
     ["No, that means the activity is wrong for you",
      "Yes, and it says very little about the activity",
      "No, enjoyment should build steadily",
      "Yes, but only when you are unwell"],
     1,
     "Enjoyment fluctuates for reasons that have nothing to do with the activity. "
     "A flat day is not evidence about whether it is the right thing.",
     "editorial"),
    ("Mind & Wellness",
     "What is the effect of comparing your progress to somebody else's?",
     ["It provides a useful benchmark for your own pace",
      "It helps most when the other person is close to your level",
      "It measures you against a starting point that is not yours",
      "It reliably increases how consistent you are"],
     2,
     "You cannot see where they started, what else is in their week, or what "
     "their body does with the same work. The comparison is missing its baseline.",
     "editorial"),
    ("Mind & Wellness",
     "Why does breaking a long streak often feel worse than never having one?",
     ["The body reacts badly to a sudden stop",
      "Losing something feels sharper than not having had it",
      "Streaks create a physical dependency on routine",
      "The days already done stop counting once it breaks"],
     1,
     "Losing a thing lands harder than never having it — a well-documented quirk. "
     "Worth knowing, because the days you already did still happened.",
     "simplified"),
    ("Mind & Wellness",
     "What usually makes a habit stick to a particular time of day?",
     ["Choosing the time when you have most energy",
      "Setting a reminder for that time each day",
      "Attaching it to something you already do then",
      "Picking a time nobody will interrupt"],
     2,
     "Anchoring to an existing routine — after brushing your teeth, before lunch "
     "— outperforms reminders, because the anchor is already reliable.",
     "simplified"),
    ("Mind & Wellness",
     "How much does a single missed day matter to long-term progress?",
     ["It sets you back roughly a week",
      "It matters only if it becomes several",
      "It undoes the adaptation you had built",
      "It matters more the longer you have been going"],
     1,
     "One day is invisible in the long run. What matters is whether it becomes "
     "the shape of the next fortnight.",
     "simplified"),
    ("Mind & Wellness",
     "Why do people often underestimate how much a short walk helped?",
     ["The benefit takes hours to appear",
      "They compare it to the walk they meant to take",
      "Short walks genuinely do very little",
      "The effect is too small to notice reliably"],
     1,
     "Ten minutes gets measured against the thirty you intended, and comes out "
     "looking like a failure instead of ten more minutes than nothing.",
     "editorial"),
    ("Mind & Wellness",
     "What is a realistic thing to expect from movement on a bad day?",
     ["It will resolve what is making the day bad",
      "It will make you enjoy the rest of the day",
      "It will probably make the day slightly more bearable",
      "It will have no effect unless you do enough of it"],
     2,
     "Slightly more bearable is a real result and it is usually what is on offer. "
     "Expecting it to fix the day is how it ends up looking useless.",
     "editorial"),
    ("Mind & Wellness",
     "Why is it worth noticing the days you nearly skipped but did not?",
     ["They burn more energy than ordinary days",
      "They are the days that prove the habit is real",
      "They indicate you are training hard enough",
      "They predict how long the streak will last"],
     1,
     "Anybody can do it on an easy day. The nearly-skipped ones are where the "
     "habit is actually being built.",
     "editorial"),
    ("Mind & Wellness",
     "What does sleep have to do with how hard a workout feels?",
     ["None — effort is purely physical",
      "Poor sleep only affects long sessions",
      "Poor sleep makes the same effort feel harder",
      "Sleep affects recovery but not the session itself"],
     2,
     "After a poor night the same work registers as more demanding. The workout "
     "did not change; your reading of it did.",
     "simplified"),

    # ── Balance & Agility ───────────────────────────────────────────────────
    ("Balance & Agility",
     "Why does standing on one leg get easier within a few days of practice?",
     ["The muscles grow measurably stronger that quickly",
      "Your inner ear becomes more sensitive",
      "The nervous system gets better at making corrections",
      "Your body shifts its weight lower over time"],
     2,
     "Early gains are almost entirely the nervous system learning to correct "
     "faster and smaller. Muscle takes considerably longer.",
     "simplified"),
    ("Balance & Agility",
     "What happens to your balance when you close your eyes?",
     ["It is unaffected if your legs are strong",
      "It improves, because you concentrate harder",
      "It gets harder; one input of three is gone",
      "It only changes for people over sixty"],
     2,
     "Balance runs on vision, the inner ear and sensors in your feet and joints. "
     "Remove vision and the other two have to work considerably harder.",
     "simplified"),
    ("Balance & Agility",
     "Why is balance practice usually done near something to hold?",
     ["Holding on makes the practice more effective",
      "So you can practice the harder version safely",
      "It keeps the exercise from becoming too easy",
      "It prevents the ankles from getting tired"],
     1,
     "A hand near a wall means you can work right at the edge of your ability, "
     "which is where balance improves, without the cost of getting it wrong.",
     "simplified"),
    ("Balance & Agility",
     "What is 'proprioception'?",
     ["The ability to balance on uneven ground",
      "Knowing where your limbs are",
      "The speed at which you react to a stumble",
      "The way the inner ear detects movement"],
     1,
     "It is why you can touch your nose with your eyes shut. Joints and muscles "
     "constantly report their position, and you never notice until it fails.",
     "established"),
    ("Balance & Agility",
     "Why does walking on sand or grass feel more tiring than pavement?",
     ["Soft ground is colder and drains energy",
      "The surface absorbs some of each push",
      "You automatically walk faster on soft ground",
      "Uneven ground shortens your stride"],
     1,
     "Some of every step goes into moving the ground rather than you, and your "
     "ankles do extra work stabilizing. Same distance, more effort.",
     "simplified"),
    ("Balance & Agility",
     "What does catching yourself after a trip mostly rely on?",
     ["Leg strength built up over years",
      "Having seen the obstacle in advance",
      "A correction that beats conscious thought",
      "Reflexes that only work when you are rested"],
     2,
     "The recovery step is underway before you are consciously aware of tripping. "
     "Deciding to catch yourself would be far too slow.",
     "simplified"),
    ("Balance & Agility",
     "Why is agility trained with changes of direction rather than straight lines?",
     ["Straight-line movement does not build muscle",
      "Turning is where control gets tested",
      "Changing direction is more tiring than running",
      "Straight lines are easier to do incorrectly"],
     1,
     "Almost nothing in ordinary life is a straight line. The moment you turn, "
     "stop or step aside is where control gets used.",
     "simplified"),
    ("Balance & Agility",
     "Does balance need to be practiced for long to improve?",
     ["Yes, at least thirty minutes at a time",
      "Yes, but only if done on an unstable surface",
      "No, brief practice done often works well",
      "No, it improves on its own with ordinary walking"],
     2,
     "A minute here and there while the kettle boils adds up better than a long "
     "session once a week. Frequency beats duration for this one.",
     "simplified"),
    ("Balance & Agility",
     "What makes carrying something in one hand harder than it looks?",
     ["The arm tires faster than the legs do",
      "Your whole body compensates for the uneven load",
      "One-sided loads are heavier to lift initially",
      "It forces you to walk more slowly"],
     1,
     "Everything from your ankle to your neck adjusts to keep you upright. That "
     "is real work, which is why a one-sided carry is a genuine exercise.",
     "simplified"),
    ("Balance & Agility",
     "Why do people often balance better on their non-dominant side than expected?",
     ["That side has naturally stronger muscles",
      "Balance is unrelated to which side you favor",
      "The dominant side is usually more injured",
      "That side gets more stabilizing practice"],
     3,
     "The side you do not lead with often spends more time holding you steady "
     "while the other one acts. It quietly gets good at it.",
     "simplified"),

    # ── Outdoors ────────────────────────────────────────────────────────────
    ("Outdoors",
     "Why does the same walk feel easier outside than on a treadmill?",
     ["Outdoor air contains more oxygen",
      "Scenery absorbs some of your attention",
      "You naturally walk more slowly outdoors",
      "Treadmills require more muscular effort"],
     1,
     "Attention is part of how hard something feels. A view takes some of it, so "
     "there is less left over to spend noticing the effort.",
     "simplified"),
    ("Outdoors",
     "What is the main reason morning daylight is recommended?",
     ["It is the coolest part of the day for exercise",
      "Morning air is cleaner in most places",
      "It helps set the timing of your sleep",
      "Sunlight is strongest and most beneficial then"],
     2,
     "Light in the morning is one of the strongest signals your body clock uses, "
     "and it mostly shows up that night as falling asleep more easily.",
     "simplified"),
    ("Outdoors",
     "Why does walking uphill raise effort so much more than speeding up?",
     ["Hills require a completely different muscle group",
      "You are lifting your whole body against gravity",
      "The incline reduces how much oxygen you take in",
      "Uphill walking uses a less efficient stride"],
     1,
     "On the flat you mostly move forward. Uphill you also move up, and raising "
     "your body weight is expensive.",
     "simplified"),
    ("Outdoors",
     "What is the sensible way to dress for exercising in the cold?",
     ["One heavy layer that blocks all wind",
      "As little as possible, since you warm up",
      "Several thin layers you can remove",
      "Whatever keeps you warmest at the start"],
     2,
     "You will be considerably warmer ten minutes in. Layers let you shed heat "
     "rather than choosing between freezing at the start and sweating later.",
     "simplified"),
    ("Outdoors",
     "Why does cold air sometimes make your chest feel tight when exercising?",
     ["Cold air carries less oxygen than warm air",
      "Cold air narrows the blood vessels in the chest",
      "The lungs contract slightly in cold conditions",
      "Cold dry air can irritate the airways"],
     3,
     "It is usually dryness and cold irritating the airway. Breathing through a "
     "scarf warms the air first. Persistent tightness is worth asking about.",
     "simplified"),
    ("Outdoors",
     "What does being near trees and greenery appear to do for people?",
     ["It improves lung function measurably",
      "It is associated with lower reported stress",
      "It increases how far people walk",
      "It has no measurable effect beyond the walking"],
     1,
     "Time in green space is consistently associated with lower stress. Exactly "
     "why is still argued over, but the association holds up well.",
     "contested"),
    ("Outdoors",
     "Why is it worth varying the route you walk?",
     ["Different routes work different muscles",
      "New surroundings keep attention engaged",
      "Repeating a route reduces its benefit",
      "Variety is needed to keep improving fitness"],
     1,
     "The physical benefit is much the same. The difference is whether you are "
     "still noticing anything, and a familiar route stops being noticed.",
     "editorial"),
    ("Outdoors",
     "What happens to your pace when you walk with headphones on?",
     ["It stays the same as walking in silence",
      "It becomes much more irregular",
      "It drifts toward the rhythm you hear",
      "It slows because you are distracted"],
     2,
     "People drift toward the beat without meaning to. Useful if you want to pick "
     "the pace up, and worth knowing if you meant to go gently.",
     "simplified"),
    ("Outdoors",
     "Why is a short walk after eating often suggested?",
     ["It makes the meal digest completely",
      "It prevents discomfort from eating too quickly",
      "It helps steady the rise in blood sugar",
      "It is the best time of day for exercise"],
     2,
     "Moving muscles take up some of the sugar from the meal, which softens the "
     "peak. Ten minutes is enough to matter.",
     "simplified"),
    ("Outdoors",
     "What is the most common reason people skip an outdoor walk?",
     ["The weather being genuinely unsuitable",
      "Not having the right clothing for it",
      "Having nowhere pleasant nearby to walk",
      "Deciding before looking outside"],
     3,
     "The forecast in your head is usually worse than the weather. Stepping "
     "outside first turns the decision into a real one.",
     "editorial"),

    # ── Habits & Consistency ────────────────────────────────────────────────
    ("Habits & Consistency",
     "How long does it actually take to form a habit?",
     ["Exactly twenty-one days for most people",
      "Around two months, with huge variation",
      "A fixed period that depends on the activity",
      "Habits form immediately or not at all"],
     1,
     "The often-quoted twenty-one days has no good evidence behind it. Measured "
     "studies found an average nearer two months and a very wide spread.",
     "established"),
    ("Habits & Consistency",
     "What is the most useful size for a new habit?",
     ["Large enough to produce visible results",
      "Whatever you can manage on a good day",
      "Small enough to do on your worst day",
      "The same size as the habit you want eventually"],
     2,
     "The floor matters more than the ceiling. A habit sized for your worst day "
     "is one that never has to break.",
     "editorial"),
    ("Habits & Consistency",
     "What tends to happen after the first missed day?",
     ["The habit is effectively broken",
      "Nothing, unless a second one follows",
      "Motivation increases to compensate",
      "The next session feels noticeably harder"],
     1,
     "One miss changes almost nothing. Two in a row is where a habit starts "
     "becoming a thing you used to do.",
     "simplified"),
    ("Habits & Consistency",
     "Why is tracking something often enough to change it?",
     ["Tracking creates a record you can analyze",
      "It makes the activity feel more official",
      "Noticing a behavior tends to shift it",
      "It reminds you at the right time of day"],
     2,
     "Simply measuring a thing nudges it, before you do anything with the "
     "numbers. Attention is part of the mechanism.",
     "simplified"),
    ("Habits & Consistency",
     "What is the effect of planning exactly when and where you will do something?",
     ["It works only for people who like structure",
      "It makes the plan harder to adapt later",
      "It markedly increases the chance you do it",
      "It matters less than wanting to do it"],
     2,
     "Deciding the when and where in advance removes the moment of deliberation, "
     "and that moment is where most intentions quietly die.",
     "established"),
    ("Habits & Consistency",
     "Why do habits often collapse during a change of routine?",
     ["Stress makes people less disciplined",
      "New routines take all your available energy",
      "The cues the habit depended on have gone",
      "Habits need consistency in effort, not timing"],
     2,
     "A habit is usually held in place by its surroundings — a time, a place, a "
     "preceding event. Move house or change jobs and the scaffolding goes.",
     "simplified"),
    ("Habits & Consistency",
     "What is the best thing to do the day after missing one?",
     ["Do double to make up the gap",
      "Do the ordinary session",
      "Restart the habit from the beginning",
      "Take a second day off to reset properly"],
     1,
     "Making up the gap treats the miss as a debt, which makes missing more "
     "costly and therefore more frightening. Just do the ordinary day.",
     "editorial"),
    ("Habits & Consistency",
     "Why is it easier to keep a habit than to start one?",
     ["The activity becomes physically easier over time",
      "Motivation grows steadily once you begin",
      "Less of the decision is being made each time",
      "Habits become more enjoyable with repetition"],
     2,
     "A settled habit costs almost no deliberation. Starting one means paying "
     "the full decision every single time, which is the expensive part.",
     "simplified"),
    ("Habits & Consistency",
     "What does a streak actually measure?",
     ["How fit you have become",
      "How much effort you have put in",
      "How many days you turned up",
      "How well the habit suits you"],
     2,
     "It counts turning up, and nothing else. That is a real thing worth counting, "
     "and it is worth being clear it is not a measure of fitness.",
     "editorial"),
    ("Habits & Consistency",
     "Why do people often overestimate what they will do next week?",
     ["Next week genuinely has more time in it",
      "A future week is imagined without interruptions",
      "Planning makes people more optimistic generally",
      "People forget how long activities take"],
     1,
     "A week you have not lived yet has no dentist appointment or late meeting in "
     "it. Plan for the week you will actually get.",
     "simplified"),

    # ── Sleep & Recovery ────────────────────────────────────────────────────
    ("Sleep & Recovery",
     "When does the body do most of its repair work after exercise?",
     ["During the session itself",
      "In the hour immediately afterward",
      "Largely while you are asleep",
      "Evenly spread across the following week"],
     2,
     "Sleep is when a lot of the rebuilding happens. It is why a training plan "
     "with no sleep in it is only half a plan.",
     "simplified"),
    ("Sleep & Recovery",
     "Why is a rest day part of training rather than a break from it?",
     ["It lets you train harder the following day",
      "The adaptation happens during the rest",
      "It prevents boredom with the routine",
      "It gives the mind time to recover too"],
     1,
     "Training is the signal; the change happens while you recover from it. "
     "Skip the recovery and you keep sending signals nothing acts on.",
     "simplified"),
    ("Sleep & Recovery",
     "What is the effect of a late, hard workout on sleep?",
     ["It reliably makes people sleep more deeply",
      "It has no effect for anybody",
      "It can delay sleep for some people",
      "It always makes falling asleep harder"],
     2,
     "It varies a lot between people. Some sleep fine; others need a couple of "
     "hours to wind down. Worth finding out which one you are.",
     "contested"),
    ("Sleep & Recovery",
     "What does 'active recovery' mean?",
     ["Stretching thoroughly after a hard session",
      "Resting completely but staying on schedule",
      "Alternating hard days with medium days",
      "Gentle movement on a day between hard efforts"],
     3,
     "An easy walk rather than the sofa. Enough to keep blood moving and stiffness "
     "down, not enough to need recovering from.",
     "simplified"),
    ("Sleep & Recovery",
     "Why does muscle soreness usually peak a day or two after, not immediately?",
     ["The muscle is still warm on the first day",
      "It follows the repair, not the effort",
      "Lactic acid takes that long to build up",
      "The nervous system masks pain for the first day"],
     1,
     "The ache tracks the small-scale damage-and-repair cycle, which takes a day "
     "or so to get going. Lactic acid is long gone by then.",
     "simplified"),
    ("Sleep & Recovery",
     "Is soreness a sign that a workout was effective?",
     ["Yes, soreness is the clearest measure of progress",
      "No, it mostly reflects doing something unfamiliar",
      "Yes, but only when it lasts more than a day",
      "No, soreness always indicates something went wrong"],
     1,
     "Soreness tracks novelty more than effectiveness. A good session you are "
     "used to may leave none at all.",
     "simplified"),
    ("Sleep & Recovery",
     "What happens to reaction time and judgment after a poor night's sleep?",
     ["Both are unaffected if you feel alert",
      "Only reaction time is affected",
      "Both decline, and people usually underrate it",
      "Both decline noticeably enough to compensate for"],
     2,
     "The awkward part is that the ability to judge your own impairment is one of "
     "the things that goes. Feeling fine is not strong evidence.",
     "established"),
    ("Sleep & Recovery",
     "Why is a consistent wake-up time often suggested over a consistent bedtime?",
     ["Waking is easier to control than falling asleep",
      "Bedtime has no effect on sleep quality",
      "Morning routines matter more than evening ones",
      "It allows for more flexibility during the week"],
     0,
     "You cannot decide to fall asleep, but you can decide to get up. Fixing the "
     "end you control tends to pull the other end into line.",
     "simplified"),
]

SOURCES = {
    "Can somebody in their eighties still build strength?": [
        "https://pubmed.ncbi.nlm.nih.gov/2360822/",
    ],
    "What is 'proprioception'?": [
        "https://www.ncbi.nlm.nih.gov/books/NBK556154/",
    ],
    "How long does it actually take to form a habit?": [
        "https://onlinelibrary.wiley.com/doi/10.1002/ejsp.674",
    ],
    "What is the effect of planning exactly when and where you will do something?": [
        "https://www.sciencedirect.com/science/article/abs/pii/S0065260106380021",
    ],
    "What happens to reaction time and judgment after a poor night's sleep?": [
        "https://pubmed.ncbi.nlm.nih.gov/12683469/",
    ],
}


def next_id_start() -> int:
    """Continue the SF-TRV run rather than reusing an id. Ids are stable and
    never reused, including after a deletion — see content/SCHEMA.md."""
    highest = 0
    for path in OUT.glob("*.jsonl"):
        # Skip this batch's own output, or every re-run shifts the ids forward
        # and abandons the previous block. Ids are never reused, so a generator
        # that is not idempotent quietly burns a hundred of them per run.
        if path.name.startswith(BATCH):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            match = re.match(r'.*"id":\s*"SF-TRV-(\d{6})"', line)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def rebalance(options: list[str], answer_index: int, target: int) -> tuple[list[str], int]:
    """Move the correct option to `target` without touching any wording.

    Written by hand, answer positions came out {0:4, 1:38, 2:39, 3:13} — one
    position correct 41% of the time, over the validator's 40% ceiling, and
    position 0 almost never right. A reader does not need to notice that
    consciously to start benefiting from it.

    Reordering four options changes nothing about what the question asks or how
    good the distractors are, so this is done mechanically and deterministically
    rather than by rewriting anything.
    """
    correct = options[answer_index]
    rest = [o for n, o in enumerate(options) if n != answer_index]
    out = rest[:target] + [correct] + rest[target:]
    return out, target


def build() -> list[dict]:
    start = next_id_start()
    items = []
    for offset, entry in enumerate(TRIVIA):
        category, question, options, answer_index, explanation, confidence = entry[:6]
        options, answer_index = rebalance(list(options), answer_index, offset % 4)
        items.append({
            "id": f"SF-TRV-{start + offset:06d}",
            "type": "trivia",
            "category": category,
            "question": question,
            "options": list(options),
            "answer_index": answer_index,
            "explanation": explanation,
            "min_age": 9,
            "confidence": confidence,
            "sources": SOURCES.get(question, []),
            "added": TODAY,
            "batch": BATCH,
            # Generated. Not reviewed, and deliberately not served. Moving this
            # to "accepted" is a review pass's job, not this script's.
            "stage": "generated",
        })
    return items


def main() -> int:
    items = build()
    path = OUT / f"{BATCH}-trivia.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    positions = {n: sum(1 for i in items if i["answer_index"] == n) for n in range(4)}
    longest = sum(1 for i in items
                  if len(i["options"][i["answer_index"]]) == max(len(o) for o in i["options"]))
    print(f"wrote {len(items)} trivia items to {path.relative_to(ROOT)}")
    print(f"  id range      : {items[0]['id']} .. {items[-1]['id']}")
    print(f"  answer spread : {positions}")
    print(f"  correct-is-longest: {longest}/{len(items)} ({longest/len(items):.0%}, "
          f"chance is 25%, the gate trips at 45%)")
    print("  stage         : generated — NOT served, NOT reviewed")

    # The corpus tells in validate.py only run over ACCEPTED items, which is
    # correct for a gate guarding what gets served — and it means a batch can
    # sit at `generated` for weeks with a tell nobody has measured. The first
    # draft of this batch had the correct option longest in 67% of questions
    # against a 25% chance rate: a reader could have scored well above guessing
    # without knowing a single answer. So the batch checks itself, here, and
    # fails rather than printing a number somebody has to notice.
    failures = []
    if max(positions.values()) / len(items) >= 0.40:
        failures.append(f"one answer position is correct too often: {positions}")
    if longest / len(items) >= 0.45:
        failures.append(f"correct option is the longest in {longest/len(items):.0%} "
                        "of questions — trim it, or make a distractor properly "
                        "specific; a vague short distractor is a weak one anyway")
    for i in items:
        correct = i["options"][i["answer_index"]]
        gap = len(correct) - max(len(o) for n, o in enumerate(i["options"])
                                 if n != i["answer_index"])
        if gap > 12:
            failures.append(f"{i['id']}: correct option stands out by {gap} characters")
    if failures:
        print("\nBATCH GATE FAILED — the file was written, but it is not fit to review:")
        for f in failures:
            print(f"  ERROR  {f}")
        return 1
    print("  tells         : within thresholds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
