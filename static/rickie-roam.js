/* Rickie, roaming.
 *
 * He is a raccoon who lives in the app rather than a sticker stuck to it. He
 * walks, sits, stretches, yawns, naps, turns up an acorn, peeks over the edge
 * of things, and — often — does absolutely nothing, because resting is part of
 * the character and a mascot that never stops moving is a mascot you turn off.
 *
 * THREE RULES THAT SHAPED EVERY DECISION HERE
 *
 * 1. He must never obstruct anything. Not exercise instructions, not safety
 *    text, not a quiz option, not an input, not a button. This is enforced
 *    structurally rather than by being careful: the layer is `position: fixed`
 *    so it can never cause layout shift, `pointer-events: none` so a tap always
 *    reaches what is underneath, and he is confined to a band of empty space
 *    above the navigation that no content occupies. Confining him is a real
 *    constraint and it is the honest trade — at 390px wide there are no margins
 *    to roam in, and "he wanders anywhere and we check he misses things" is a
 *    promise that breaks the first time somebody adds a card.
 *
 * 2. He must not be a loop. Behaviour is chosen by weight, with the last few
 *    excluded, and the gap between actions is drawn from a range rather than
 *    set to a constant. Watch him for five minutes and you should not be able
 *    to predict the next thing.
 *
 * 3. He must be cheap. No animation frame runs while he is idle — and he is
 *    idle most of the time. No timers fire while the tab is hidden. A walk is
 *    a CSS transform transition the compositor handles, not a per-frame
 *    JavaScript position update.
 */
(function () {
    'use strict';

    var POSES = {
        neutral: '/static/rickie.svg',
        walk_a: '/static/rickie_walk_a.svg',
        walk_b: '/static/rickie_walk_b.svg',
        hop: '/static/rickie_hop.svg',
        sit: '/static/rickie_sit.svg',
        stretch: '/static/rickie_stretch.svg',
        yawn: '/static/rickie_yawn.svg',
        peek: '/static/rickie_peek.svg',
        acorn: '/static/rickie_acorn.svg',
        rest: '/static/rickie_rest.svg',
        cheer: '/static/rickie_cheer.svg',
        happy: '/static/rickie_happy.svg',
        curious: '/static/rickie_curious.svg',
        proud: '/static/rickie_proud.svg'
    };

    /* The behaviour library.
     *
     * `weight` is relative, not a percentage — `doze` at 14 against `potter` at
     * 18 means he rests nearly as often as he wanders, which is the intended
     * character. `rare` entries sit at 1 and turn up perhaps once a session;
     * they are deliberately NOT rewards, carry no XP, announce nothing, and
     * cannot be sought out, because a rare animation somebody can farm is a
     * reason to keep a child staring at an app.
     */
    var BEHAVIOURS = [
        { id: 'potter',   weight: 18, run: potter },
        { id: 'sit',      weight: 16, run: poseFor('sit', 4200, 9000) },
        { id: 'doze',     weight: 14, run: poseFor('rest', 6000, 14000) },
        { id: 'stretch',  weight: 10, run: poseFor('stretch', 1400, 2200) },
        { id: 'yawn',     weight: 10, run: poseFor('yawn', 1500, 2400) },
        { id: 'watch',    weight: 10, run: poseFor('curious', 2500, 5000) },
        { id: 'acorn',    weight:  8, run: poseFor('acorn', 3000, 6000) },
        { id: 'scamper',  weight:  7, run: scamper },
        { id: 'peek',     weight:  6, run: peek },
        { id: 'hopabout', weight:  5, run: hopAbout },
        /* Rare. */
        { id: 'tumble',   weight:  1, run: tumble },
        { id: 'acornjuggle', weight: 1, run: acornJuggle }
    ];

    var REACTIONS = {
        exercise_done: ['cheer_small', 'hop_cheer', 'proud_beat'],
        mission_done: ['cheer_big', 'hop_cheer', 'cheer_small'],
        answered_right: ['proud_beat', 'cheer_small'],
        answered_wrong: ['curious_beat']
    };

    var el = null, img = null;
    var state = {
        x: 0.18,            /* 0..1 across the viewport */
        y: 0.86,            /* 0..1 down the viewport */
        facing: 1,          /* 1 right, -1 left */
        busy: false,
        paused: false,
        suspended: false,
        recent: [],         /* short-term repetition avoidance */
        timer: null,
        walkTimer: null,
        walking: false,     /* mid-walk: do not reposition under him */
        reactionGeneration: 0,  /* only the newest reaction may end the reaction */
        pose: 'neutral'
    };

    function reducedMotion() {
        try {
            return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        } catch (e) { return false; }
    }

    function stored(key, fallback) {
        try { var v = localStorage.getItem(key); return v === null ? fallback : v; }
        catch (e) { return fallback; }
    }

    function rand(lo, hi) { return lo + Math.random() * (hi - lo); }

    /* Staying out of the way.
     *
     * The band is fixed, so mid-scroll the page runs underneath it and he can
     * end up drawn over an exercise row or the "I did this" button. Reserving
     * space at the foot of the document only solves the bottom of the page.
     *
     * So he dodges. Before moving he works out which parts of the band are
     * clear of anything that matters, and only goes there. When the whole band
     * is busy — common on a full mission card — he leaves, by slipping off an
     * edge rather than standing on top of what somebody is reading.
     */
    /* What counts as "in the way".
     *
     * Interactive things and things with words in them — NOT whole cards. A
     * card's rect runs the full width of the screen including its padding, and
     * treating that as occupied left literally zero clear positions on a 390px
     * phone with a mission card open, which would have meant a roaming
     * character who spends his whole life off-screen. Standing over 16px of
     * card padding obstructs nothing; standing over the "I did this" button
     * obstructs everything.
     */
    /* Controls, always. Kept as an explicit list because some of these are
     * empty of text (an icon button, a bare input) and would otherwise look
     * like free space. */
    var CONTROLS = 'button, a, input, select, textarea, img, svg, ' +
                   '.bb-option-btn, .daily-exercise-thumb-btn, .pane-nav';

    /* Everything he must never stand on is EVERY VISIBLE PIECE OF TEXT, found
     * by walking the DOM — not a list of class names.
     *
     * It was a list of class names, and an independent review of the running
     * app found him parked over the mission counter, the date subtitle, the
     * "Exercise Tips" button label and the Brain Boost question, in four of six
     * phone screenshots. The automated check PASSED throughout, because it
     * measured the same hand-written list the placement engine did: both knew
     * about `.daily-exercise-name` and neither knew about the counter.
     *
     * A list of "text that matters" is a denylist, and a denylist of page
     * content is wrong by construction: every element added to the app is
     * implicitly declared safe to stand on until somebody remembers to add it.
     * The conservative default has to be the other way round — text is
     * occupied, and free space is what is left.
     *
     * Leaf-ish only: an element is counted when it holds a non-empty text node
     * of its own, so a wrapping <div> around a paragraph does not blank out the
     * whole card and leave him nowhere to go. */
    function textBearing(node) {
        for (var i = 0; i < node.childNodes.length; i++) {
            var c = node.childNodes[i];
            if (c.nodeType === 3 && c.nodeValue && c.nodeValue.trim()) return true;
        }
        return false;
    }

    /* `offsetParent` is null for POSITION:FIXED elements, so using it as the
     * "is this on screen" test made every fixed thing invisible to the
     * occupancy check — the celebration toast (.rickie-reaction), the modal
     * overlays, the confetti layer. He could stand on his own toast, and an
     * independent walkthrough saw him drawn over an open modal.
     *
     * The rect is checked first because it is cheap and already needed, and it
     * is zero for display:none. getComputedStyle is only reached for the small
     * set of elements that have a real rect but no offsetParent, which is
     * essentially the fixed ones.
     */
    function isRendered(node) {
        var r = node.getBoundingClientRect();
        if (!r.width || !r.height) return false;
        if (node.offsetParent) return true;
        try {
            return getComputedStyle(node).position === 'fixed';
        } catch (e) { return false; }
    }

    function occupiedRects() {
        var s = stage();
        var rects = [];
        var band = document.getElementById('rickie-roam-band');
        var seen = [];
        var nodes = document.querySelectorAll(CONTROLS);
        for (var i = 0; i < nodes.length; i++) seen.push(nodes[i]);
        var all = document.body.querySelectorAll('*');
        for (var j = 0; j < all.length; j++) {
            if (textBearing(all[j])) seen.push(all[j]);
        }
        for (var k = 0; k < seen.length; k++) {
            var node = seen[k];
            if (!isRendered(node)) continue;
            /* He is not an obstruction to himself. */
            if (band && (node === band || band.contains(node))) continue;
            var r = node.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            if (r.bottom < 0 || r.top > s.height || r.right < 0 || r.left > s.width) continue;
            /* A little breathing room, so he stands beside a button rather
             * than brushing it. */
            /* 6px, not 10: with text counted, 10px of margin on every block
             * halved the gaps he could reach (8 clear positions vs 15). Still
             * enough that he stands beside a thing rather than brushing it. */
            rects.push({ left: r.left - 6, right: r.right + 6,
                         top: r.top - 6, bottom: r.bottom + 6 });
        }
        return rects;
    }

    /* Keep the whole raccoon on the screen.
     *
     * The grid's first column sits a few pixels from the left edge, and with
     * free positions now scarce he landed there repeatedly — a walkthrough
     * found him in that one spot for 11 of 23 samples with half his body cut
     * off by the edge. Half a mascot reads as a rendering bug, not a character.
     */
    var EDGE_MARGIN = 8;

    function clampToScreen(left, width) {
        return Math.max(EDGE_MARGIN, Math.min(left, width - SIZE - EDGE_MARGIN));
    }

    function isClear(x, y, rects) {
        var s = stage();
        var left = clampToScreen(x * Math.max(1, s.width - SIZE), s.width);
        var top = y * Math.max(1, s.height - SIZE);
        var right = left + SIZE, bottom = top + SIZE;
        for (var i = 0; i < rects.length; i++) {
            var r = rects[i];
            if (!(right < r.left || left > r.right || bottom < r.top || top > r.bottom)) {
                return false;
            }
        }
        return true;
    }

    /* Where he could stand without covering anything that matters.
     *
     * A grid over the whole viewport, not a strip along the bottom. A strip was
     * the first design and it does not survive contact with a phone: at 390px
     * a mission card runs edge to edge — thumbnail, name, button — and there is
     * no horizontal line across the bottom of the screen that is reliably
     * clear. Measured: zero free positions out of twenty-one. A character
     * confined to a strip would have spent his entire life hidden off-screen.
     *
     * Lower spots are preferred, so he reads as being on the ground rather
     * than floating, but he will go wherever is actually free.
     */
    function freeSpots() {
        var rects = occupiedRects();
        var spots = [];
        /* A FINE grid — 17 x 21, not 9 x 11.
         *
         * Once text counts as occupied, the gaps he can stand in are the
         * spaces between blocks, and they are smaller than the old grid's
         * step. Measured on a real phone-width mission card: the coarse grid
         * found 0 clear positions of 99 and reported that he had nowhere to
         * go, while a fine grid over the SAME occupancy found 8 of 357. The
         * space was always there; the grid was stepping over it.
         *
         * It is pure arithmetic over a list of rectangles, run when he decides
         * to move and on scroll — not per frame — so the extra points cost
         * nothing worth measuring.
         */
        for (var row = 0; row <= 16; row++) {
            var y = 0.04 + (row / 16) * 0.90;
            for (var col = 0; col <= 20; col++) {
                var x = 0.02 + (col / 20) * 0.94;
                if (isClear(x, y, rects)) spots.push({ x: x, y: y, weight: 1 + y * 3 });
            }
        }
        return spots;
    }

    function somewhereClear() {
        var spots = freeSpots();
        if (!spots.length) return null;
        var total = spots.reduce(function (sum, s) { return sum + s.weight; }, 0);
        var roll = Math.random() * total;
        for (var i = 0; i < spots.length; i++) {
            roll -= spots[i].weight;
            if (roll <= 0) return spots[i];
        }
        return spots[spots.length - 1];
    }

    function setPose(name) {
        if (!img || state.pose === name) return;
        state.pose = name;
        img.src = POSES[name] || POSES.neutral;
    }

    var SIZE = 56;

    function stage() {
        return el.parentNode.getBoundingClientRect();
    }

    function place() {
        if (!el) return;
        var s = stage();
        /* Same clamp as isClear uses, or the collision test and the render
         * would disagree about where he is. */
        var left = clampToScreen(state.x * Math.max(0, s.width - SIZE), s.width);
        var top = Math.max(0, Math.min(state.y * Math.max(0, s.height - SIZE),
                                       s.height - SIZE));
        el.style.transform = 'translate(' + Math.round(left) + 'px,' + Math.round(top) + 'px)' +
                             ' scaleX(' + state.facing + ')';
    }

    /* ── Behaviours ─────────────────────────────────────────────────────── */

    function poseFor(name, lo, hi) {
        return function (done) {
            setPose(name);
            setTimeout(function () { setPose('neutral'); done(); }, rand(lo, hi));
        };
    }

    function walkTo(target, speedPxPerSec, onArrive) {
        var s = stage();
        var dx = (target.x - state.x) * Math.max(1, s.width - SIZE);
        var dy = (target.y - state.y) * Math.max(1, s.height - SIZE);
        var distance = Math.sqrt(dx * dx + dy * dy);
        var ms = Math.max(300, (distance / speedPxPerSec) * 1000);
        state.facing = target.x > state.x ? 1 : -1;
        state.x = target.x;
        state.y = target.y;

        /* The step cycle is two frames swapped on a timer — the movement itself
         * is one CSS transition, so the compositor does the work and no
         * animation frame runs in JavaScript. */
        var frame = 0;
        clearInterval(state.walkTimer);
        state.walking = true;
        state.walkTimer = setInterval(function () {
            frame = 1 - frame;
            setPose(frame ? 'walk_a' : 'walk_b');
        }, 190);

        el.style.transition = 'transform ' + Math.round(ms) + 'ms linear';
        place();
        setTimeout(function () {
            clearInterval(state.walkTimer);
            state.walking = false;
            el.style.transition = '';
            setPose('neutral');
            if (onArrive) onArrive();
        }, ms + 40);
    }

    function potter(done) {
        var target = somewhereClear();
        if (target === null) return leaveTheScreen(done);
        walkTo(target, rand(26, 42), function () {
            /* Having arrived somewhere, he usually settles rather than
             * immediately setting off again. */
            if (Math.random() < 0.55) { poseFor('sit', 2500, 6000)(done); }
            else { done(); }
        });
    }

    function scamper(done) {
        var target = somewhereClear();
        if (target === null) return leaveTheScreen(done);
        walkTo(target, rand(80, 130), function () {
            setPose('curious');
            setTimeout(function () { setPose('neutral'); done(); }, rand(900, 1800));
        });
    }

    function hopAbout(done) {
        var hops = Math.round(rand(2, 4));
        (function next() {
            if (!hops--) { setPose('neutral'); return done(); }
            setPose('hop');
            el.style.transition = 'transform 260ms ease-out';
            var next = Math.min(0.92, Math.max(0.03, state.x + state.facing * rand(0.05, 0.13)));
            if (isClear(next, state.y, occupiedRects())) state.x = next;
            else state.facing = -state.facing;
            place();
            setTimeout(function () {
                setPose('neutral');
                setTimeout(next, rand(160, 320));
            }, 260);
        })();
    }

    function leaveTheScreen(done) {
        var edge = { x: state.x < 0.5 ? 0.0 : 1.0, y: state.y };
        walkTo(edge, 90, function () {
            el.classList.add('rickie-roam-hidden');
            setTimeout(function () {
                var spot = somewhereClear();
                if (spot) {
                    state.x = spot.x;
                    state.y = spot.y;
                    el.style.transition = '';
                    place();
                    el.classList.remove('rickie-roam-hidden');
                }
                done();
            }, rand(3000, 7000));
        });
    }

    function peek(done) {
        /* Slip to an edge, drop out of sight, then look back over. */
        var edge = { x: Math.random() < 0.5 ? 0.0 : 1.0, y: state.y };
        walkTo(edge, rand(60, 90), function () {
            el.classList.add('rickie-roam-hidden');
            setTimeout(function () {
                setPose('peek');
                el.classList.remove('rickie-roam-hidden');
                el.classList.add('rickie-roam-peeking');
                setTimeout(function () {
                    el.classList.remove('rickie-roam-peeking');
                    setPose('neutral');
                    done();
                }, rand(1800, 3200));
            }, rand(500, 1100));
        });
    }

    function tumble(done) {
        /* Rare: trips, sits down heavily, looks around to check nobody saw. */
        var target = somewhereClear();
        if (target === null) return leaveTheScreen(done);
        walkTo(target, 110, function () {
            setPose('hop');
            el.style.transition = 'transform 300ms ease-in';
            el.style.transform += ' rotate(14deg)';
            setTimeout(function () {
                el.style.transition = '';
                place();
                setPose('sit');
                setTimeout(function () {
                    setPose('curious');
                    setTimeout(function () { setPose('neutral'); done(); }, 1400);
                }, 1200);
            }, 320);
        });
    }

    function acornJuggle(done) {
        /* Rare: finds an acorn, hops with it, loses interest. */
        setPose('acorn');
        setTimeout(function () {
            setPose('hop');
            setTimeout(function () {
                setPose('acorn');
                setTimeout(function () { setPose('neutral'); done(); }, 1800);
            }, 400);
        }, 1800);
    }

    /* ── Reactions ──────────────────────────────────────────────────────── */

    var REACTION_RUNNERS = {
        cheer_small: poseFor('happy', 1600, 2200),
        cheer_big: function (done) {
            setPose('cheer');
            setTimeout(function () { setPose('happy'); }, 1400);
            setTimeout(function () { setPose('neutral'); done(); }, 3000);
        },
        hop_cheer: function (done) {
            setPose('cheer');
            setTimeout(function () { hopAbout(done); }, 700);
        },
        proud_beat: poseFor('proud', 1800, 2600),
        curious_beat: poseFor('curious', 1500, 2200)
    };

    /* ── Choosing what to do next ───────────────────────────────────────── */

    function pick(list, recentIds) {
        var pool = list.filter(function (b) { return recentIds.indexOf(b.id) === -1; });
        if (!pool.length) pool = list;
        var total = pool.reduce(function (sum, b) { return sum + b.weight; }, 0);
        var roll = Math.random() * total;
        for (var i = 0; i < pool.length; i++) {
            roll -= pool[i].weight;
            if (roll <= 0) return pool[i];
        }
        return pool[pool.length - 1];
    }

    function remember(id) {
        state.recent.push(id);
        /* Three deep: long enough that he does not repeat himself, short enough
         * that a favourite behaviour can still come round again. */
        while (state.recent.length > 3) state.recent.shift();
    }

    function idleDelay() {
        /* Wide and skewed long. A constant interval is the thing that makes a
         * character read as a screensaver. */
        return Math.random() < 0.25 ? rand(3000, 8000) : rand(9000, 34000);
    }

    function schedule(ms) {
        clearTimeout(state.timer);
        state.timer = setTimeout(step, ms === undefined ? idleDelay() : ms);
    }

    function step() {
        if (state.paused || state.suspended || state.busy || document.hidden) {
            return schedule(6000);
        }
        var behaviour = pick(BEHAVIOURS, state.recent);
        remember(behaviour.id);
        state.busy = true;
        behaviour.run(function () { state.busy = false; schedule(); });
    }

    /* ── When he must stop ──────────────────────────────────────────────── */
    //
    // Anything that asks for the reader's attention suspends him: an open
    // exercise modal, the coach panel, a revealed Brain Boost, a focused text
    // input. He does not vanish — he sits down where he is, which is both
    // cheaper and more in character than disappearing.

    var ATTENTION_SELECTORS = [
        '.exercise-modal:not([hidden])',
        '.coach-panel:not([hidden])',
        '.photo-composer:not([hidden])',
        '.bb-options',
        /* Settings. An independent walkthrough caught him standing on the
         * Rickie-mode explanation while somebody was reading it to decide how
         * much of him they wanted — which is funny once and annoying after. */
        '#settings-menu:not([hidden])'
    ];

    function attentionWanted() {
        for (var i = 0; i < ATTENTION_SELECTORS.length; i++) {
            var node = document.querySelector(ATTENTION_SELECTORS[i]);
            if (node && node.offsetParent) return true;
        }
        var active = document.activeElement;
        if (active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) return true;
        return false;
    }

    function updateSuspension() {
        var want = attentionWanted();
        if (want === state.suspended) return;
        state.suspended = want;
        if (want) {
            clearInterval(state.walkTimer);
            el.style.transition = '';
            setPose('sit');
        } else if (!state.busy) {
            setPose('neutral');
            schedule(rand(1200, 4000));
        }
    }

    /* ── Public surface ─────────────────────────────────────────────────── */

    function setPaused(paused) {
        state.paused = !!paused;
        try { localStorage.setItem('rickie_roam_paused', paused ? '1' : '0'); } catch (e) {}
        if (el) el.classList.toggle('rickie-roam-still', state.paused);
        if (paused) {
            clearTimeout(state.timer);
            clearInterval(state.walkTimer);
            el.style.transition = '';
            setPose('sit');
        } else {
            setPose('neutral');
            schedule(1200);
        }
        var btn = document.getElementById('rickie-roam-toggle');
        if (btn) {
            btn.textContent = state.paused ? 'Let Rickie roam' : 'Ask Rickie to settle';
            btn.setAttribute('aria-pressed', String(state.paused));
        }
    }

    function react(event) {
        if (!el || state.paused) return;
        var options = REACTIONS[event];
        if (!options) return;
        /* A reaction always interrupts pottering — being congratulated is the
         * one moment he should not be asleep for. */
        clearTimeout(state.timer);
        clearInterval(state.walkTimer);
        el.style.transition = '';
        state.busy = true;

        /* Two reactions can land on the same moment: answering a Brain Boost
         * question fires the shared showRickieReaction path (exercise_done)
         * AND an answered_right/answered_wrong of its own, one line apart.
         * Without a generation token the FIRST runner's callback still fires
         * partway through the second, sets busy=false and calls schedule() —
         * so a behaviour starts mid-reaction and the second callback then
         * schedules a rival timer. The result is two concurrent walk loops
         * after every answer, compounding each time.
         *
         * The token means only the newest reaction can end the reaction state.
         * A superseded runner is left to finish its animation harmlessly. */
        var generation = ++state.reactionGeneration;
        var id = options[Math.floor(Math.random() * options.length)];
        (REACTION_RUNNERS[id] || REACTION_RUNNERS.cheer_small)(function () {
            if (generation !== state.reactionGeneration) return;
            state.busy = false;
            schedule();
        });
    }

    function mount() {
        if (el || document.getElementById('rickie-roam-band')) return;
        var band = document.createElement('div');
        band.id = 'rickie-roam-band';
        band.className = 'rickie-roam-band';
        /* Decorative. He is never the only way to reach anything, so a screen
         * reader has nothing to gain from him and plenty to lose. */
        band.setAttribute('aria-hidden', 'true');

        el = document.createElement('div');
        el.className = 'rickie-roam';
        img = document.createElement('img');
        img.className = 'rickie-roam-img';
        img.src = POSES.neutral;
        img.alt = '';
        el.appendChild(img);
        band.appendChild(el);
        document.body.appendChild(band);

        place();
        window.addEventListener('resize', place);

        /* Step aside when the page changes under him.
         *
         * This used to run on scroll ONLY, so placement was correct at the
         * moment he moved and stale for as long as he stood still — and he
         * stands still most of the time. Anything that rendered beneath him
         * without a scroll left him sitting on it: a Brain Boost revealing its
         * options, a card finishing its load, a pane switch, a toast. An
         * independent walkthrough found him parked on an exercise illustration
         * and on the acorns explanation, both reached without scrolling.
         *
         * Now the same check runs whenever the DOM changes. It is event-driven
         * rather than a timer, so the "nothing runs while he is idle" property
         * survives: if the page is not changing, this never fires.
         */
        var asideCheck = null;

        function stepAsideIfCovered() {
            clearTimeout(asideCheck);
            asideCheck = setTimeout(function () {
                /* NOT gated on state.busy.
                 *
                 * `busy` is true for the whole of any behaviour — sitting,
                 * dozing, watching — which is most of the time. Gating on it
                 * made this check, and the scroll check it replaced, inert
                 * almost always: a diagnostic found him standing on occupied
                 * space with busy=true and the handler declining to act.
                 * Being mid-doze is not a reason to keep sitting on somebody's
                 * text.
                 *
                 * Mid-WALK is different: he is already travelling to a spot
                 * that was clear when chosen, and moving him now would fight
                 * the transition. */
                if (!el || state.paused || state.suspended || state.walking) return;
                if (isClear(state.x, state.y, occupiedRects())) return;
                var spot = somewhereClear();
                if (!spot) return;
                state.x = spot.x;
                state.y = spot.y;
                el.style.transition = 'transform 420ms ease-in-out';
                place();
            }, 250);
        }

        window.addEventListener('scroll', stepAsideIfCovered, { passive: true });

        if (window.MutationObserver) {
            var observer = new MutationObserver(stepAsideIfCovered);
            observer.observe(document.body, {
                childList: true,
                subtree: true,
                /* `hidden` and `class` are how this app shows and hides panes
                 * and cards, so they change what occupies the screen. Other
                 * attributes do not and would only add noise. */
                attributes: true,
                attributeFilter: ['hidden', 'class']
            });
        }
        document.addEventListener('visibilitychange', function () {
            /* Nothing runs while the tab is hidden. */
            if (document.hidden) { clearTimeout(state.timer); clearInterval(state.walkTimer); }
            else if (!state.paused) schedule(rand(2000, 6000));
        });
        document.addEventListener('focusin', updateSuspension);
        document.addEventListener('focusout', function () { setTimeout(updateSuspension, 50); });
        setInterval(updateSuspension, 900);

        if (reducedMotion()) {
            /* He still exists and still reacts; he simply does not travel. */
            state.paused = true;
            setPose('sit');
            return;
        }
        setPaused(stored('rickie_roam_paused', '0') === '1');
    }

    window.RickieRoam = {
        mount: mount,
        _freeSpots: freeSpots,
        _isClear: isClear,
        _occupiedRects: occupiedRects,
        _somewhereClear: somewhereClear,
        _place: place,
        react: react,
        setPaused: setPaused,
        isPaused: function () { return state.paused; },
        /* Exposed for the browser checks: which behaviours ran, in order. */
        _state: state,
        _behaviours: BEHAVIOURS,
        _pick: pick,
        _step: step
    };
})();
