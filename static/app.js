// ── Service Worker ────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(function () {
        // Registration failed — app still works without PWA features
    });
}

// ── PWA Install Prompt ────────────────────────────────────────────────────────
var _deferredInstallPrompt = null;

window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    _deferredInstallPrompt = e;
    // The install card's click handler reads _deferredInstallPrompt fresh each
    // time, so a late-firing event is already picked up automatically. Still
    // safe to call in case the card hasn't initialized yet on this load.
    updateInstallCard();
});

function _isStandalone() {
    return window.matchMedia('(display-mode: standalone)').matches ||
           window.navigator.standalone === true;
}

function _isIOSSafari() {
    var ua = navigator.userAgent;
    return /iphone|ipad|ipod/i.test(ua) &&
           /safari/i.test(ua) &&
           !/chrome|crios|fxios|edgios/i.test(ua);
}

function _isAndroidChrome() {
    var ua = navigator.userAgent;
    return /android/i.test(ua) && /chrome/i.test(ua) &&
           !/edg|opr|samsungbrowser|firefox/i.test(ua);
}

// ── Retention prompts ─────────────────────────────────────────────────────────
// Called once per dashboard session. Refreshes the install card and shows the
// notification ask. Never shown to guests. The notification ask stores a
// localStorage flag after being acted on so it never appears again; the
// install card has no such flag — it should reflect current install state
// every time the dashboard loads.
//
// The notification ask is withheld until the user has completed at least one
// mission ever — a brand-new user should see the app deliver value before
// being asked for a permission, not get a permission prompt as the first
// thing they see on their very first dashboard load.

function _getOrCreatePromptsContainer() {
    var el = document.getElementById('retention-prompts');
    if (!el) {
        el = document.createElement('div');
        el.id = 'retention-prompts';
        var main = document.querySelector('#dashboard-view main');
        if (main) main.insertBefore(el, main.firstChild);
    }
    return el;
}

function checkRetentionPrompts() {
    if (isGuest) return;
    updateInstallCard();
    if (!currentUser || !currentUser.total_missions) return;
    _maybeShowNotificationBanner();
}

// ── Install card ──────────────────────────────────────────────────────────────
// Persistent (not dismissible) card on the dashboard, below Progress. Always
// shows the same app-level CTA — no browser-specific text up front. Only on
// click, if there's no real install prompt to trigger, do we expand
// platform-specific instructions (and only then does Chrome get named, and
// only for Android Chrome specifically).

function updateInstallCard() {
    var card = document.getElementById('install-card');
    if (!card) return;
    var btn = document.getElementById('install-card-btn');
    var instructions = document.getElementById('install-card-instructions');

    if (isGuest || _isStandalone()) {
        card.hidden = true;
        return;
    }

    card.hidden = false;
    btn.disabled = false;
    instructions.hidden = true;
    instructions.textContent = '';

    btn.onclick = function () {
        var captured = _deferredInstallPrompt;
        if (captured) {
            btn.disabled = true;
            fireEvent('install_prompt_accepted');
            _deferredInstallPrompt = null; // browsers only allow one call
            captured.prompt();
            captured.userChoice.then(function (choice) {
                if (choice.outcome === 'accepted') {
                    card.hidden = true;
                } else {
                    fireEvent('install_prompt_dismissed');
                    btn.disabled = false;
                    _showInstallInstructions(instructions); // prompt consumed — explain manually
                }
            }).catch(function () {
                btn.disabled = false;
            });
        } else {
            _showInstallInstructions(instructions);
        }
    };

    fireEvent('install_prompt_shown');
}

function _showInstallInstructions(instructions) {
    if (_isIOSSafari()) {
        instructions.textContent = 'Tap Share, then Add to Home Screen.';
    } else if (_isAndroidChrome()) {
        instructions.textContent = 'Install from Chrome menu: ⋮ → Install app or Add to Home screen.';
    } else {
        instructions.textContent = "Use your browser's install option or create shortcut option.";
    }
    instructions.hidden = false;
}

// ── Notification ask banner ───────────────────────────────────────────────────

function _maybeShowNotificationBanner() {
    if (!('Notification' in window) || !('serviceWorker' in navigator)) return;
    if (localStorage.getItem('sf_notif_asked')) return;
    if (Notification.permission !== 'default') {
        // Already granted or denied — mark as asked so we skip the banner
        localStorage.setItem('sf_notif_asked', '1');
        return;
    }

    var container = _getOrCreatePromptsContainer();
    var banner = document.createElement('div');
    banner.className = 'retention-banner';
    banner.id = 'notif-banner';

    var text = document.createElement('p');
    text.className = 'retention-text';
    text.textContent = 'Would you like a daily reminder for your mission?';

    var btnRow = document.createElement('div');
    btnRow.className = 'retention-btn-row';

    var yesBtn = document.createElement('button');
    yesBtn.className = 'retention-btn';
    yesBtn.textContent = 'Yes';
    yesBtn.onclick = function () {
        localStorage.setItem('sf_notif_asked', '1');
        banner.remove();
        Notification.requestPermission().then(function (perm) {
            if (perm === 'granted') {
                fireEvent('notification_permission_granted');
            } else {
                fireEvent('notification_permission_denied');
            }
        });
    };

    var notNowBtn = document.createElement('button');
    notNowBtn.className = 'retention-btn retention-btn-ghost';
    notNowBtn.textContent = 'Not now';
    notNowBtn.onclick = function () {
        localStorage.setItem('sf_notif_asked', '1');
        banner.remove();
    };

    btnRow.appendChild(yesBtn);
    btnRow.appendChild(notNowBtn);
    banner.appendChild(text);
    banner.appendChild(btnRow);
    container.appendChild(banner);
}

// ── Mission complete notification ─────────────────────────────────────────────

function _showMissionCompleteNotification() {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    var today = new Date().toDateString();
    if (localStorage.getItem('sf_complete_notif_date') === today) return;
    localStorage.setItem('sf_complete_notif_date', today);
    fireEvent('notification_sent_completion');

    navigator.serviceWorker.ready.then(function (reg) {
        reg.showNotification('🔥 Nice work!', {
            body:  "Today's mission is complete.",
            icon:  '/static/icons/icon.svg',
            tag:   'streakfit-complete',
            renotify: false,
        });
    }).catch(function () {});
}

// ── User state ────────────────────────────────────────────────────────────────
// Set by loadUserPreferences() on every dashboard load. Streak data lives here
// rather than in the /api/daily response.
var currentUser = null;

// ── Guest state ───────────────────────────────────────────────────────────────
// In-memory only — intentionally resets on page refresh.
var isGuest = false;
var guestCompleted = new Set();
var guestCompleteFired = false;

// Picked once per page load, reused across re-renders — see renderJourneyCard's
// caller and the Rickie intro line below.
var _cachedGreetingLine = null;

// ── Rickie's Journey ──────────────────────────────────────────────────────────
// Never guilt, never shame, never mention losing progress — only encouragement.
// Message pool lives in RICKIE_LINES (general + an occasional fun aside) so
// Rickie's Journey draws from the same voice as every other surface.
function _journeyMessageForToday() {
    var dayOfYear = Math.floor((Date.now() - new Date(new Date().getFullYear(), 0, 0)) / 86400000);
    // Deterministic per day (stable across re-renders, same as before) —
    // roughly one day in five pulls a lighter, funnier aside instead of
    // straight encouragement, so the card doesn't feel like it's reciting
    // the same seven lines on a loop. Quiet/minimal never get the fun pool.
    var pool = (_rickieAllowsFun() && dayOfYear % 5 === 0) ? RICKIE_LINES.fun : RICKIE_LINES.general;
    return pool[dayOfYear % pool.length];
}

function renderJourneyCard() {
    var journeyCard = document.getElementById('journey-card');
    if (!journeyCard || !currentUser) return;

    document.getElementById('journey-level').textContent = 'Level ' + (currentUser.level || 1);
    document.getElementById('journey-level-title').textContent = currentUser.level_title || 'Explorer';

    var xpIntoLevel = currentUser.xp_into_level || 0;
    var xpRequired  = currentUser.xp_required   || 100;
    var xpToNext    = currentUser.xp_to_next_level;
    if (xpToNext === undefined || xpToNext === null) xpToNext = xpRequired - xpIntoLevel;

    var pct = xpRequired > 0 ? Math.min(100, (xpIntoLevel / xpRequired) * 100) : 100;
    var fill = document.getElementById('journey-xp-fill');
    if (fill) fill.style.width = pct + '%';
    var track = document.querySelector('.journey-xp-track');
    if (track) track.setAttribute('aria-valuenow', Math.round(pct));

    document.getElementById('journey-xp-caption').textContent = xpToNext + ' XP to next level';

    document.getElementById('journey-acorns-value').textContent = currentUser.acorns_total || 0;
    var messageEl = document.getElementById('journey-message');
    if (_rickieMode() === 'minimal') {
        messageEl.hidden = true;
    } else {
        messageEl.hidden = false;
        messageEl.textContent = _journeyMessageForToday();
    }

    journeyCard.hidden = false;
}

// ── Teams (R2.2 Team List UI) ────────────────────────────────────────────────
// Read-only display of R2.1's team data. No create/join actions wired here —
// this sprint is "make existing team data visible," not "add new interactive
// capability." Team Rickie is UI-only throughout: no API call, no fake team
// id, no campfire, no membership row — it renders from data the dashboard
// already has (currentUser, currentRickieExpression).

var CAMPFIRE_STAGE_EMOJI = {
    'Kindling':    '✨',
    'Small Flame': '🔥',
    'Campfire':    '🔥',
    'Bonfire':     '🔥',
    'Beacon':      '🔥'
};

async function loadTeams() {
    var container = document.getElementById('teams-list');
    if (!container) return;

    if (isGuest) {
        renderTeamsSection({ guest: true });
        return;
    }

    renderTeamsSection({ loading: true });

    var result = await api('/api/teams');
    if (!result || result.status !== 200) {
        renderTeamsSection({ error: true });
        return;
    }

    renderTeamsSection({ teams: result.data });
}

function renderTeamsSection(state) {
    var container = document.getElementById('teams-list');
    if (!container) return;
    container.innerHTML = '';

    if (state.guest) {
        container.appendChild(_buildGuestTeamsPreview());
        return;
    }

    if (state.loading) {
        var loading = document.createElement('div');
        loading.className = 'state-loading';
        loading.innerHTML = '<div class="spinner"></div><p>Loading teams…</p>';
        container.appendChild(loading);
        return;
    }

    if (state.error) {
        var errWrap = document.createElement('div');
        errWrap.className = 'teams-error-state';
        var errMsg = document.createElement('p');
        errMsg.className = 'error';
        errMsg.textContent = "Couldn't load teams — try again in a moment.";
        var retryBtn = document.createElement('button');
        retryBtn.className = 'btn-primary teams-retry-btn';
        retryBtn.textContent = 'Retry';
        retryBtn.onclick = loadTeams;
        errWrap.appendChild(errMsg);
        errWrap.appendChild(retryBtn);
        container.appendChild(errWrap);
        return;
    }

    // Team Rickie always renders first — present whether or not any real
    // teams exist yet, same as day one.
    container.appendChild(_buildTeamRickieCard());

    var teams = state.teams || [];
    if (teams.length === 0) {
        container.appendChild(_buildEmptyTeamsCard());
    } else {
        teams.forEach(function (team) {
            container.appendChild(_buildTeamCard(team));
        });
    }
}

function _buildTeamRickieCard() {
    var card = document.createElement('div');
    card.className = 'team-card team-rickie-card';

    var header = document.createElement('div');
    header.className = 'team-card-header';

    var avatar = document.createElement('img');
    avatar.className = 'rickie-avatar-sm';
    avatar.src = RICKIE_EXPRESSION_SVG[currentRickieExpression] || RICKIE_EXPRESSION_SVG.neutral;
    avatar.alt = '';

    var titleWrap = document.createElement('div');
    var title = document.createElement('p');
    title.className = 'team-card-title';
    title.textContent = '🦝 Team Rickie';
    titleWrap.appendChild(title);

    if (_rickieMode() !== 'minimal') {
        var line = document.createElement('p');
        line.className = 'team-rickie-line';
        line.textContent = _pickRickieLine('general');
        titleWrap.appendChild(line);
    }

    header.appendChild(avatar);
    header.appendChild(titleWrap);
    card.appendChild(header);

    // Team Rickie has no Campfire — this shows the user's own Journey stats
    // in that visual slot instead, per TEAM_SYSTEM_BASELINE Section 1.
    var streak = (currentUser && currentUser.current_streak) || 0;
    var stats = document.createElement('p');
    stats.className = 'team-card-stats';
    stats.textContent = '🔥 ' + streak + (streak === 1 ? ' day' : ' days') + ' together';
    card.appendChild(stats);

    return card;
}

// Team Rickie's avatar/streak are derived from currentUser and
// currentRickieExpression, both of which change after every completion —
// re-rendered wherever renderJourneyCard() is (same freshness dependency),
// without re-fetching the real teams list, which nothing here changed.
function _refreshTeamRickieCard() {
    var existing = document.querySelector('.team-rickie-card');
    if (!existing) return;
    existing.replaceWith(_buildTeamRickieCard());
}

// R2.3 Campfire MVP: patches already-rendered team cards in place from the
// completion response's team_campfire_updates, so a mission completion shows
// up immediately without re-fetching the whole teams list. A page reload
// would show the same numbers anyway — GET /api/teams always computes them
// fresh — this is purely so the user doesn't have to reload to see it.
function _applyCampfireUpdates(updates) {
    if (!updates || !updates.length) return;
    updates.forEach(function (u) {
        var card = document.querySelector('.team-card[data-team-id="' + u.team_id + '"]');
        if (!card) return;
        var statsEl = card.querySelector('.team-card-stats');
        if (!statsEl) return;
        var stageEmoji = CAMPFIRE_STAGE_EMOJI[u.stage] || '✨';
        statsEl.textContent = stageEmoji + ' ' + u.stage + ' · ' +
            u.total_team_missions + (u.total_team_missions === 1 ? ' log' : ' logs');
    });
}

function _buildTeamCard(team) {
    var card = document.createElement('div');
    card.className = 'team-card';
    card.dataset.teamId = team.id;

    var header = document.createElement('div');
    header.className = 'team-card-header';

    var titleWrap = document.createElement('div');
    var title = document.createElement('p');
    title.className = 'team-card-title';
    title.textContent = team.name;
    titleWrap.appendChild(title);

    var meta = document.createElement('p');
    meta.className = 'team-card-meta';
    meta.textContent = team.member_count + (team.member_count === 1 ? ' member' : ' members');
    titleWrap.appendChild(meta);

    header.appendChild(titleWrap);
    card.appendChild(header);

    var stats = document.createElement('p');
    stats.className = 'team-card-stats';
    var stageEmoji = CAMPFIRE_STAGE_EMOJI[team.campfire_stage] || '✨';
    stats.textContent = stageEmoji + ' ' + team.campfire_stage + ' · ' +
        team.total_team_missions + (team.total_team_missions === 1 ? ' log' : ' logs');
    card.appendChild(stats);

    var openBtn = document.createElement('button');
    openBtn.className = 'team-card-open-btn';
    openBtn.textContent = 'Open';
    openBtn.disabled = true;
    openBtn.title = 'Coming soon';
    card.appendChild(openBtn);

    return card;
}

function _buildEmptyTeamsCard() {
    var card = document.createElement('div');
    card.className = 'team-card team-empty-card';

    var title = document.createElement('p');
    title.className = 'team-card-title';
    title.textContent = 'Create or join a team';
    card.appendChild(title);

    var sub = document.createElement('p');
    sub.className = 'team-card-meta';
    sub.textContent = (_rickieMode() === 'minimal')
        ? 'Build a shared Campfire with people you know.'
        : "Bring people along — a family, a few friends, whoever you want cheering you on.";
    card.appendChild(sub);

    var btnRow = document.createElement('div');
    btnRow.className = 'team-empty-btn-row';

    var createBtn = document.createElement('button');
    createBtn.className = 'team-card-open-btn';
    createBtn.textContent = 'Create a team';
    createBtn.disabled = true;
    createBtn.title = 'Coming soon';

    var joinBtn = document.createElement('button');
    joinBtn.className = 'team-card-open-btn';
    joinBtn.textContent = 'Join a team';
    joinBtn.disabled = true;
    joinBtn.title = 'Coming soon';

    btnRow.appendChild(createBtn);
    btnRow.appendChild(joinBtn);
    card.appendChild(btnRow);

    return card;
}

function _buildGuestTeamsPreview() {
    var card = document.createElement('div');
    card.className = 'team-card team-guest-card';

    var title = document.createElement('p');
    title.className = 'team-card-title';
    title.textContent = 'Sign up to build teams with Rickie.';
    card.appendChild(title);

    return card;
}

// ── Rickie's voice ──────────────────────────────────────────────────────────────
// Every line in here follows the same rules, everywhere Rickie speaks:
// he never guilts, nags, pressures, compares, or manipulates. He notices,
// celebrates, encourages, and keeps it short. This is the single library —
// every Rickie-voiced surface in the app (greeting, reactions, Journey,
// Rise Again, Memory Book) draws from it, so his voice stays consistent
// instead of each surface inventing its own tone.
var RICKIE_LINES = {
    morning: [
        "Morning! Rickie's already up.",
        "Good morning. Ready when you are.",
        "Rise and shine.",
        "A fresh morning, a fresh mission.",
        "Rickie had an acorn for breakfast.",
        "Morning light looks good on you."
    ],
    afternoon: [
        "Afternoon! Rickie's here.",
        "Halfway through the day — nice.",
        "Afternoon check-in: Rickie's around.",
        "Hope your day's going well so far.",
        "Rickie's taking a break too. Join him?",
        "Good afternoon. Whenever you're ready."
    ],
    evening: [
        "Evening! Rickie's winding down too.",
        "Ending the day on a good note.",
        "Evenings are for showing up too.",
        "Rickie likes evening visits best.",
        "Good evening. Rickie's here.",
        "The day's not over yet."
    ],
    missionComplete: [
        "Nice work. That one counts.",
        "We showed up today.",
        "That was a good step.",
        "Rickie saw that.",
        "A little stronger, one move at a time.",
        "That's how a journey gets built.",
        "You added something good today.",
        "Small wins still count.",
        "Rickie is proud of that one.",
        "That's one more for the books.",
        "Rickie noticed that.",
        "Nice. Onward."
    ],
    brainBoostCorrect: [
        "Good thinking.",
        "Rickie likes that answer.",
        "That brain got some exercise too.",
        "Sharp.",
        "Rickie's impressed.",
        "You got it."
    ],
    brainBoostIncorrect: [
        "Nice try. We learned something.",
        "Curious counts too.",
        "Close enough to count as trying.",
        "Rickie still thinks that was a good guess.",
        "Now you know for next time."
    ],
    levelUp: [
        "Level up! Look at you.",
        "New level, same great you.",
        "Rickie's throwing acorns in the air right now.",
        "That's a new chapter.",
        "Leveled up. Rickie's doing a little dance.",
        "Onward and upward."
    ],
    perfectMission: [
        "All five, done. Rickie's impressed.",
        "A perfect day for showing up.",
        "Every single one. Nice.",
        "That's a clean sweep.",
        "Rickie counted. All five.",
        "Full mission, no shortcuts."
    ],
    firstMission: [
        "Your very first mission. Rickie will remember this one.",
        "That's the first of many.",
        "Welcome to the journey — that was mission one.",
        "First one's done. Rickie's excited for what's next.",
        "That's how it starts."
    ],
    firstWeek: [
        "A whole week. That's real.",
        "Seven days — Rickie's genuinely impressed.",
        "One week in the books.",
        "That's a habit forming.",
        "A week together. Rickie likes this."
    ],
    milestone: [
        "Rickie's been keeping track — you've earned a few of these.",
        "Look at everything you've built so far.",
        "Rickie flips back through these sometimes. Good stuff in here.",
        "A few milestones deep already.",
        "Every one of these took showing up.",
        "Rickie's proud of this collection."
    ],
    general: [
        "Every little adventure counts.",
        "You're building something that lasts.",
        "Rickie can't wait to see what you do next.",
        "One step today, a whole journey over time.",
        "Rickie's cheering you on.",
        "Small moves add up to big stories.",
        "This is the good kind of building — slow and steady.",
        "Rickie's glad you're here."
    ],
    guest: [
        "Trying things out? Rickie approves.",
        "No pressure — look around as long as you like.",
        "Guest or not, Rickie's glad you showed up.",
        "Take your time deciding.",
        "Rickie likes new faces."
    ],
    returning: [
        "You came back. That's what matters.",
        "Welcome back.",
        "Good to see you again.",
        "Rickie kept your spot.",
        "Whenever you're ready, Rickie's here.",
        "However long it's been, you're here now.",
        "Let's pick up where we left off."
    ],
    fun: [
        "Rickie once tried to do a push-up. It did not go well.",
        "Fun fact: raccoons wash their food. Rickie is very committed to this.",
        "Rickie thinks acorns are underrated as currency.",
        "If Rickie had thumbs, he'd give you one up right now.",
        "Rickie's favorite exercise is naps. Unofficially.",
        "Somewhere, a raccoon is proud of you.",
        "Rickie's been told he has good posture for a raccoon.",
        "This message brought to you by a very supportive raccoon."
    ]
};

var _lastRickieLineByPool = {};

// Picks from a named pool in RICKIE_LINES, never repeating the immediately
// previous pick *within that same pool* (each pool tracks its own history,
// so an exercise-completion pick can't be blocked by a Brain Boost pick).
function _pickRickieLine(poolKey) {
    var pool = RICKIE_LINES[poolKey];
    if (!pool || pool.length === 0) return '';
    if (pool.length === 1) return pool[0];
    var last = _lastRickieLineByPool[poolKey];
    var choice;
    do {
        choice = pool[Math.floor(Math.random() * pool.length)];
    } while (choice === last);
    _lastRickieLineByPool[poolKey] = choice;
    return choice;
}

function _rickieTimeOfDayPool() {
    var hour = new Date().getHours();
    if (hour < 12) return 'morning';
    if (hour < 18) return 'afternoon';
    return 'evening';
}

// ── Rickie preference gating ─────────────────────────────────────────────────
// Guests always get 'full' — no persisted preference exists for them, and the
// mode selector is registered-users-only. Defaults to 'full' if currentUser
// hasn't loaded yet, matching the server-side default.
function _rickieMode() {
    if (isGuest || !currentUser) return 'full';
    return currentUser.rickie_mode || 'full';
}

function _rickieAllowsFun() {
    return _rickieMode() === 'full';
}

// Reaction toasts: full shows every time; quiet only for milestone-significant
// moments (mission complete, perfect mission, first mission, level-up) and
// never for Brain Boost or exercises 1-4; minimal shows none.
function _rickieAllowsReaction(isMilestoneMoment) {
    var mode = _rickieMode();
    if (mode === 'minimal') return false;
    if (mode === 'quiet') return !!isMilestoneMoment;
    return true;
}

// ── R1.5.2 Expression Engine ─────────────────────────────────────────────────
// Which SVG each expression maps to. Neutral/Happy/Proud/Curious are real
// Phase A assets; the rest have no dedicated art yet (Phase B), so they fall
// back to the closest existing expression rather than a broken image.
var RICKIE_EXPRESSION_SVG = {
    neutral:     '/static/rickie.svg',
    happy:       '/static/rickie_happy.svg',
    proud:       '/static/rickie_proud.svg',
    curious:     '/static/rickie_curious.svg',
    celebrating: '/static/rickie_proud.svg',   // placeholder — no dedicated asset yet
    encouraging: '/static/rickie.svg',         // placeholder — no dedicated asset yet
    thinking:    '/static/rickie.svg',         // placeholder — no trigger reaches this yet either
    cozy:        '/static/rickie.svg'          // placeholder — no trigger reaches this yet either
};

var currentRickieExpression = 'neutral';

// Single source of truth for "what should Rickie's face look like right now."
// Reuses the exact same signals the R1.6 reaction-line system already computes
// (firstMissionEver, leveledUp, completed_count === 5) in the same priority
// order (first_mission > level_up > perfect_mission > mission_complete) —
// this is not a second priority system, it's the same one read differently.
function getRickieExpression(eventContext) {
    eventContext = eventContext || {};
    switch (eventContext.type) {
        case 'mission_complete':
            if (eventContext.firstMissionEver) return 'celebrating';
            if (eventContext.leveledUp) return 'celebrating';
            if (eventContext.perfectMission) return 'proud';
            return 'happy';
        case 'brain_boost_correct':
            return 'curious';
        case 'returning_user':
            return 'encouraging';
        case 'guest_mode':
        case 'idle_dashboard':
        default:
            return 'neutral';
    }
}

// Pushes currentRickieExpression to every surface that shows Rickie's face.
// Minimal mode always displays neutral regardless of the computed expression —
// same "branding only" rule _updateRickieMoodBadge already applies to the mood
// badge, applied here to the avatar itself.
function _applyRickieExpression() {
    var expr = (_rickieMode() === 'minimal') ? 'neutral' : currentRickieExpression;
    var src = RICKIE_EXPRESSION_SVG[expr] || RICKIE_EXPRESSION_SVG.neutral;
    var avatarEls = document.querySelectorAll(
        '.journey-avatar, .rickie-avatar-sm, .rickie-reaction-avatar, .coach-avatar, .mb-avatar'
    );
    avatarEls.forEach(function (el) { el.src = src; });
}

function _summarizeProgress(data) {
    if (!data) return { xp: 0, acorns: 0, leveledUp: false };
    return {
        xp: data.xp_awarded || 0,
        acorns: data.acorns_awarded || 0,
        leveledUp: !!data.leveled_up,
        newLevel: data.new_level,
        levelTitle: data.level_title
    };
}

var _rickieReactionHideTimer = null;
var _rickieReactionRemoveTimer = null;

function showRickieReaction(line, summary) {
    summary = summary || {};
    var toast = document.getElementById('rickie-reaction');
    if (!toast) return;

    document.getElementById('rickie-reaction-line').textContent = line;

    var progressEl = document.getElementById('rickie-reaction-progress');
    var xp = summary.xp || 0;
    var acorns = summary.acorns || 0;
    if (xp > 0 || acorns > 0) {
        var parts = [];
        if (xp > 0) parts.push('+' + xp + ' XP');
        if (acorns > 0) parts.push('+' + acorns + ' 🌰');
        progressEl.textContent = parts.join('  ·  ');
        progressEl.hidden = false;
    } else {
        progressEl.hidden = true;
    }

    var levelUpEl = document.getElementById('rickie-reaction-levelup');
    if (summary.leveledUp) {
        levelUpEl.textContent = _pickRickieLine('levelUp') + ' 🎉 Level ' + summary.newLevel + ' — ' + summary.levelTitle;
        levelUpEl.hidden = false;
    } else {
        levelUpEl.hidden = true;
    }

    if (_rickieReactionHideTimer) clearTimeout(_rickieReactionHideTimer);
    if (_rickieReactionRemoveTimer) clearTimeout(_rickieReactionRemoveTimer);
    toast.classList.remove('leaving');
    toast.hidden = false;

    var displayMs = summary.leveledUp ? 4600 : 3600;
    _rickieReactionHideTimer = setTimeout(function () {
        toast.classList.add('leaving');
        _rickieReactionRemoveTimer = setTimeout(function () {
            toast.hidden = true;
            toast.classList.remove('leaving');
        }, 300);
    }, displayMs);
}

// ── Rickie's Memory Book ────────────────────────────────────────────────────────
// This is a scrapbook, not a stats page. Memories lead; numbers only ever
// support them. Never guilt, never shame, never mention missed days, never
// compare to other users.

var MEMORY_BOOK_PAGES = [
    { key: 'today',      title: '📖 Today' },
    { key: 'thisWeek',   title: '📖 Earlier this week' },
    { key: 'journey',    title: '📖 Your Journey' },
    { key: 'milestones', title: '📖 Your Milestones' },
    { key: 'notes',      title: "📖 Rickie's Notes" }
];

var MEMORY_EVENT_COPY = {
    mission_complete:    "You completed the day's mission.",
    perfect_mission:     "A perfect day — all five, done.",
    new_exercise:        "You tried something new.",
    brain_boost_attempt: "You gave a Brain Boost question a try.",
    brain_boost_correct: "You got a Brain Boost question right.",
    family_session:      "A family session happened."
};

// Event types that can repeat within one day get a count-aware line instead
// of one identical bullet per occurrence — a scrapbook summarizes, it
// doesn't itemize like a receipt.
var MEMORY_EVENT_COPY_MULTI = {
    new_exercise: function (n) {
        return n === 1 ? "You tried something new." : "You tried " + n + " new things.";
    },
    family_session: function (n) {
        return n === 1 ? "A family session happened." : n + " family sessions happened.";
    }
};

var MEMORY_CATEGORY_LABELS = {
    upper_body:   'Upper Body',
    lower_body:   'Lower Body',
    core:         'Core',
    mobility:     'Mobility',
    conditioning: 'Conditioning'
};

var MEMORY_MILESTONE_COPY = {
    first_mission:    { unlocked: "🎉 Your first mission — that's when this all began.",
                         locked:  "Your first mission is still ahead." },
    exercises_100:    { unlocked: "🎉 100 exercises completed. That adds up to something real.",
                         locked:  "exercises completed so far, on the way to 100." },
    exercises_500:    { unlocked: "🎉 500 exercises completed. That's a lot of showing up.",
                         locked:  "exercises completed so far, on the way to 500." },
    brain_boost_100:  { unlocked: "🎉 100 Brain Boost questions answered. Rickie's impressed.",
                         locked:  "Brain Boost questions answered so far, on the way to 100." },
    xp_1000:          { unlocked: "🎉 1000 XP earned. A whole journey's worth.",
                         locked:  "XP earned so far, on the way to 1000." },
    acorns_100:       { unlocked: "🎉 100 acorns collected. Quite a stash.",
                         locked:  "acorns collected so far, on the way to 100." },
    level_10:         { unlocked: "🎉 Level 10 reached. Look how far you've come.",
                         locked:  "Still climbing toward Level 10." }
};

var _mbOverlay = null;
var _mbData = null;
var _mbPageIndex = 0;

function _mbDateOnly(isoString) {
    return isoString.slice(0, 10);
}

function _mbRelativeDayLabel(isoDateStr, todayStr) {
    var d = new Date(isoDateStr + 'T00:00:00');
    var today = new Date(todayStr + 'T00:00:00');
    var diffDays = Math.round((today - d) / 86400000);
    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    return diffDays + ' days ago';
}

// Groups events by type (order = first-seen, which is newest since the
// timeline arrives newest-first) and returns one warm, count-aware line per
// type rather than one bullet per raw event.
function _mbGroupEventLines(events) {
    var counts = {};
    var order = [];
    events.forEach(function (e) {
        if (!counts[e.event_type]) { counts[e.event_type] = 0; order.push(e.event_type); }
        counts[e.event_type]++;
    });
    return order.map(function (type) {
        var n = counts[type];
        if (MEMORY_EVENT_COPY_MULTI[type]) return MEMORY_EVENT_COPY_MULTI[type](n);
        return MEMORY_EVENT_COPY[type] || "Something good happened.";
    });
}

function _mbEl(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
}

function _mbBuildToday(data) {
    var frag = document.createDocumentFragment();
    var todayStr = new Date().toISOString().slice(0, 10);
    var events = (data.timeline || []).filter(function (e) {
        return _mbDateOnly(e.created_at) === todayStr;
    });

    if (events.length === 0) {
        frag.appendChild(_mbEl('p', 'mb-empty', "Nothing written down yet today — Rickie's ready whenever you are."));
        return frag;
    }

    var list = _mbEl('ul', 'mb-memory-list');
    _mbGroupEventLines(events).forEach(function (line) {
        list.appendChild(_mbEl('li', 'mb-memory-item', line));
    });
    frag.appendChild(list);
    return frag;
}

function _mbBuildThisWeek(data) {
    var frag = document.createDocumentFragment();
    var todayStr = new Date().toISOString().slice(0, 10);
    var weekAgo = new Date();
    weekAgo.setDate(weekAgo.getDate() - 7);
    var weekAgoStr = weekAgo.toISOString().slice(0, 10);

    var events = (data.timeline || []).filter(function (e) {
        var d = _mbDateOnly(e.created_at);
        return d !== todayStr && d >= weekAgoStr;
    });

    if (events.length === 0) {
        frag.appendChild(_mbEl('p', 'mb-empty', "Nothing from earlier this week yet — today's page is still being written."));
        return frag;
    }

    var byDay = {};
    var order = [];
    events.forEach(function (e) {
        var d = _mbDateOnly(e.created_at);
        if (!byDay[d]) { byDay[d] = []; order.push(d); }
        byDay[d].push(e);
    });

    order.forEach(function (d) {
        frag.appendChild(_mbEl('p', 'mb-day-label', _mbRelativeDayLabel(d, todayStr)));
        var list = _mbEl('ul', 'mb-memory-list');
        _mbGroupEventLines(byDay[d]).forEach(function (line) {
            list.appendChild(_mbEl('li', 'mb-memory-item', line));
        });
        frag.appendChild(list);
    });
    return frag;
}

function _mbBuildJourney(data) {
    var frag = document.createDocumentFragment();
    var level      = (currentUser && currentUser.level) || 1;
    var title      = (currentUser && currentUser.level_title) || 'Explorer';
    var xpToNext   = (currentUser && currentUser.xp_to_next_level) || 0;
    var daysActive = data.lifetime.days_active || 0;

    frag.appendChild(_mbEl('p', 'mb-journey-line',
        "Right now you're Level " + level + " — " + title + "."));
    frag.appendChild(_mbEl('p', 'mb-journey-sub',
        xpToNext + " XP until the next chapter."));
    frag.appendChild(_mbEl('p', 'mb-journey-line',
        "You've shown up on " + daysActive + (daysActive === 1 ? ' day' : ' days') + " so far."));
    frag.appendChild(_mbEl('p', 'mb-journey-sub',
        '🌰 ' + (data.lifetime.acorns_total || 0) + ' acorns collected along the way.'));
    return frag;
}

function _mbBuildMilestones(data) {
    var frag = document.createDocumentFragment();
    var milestones = data.milestones || [];

    var list = _mbEl('ul', 'mb-milestone-list');
    milestones.forEach(function (m) {
        var copy = MEMORY_MILESTONE_COPY[m.key];
        var item = _mbEl('li', 'mb-milestone-item' + (m.unlocked ? ' unlocked' : ''));
        if (m.unlocked) {
            item.textContent = copy ? copy.unlocked : ('🎉 ' + m.label);
        } else {
            var prefix = m.progress + ' / ' + m.target + ' — ';
            item.textContent = '🔒 ' + prefix + (copy ? copy.locked : m.label.toLowerCase());
        }
        list.appendChild(item);
    });
    frag.appendChild(list);
    return frag;
}

function _mbBuildNotes(data) {
    var frag = document.createDocumentFragment();
    var notes = [];
    var lifetime = data.lifetime;
    var favorites = data.favorites;

    if (lifetime.missions_completed >= 1) {
        notes.push("Rickie remembers your first mission — that's when this all started.");
    }
    if (favorites.favorite_exercise) {
        notes.push("Your favorite move seems to be " + favorites.favorite_exercise + ". Rickie's noticed.");
    }
    if (favorites.favorite_category) {
        var label = MEMORY_CATEGORY_LABELS[favorites.favorite_category] || favorites.favorite_category;
        notes.push("You gravitate toward " + label + " days.");
    }
    if (lifetime.days_active >= 1) {
        notes.push("You've shown up on " + lifetime.days_active +
            (lifetime.days_active === 1 ? ' different day.' : ' different days.') + " That's not nothing.");
    }
    if (currentUser && currentUser.level_title) {
        notes.push("Right now, your title is " + currentUser.level_title + ". Rickie's excited to see what's next.");
    }
    var unlockedCount = (data.milestones || []).filter(function (m) { return m.unlocked; }).length;
    if (unlockedCount >= 2) {
        notes.push(_pickRickieLine('milestone'));
    }
    // A small, occasional aside — not on every visit, just often enough to
    // feel like Rickie sometimes says something a little different.
    // Quiet/minimal never get the fun pool.
    if (_rickieAllowsFun() && Math.random() < 0.25) {
        notes.push(_pickRickieLine('fun'));
    }

    if (notes.length === 0) {
        frag.appendChild(_mbEl('p', 'mb-empty', "Rickie hasn't written anything yet — let's make a memory today."));
        return frag;
    }

    var list = _mbEl('ul', 'mb-memory-list');
    notes.forEach(function (n) { list.appendChild(_mbEl('li', 'mb-memory-item', n)); });
    frag.appendChild(list);
    return frag;
}

var MEMORY_BOOK_BUILDERS = {
    today:      _mbBuildToday,
    thisWeek:   _mbBuildThisWeek,
    journey:    _mbBuildJourney,
    milestones: _mbBuildMilestones,
    notes:      _mbBuildNotes
};

function _renderMemoryBookPage() {
    if (!_mbOverlay || !_mbData) return;
    var pageDef = MEMORY_BOOK_PAGES[_mbPageIndex];

    document.getElementById('mb-page-title').textContent = pageDef.title;

    var pageContent = document.getElementById('mb-page-content');
    pageContent.innerHTML = '';
    pageContent.appendChild(MEMORY_BOOK_BUILDERS[pageDef.key](_mbData));

    document.getElementById('mb-prev').disabled = _mbPageIndex === 0;
    document.getElementById('mb-next').disabled = _mbPageIndex === MEMORY_BOOK_PAGES.length - 1;

    var dots = document.getElementById('mb-dots');
    dots.innerHTML = '';
    MEMORY_BOOK_PAGES.forEach(function (p, i) {
        var dot = _mbEl('span', 'mb-dot' + (i === _mbPageIndex ? ' active' : ''));
        dots.appendChild(dot);
    });
}

function _mbGoToPage(index) {
    if (index < 0 || index >= MEMORY_BOOK_PAGES.length) return;
    _mbPageIndex = index;
    _renderMemoryBookPage();
}

function closeMemoryBook() {
    if (_mbOverlay) {
        _mbOverlay.classList.remove('open');
        document.body.style.overflow = '';
    }
}

async function openMemoryBook() {
    if (!_mbOverlay) {
        _mbOverlay = document.createElement('div');
        _mbOverlay.className = 'mb-overlay';
        _mbOverlay.setAttribute('role', 'dialog');
        _mbOverlay.setAttribute('aria-modal', 'true');
        _mbOverlay.setAttribute('aria-labelledby', 'mb-page-title');
        _mbOverlay.addEventListener('click', function (e) {
            if (e.target === _mbOverlay) closeMemoryBook();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && _mbOverlay.classList.contains('open')) closeMemoryBook();
        });

        var book = _mbEl('div', 'mb-book');

        var closeBtn = _mbEl('button', 'mb-close', '✕');
        closeBtn.id = 'mb-close';
        closeBtn.setAttribute('aria-label', 'Close Memory Book');
        closeBtn.addEventListener('click', closeMemoryBook);

        var header = _mbEl('div', 'mb-header');
        var avatar = document.createElement('img');
        avatar.className = 'mb-avatar';
        avatar.src = '/static/rickie.svg';
        avatar.alt = '';
        var headerText = _mbEl('div', 'mb-header-text');
        headerText.appendChild(_mbEl('h2', 'mb-title', "Rickie's Memory Book"));
        headerText.appendChild(_mbEl('p', 'mb-subtitle', "Let's remember what we've done together."));
        header.appendChild(avatar);
        header.appendChild(headerText);

        var pageTitle = _mbEl('h3', 'mb-page-title');
        pageTitle.id = 'mb-page-title';

        var pageContent = _mbEl('div', 'mb-page-content');
        pageContent.id = 'mb-page-content';

        var nav = _mbEl('div', 'mb-nav');
        var prevBtn = _mbEl('button', 'mb-nav-btn', '‹');
        prevBtn.id = 'mb-prev';
        prevBtn.setAttribute('aria-label', 'Previous page');
        prevBtn.addEventListener('click', function () { _mbGoToPage(_mbPageIndex - 1); });
        var dots = _mbEl('div', 'mb-dots');
        dots.id = 'mb-dots';
        var nextBtn = _mbEl('button', 'mb-nav-btn', '›');
        nextBtn.id = 'mb-next';
        nextBtn.setAttribute('aria-label', 'Next page');
        nextBtn.addEventListener('click', function () { _mbGoToPage(_mbPageIndex + 1); });
        nav.appendChild(prevBtn);
        nav.appendChild(dots);
        nav.appendChild(nextBtn);

        book.appendChild(closeBtn);
        book.appendChild(header);
        book.appendChild(pageTitle);
        book.appendChild(pageContent);
        book.appendChild(nav);

        _mbOverlay.appendChild(book);
        document.body.appendChild(_mbOverlay);
    }

    document.getElementById('mb-page-content').innerHTML = '<p class="mb-empty">Rickie is turning the pages…</p>';
    _mbOverlay.classList.add('open');
    document.body.style.overflow = 'hidden';
    _applyRickieExpression();

    var result = await api('/api/memory-book');
    if (!result || result.status !== 200) {
        document.getElementById('mb-page-content').innerHTML =
            '<p class="mb-empty">🦝 Rickie couldn\'t quite find the pages — try again in a moment.</p>';
        return;
    }

    _mbData = result.data;
    _mbPageIndex = 0;
    _renderMemoryBookPage();
}

// ── Analytics ─────────────────────────────────────────────────────────────────
// Fire-and-forget. Swallows all errors — never affects user experience.
function fireEvent(name) {
    fetch('/api/events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event: name })
    }).catch(function () {});
}

// ── API wrapper ───────────────────────────────────────────────────────────────

async function api(path, method, body) {
    method = method || 'GET';
    var token = localStorage.getItem('streakfit_token');
    var headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = 'Bearer ' + token;

    var opts = { method: method, headers: headers };
    if (body) opts.body = JSON.stringify(body);

    var res;
    try {
        res = await fetch(path, opts);
    } catch (err) {
        return { status: 0, data: { error: 'Network error. Check your connection.' } };
    }

    // Authenticated request received 401 — token has expired
    if (res.status === 401 && token) {
        localStorage.removeItem('streakfit_token');
        showView('auth');
        setError('login-error', 'Session expired. Please log in again.');
        return null;
    }

    var data = await res.json();
    return { status: res.status, data: data };
}

// ── View helpers ──────────────────────────────────────────────────────────────

function showView(name) {
    document.getElementById('auth-view').hidden      = (name !== 'auth');
    document.getElementById('dashboard-view').hidden = (name !== 'dashboard');
}

function showTab(name) {
    document.getElementById('login-form').hidden    = (name !== 'login');
    document.getElementById('register-form').hidden = (name !== 'register');
    document.getElementById('tab-login').classList.toggle('active',    name === 'login');
    document.getElementById('tab-register').classList.toggle('active', name === 'register');
    clearErrors();
}

function setError(id, msg) {
    var el = document.getElementById(id);
    if (el) el.textContent = msg;
}

function clearErrors() {
    ['login-error', 'register-error', 'create-error'].forEach(function (id) {
        setError(id, '');
    });
}

// ── Guest Mode ────────────────────────────────────────────────────────────────

function handleGuestMode() {
    isGuest = true;
    guestCompleted = new Set();
    guestCompleteFired = false;
    applyTheme('game');
    setGuestUI(true);
    fireEvent('guest_start');
    loadDailyExercises();
    loadTeams();
    showView('dashboard');
}

function handleExitGuest() {
    isGuest = false;
    guestCompleted = new Set();
    guestCompleteFired = false;
    setGuestUI(false);
    clearErrors();
    showTab('login');
    showView('auth');
}

function setGuestUI(guest) {
    var guestBanner = document.getElementById('guest-mode-banner');
    if (guestBanner) guestBanner.hidden = !guest;

    var skillSel = document.getElementById('skill-level-select');
    if (skillSel) skillSel.hidden = guest;

    var rickieSel = document.getElementById('rickie-mode-select');
    if (rickieSel) rickieSel.hidden = guest;

    var sideQuests = document.getElementById('side-quests-section');
    if (sideQuests) sideQuests.hidden = guest;

    var coachBtn = document.getElementById('coach-ask-btn');
    if (coachBtn) coachBtn.hidden = guest;

    var logoutBtn = document.getElementById('btn-logout');
    if (logoutBtn) {
        logoutBtn.textContent = guest ? 'Exit' : 'Log Out';
        logoutBtn.onclick = guest ? handleExitGuest : handleLogout;
    }
}

// ── Auth ──────────────────────────────────────────────────────────────────────

async function handleLogin(event) {
    event.preventDefault();
    setError('login-error', '');

    var username = document.getElementById('login-username').value.trim();
    var password = document.getElementById('login-password').value;

    var result = await api('/api/login', 'POST', { username: username, password: password });
    if (!result) return;

    if (result.status === 200) {
        localStorage.setItem('streakfit_token', result.data.access_token);
        document.getElementById('login-form').reset();
        await loadDashboard();
        showView('dashboard');
    } else {
        setError('login-error', result.data.error || 'Login failed.');
    }
}

async function handleRegister(event) {
    event.preventDefault();
    setError('register-error', '');

    var username = document.getElementById('reg-username').value.trim();
    var password = document.getElementById('reg-password').value;

    var result = await api('/api/register', 'POST', { username: username, password: password });
    if (!result) return;

    if (result.status === 201) {
        var loginResult = await api('/api/login', 'POST', { username: username, password: password });
        if (loginResult && loginResult.status === 200) {
            localStorage.setItem('streakfit_token', loginResult.data.access_token);
            document.getElementById('register-form').reset();
            await loadDashboard();
            showView('dashboard');
        } else {
            showTab('login');
            setError('login-error', 'Account created. Please log in.');
        }
    } else {
        setError('register-error', result.data.error || 'Registration failed.');
    }
}

function handleLogout() {
    isGuest = false;
    guestCompleted = new Set();
    localStorage.removeItem('streakfit_token');
    clearErrors();
    showTab('login');
    showView('auth');
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

async function loadDashboard() {
    await loadUserPreferences();
    await loadDailyExercises();
    await loadChallenges();
    checkRetentionPrompts();
    await loadTeams();
}

async function loadUserPreferences() {
    var result = await api('/api/me');
    if (!result || result.status !== 200) return;
    currentUser = result.data;
    applyTheme(result.data.display_mode);
    var sel = document.getElementById('skill-level-select');
    if (sel && result.data.skill_level) sel.value = result.data.skill_level;
    var rickieSel = document.getElementById('rickie-mode-select');
    if (rickieSel && result.data.rickie_mode) rickieSel.value = result.data.rickie_mode;
}

var THEME_COLORS = { game: '#4338ca', bright: '#0891b2', classic: '#4f46e5' };

function applyTheme(mode) {
    mode = mode || 'game';
    var body = document.body;
    body.className = body.className.replace(/\btheme-\w+\b/g, '').trim();
    body.classList.add('theme-' + mode);

    // Keep browser chrome and PWA status bar in sync with the active theme
    var metaTheme = document.getElementById('meta-theme-color');
    if (metaTheme) metaTheme.content = THEME_COLORS[mode] || '#4338ca';

    document.querySelectorAll('.theme-btn').forEach(function (btn) {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
}

async function handleDisplayModeChange(mode) {
    applyTheme(mode); // instant feedback — no waiting for API
    var result = await api('/api/me', 'PATCH', { display_mode: mode });
    if (!result || result.status !== 200) {
        // Server rejected it — reload preferences to restore correct state
        await loadUserPreferences();
    }
}

async function handleRickieModeChange(mode) {
    var result = await api('/api/me', 'PATCH', { rickie_mode: mode });
    if (!result) return;
    // Reload either way — on failure, reverts the select to server value.
    // loadDailyExercises() refetches the full /api/me (with xp/level fields
    // the PATCH response doesn't include) and re-renders the Journey card,
    // so the new mode's effects show up immediately without a page refresh.
    await loadDailyExercises();
    _updateRickieMoodBadge();
}

async function loadDailyExercises() {
    var list = document.getElementById('daily-exercises-list');
    list.innerHTML = '';

    // Show spinner while loading
    var spinner = document.createElement('div');
    spinner.className = 'state-loading';
    spinner.innerHTML = '<div class="spinner"></div><p>Rickie\'s getting today\'s mission ready…</p>';
    list.appendChild(spinner);

    var daily;

    if (isGuest) {
        var guestRes;
        try {
            guestRes = await fetch('/api/demo/daily');
        } catch (e) {
            list.innerHTML = '';
            return;
        }
        if (!guestRes.ok) { list.innerHTML = ''; return; }
        daily = await guestRes.json();
        daily.exercises = daily.exercises.map(function (ex) {
            return Object.assign({}, ex, { completed: guestCompleted.has(ex.key) });
        });
        daily.completed_count = guestCompleted.size;
        list.innerHTML = '';
    } else {
        var [result, meResult] = await Promise.all([api('/api/daily'), api('/api/me')]);
        if (meResult && meResult.status === 200) currentUser = meResult.data;
        if (!result) return;
        list.innerHTML = '';
        if (result.status !== 200) return;
        daily = result.data;
    }

    // Baseline Rickie expression for this load — overridden moment-to-moment
    // by mission-complete/Brain Boost reactions, then recomputed back to this
    // baseline the next time loadDailyExercises runs (e.g. 480ms after a
    // completion), the same way the reaction toast is itself transient.
    if (isGuest) {
        currentRickieExpression = getRickieExpression({ type: 'guest_mode' });
    } else if (daily.rise_again) {
        currentRickieExpression = getRickieExpression({ type: 'returning_user' });
    } else {
        currentRickieExpression = getRickieExpression({ type: 'idle_dashboard' });
    }
    _applyRickieExpression();

    // Sync the skill-level select
    var select = document.getElementById('skill-level-select');
    if (select) select.value = daily.skill_level;

    // Sync the Rickie Mode select — registered users only, currentUser has
    // the field since it's a guest-hidden control anyway.
    var rickieModeSel = document.getElementById('rickie-mode-select');
    if (rickieModeSel && currentUser && currentUser.rickie_mode) {
        rickieModeSel.value = currentUser.rickie_mode;
    }

    // Populate mission subtitle: date · skill level · Refreshes tomorrow
    var dateEl = document.getElementById('daily-mission-date');
    if (dateEl) {
        var d = new Date();
        var dateStr = d.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
        var level = daily.skill_level.charAt(0).toUpperCase() + daily.skill_level.slice(1);
        dateEl.textContent = dateStr + ' · ' + level + ' · Refreshes tomorrow';
    }

    // Rickie's pre-mission intro line — hidden once the mission is complete,
    // since the post-completion Rickie handoff line takes over from there.
    // Varies by time of day (and by guest vs. registered) instead of a single
    // fixed line every visit — small thing, but it's the difference between
    // software and a companion who's actually there in the moment.
    var rickieIntro = document.getElementById('rickie-mission-intro');
    if (rickieIntro) {
        rickieIntro.hidden = daily.completed_count >= 5;
        var introTextEl = document.getElementById('rickie-intro-text');
        if (introTextEl && !rickieIntro.hidden) {
            if (_rickieMode() === 'minimal') {
                // No personality greeting — plain functional line only.
                introTextEl.textContent = 'Complete today’s mission and come see me afterward.';
            } else {
                // Picked once per page load and reused across re-renders (e.g.
                // after each of exercises 1-4) so the greeting doesn't flicker
                // to a new line after every click — just once per visit.
                if (!_cachedGreetingLine) {
                    var greetingPool = isGuest ? 'guest' : _rickieTimeOfDayPool();
                    _cachedGreetingLine = _pickRickieLine(greetingPool);
                }
                introTextEl.textContent = _cachedGreetingLine + ' Complete today’s mission and come see me afterward.';
            }
        }
    }

    // Populate streak helper text (below progress bar, hidden when mission complete)
    var helperEl = document.getElementById('daily-streak-helper');
    if (helperEl) {
        if (daily.completed_count < 5) {
            var helperStreak = (currentUser && currentUser.current_streak) || 0;
            helperEl.textContent = helperStreak > 0
                ? 'Complete all 5 to keep your streak'
                : 'Complete all 5 to start your streak';
            helperEl.hidden = false;
        } else {
            helperEl.hidden = true;
        }
    }

    // Populate streak badge (hidden when streak is 0)
    var streakBadge = document.getElementById('daily-streak-badge');
    if (streakBadge) {
        var streak = (currentUser && currentUser.current_streak) || 0;
        if (streak > 0) {
            // Days 1–6: journey framing ("Day N") — you are at a point on a path
            // Day 7+:   record framing ("N days") — you have built something
            streakBadge.textContent = streak <= 6
                ? '🔥 Day ' + streak
                : '🔥 ' + streak + ' days';
            streakBadge.hidden = false;
        } else {
            streakBadge.hidden = true;
        }
    }

    // Populate stats row (current streak · best streak · total missions)
    var statsRow = document.getElementById('daily-stats-row');
    if (statsRow && currentUser) {
        var cs = currentUser.current_streak || 0;
        var bs = currentUser.best_streak    || 0;
        var tm = currentUser.total_missions || 0;
        document.getElementById('stat-current-streak').textContent  =
            '🔥 ' + cs + (cs === 1 ? ' day' : ' days');
        document.getElementById('stat-best-streak').textContent =
            '🏅 Best: ' + bs + (bs === 1 ? ' day' : ' days');
        document.getElementById('stat-total-missions').textContent =
            '✓ ' + tm + (tm === 1 ? ' mission' : ' missions');
        statsRow.hidden = false;
    }

    renderJourneyCard();
    _refreshTeamRickieCard();

    // Update progress bar
    var pct = (daily.completed_count / 5) * 100;
    var bar = document.getElementById('daily-progress-bar');
    if (bar) {
        bar.style.width = pct + '%';
        if (daily.completed_count === 5) bar.classList.add('complete');
        else bar.classList.remove('complete');
    }

    // Update count badge
    var badge = document.getElementById('daily-count-badge');
    if (badge) {
        badge.textContent = daily.completed_count + '/5';
        if (daily.completed_count === 5) badge.classList.add('badge-complete');
        else badge.classList.remove('badge-complete');
    }

    // Update ARIA
    var track = document.querySelector('.daily-progress-track');
    if (track) track.setAttribute('aria-valuenow', daily.completed_count);

    // All-complete banner + Today's Insight
    if (daily.rise_again && localStorage.getItem('rise_again_dismissed') !== daily.date) {
        var ceremony = document.createElement('div');
        ceremony.className = 'rise-again-ceremony';

        var rAvatar = document.createElement('img');
        rAvatar.className = 'rickie-avatar-sm rise-again-avatar';
        rAvatar.src = '/static/rickie.svg';
        rAvatar.alt = '';

        var rEmoji = document.createElement('p');
        rEmoji.className = 'rise-again-emoji';
        rEmoji.textContent = '🌅';

        var rTitle = document.createElement('p');
        rTitle.className = 'rise-again-title';
        rTitle.textContent = 'Rise Again';

        // This is the app's most important anti-guilt moment, so it draws
        // from the "returning" pool — Rickie's voice, never a mention of
        // how long it's been or why.
        var rLine1 = document.createElement('p');
        rLine1.className = 'rise-again-body';
        rLine1.textContent = _pickRickieLine('returning');

        var rLine2 = document.createElement('p');
        rLine2.className = 'rise-again-body';
        rLine2.textContent = 'That’s what matters.';

        var rBtn = document.createElement('button');
        rBtn.className = 'btn-primary rise-again-btn';
        rBtn.textContent = 'Continue';
        rBtn.addEventListener('click', function () {
            localStorage.setItem('rise_again_dismissed', daily.date);
            list.removeChild(ceremony);
            daily.exercises.forEach(function (ex) {
                list.appendChild(renderDailyExercise(ex));
            });
            var pt = document.getElementById('daily-progress-text');
            if (pt) pt.textContent = daily.completed_count + ' / 5 completed';
        });

        ceremony.appendChild(rAvatar);
        ceremony.appendChild(rEmoji);
        ceremony.appendChild(rTitle);
        ceremony.appendChild(rLine1);
        ceremony.appendChild(rLine2);
        ceremony.appendChild(rBtn);
        list.appendChild(ceremony);
        return;
    }

    if (daily.completed_count === 5) {
        if (isGuest) {
            if (!guestCompleteFired) {
                guestCompleteFired = true;
                fireEvent('guest_complete');
            }
            list.appendChild(renderGuestCompleteBanner());
        } else {
            _showMissionCompleteNotification();

            var bannerStreak = (currentUser && currentUser.current_streak) || 0;

            var banner    = document.createElement('div');
            banner.className = 'daily-complete-banner';

            var bannerEmoji = document.createElement('span');
            bannerEmoji.className = 'complete-emoji';
            bannerEmoji.textContent = '🔥';

            var bannerText  = document.createElement('div');

            var bannerTitle = document.createElement('p');
            bannerTitle.className = 'complete-title';
            bannerTitle.textContent = getStreakBannerCopy(bannerStreak);

            var bannerSub   = document.createElement('p');
            bannerSub.className = 'complete-sub';
            bannerSub.textContent = bannerStreak === 1
                ? 'Come back tomorrow for Day 2.'
                : 'Come back tomorrow to keep it alive.';

            bannerText.appendChild(bannerTitle);
            bannerText.appendChild(bannerSub);
            banner.appendChild(bannerEmoji);
            banner.appendChild(bannerText);
            list.appendChild(banner);
        }

        // Mission → Insight → Rickie: hand the user off to what's next.
        // Varies by context instead of one fixed line every day — a streak
        // of exactly 7 gets its own moment; otherwise a general pick keeps
        // this from feeling identical on the 50th visit as the 1st.
        // Minimal mode drops the personality pick entirely for neutral copy.
        var rickieHandoff = document.createElement('p');
        rickieHandoff.className = 'rickie-handoff-line';
        if (_rickieMode() === 'minimal') {
            rickieHandoff.textContent = 'Ready for today’s insight?';
        } else {
            var handoffPoolKey = (!isGuest && bannerStreak === 7) ? 'firstWeek' : 'general';
            rickieHandoff.textContent = '🦝 ' + _pickRickieLine(handoffPoolKey) + ' Ready for today’s insight?';
        }
        list.appendChild(rickieHandoff);

        if (daily.insight) {
            list.appendChild(renderInsightCard(daily.insight));
        }
        if (daily.brain_boost) {
            list.appendChild(renderBrainBoostCard(daily.brain_boost));
        }
    }

    // Render exercises
    daily.exercises.forEach(function (ex) {
        list.appendChild(renderDailyExercise(ex));
    });

    // Progress text
    var progressText = document.getElementById('daily-progress-text');
    if (progressText) {
        if (daily.completed_count === 5) {
            progressText.textContent = '5 / 5 complete 🔥';
        } else {
            progressText.textContent = daily.completed_count + ' / 5 completed';
        }
    }
}

// ── Streak milestone copy ─────────────────────────────────────────────────────

var STREAK_MILESTONES = {
    1:   'Day 1. The streak starts here.',
    2:   'Day 2. You came back.',
    3:   '3 days in a row. You\'re building something.',
    4:   'The beginning of something real.',
    5:   'Almost there.',
    6:   'One more day.',
    7:   'One week straight. This is becoming real.',
    14:  '14 days. You haven\'t broken it.',
    30:  '30 days. A month of showing up.',
    100: '100 days. That changes a person.'
};

function getStreakBannerCopy(streak) {
    if (STREAK_MILESTONES[streak]) return STREAK_MILESTONES[streak];
    if (streak > 1) return streak + '-day streak. Keep it going.';
    return 'Mission complete.';
}

// ── Guest completion banner ───────────────────────────────────────────────────

function renderGuestCompleteBanner() {
    var banner = document.createElement('div');
    banner.className = 'daily-complete-banner guest-complete-banner';

    var topRow = document.createElement('div');
    topRow.className = 'guest-banner-top';

    var emoji = document.createElement('span');
    emoji.className = 'complete-emoji';
    emoji.textContent = '🔥';

    var textDiv = document.createElement('div');

    var title = document.createElement('p');
    title.className = 'complete-title';
    title.textContent = 'Day 1 Complete';

    var sub = document.createElement('p');
    sub.className = 'complete-sub';
    sub.textContent = 'Your streak starts here.';

    textDiv.appendChild(title);
    textDiv.appendChild(sub);
    topRow.appendChild(emoji);
    topRow.appendChild(textDiv);

    var desc = document.createElement('p');
    desc.className = 'guest-banner-desc';
    desc.textContent = 'Create a free account to save your streak and come back tomorrow for Day 2.';

    var actions = document.createElement('div');
    actions.className = 'guest-banner-actions';

    var createBtn = document.createElement('button');
    createBtn.className = 'btn-primary';
    createBtn.textContent = 'Create Account';
    createBtn.addEventListener('click', function () {
        fireEvent('guest_create_account_click');
        handleExitGuest();
        showTab('register');
    });

    var loginBtn = document.createElement('button');
    loginBtn.className = 'btn-guest-login';
    loginBtn.textContent = 'Log In';
    loginBtn.addEventListener('click', function () {
        handleExitGuest();
    });

    actions.appendChild(createBtn);
    actions.appendChild(loginBtn);

    banner.appendChild(topRow);
    banner.appendChild(desc);
    banner.appendChild(actions);

    return banner;
}

// ── Today's Insight card ──────────────────────────────────────────────────────

function renderInsightCard(insight) {
    var card = document.createElement('div');
    card.className = 'insight-card';

    var teaser = document.createElement('div');
    teaser.className = 'insight-teaser';

    var teaserTop = document.createElement('div');
    teaserTop.className = 'insight-teaser-top';

    var teaserAvatar = document.createElement('img');
    teaserAvatar.className = 'rickie-avatar-sm';
    teaserAvatar.src = '/static/rickie.svg';
    teaserAvatar.alt = 'Rickie';

    var teaserText = document.createElement('p');
    teaserText.className = 'insight-teaser-text';
    teaserText.textContent = '🦝 Rickie found today\'s Insight...';

    teaserTop.appendChild(teaserAvatar);
    teaserTop.appendChild(teaserText);

    var revealBtn = document.createElement('button');
    revealBtn.className = 'insight-reveal-btn';
    revealBtn.textContent = 'Reveal Insight';

    teaser.appendChild(teaserTop);
    teaser.appendChild(revealBtn);

    var revealed = document.createElement('div');
    revealed.className = 'insight-revealed';
    revealed.hidden = true;

    var category = document.createElement('p');
    category.className = 'insight-category';
    category.textContent = insight.category;

    var text = document.createElement('p');
    text.className = 'insight-text';
    text.textContent = insight.text;

    revealed.appendChild(category);
    revealed.appendChild(text);

    if (!isGuest) {
        var tellMore = document.createElement('button');
        tellMore.className = 'insight-tell-more';
        tellMore.textContent = 'Tell me more →';
        tellMore.addEventListener('click', function () {
            openCoach({ type: 'insight', insight_text: insight.text, insight_category: insight.category });
        });
        revealed.appendChild(tellMore);
    }

    revealBtn.addEventListener('click', function () {
        teaser.hidden = true;
        revealed.hidden = false;
    });

    card.appendChild(teaser);
    card.appendChild(revealed);

    return card;
}

// ── Brain Boost card (own card, multiple choice, answer once, no penalties) ───

function renderBrainBoostCard(brainBoost) {
    var card = document.createElement('div');
    card.className = 'insight-card';

    var teaser = document.createElement('div');
    teaser.className = 'insight-teaser';

    var teaserTop = document.createElement('div');
    teaserTop.className = 'insight-teaser-top';

    var teaserAvatar = document.createElement('img');
    teaserAvatar.className = 'rickie-avatar-sm';
    teaserAvatar.src = '/static/rickie.svg';
    teaserAvatar.alt = 'Rickie';

    var teaserText = document.createElement('p');
    teaserText.className = 'insight-teaser-text';
    teaserText.textContent = '🦝 Rickie found today\'s Brain Boost...';

    teaserTop.appendChild(teaserAvatar);
    teaserTop.appendChild(teaserText);

    var revealBtn = document.createElement('button');
    revealBtn.className = 'insight-reveal-btn';
    revealBtn.textContent = 'Reveal Brain Boost';

    teaser.appendChild(teaserTop);
    teaser.appendChild(revealBtn);

    var revealed = document.createElement('div');
    revealed.className = 'insight-revealed';
    revealed.hidden = true;
    revealed.appendChild(renderBrainBoostQuestion(brainBoost));

    revealBtn.addEventListener('click', function () {
        teaser.hidden = true;
        revealed.hidden = false;
    });

    card.appendChild(teaser);
    card.appendChild(revealed);

    return card;
}

function renderBrainBoostQuestion(brainBoost) {
    var wrap = document.createElement('div');
    wrap.className = 'bb-question-wrap';

    var qText = document.createElement('p');
    qText.className = 'bb-question-text';
    qText.textContent = brainBoost.question;
    wrap.appendChild(qText);

    var optionsWrap = document.createElement('div');
    optionsWrap.className = 'bb-options';

    var feedback = document.createElement('p');
    feedback.className = 'bb-feedback';
    feedback.hidden = true;

    var explanation = document.createElement('p');
    explanation.className = 'bb-explanation';
    explanation.hidden = true;

    var pointsNote = document.createElement('p');
    pointsNote.className = 'bb-points-note';
    pointsNote.hidden = true;

    function showResult(correct, points, correctIndex, explanationText, selectedIndex) {
        var buttons = optionsWrap.querySelectorAll('.bb-option-btn');
        buttons.forEach(function (btn, i) {
            btn.disabled = true;
            if (i === correctIndex) btn.classList.add('bb-option-correct');
            else if (i === selectedIndex) btn.classList.add('bb-option-incorrect');
        });
        feedback.textContent = correct
            ? '🦝 Rickie\'s impressed — you got it!'
            : '🦝 Not quite — but you still learned something.';
        feedback.className = 'bb-feedback ' + (correct ? 'bb-feedback-correct' : 'bb-feedback-incorrect');
        feedback.hidden = false;

        if (explanationText) {
            explanation.textContent = explanationText;
            explanation.hidden = false;
        }

        pointsNote.textContent = '+' + points + ' points';
        pointsNote.hidden = false;
    }

    brainBoost.options.forEach(function (opt, i) {
        var btn = document.createElement('button');
        btn.className = 'bb-option-btn';
        btn.textContent = opt;
        optionsWrap.appendChild(btn);

        if (brainBoost.answered) {
            btn.disabled = true;
        } else {
            btn.addEventListener('click', async function () {
                var buttons = optionsWrap.querySelectorAll('.bb-option-btn');
                buttons.forEach(function (b) { b.disabled = true; });

                var result = await api('/api/brain-boost/answer', 'POST', { selected_index: i });
                if (!result || result.status !== 200) {
                    buttons.forEach(function (b) { b.disabled = false; });
                    feedback.textContent = "Couldn't save that — try again later.";
                    feedback.className = 'bb-feedback';
                    feedback.hidden = false;
                    return;
                }
                showResult(result.data.correct, result.data.points_earned, result.data.correct_index, result.data.explanation, i);

                var summary = _summarizeProgress(result.data);
                // Brain Boost reactions are Full-only — quiet mode explicitly
                // treats Brain Boost as noise to suppress, not just per-exercise.
                if ((summary.xp > 0 || summary.acorns > 0) && _rickieMode() === 'full') {
                    var poolKey = result.data.correct ? 'brainBoostCorrect' : 'brainBoostIncorrect';
                    showRickieReaction(_pickRickieLine(poolKey), summary);
                    // Only "correct" has a defined expression mapping — incorrect
                    // answers leave the current expression as-is rather than
                    // inventing an unspecified one.
                    if (result.data.correct) {
                        currentRickieExpression = getRickieExpression({ type: 'brain_boost_correct' });
                        _applyRickieExpression();
                    }
                }

                // Brain Boost now awards XP/acorns too, so refresh from /api/me
                // rather than hand-patching a single counter.
                var meResult = await api('/api/me');
                if (meResult && meResult.status === 200) {
                    currentUser = meResult.data;
                    renderJourneyCard();
                    _refreshTeamRickieCard();
                }
            });
        }
    });

    if (brainBoost.answered) {
        showResult(brainBoost.correct, brainBoost.points_earned, brainBoost.correct_index, brainBoost.explanation);
    }

    wrap.appendChild(optionsWrap);
    wrap.appendChild(feedback);
    wrap.appendChild(explanation);
    wrap.appendChild(pointsNote);
    return wrap;
}

var CATEGORY_PILL = {
    upper_body:   'pill-upper-body',
    lower_body:   'pill-lower-body',
    core:         'pill-core',
    mobility:     'pill-mobility',
    conditioning: 'pill-conditioning',
};

// ── Coach modal data ───────────────────────────────────────────────────────────
// Static content for every exercise in the library.
// mistakes: common form errors to correct. tip: one actionable beginner cue.

var COACH_DATA = {
    "wall_push_up": {
        "what": "Push against a wall like you're trying to push it away.",
        "how": [
            "Stand facing a wall, an arm's length away.",
            "Put your hands on the wall at shoulder height.",
            "Bend your elbows and lean your chest toward the wall.",
            "Push back until your arms are straight again."
        ],
        "why": "Makes your arms and chest stronger without needing to get on the floor.",
        "mistakes": [
            "Standing too close to the wall, which makes it too easy.",
            "Letting your back curve instead of staying straight.",
            "Moving too fast instead of slow and controlled."
        ],
        "tip": "Stand a little farther from the wall as it gets easier."
    },
    "knee_push_up": {
        "what": "A push-up you do with your knees on the floor to make it easier.",
        "how": [
            "Get on your hands and knees on the floor.",
            "Keep your back flat, like a tabletop.",
            "Bend your elbows and lower your chest toward the floor.",
            "Push back up until your arms are straight."
        ],
        "why": "Builds arm and chest strength while your knees help take some of the weight.",
        "mistakes": [
            "Letting your hips droop down or stick up in the air.",
            "Only bending halfway instead of going all the way down.",
            "Holding your breath instead of breathing normally."
        ],
        "tip": "Put a towel or pillow under your knees if the floor feels hard."
    },
    "arm_circles": {
        "what": "Make big circles in the air with your arms, like you're swimming.",
        "how": [
            "Stand with your arms stretched out to the sides.",
            "Make small circles forward in the air.",
            "After a while, switch and circle backward.",
            "Keep your arms at shoulder height the whole time."
        ],
        "why": "Warms up your shoulders and helps them move more easily.",
        "mistakes": [
            "Letting your shoulders creep up toward your ears.",
            "Making the circles so big and fast that you lose control.",
            "Dropping your arms below shoulder height."
        ],
        "tip": "Point your thumbs up to the ceiling — it makes the circles easier to control."
    },
    "shoulder_tap": {
        "what": "Hold yourself up like a tabletop and tap your shoulder with one hand.",
        "how": [
            "Get into a push-up position with arms straight.",
            "Keep your hips still and your body steady.",
            "Lift one hand and tap the opposite shoulder.",
            "Put it back down and switch hands."
        ],
        "why": "Helps you stay steady and strong through your whole body.",
        "mistakes": [
            "Letting your hips wiggle side to side with each tap.",
            "Looking up instead of down at the floor.",
            "Putting your feet too wide apart to make it easier."
        ],
        "tip": "Try placing a small object on your lower back — if it falls off, slow down and steady yourself."
    },
    "chest_opener": {
        "what": "Stretch your arms behind you like you're about to give a big hug from behind.",
        "how": [
            "Stand up tall.",
            "Clasp your hands together behind your back.",
            "Gently squeeze your shoulder blades together and lift your chest.",
            "Hold still and breathe for 30 seconds."
        ],
        "why": "Helps loosen up shoulders that get tight from sitting or looking at screens.",
        "mistakes": [
            "Arching your lower back to lift your arms higher.",
            "Holding tension in your neck instead of relaxing.",
            "Letting go after just a couple seconds instead of holding it."
        ],
        "tip": "Pull your shoulders back and down, not up — think 'proud chest,' not 'shrug.'"
    },
    "floor_tricep_dip": {
        "what": "Lower your body up and down using just your arms while sitting on the floor.",
        "how": [
            "Sit on the floor with your knees bent and feet flat.",
            "Put your hands on the floor beside your hips, fingers pointing forward.",
            "Lift your hips up and bend your elbows to lower yourself down.",
            "Push back up until your arms are straight."
        ],
        "why": "Builds strength in the back of your arms.",
        "mistakes": [
            "Letting your shoulders roll forward instead of staying open.",
            "Letting your elbows point out to the sides instead of straight back.",
            "Putting your hands too far away from your hips, which strains your wrists."
        ],
        "tip": "Keep your feet close to your body to make this easier when you're starting out."
    },
    "bodyweight_squat": {
        "what": "Pretend you're sitting down into an invisible chair, then stand back up.",
        "how": [
            "Stand with your feet about shoulder-width apart.",
            "Push your hips back and bend your knees like you're sitting down.",
            "Go down until your thighs are about flat, like a chair seat.",
            "Push through your feet to stand back up."
        ],
        "why": "Makes your legs strong for everyday things like standing up and climbing stairs.",
        "mistakes": [
            "Letting your knees cave in toward each other.",
            "Rising up onto your toes instead of staying flat-footed.",
            "Leaning so far forward that you tip onto your toes."
        ],
        "tip": "Squat down toward a chair without sitting all the way — it helps you find the right depth."
    },
    "reverse_lunge": {
        "what": "Step one foot backward and dip down like you're bowing.",
        "how": [
            "Stand tall with your feet together.",
            "Step one foot backward.",
            "Bend both knees until the back knee almost touches the floor.",
            "Push off your front foot to stand back up and return your feet together."
        ],
        "why": "Builds leg strength and helps you balance better.",
        "mistakes": [
            "Leaning your whole body too far forward.",
            "Letting your front knee go too far past your toes.",
            "Pushing off your back foot instead of your front foot to stand up."
        ],
        "tip": "Pick a spot on the wall to stare at — it helps you stay tall and balanced."
    },
    "glute_bridge": {
        "what": "Lie on your back and lift your hips up like a bridge.",
        "how": [
            "Lie on your back with your knees bent and feet flat on the floor.",
            "Press your feet down and lift your hips toward the ceiling.",
            "Make a straight line from your shoulders to your knees.",
            "Lower back down slowly and repeat."
        ],
        "why": "Strengthens your bottom and lower back muscles, which helps your posture.",
        "mistakes": [
            "Placing your feet too far away from your body.",
            "Only lifting your hips partway up.",
            "Letting your knees fall apart at the top."
        ],
        "tip": "Squeeze your bottom muscles tight for one second at the very top of each lift."
    },
    "calf_raise": {
        "what": "Rise up onto your toes like you're trying to reach something on a high shelf.",
        "how": [
            "Stand with your feet about hip-width apart.",
            "Rise up onto your tiptoes as high as you can.",
            "Pause for a second at the top.",
            "Slowly lower back down."
        ],
        "why": "Strengthens the muscles in the back of your lower legs.",
        "mistakes": [
            "Bouncing quickly instead of pausing at the bottom.",
            "Letting your ankles roll outward.",
            "Using a quick jerky motion instead of slow control."
        ],
        "tip": "Try this standing on the edge of a step for an extra stretch once it feels easy."
    },
    "wall_sit": {
        "what": "Slide down a wall and hold still like you're sitting in an invisible chair.",
        "how": [
            "Stand with your back against a wall.",
            "Slide down until your thighs are flat, like sitting in a chair.",
            "Keep your knees right above your ankles.",
            "Hold still for the time given, then slide back up."
        ],
        "why": "Builds leg strength and teaches your muscles to hold steady.",
        "mistakes": [
            "Not sliding down far enough — aim for a flat seat, not a half-squat.",
            "Pushing down on your knees with your hands.",
            "Letting your feet slide too close to the wall."
        ],
        "tip": "Keep your arms relaxed at your sides or crossed over your chest — no hands on your legs."
    },
    "step_up": {
        "what": "Step up onto a stair like you're climbing, one foot at a time.",
        "how": [
            "Stand facing a sturdy step or stair.",
            "Step up with one foot, placing it fully on the step.",
            "Bring your other foot up to meet it.",
            "Step back down and repeat, then switch which foot leads."
        ],
        "why": "Builds leg strength using a movement you already do every day.",
        "mistakes": [
            "Pushing off your back foot instead of the leg on the step.",
            "Using a step so high it's hard to keep your balance.",
            "Leaning your body far forward instead of staying upright."
        ],
        "tip": "Pause for a second at the top with your leg straight before stepping back down."
    },
    "dead_bug": {
        "what": "Lie on your back and move your arms and legs like a bug stuck on its back.",
        "how": [
            "Lie on your back with your arms pointing at the ceiling and knees bent.",
            "Slowly lower one arm and the opposite leg toward the floor.",
            "Stop just before they touch.",
            "Bring them back up and switch sides."
        ],
        "why": "Helps your middle and your arms and legs learn to work together.",
        "mistakes": [
            "Letting your lower back lift off the floor.",
            "Moving too fast instead of slow and smooth.",
            "Holding your breath instead of breathing normally."
        ],
        "tip": "Press your lower back into the floor and try to keep it there the whole time."
    },
    "bird_dog": {
        "what": "Pretend you're a dog pointing at something.",
        "how": [
            "Get on your hands and knees.",
            "Reach your right arm forward.",
            "Stretch your left leg behind you.",
            "Hold for a second.",
            "Switch sides."
        ],
        "why": "Improves balance and helps strengthen your middle.",
        "mistakes": [
            "Letting one hip twist up higher than the other.",
            "Reaching so high that your back arches.",
            "Rushing instead of holding still for a moment at the top."
        ],
        "tip": "Imagine balancing a cup of water on your back — try not to spill it."
    },
    "knee_plank": {
        "what": "Hold your body still like a stiff board, resting on your knees.",
        "how": [
            "Get on the floor on your forearms and knees.",
            "Make a straight line from your head to your knees.",
            "Keep your tummy muscles tight and breathe steadily.",
            "Hold still for the time given."
        ],
        "why": "Builds strength in your middle, which helps with balance and posture.",
        "mistakes": [
            "Letting your hips poke up high in the air.",
            "Looking up instead of down at the floor.",
            "Letting your back sag toward the floor."
        ],
        "tip": "Picture a broomstick balanced along your back — keep it from rolling off."
    },
    "crunch": {
        "what": "Curl your shoulders up off the floor like a turtle peeking out of its shell.",
        "how": [
            "Lie on your back with your knees bent and feet flat.",
            "Put your hands lightly behind your head or crossed on your chest.",
            "Curl your shoulders up off the floor.",
            "Lower back down slowly and repeat."
        ],
        "why": "Strengthens your belly muscles.",
        "mistakes": [
            "Pulling on your neck with your hands.",
            "Using a fast jerky motion instead of a slow curl.",
            "Lifting your whole back off the floor instead of just your shoulders."
        ],
        "tip": "Picture lifting your shoulder blades just an inch off the floor — you don't need to sit all the way up."
    },
    "bent_knee_leg_raise": {
        "what": "Lie on your back and lower your bent legs toward the floor without touching.",
        "how": [
            "Lie on your back with your knees bent and lifted toward your chest.",
            "Slowly lower your feet toward the floor.",
            "Stop just before your feet touch.",
            "Lift your knees back up and repeat."
        ],
        "why": "Strengthens your lower belly muscles.",
        "mistakes": [
            "Letting your lower back lift off the floor as your legs lower.",
            "Letting your feet touch the floor between reps.",
            "Moving too quickly instead of slow and controlled."
        ],
        "tip": "Keep your lower back pressed flat against the floor the entire time."
    },
    "superman": {
        "what": "Pretend you're Superman flying through the air.",
        "how": [
            "Lie on your stomach.",
            "Lift your arms slightly.",
            "Lift your legs slightly.",
            "Hold for the required time.",
            "Lower and relax."
        ],
        "why": "Helps strengthen your back.",
        "mistakes": [
            "Lifting so high that your neck cranks back uncomfortably.",
            "Holding your breath instead of breathing normally.",
            "Jerking up quickly instead of lifting smoothly."
        ],
        "tip": "Look down at the floor the whole time to keep your neck comfortable."
    },
    "cat_cow": {
        "what": "Move like a cat stretching, then like a cow with its belly sagging.",
        "how": [
            "Get on your hands and knees.",
            "Breathe in and let your belly dip down while you lift your head (the cow).",
            "Breathe out and round your back up toward the ceiling, tucking your chin (the cat).",
            "Keep moving slowly back and forth with your breathing."
        ],
        "why": "Loosens up your back and feels relaxing.",
        "mistakes": [
            "Moving too fast instead of slow and gentle.",
            "Forgetting to breathe along with the movement.",
            "Only moving your head instead of your whole back."
        ],
        "tip": "Imagine a string pulling your belly button up and down with each breath."
    },
    "hip_flexor_kneeling": {
        "what": "Kneel down with one leg forward, like you're about to propose, and lean forward gently.",
        "how": [
            "Kneel on one knee with the other foot flat on the floor in front of you.",
            "Keep your body tall and upright.",
            "Gently shift your weight forward until you feel a stretch in the front of your back hip.",
            "Hold still and breathe, then switch legs."
        ],
        "why": "Loosens up tight hips, which often get stiff from sitting a lot.",
        "mistakes": [
            "Leaning your whole upper body forward instead of just shifting your hips.",
            "Bouncing instead of holding the stretch still.",
            "Forgetting to switch and stretch the other side."
        ],
        "tip": "If your knee on the floor is sore, put a folded towel or pillow under it."
    },
    "standing_hamstring_stretch": {
        "what": "Stretch the back of your leg like you're trying to touch your toes with one foot up high.",
        "how": [
            "Place one foot on a low step or stool.",
            "Keep that leg mostly straight.",
            "Lean forward from your hips with a flat back until you feel a stretch behind your thigh.",
            "Hold still, then switch legs."
        ],
        "why": "Loosens up the back of your legs, which helps you move more freely.",
        "mistakes": [
            "Rounding your back forward instead of keeping it flat.",
            "Bouncing instead of holding the stretch still.",
            "Choosing a surface so high it hurts instead of stretches."
        ],
        "tip": "Only lean forward until you feel a gentle pull — it should never hurt."
    },
    "childs_pose": {
        "what": "Sit back on your heels and stretch forward like you're bowing.",
        "how": [
            "Kneel down and sit back onto your heels.",
            "Stretch your arms forward on the floor.",
            "Rest your forehead down.",
            "Breathe slowly and let your body relax."
        ],
        "why": "Helps you relax and gently stretches your back.",
        "mistakes": [
            "Holding tension in your shoulders instead of letting them relax.",
            "Breathing fast and shallow instead of slow and deep.",
            "Forcing your hips down instead of letting them sink naturally."
        ],
        "tip": "Let your whole body feel heavy, like you're melting into the floor."
    },
    "thoracic_rotation": {
        "what": "Sit cross-legged and twist your upper body like you're looking behind you.",
        "how": [
            "Sit cross-legged with one hand resting behind your head.",
            "Keep your hips facing forward.",
            "Twist your upper body to bring that elbow backward.",
            "Return to the front and switch sides."
        ],
        "why": "Helps your upper back twist and move more easily.",
        "mistakes": [
            "Twisting from your hips instead of your upper back.",
            "Forcing the twist too far instead of going gently.",
            "Forgetting to do both sides evenly."
        ],
        "tip": "Move slowly and only twist as far as feels comfortable."
    },
    "ankle_circles": {
        "what": "Draw circles in the air with your foot, like stirring a pot with your toes.",
        "how": [
            "Lift one foot slightly off the floor.",
            "Slowly draw circles with your foot, like stirring a big pot.",
            "Do all your circles one way, then switch direction.",
            "Put that foot down and switch to the other foot."
        ],
        "why": "Keeps your ankles loose and moving well.",
        "mistakes": [
            "Moving too fast instead of slow and controlled.",
            "Making circles too small to really feel the stretch.",
            "Forgetting to do both directions and both feet."
        ],
        "tip": "Pretend your big toe is a pencil drawing the biggest circle it can."
    },
    "marching_in_place": {
        "what": "March like you're in a parade, lifting your knees high.",
        "how": [
            "Stand up tall.",
            "Lift one knee up to about hip height.",
            "Lower it and lift the other knee.",
            "Swing your arms naturally as you march."
        ],
        "why": "Gets your heart pumping without any jumping or hard moves.",
        "mistakes": [
            "Slouching forward instead of standing tall.",
            "Barely lifting your knees instead of bringing them up high.",
            "Forgetting to swing your arms, which helps you keep rhythm."
        ],
        "tip": "Pretend you're marching to your favorite song to keep a steady beat."
    },
    "jumping_jack": {
        "what": "Jump while spreading your arms and legs out like a starfish, then jump back together.",
        "how": [
            "Stand with your feet together and arms at your sides.",
            "Jump your feet out wide while raising your arms overhead.",
            "Jump your feet back together while lowering your arms.",
            "Keep repeating at a steady pace."
        ],
        "why": "Gets your whole body moving and your heart beating faster.",
        "mistakes": [
            "Landing hard and stiff instead of soft and bent.",
            "Letting your arms and legs move out of sync.",
            "Going so fast that your form falls apart."
        ],
        "tip": "Land softly on the balls of your feet, like a cat."
    },
    "step_touch": {
        "what": "Step side to side like a simple dance move.",
        "how": [
            "Step one foot out to the side.",
            "Bring your other foot in to meet it.",
            "Step back the other direction.",
            "Keep going side to side at a steady pace."
        ],
        "why": "Gets your heart rate up gently, with no jumping needed.",
        "mistakes": [
            "Taking steps so small that it barely counts as exercise.",
            "Looking down at your feet instead of ahead.",
            "Forgetting to swing your arms along with your steps."
        ],
        "tip": "Try clapping your hands together each time your feet meet — it adds rhythm."
    },
    "standing_bicycle": {
        "what": "Stand and twist while lifting your knees, like pedaling a bike standing up.",
        "how": [
            "Stand with your hands gently behind your head.",
            "Lift one knee up while twisting the opposite elbow toward it.",
            "Lower and switch to the other side.",
            "Keep alternating in a smooth rhythm."
        ],
        "why": "Works your middle and gets your heart pumping at the same time.",
        "mistakes": [
            "Pulling hard on your neck with your hands.",
            "Rushing so fast that the twisting motion disappears.",
            "Leaning too far backward instead of staying upright."
        ],
        "tip": "Move slowly at first to get the twist-and-lift feeling right, then speed up."
    },
    "low_skip": {
        "what": "Skip in place gently, like skipping rope without the rope.",
        "how": [
            "Stand with your feet hip-width apart.",
            "Hop lightly from one foot to the other.",
            "Keep the hops small and close to the floor.",
            "Swing your arms naturally as you go."
        ],
        "why": "Gets your heart pumping with gentle, joint-friendly hops.",
        "mistakes": [
            "Jumping too high instead of keeping hops low and light.",
            "Landing flat-footed and heavy instead of soft.",
            "Forgetting to breathe steadily as you go."
        ],
        "tip": "Imagine you're skipping over a low rope just inches off the ground."
    },
    "boxer_shuffle": {
        "what": "Bounce lightly from foot to foot like a boxer warming up.",
        "how": [
            "Stand with your knees slightly bent.",
            "Bounce gently from one foot to the other.",
            "Keep the movement small and quick.",
            "Let your arms move naturally at your sides."
        ],
        "why": "Warms up your whole body and gets your heart beating faster.",
        "mistakes": [
            "Bouncing too high instead of keeping it light and quick.",
            "Locking your knees straight instead of staying slightly bent.",
            "Tensing your shoulders up instead of staying loose."
        ],
        "tip": "Stay light on your feet, like you're standing on hot sand."
    },
    "push_up": {
        "what": "Lower your whole body down and push it back up using just your arms.",
        "how": [
            "Get into a high push-up position with your hands under your shoulders.",
            "Keep your body in a straight line from head to feet.",
            "Bend your elbows to lower your chest almost to the floor.",
            "Push back up until your arms are fully straight."
        ],
        "why": "Builds strength in your arms, chest, and middle all at once.",
        "mistakes": [
            "Letting your hips sag down or poke up.",
            "Only lowering halfway instead of all the way down.",
            "Flaring your elbows straight out to the sides."
        ],
        "tip": "If this is too hard right now, try the knee version first and work up to this."
    },
    "diamond_push_up": {
        "what": "Do a push-up with your hands close together making a diamond shape.",
        "how": [
            "Get into a push-up position with your hands close together under your chest.",
            "Touch your thumbs and index fingers together to form a diamond shape.",
            "Lower your chest down while keeping your elbows close to your sides.",
            "Push back up until your arms are straight."
        ],
        "why": "Builds extra strength in the back of your arms.",
        "mistakes": [
            "Letting your elbows flare out wide instead of staying close.",
            "Letting your hips sag instead of staying in a straight line.",
            "Placing your hands too far forward instead of under your chest."
        ],
        "tip": "Start with the knee version of this if the regular push-up is still tricky."
    },
    "pike_push_up": {
        "what": "Make an upside-down V shape with your body and lower your head toward the floor.",
        "how": [
            "Start with your hips lifted high and hands and feet on the floor, making an upside-down V.",
            "Bend your elbows to lower the top of your head toward the floor.",
            "Pause briefly near the bottom.",
            "Push back up until your arms are straight."
        ],
        "why": "Builds strength in your shoulders.",
        "mistakes": [
            "Letting your hips drop instead of staying lifted high.",
            "Lowering your head too fast instead of with control.",
            "Placing your hands too close to your feet, which can feel cramped."
        ],
        "tip": "Walk your feet a bit closer to your hands to make the V shape steeper and the move easier."
    },
    "decline_push_up": {
        "what": "Do a push-up with your feet up on something higher than your hands.",
        "how": [
            "Place your feet on a sturdy elevated surface like a step or low chair.",
            "Put your hands on the floor, slightly wider than your shoulders.",
            "Lower your chest toward the floor, keeping your body in a straight line.",
            "Push back up until your arms are straight."
        ],
        "why": "Makes a regular push-up harder by shifting more weight to your arms.",
        "mistakes": [
            "Letting your hips sag or rise instead of staying in line.",
            "Choosing a surface so high or unstable that you feel wobbly.",
            "Rushing through reps instead of moving with control."
        ],
        "tip": "Start with a lower surface and work your way up to something taller."
    },
    "wide_push_up": {
        "what": "Do a push-up with your hands spread out wider than usual.",
        "how": [
            "Get into a push-up position with your hands wider than your shoulders.",
            "Keep your body in a straight line from head to feet.",
            "Bend your elbows out to the sides as you lower your chest.",
            "Push back up until your arms are straight."
        ],
        "why": "Works your chest muscles a little differently than a regular push-up.",
        "mistakes": [
            "Spreading your hands so wide it strains your shoulders.",
            "Letting your hips sag toward the floor.",
            "Only lowering partway instead of going all the way down."
        ],
        "tip": "If your shoulders feel uncomfortable, bring your hands in a little closer."
    },
    "sphinx_push_up": {
        "what": "Push up from your forearms to your hands one arm at a time, then lower back down.",
        "how": [
            "Start lying on your forearms like a sphinx statue.",
            "Press into one palm and straighten that arm.",
            "Press into the other palm and straighten that arm too, so you're in a high push-up position.",
            "Lower back down to your forearms one arm at a time, keeping your hips steady."
        ],
        "why": "Builds strength and control through your arms and shoulders.",
        "mistakes": [
            "Letting your hips twist or dip as you push up.",
            "Rushing through instead of moving one arm at a time with control.",
            "Letting your hips sag toward the floor."
        ],
        "tip": "Move slowly and imagine a flat tray balanced on your back that you can't spill."
    },
    "jump_squat": {
        "what": "Squat down low, then jump up high into the air.",
        "how": [
            "Stand with your feet shoulder-width apart.",
            "Bend your knees and lower into a squat.",
            "Push off the floor and jump straight up.",
            "Land softly with bent knees and go straight into the next squat."
        ],
        "why": "Builds powerful legs and gets your heart pumping fast.",
        "mistakes": [
            "Landing stiff-legged instead of bending your knees to land softly.",
            "Not squatting low enough before jumping.",
            "Landing off-balance instead of in a controlled squat."
        ],
        "tip": "Focus on landing as quietly and softly as possible — that means you're doing it right."
    },
    "walking_lunge": {
        "what": "Step forward into a lunge, then bring your back foot through to take the next step.",
        "how": [
            "Step forward with one foot into a lunge.",
            "Lower your back knee toward the floor.",
            "Push through your front foot to stand, then step your back foot forward.",
            "Continue stepping forward, alternating legs."
        ],
        "why": "Builds leg strength while you move across the room instead of staying still.",
        "mistakes": [
            "Letting your front knee travel too far past your toes.",
            "Taking steps too short, which makes the move less effective.",
            "Leaning your upper body too far forward."
        ],
        "tip": "Make sure you have a few steps of open space before you start."
    },
    "single_leg_glute_bridge": {
        "what": "Lift your hips up using just one leg while the other leg stays straight in the air.",
        "how": [
            "Lie on your back with one knee bent and foot flat on the floor.",
            "Stretch your other leg straight out, lifted in the air.",
            "Push through your planted heel to lift your hips up.",
            "Lower back down slowly, then switch legs."
        ],
        "why": "Builds extra strength and balance in your hips and bottom muscles, one side at a time.",
        "mistakes": [
            "Letting your hips tilt sideways instead of staying level.",
            "Pushing through your toes instead of your heel.",
            "Rushing the movement instead of lifting with control."
        ],
        "tip": "Keep your raised leg in line with your body instead of letting it drift."
    },
    "lateral_lunge": {
        "what": "Step out to the side and bend that knee, like you're dodging something.",
        "how": [
            "Stand with your feet together.",
            "Take a big step out to one side.",
            "Bend that knee and push your hips back, keeping the other leg straight.",
            "Push off that foot to return to standing, then switch sides."
        ],
        "why": "Builds strength in your legs in a side-to-side direction, which everyday squats don't cover.",
        "mistakes": [
            "Letting the bent knee cave inward instead of staying over your foot.",
            "Rounding your back instead of staying upright.",
            "Taking a step too small to really feel the stretch and effort."
        ],
        "tip": "Keep your toes pointed forward the whole time, even as you step sideways."
    },
    "sumo_squat": {
        "what": "Squat down with your feet spread wide and toes pointed out, like a sumo wrestler.",
        "how": [
            "Stand with your feet wider than shoulder-width and toes turned out.",
            "Bend your knees and lower into a deep squat.",
            "Keep your chest up and back straight.",
            "Push through your feet to stand back up."
        ],
        "why": "Works your legs and inner thighs in a slightly different way than a regular squat.",
        "mistakes": [
            "Letting your knees cave inward instead of tracking over your toes.",
            "Leaning your chest too far forward.",
            "Not squatting low enough to feel it working."
        ],
        "tip": "Imagine you're trying to keep a beach ball squeezed between your knees the whole time."
    },
    "bodyweight_good_morning": {
        "what": "Bend forward from your hips with a flat back, like you're taking a bow.",
        "how": [
            "Stand with your feet hip-width apart and hands lightly behind your head.",
            "Keep a soft bend in your knees.",
            "Bend forward from your hips, keeping your back flat, until your body is close to parallel with the floor.",
            "Push your hips forward to stand back up."
        ],
        "why": "Strengthens your lower back and the back of your legs.",
        "mistakes": [
            "Rounding your back instead of keeping it flat.",
            "Bending from your waist instead of from your hips.",
            "Standing up too fast instead of with control."
        ],
        "tip": "Pretend you're trying to keep a broomstick flat along your back the whole time."
    },
    "plank": {
        "what": "Hold your body stiff and straight, like a board, propped up on your forearms and toes.",
        "how": [
            "Get into a push-up position but rest on your forearms instead of your hands.",
            "Make a straight line from your head to your heels.",
            "Tighten your tummy muscles and keep breathing.",
            "Hold still for the time given."
        ],
        "why": "Builds strength all through your middle, which helps with posture and balance.",
        "mistakes": [
            "Letting your hips sag down toward the floor.",
            "Letting your hips poke up too high in the air.",
            "Holding your breath instead of breathing steadily."
        ],
        "tip": "Picture a broomstick balanced along your back from head to heels — don't let it roll off."
    },
    "hollow_body_hold": {
        "what": "Lie on your back and lift your arms and legs slightly, making a curved banana shape.",
        "how": [
            "Lie on your back with your arms stretched overhead.",
            "Press your lower back firmly into the floor.",
            "Lift your arms and legs a few inches off the floor.",
            "Hold this curved shape still for the time given."
        ],
        "why": "Builds strength in your belly muscles and teaches your body to stay steady.",
        "mistakes": [
            "Letting your lower back lift off the floor.",
            "Lifting your legs so high that you lose the curve.",
            "Holding your breath instead of breathing steadily."
        ],
        "tip": "If this is too hard, bend your knees and keep your feet closer to the floor."
    },
    "russian_twist": {
        "what": "Sit back slightly and twist side to side, like turning a steering wheel.",
        "how": [
            "Sit on the floor with your knees bent and lean back slightly.",
            "Lift your feet a little off the floor if you can.",
            "Clasp your hands together and twist to touch the floor on one side.",
            "Twist to the other side and keep alternating."
        ],
        "why": "Strengthens the muscles along the sides of your middle.",
        "mistakes": [
            "Rounding your back instead of staying tall while leaning back.",
            "Moving so fast that you lose control of the twist.",
            "Twisting only your arms instead of your whole upper body."
        ],
        "tip": "Keep your feet down on the floor at first if lifting them feels too hard."
    },
    "bicycle_crunch": {
        "what": "Lie on your back and pedal your legs while twisting side to side, like riding an upside-down bike.",
        "how": [
            "Lie on your back with your hands lightly behind your head.",
            "Lift your knees and bring one knee toward your chest.",
            "Twist to bring the opposite elbow toward that knee.",
            "Switch sides in a smooth pedaling motion."
        ],
        "why": "Works your belly muscles from multiple directions at once.",
        "mistakes": [
            "Pulling hard on your neck with your hands.",
            "Moving so fast that the twisting motion disappears.",
            "Letting your lower back arch up off the floor."
        ],
        "tip": "Slow down and focus on really twisting your shoulder toward your knee."
    },
    "straight_leg_raise": {
        "what": "Lie on your back and lift both straight legs up toward the ceiling, then lower them slowly.",
        "how": [
            "Lie on your back with your legs straight.",
            "Lift both legs up until they point toward the ceiling.",
            "Lower them slowly back down.",
            "Stop just before your feet touch the floor, then lift again."
        ],
        "why": "Strengthens your lower belly muscles.",
        "mistakes": [
            "Letting your lower back arch up off the floor.",
            "Lowering your legs too fast instead of slow and controlled.",
            "Letting your feet touch the floor between reps."
        ],
        "tip": "Keep your lower back pressed flat against the floor the whole time."
    },
    "side_plank": {
        "what": "Hold your body stiff and straight on your side, propped up on one forearm.",
        "how": [
            "Lie on your side and prop yourself up on one forearm.",
            "Stack your feet on top of each other.",
            "Lift your hips up so your body forms a straight line.",
            "Hold still, then switch sides."
        ],
        "why": "Strengthens the muscles along the sides of your middle, which helps with balance.",
        "mistakes": [
            "Letting your hips sink down toward the floor.",
            "Letting your top shoulder roll forward.",
            "Forgetting to do both sides evenly."
        ],
        "tip": "Stack your shoulder directly over your elbow before you lift your hips."
    },
    "worlds_greatest_stretch": {
        "what": "Step into a deep lunge and twist your body open, like you're unwrapping a present.",
        "how": [
            "Step forward into a deep lunge.",
            "Place your front hand on the floor next to your front foot.",
            "Twist your other arm up toward the ceiling, opening your chest.",
            "Straighten your front leg for a stretch, then switch sides."
        ],
        "why": "Loosens up your hips, legs, and back all in one smooth move.",
        "mistakes": [
            "Rushing through the steps instead of holding each part for a moment.",
            "Letting your front knee cave inward instead of staying steady.",
            "Forgetting to switch and stretch the other side."
        ],
        "tip": "Move slowly through each part — there's no need to rush a stretch."
    },
    "deep_squat_hold": {
        "what": "Squat down low and just sit there, holding the position.",
        "how": [
            "Stand with your feet shoulder-width apart.",
            "Squat down low, keeping your heels flat on the floor.",
            "Rest your elbows gently against the inside of your knees.",
            "Hold the position, keeping your chest lifted."
        ],
        "why": "Loosens up your hips and ankles, and is a great way to rest between activities.",
        "mistakes": [
            "Letting your heels lift up off the floor.",
            "Rounding your back instead of keeping your chest lifted.",
            "Forcing yourself down too fast instead of easing in gently."
        ],
        "tip": "If your heels keep lifting, try placing a small rolled towel under them."
    },
    "pigeon_pose": {
        "what": "Sit with one leg bent in front of you and the other stretched behind, like a resting pigeon.",
        "how": [
            "Start on your hands and knees.",
            "Bring one knee forward and let that shin rest at an angle in front of you.",
            "Stretch your other leg straight out behind you.",
            "Lower your hips down and hold still, then switch sides."
        ],
        "why": "Loosens up tight hips, which often get stiff from sitting.",
        "mistakes": [
            "Forcing your hips down too fast instead of easing in slowly.",
            "Letting your back leg twist instead of pointing straight back.",
            "Forgetting to switch and stretch the other side."
        ],
        "tip": "Stay higher up on your hands at first — you can sink lower as your hips loosen over time."
    },
    "spinal_twist": {
        "what": "Lie on your back and let your knee fall across your body for a gentle twist.",
        "how": [
            "Lie on your back with your legs straight.",
            "Bring one knee up toward your chest.",
            "Gently guide that knee across your body toward the floor.",
            "Stretch your opposite arm out to the side and hold, then switch sides."
        ],
        "why": "Gently loosens up your back and feels relaxing.",
        "mistakes": [
            "Forcing the knee down too far instead of letting it settle naturally.",
            "Holding your breath instead of breathing slowly.",
            "Forgetting to switch and stretch the other side."
        ],
        "tip": "Let gravity do the work — relax and let your knee sink down on its own."
    },
    "doorway_pec_stretch": {
        "what": "Rest your arms on a doorframe and lean forward gently to stretch your chest.",
        "how": [
            "Stand in a doorway.",
            "Place your forearms on the door frame at shoulder height.",
            "Gently lean your body forward through the doorway.",
            "Hold still until you feel a comfortable stretch."
        ],
        "why": "Loosens up a chest and shoulders that get tight from sitting at a desk all day.",
        "mistakes": [
            "Leaning so far forward that it hurts instead of just stretches.",
            "Holding your breath instead of breathing normally.",
            "Letting your shoulders shrug up toward your ears."
        ],
        "tip": "Ease in slowly — you should feel a gentle pull, never sharp pain."
    },
    "downdog_calf_stretch": {
        "what": "Make an upside-down V shape with your body and pedal your feet to stretch your calves.",
        "how": [
            "Get into an upside-down V shape with your hands and feet on the floor.",
            "Press one heel down toward the floor.",
            "Hold for a couple seconds, then switch to the other foot.",
            "Keep alternating in a gentle pedaling motion."
        ],
        "why": "Loosens up the back of your lower legs.",
        "mistakes": [
            "Bending your knees too much instead of keeping legs mostly straight.",
            "Rushing through instead of holding each side for a moment.",
            "Letting your hips drop down low instead of staying lifted."
        ],
        "tip": "Bend the knee of the leg you're not stretching to make the move more comfortable."
    },
    "no_jump_burpee": {
        "what": "Squat down, kick your feet back, do a push-up, then stand back up — no jumping needed.",
        "how": [
            "Start standing, then place your hands on the floor.",
            "Step your feet back so you're in a push-up position.",
            "Do one push-up, then step your feet forward again.",
            "Stand back up to finish the move."
        ],
        "why": "Works your whole body and gets your heart pumping, without any jumping.",
        "mistakes": [
            "Letting your hips sag during the push-up part.",
            "Rushing so much that your form falls apart.",
            "Skipping the push-up instead of doing a full one."
        ],
        "tip": "Move at your own pace — this works just as well done slowly as done fast."
    },
    "mountain_climber": {
        "what": "Hold a push-up position and drive your knees toward your chest quickly, one at a time.",
        "how": [
            "Get into a push-up position with your arms straight.",
            "Drive one knee toward your chest.",
            "Quickly switch and drive the other knee forward.",
            "Keep alternating at a steady pace."
        ],
        "why": "Gets your heart pumping while also working your arms and middle.",
        "mistakes": [
            "Letting your hips bounce up and down instead of staying level.",
            "Letting your hands drift too far forward.",
            "Going so fast that your form falls apart."
        ],
        "tip": "Slow down if you feel wobbly — a controlled pace still works great."
    },
    "high_knees": {
        "what": "Run in place, lifting your knees up high with each step.",
        "how": [
            "Stand tall with your feet hip-width apart.",
            "Run in place, driving one knee up to hip height.",
            "Quickly switch to the other knee.",
            "Pump your arms and keep a fast, steady rhythm."
        ],
        "why": "Gets your heart pumping fast and works your legs at the same time.",
        "mistakes": [
            "Leaning back too far instead of staying upright.",
            "Barely lifting your knees instead of bringing them up high.",
            "Landing flat-footed and heavy instead of light on your toes."
        ],
        "tip": "Pretend you're running through tall grass and need to lift your knees to clear it."
    },
    "skater_jump": {
        "what": "Hop side to side on one foot at a time, like a speed skater.",
        "how": [
            "Stand on one leg with a slight bend in your knee.",
            "Push off and leap sideways onto your other foot.",
            "Land softly with a slight knee bend.",
            "Push off again and leap back the other way."
        ],
        "why": "Builds balance and leg strength while getting your heart pumping.",
        "mistakes": [
            "Landing stiff-legged instead of bending your knee to absorb the landing.",
            "Looking down at your feet instead of ahead.",
            "Jumping a distance that's too far for your comfort level."
        ],
        "tip": "Start with small hops and make them bigger only once you feel steady."
    },
    "plank_to_downdog": {
        "what": "Move between a flat board position and an upside-down V shape.",
        "how": [
            "Start in a push-up position with your arms straight.",
            "Push your hips up and back into an upside-down V shape.",
            "Hold for a moment.",
            "Flow back forward into the straight board position."
        ],
        "why": "Gets your heart rate up gently while also stretching your body.",
        "mistakes": [
            "Rushing through the movement instead of flowing smoothly.",
            "Letting your hips sag in the board position.",
            "Forgetting to breathe along with the movement."
        ],
        "tip": "Match each movement to a breath — push up and back as you breathe out."
    },
    "speed_squat": {
        "what": "Do squats as quickly as you can while still doing them correctly.",
        "how": [
            "Stand with your feet shoulder-width apart.",
            "Squat down quickly until your thighs are about flat.",
            "Stand back up quickly to full height.",
            "Repeat right away at a fast pace."
        ],
        "why": "Builds leg strength and gets your heart pumping at the same time.",
        "mistakes": [
            "Going so fast that you stop squatting low enough.",
            "Letting your knees cave inward to keep up the pace.",
            "Forgetting to fully stand up straight between reps."
        ],
        "tip": "If your form starts slipping, slow down — good squats beat fast sloppy ones."
    },
    "archer_push_up": {
        "what": "Do a push-up while leaning your weight to one side, like pulling back a bow and arrow.",
        "how": [
            "Get into a push-up position with your hands wider than usual.",
            "Lower your chest toward one hand while keeping your other arm straight out to the side.",
            "Push back up to the starting position.",
            "Repeat, leaning toward the other hand next time."
        ],
        "why": "Builds a lot of strength in one arm at a time, preparing you for harder moves.",
        "mistakes": [
            "Letting your hips sag instead of staying in a straight line.",
            "Not lowering far enough toward the working hand.",
            "Rushing through instead of controlling the lower and push."
        ],
        "tip": "Make sure you can already do regular push-ups comfortably before trying this one."
    },
    "pseudo_planche_push_up": {
        "what": "Do a push-up with your hands turned backward and your shoulders leaning way forward.",
        "how": [
            "Place your hands facing backward at about hip level.",
            "Lean your shoulders forward, past your wrists.",
            "Bend your elbows to lower your chest while keeping that forward lean.",
            "Push back up, keeping the lean the whole time."
        ],
        "why": "Builds serious strength in your shoulders and arms.",
        "mistakes": [
            "Losing the forward lean partway through the move.",
            "Going too fast instead of staying slow and controlled.",
            "Trying this before you're ready for it — it needs strong wrists."
        ],
        "tip": "Practice the lean by itself, without lowering down, until it feels stable."
    },
    "typewriter_push_up": {
        "what": "Lower into a wide push-up, then shift your weight side to side like an old typewriter.",
        "how": [
            "Lower into the bottom of a wide push-up.",
            "Shift your weight across to one hand.",
            "Push up on that arm while keeping the other arm straight.",
            "Shift back to the other side and repeat."
        ],
        "why": "Builds strength and control in each arm individually.",
        "mistakes": [
            "Letting your hips sag during the shift.",
            "Rushing the side-to-side shift instead of moving with control.",
            "Not lowering deep enough at the start."
        ],
        "tip": "Move slowly — the sideways shift is the hardest part, so don't rush it."
    },
    "plyometric_push_up": {
        "what": "Push up so hard and fast that your hands leave the floor.",
        "how": [
            "Get into a push-up position.",
            "Lower your chest toward the floor.",
            "Push up explosively so your hands lift off the ground.",
            "Land softly with bent elbows and lower right into the next rep."
        ],
        "why": "Builds power and speed in your arms and chest.",
        "mistakes": [
            "Landing stiff-armed instead of with soft, bent elbows.",
            "Not pushing hard enough for your hands to actually leave the floor.",
            "Rushing into the next rep without controlling the landing first."
        ],
        "tip": "Make sure regular push-ups feel easy before adding this explosive version."
    },
    "wall_handstand_hold": {
        "what": "Kick up into a handstand against a wall and hold still, upside down.",
        "how": [
            "Start facing away from a wall in a kneeling position.",
            "Kick one leg up and walk your hands back until your feet rest against the wall.",
            "Stack your wrists, elbows, and shoulders in a straight line.",
            "Hold the position, pressing the floor away with your hands."
        ],
        "why": "Builds serious shoulder and arm strength, plus a fun new skill.",
        "mistakes": [
            "Letting your back arch instead of staying straight.",
            "Holding your breath instead of breathing steadily.",
            "Trying this without a spotter or safe space the first few times."
        ],
        "tip": "Practice near a wall with someone nearby until you feel confident and steady."
    },
    "assisted_one_arm_push_up": {
        "what": "Do a push-up on one hand while your other hand rests lightly on something low for support.",
        "how": [
            "Get into a push-up position with one hand on the floor and the other resting on a low support, like a book.",
            "Keep your body square and facing the floor.",
            "Lower with control on your main arm.",
            "Push back up, then switch arms."
        ],
        "why": "Builds toward doing a full push-up on just one arm.",
        "mistakes": [
            "Letting your body twist to one side instead of staying square.",
            "Relying too much on the support hand instead of your working arm.",
            "Rushing through instead of lowering with control."
        ],
        "tip": "Use a taller support at first, and a lower one as you get stronger."
    },
    "assisted_pistol_squat": {
        "what": "Squat all the way down on one leg while holding something for balance.",
        "how": [
            "Hold onto something sturdy for balance, like a doorframe.",
            "Stand on one leg with the other leg stretched out in front of you.",
            "Slowly squat down as low as you can on the standing leg.",
            "Push back up to standing, then switch legs."
        ],
        "why": "Builds serious strength and balance in one leg at a time.",
        "mistakes": [
            "Letting your standing knee cave inward.",
            "Rushing down instead of lowering with control.",
            "Relying too heavily on your hands instead of your leg."
        ],
        "tip": "Hold something sturdy at first, and use less and less support as you get stronger."
    },
    "plyometric_lunge": {
        "what": "Lunge down, then jump and switch your legs in the air.",
        "how": [
            "Step into a lunge with one foot forward.",
            "Jump up, switching your legs in mid-air.",
            "Land softly in a lunge with the opposite leg forward.",
            "Immediately continue into the next jump."
        ],
        "why": "Builds powerful legs and gets your heart pumping fast.",
        "mistakes": [
            "Landing stiff-legged instead of bending your knees to absorb impact.",
            "Losing your balance because the jump is too big.",
            "Forgetting to land softly with control before the next jump."
        ],
        "tip": "Start with small, slow jumps and build up speed once you feel steady."
    },
    "single_leg_good_morning": {
        "what": "Stand on one leg and bend forward, lifting your other leg straight out behind you.",
        "how": [
            "Stand on one leg with a slight bend in the knee.",
            "Put your hands lightly behind your head.",
            "Bend forward from your hips while your other leg lifts straight out behind you.",
            "Push back up to standing, then switch legs."
        ],
        "why": "Builds balance along with strength in your legs and lower back.",
        "mistakes": [
            "Rounding your back instead of keeping it flat.",
            "Wobbling out of control instead of moving slowly.",
            "Letting your standing knee lock out stiffly."
        ],
        "tip": "Practice next to a wall or chair you can lightly touch if you lose your balance."
    },
    "shrimp_squat": {
        "what": "Squat all the way down on one leg while holding your other foot behind you.",
        "how": [
            "Stand on one leg and hold your other foot up behind you with your hand.",
            "Slowly lower your back knee toward the floor.",
            "Keep your balance on the standing leg the whole way down.",
            "Push back up to standing, then switch legs."
        ],
        "why": "Builds a huge amount of strength and balance in one leg at a time.",
        "mistakes": [
            "Rushing down instead of lowering with full control.",
            "Letting your standing knee cave inward.",
            "Losing your grip on your back foot, which throws off your balance."
        ],
        "tip": "Try this near a wall at first in case you need a hand to steady yourself."
    },
    "broad_jump": {
        "what": "Jump forward as far as you can, then land softly.",
        "how": [
            "Stand with your feet shoulder-width apart.",
            "Swing your arms back and bend your knees.",
            "Explode forward, jumping as far as you can.",
            "Land softly with bent knees, sinking into a squat."
        ],
        "why": "Builds explosive leg power.",
        "mistakes": [
            "Landing stiff-legged instead of bending your knees to absorb the landing.",
            "Looking down instead of ahead while jumping.",
            "Not leaving enough open space to land safely."
        ],
        "tip": "Make sure you have a long, clear, soft space in front of you before jumping."
    },
    "sprint_intervals": {
        "what": "Run as fast as you possibly can for a short burst, then rest, and repeat.",
        "how": [
            "Pick a safe, open space to run.",
            "Sprint at your fastest possible speed for 20 seconds.",
            "Slow down and rest for 10 seconds.",
            "Repeat the sprint-and-rest pattern for all the rounds."
        ],
        "why": "Builds speed and gets your heart and lungs working hard.",
        "mistakes": [
            "Not actually resting during the rest periods.",
            "Starting too fast and running out of energy partway through.",
            "Sprinting on an uneven or unsafe surface."
        ],
        "tip": "Pick a flat, clear path ahead of time so you can focus on running, not watching your feet."
    },
    "straddle_v_up": {
        "what": "Lie on your back with legs spread wide, then sit up and reach for your toes.",
        "how": [
            "Lie on your back with your arms stretched overhead and legs spread wide.",
            "Lift your upper body and legs up at the same time.",
            "Reach your hands toward your feet at the top.",
            "Lower back down slowly and with control."
        ],
        "why": "Builds a lot of strength in your belly muscles.",
        "mistakes": [
            "Using a fast jerky motion to sit up instead of a controlled lift.",
            "Rushing the lowering part instead of taking your time.",
            "Letting your legs come together instead of staying spread."
        ],
        "tip": "Lower down slowly, taking about three seconds — that's where most of the work happens."
    },
    "l_sit_hold": {
        "what": "Sit on the floor and push your whole body up off the ground with just your hands.",
        "how": [
            "Sit on the floor with your legs stretched out in front of you.",
            "Place your hands flat on the floor beside your hips.",
            "Press down hard to lift your whole body off the floor.",
            "Hold the position with your legs out straight."
        ],
        "why": "Builds incredible strength in your arms and belly muscles.",
        "mistakes": [
            "Letting your shoulders shrug up toward your ears.",
            "Bending your knees when you don't need to yet.",
            "Holding your breath instead of breathing steadily."
        ],
        "tip": "If holding your legs straight is too hard right now, tuck your knees in instead."
    },
    "pike_walk_out": {
        "what": "Walk your hands down to the floor until you're in a stiff board shape, then walk back up.",
        "how": [
            "Stand with your feet hip-width apart.",
            "Walk your hands down your legs and onto the floor.",
            "Walk your hands forward until your body forms a straight line, like a board.",
            "Walk your hands back toward your feet and stand back up."
        ],
        "why": "Builds strength in your belly muscles and shoulders together.",
        "mistakes": [
            "Letting your hips sag once you reach the straight position.",
            "Rushing through instead of walking your hands slowly.",
            "Bending your knees a lot to make it easier instead of keeping them straighter."
        ],
        "tip": "Take small steps with your hands — there's no rush to get all the way out."
    },
    "tuck_to_straight_leg_raise": {
        "what": "Lie on your back, pull your knees in, then straighten your legs up high.",
        "how": [
            "Lie on your back with your arms stretched overhead, pressing into the floor.",
            "Pull your knees in toward your chest.",
            "Straighten your legs up toward the ceiling.",
            "Lower your straight legs slowly, stopping just above the floor."
        ],
        "why": "Builds strength in your lower belly muscles.",
        "mistakes": [
            "Letting your lower back lift off the floor.",
            "Lowering your legs too fast instead of slow and controlled.",
            "Letting your legs touch the floor between reps."
        ],
        "tip": "Keep your lower back pressed flat against the floor the entire time."
    },
    "hollow_body_rock": {
        "what": "Hold the curved banana shape and gently rock forward and backward.",
        "how": [
            "Lie on your back and lift into the curved hollow shape, arms and legs raised.",
            "Gently rock your body forward.",
            "Gently rock back the other way.",
            "Keep rocking smoothly without losing the curved shape."
        ],
        "why": "Builds strength and control in your belly muscles.",
        "mistakes": [
            "Letting your back arch instead of staying curved.",
            "Rocking so hard that you lose the shape completely.",
            "Holding your breath instead of breathing steadily."
        ],
        "tip": "If you feel your back arching, that's the signal to stop and reset the curve."
    },
    "planche_lean": {
        "what": "Hold a stiff board position on straight arms and lean your body forward over your hands.",
        "how": [
            "Start in a straight-arm board position.",
            "Slowly shift your weight forward over your wrists.",
            "Keep your whole body rigid and straight.",
            "Hold the forward lean, then ease back to start."
        ],
        "why": "Builds a huge amount of strength in your shoulders and wrists.",
        "mistakes": [
            "Letting your hips sag instead of staying in a straight line.",
            "Leaning so far forward that your wrists feel strained.",
            "Rushing into a big lean before you're ready for it."
        ],
        "tip": "Start with a small lean and only go further forward as it feels comfortable."
    },
    "pancake_stretch": {
        "what": "Sit with your legs spread wide and fold forward, flat like a pancake.",
        "how": [
            "Sit on the floor with your legs spread wide apart.",
            "Keep your back flat.",
            "Lean forward from your hips, walking your hands along the floor.",
            "Relax and breathe deeply into the stretch."
        ],
        "why": "Loosens up your inner legs and hips.",
        "mistakes": [
            "Forcing yourself down too far instead of easing in gently.",
            "Holding your breath instead of breathing slowly.",
            "Rounding your back instead of keeping it flat."
        ],
        "tip": "Never force this stretch — let your breathing relax you deeper over time."
    },
    "bodyweight_jefferson_curl": {
        "what": "Stand tall and slowly curl down one piece of your back at a time, like a wave.",
        "how": [
            "Stand with your feet together.",
            "Starting from your head, curl your chin to your chest.",
            "Keep curling down through your upper back, then your lower back, until you're hanging forward with your arms relaxed.",
            "Slowly uncurl back up, starting from your lower back."
        ],
        "why": "Loosens up your whole back, one section at a time.",
        "mistakes": [
            "Rushing through instead of moving slowly, piece by piece.",
            "Bending your knees a lot instead of keeping them mostly straight.",
            "Bouncing at the bottom instead of hanging relaxed."
        ],
        "tip": "Take about five slow seconds to curl down and five more to come back up."
    },
    "front_split_prep": {
        "what": "Kneel in a low lunge and slide your front foot forward to stretch toward a split.",
        "how": [
            "Kneel down in a low lunge with one foot forward.",
            "Use your hands on the floor for support.",
            "Slowly slide your front foot further forward.",
            "Sink down as far as feels comfortable and hold."
        ],
        "why": "Loosens up your hips and legs over time.",
        "mistakes": [
            "Sliding forward too fast instead of slow and gradual.",
            "Forcing yourself into pain instead of stopping at a gentle stretch.",
            "Forgetting to switch and stretch the other side."
        ],
        "tip": "This takes weeks or months to improve — a tiny bit of progress each time is enough."
    },
    "cossack_squat": {
        "what": "Stand wide and squat down on one leg while the other leg stays straight out to the side.",
        "how": [
            "Stand in a wide stance with your toes pointed slightly out.",
            "Shift your weight onto one leg.",
            "Squat down deep on that leg while your other leg stays straight out to the side.",
            "Push back up to the middle, then switch sides."
        ],
        "why": "Loosens up your hips and builds leg strength at the same time.",
        "mistakes": [
            "Letting your heel lift off the floor on the squatting leg.",
            "Rushing the shift from side to side.",
            "Leaning your upper body too far forward."
        ],
        "tip": "Hold something sturdy nearby at first if your balance feels shaky."
    },
    "shoulder_cars": {
        "what": "Move your arm through every direction it can go, like drawing a giant circle with your whole shoulder.",
        "how": [
            "Stand tall and hold one arm firmly against your side.",
            "With your other arm, slowly lift it forward and up overhead.",
            "Continue the circle by sweeping the arm behind your body.",
            "Complete the circle back to the start, then switch arms."
        ],
        "why": "Keeps your shoulders moving well through their full range.",
        "mistakes": [
            "Moving too fast instead of slow and controlled.",
            "Letting your back arch to help the arm move further.",
            "Skipping parts of the circle instead of completing the whole range."
        ],
        "tip": "Move as slowly as you can — the slower you go, the more it helps your shoulder."
    },
    "wrist_prep": {
        "what": "Move your wrists and fingers through different stretches to wake them up.",
        "how": [
            "Get on your hands and knees.",
            "Make slow circles with your wrists in both directions.",
            "Spread and curl your fingers slowly several times.",
            "Gently lean your weight forward and back over your hands to stretch them."
        ],
        "why": "Gets your wrists ready for moves that put extra weight on your hands.",
        "mistakes": [
            "Skipping this before hand-balancing moves like handstands.",
            "Moving too fast instead of slow and gentle.",
            "Pushing through pain instead of stopping at a comfortable stretch."
        ],
        "tip": "Spend a couple of minutes here before any move that leans hard on your hands."
    },
    "full_burpee": {
        "what": "Squat down, kick back to a board shape, do a push-up, jump your feet in, then jump up high.",
        "how": [
            "Start standing, then squat down and place your hands on the floor.",
            "Kick your feet back into a push-up position and do one push-up.",
            "Jump your feet back up to your hands.",
            "Explode upward into a jump with your arms overhead."
        ],
        "why": "Works your entire body and gets your heart pumping hard.",
        "mistakes": [
            "Letting your hips sag during the push-up part.",
            "Skipping the push-up to go faster.",
            "Landing the jump stiff-legged instead of bending your knees."
        ],
        "tip": "Slow down and do each part fully — a slower complete burpee beats a fast sloppy one."
    },
    "tabata_mountain_climber": {
        "what": "Do mountain climbers as fast as you can for short bursts, with quick rests in between.",
        "how": [
            "Get into a push-up position with your arms straight.",
            "Drive your knees toward your chest as fast as you can, alternating legs.",
            "Go at top speed for 20 seconds.",
            "Rest for exactly 10 seconds, then repeat for all the rounds."
        ],
        "why": "Pushes your heart and lungs hard in short, intense bursts.",
        "mistakes": [
            "Letting your hips bounce up and down instead of staying level.",
            "Skipping the rest periods instead of using them to recover.",
            "Letting your form fall apart for the sake of speed."
        ],
        "tip": "It's okay to slow down slightly if your form starts slipping — staying steady matters more than raw speed."
    },
    "tuck_jump": {
        "what": "Jump up high and pull both knees up to your chest in the air.",
        "how": [
            "Stand with your feet hip-width apart.",
            "Dip down into a small squat.",
            "Explode upward, pulling both knees toward your chest at the top.",
            "Land softly with bent knees and reset for the next jump."
        ],
        "why": "Builds explosive power in your legs.",
        "mistakes": [
            "Landing stiff-legged instead of bending your knees to absorb impact.",
            "Not pulling your knees high enough to really feel the move.",
            "Rushing into the next jump before fully landing and resetting."
        ],
        "tip": "Focus on landing soft and quiet — that's the sign you're controlling the move well."
    },
    "tuck_jump_burpee": {
        "what": "Do a burpee, but instead of just jumping up, pull your knees to your chest at the top.",
        "how": [
            "Start standing, then place your hands on the floor and kick back to a board shape.",
            "Do one push-up, then jump your feet forward.",
            "Explode upward, pulling both knees to your chest at the peak of the jump.",
            "Land softly with bent knees and flow right into the next rep."
        ],
        "why": "Combines a full-body move with an explosive jump for extra heart-pumping effort.",
        "mistakes": [
            "Letting your hips sag during the push-up part.",
            "Landing the jump stiff-legged instead of with bent knees.",
            "Rushing so much that the push-up gets skipped or sloppy."
        ],
        "tip": "Make sure you're comfortable with a full burpee and a tuck jump separately before combining them."
    },
    "broad_jump_consecutive": {
        "what": "Do several big jumps forward in a row without stopping in between.",
        "how": [
            "Stand with your feet shoulder-width apart.",
            "Swing your arms back, bend your knees, and jump forward as far as you can.",
            "Land with bent knees and immediately load into the next jump.",
            "Repeat for the number of jumps in a row."
        ],
        "why": "Builds explosive leg power and teaches your body to recover quickly between efforts.",
        "mistakes": [
            "Pausing too long between jumps instead of flowing into the next one.",
            "Landing stiff-legged instead of bending your knees.",
            "Not leaving enough open space to land safely for every jump."
        ],
        "tip": "Pick a long, clear stretch of space before you start so you're not cut short mid-jump."
    },
    "shuttle_run": {
        "what": "Sprint to a marker, touch it, then sprint back, over and over.",
        "how": [
            "Set up two markers a set distance apart.",
            "Sprint from one marker to the other and touch it.",
            "Sprint back to the starting marker and touch it.",
            "Keep repeating for the number of lengths in a round, then rest before the next round."
        ],
        "why": "Builds speed along with the ability to change direction quickly.",
        "mistakes": [
            "Slowing down too early instead of sprinting through each marker.",
            "Turning too sharply without slowing down a little first, which risks a slip.",
            "Skipping the rest between rounds instead of using it to recover."
        ],
        "tip": "Practice on a surface with good grip so quick turns feel safe."
    }
};

function renderDailyExercise(ex) {
    var row = document.createElement('div');
    row.className = 'daily-exercise-row' + (ex.completed ? ' daily-exercise-done' : '');

    // ── Exercise info (name + reps + how-to toggle) ──────────────────────────────
    var info = document.createElement('div');
    info.className = 'daily-exercise-info';

    var name = document.createElement('p');
    name.className = 'daily-exercise-name';
    name.textContent = ex.name; // textContent — never innerHTML

    var meta = document.createElement('span');
    meta.className = 'daily-exercise-meta';
    meta.textContent = ex.reps_or_duration;

    var howBtn = document.createElement('button');
    howBtn.className = 'btn-how-to';
    howBtn.textContent = 'How to do this';
    howBtn.setAttribute('aria-expanded', 'false');

    var coachLink = document.createElement('button');
    coachLink.className = 'btn-coach-link';
    coachLink.textContent = 'Exercise Tips';
    coachLink.addEventListener('click', function () { openExerciseModal(ex); });

    var linkRow = document.createElement('div');
    linkRow.className = 'exercise-link-row';
    linkRow.appendChild(howBtn);
    linkRow.appendChild(coachLink);

    info.appendChild(name);
    info.appendChild(meta);
    info.appendChild(linkRow);

    // ── Category pill ──────────────────────────────────────────────────────────────────────
    var cat = document.createElement('span');
    var pillClass = CATEGORY_PILL[ex.category] || '';
    cat.className = 'daily-category-pill ' + pillClass;
    cat.textContent = ex.category.replace(/_/g, ' ');

    // ── "I did this" / Done button ───────────────────────────────────────────────────────────────
    var btn = document.createElement('button');
    if (ex.completed) {
        btn.textContent = '✓';
        btn.className   = 'btn-daily-done';
        btn.disabled    = true;
    } else {
        btn.textContent = 'I did this';
        btn.className   = 'btn-daily-complete';
        btn.onclick     = function () { handleCompleteExercise(ex.key, btn, row); };
    }

    // ── Expandable instructions ──────────────────────────────────────────────────────────────────
    var instrPanel = document.createElement('div');
    instrPanel.className = 'exercise-instructions';
    instrPanel.hidden = true;

    if (ex.image_url) {
        var img = document.createElement('img');
        img.src = ex.image_url;
        img.alt = ex.name;
        img.className = 'exercise-illustration';
        img.onerror = function () { this.style.display = 'none'; };
        instrPanel.appendChild(img);
    }

    var instrText = document.createElement('p');
    instrText.textContent = ex.instructions; // textContent — never innerHTML
    instrPanel.appendChild(instrText);

    howBtn.onclick = function () {
        var opening = instrPanel.hidden;
        instrPanel.hidden = !opening;
        howBtn.textContent = opening ? 'Hide' : 'How to do this';
        howBtn.setAttribute('aria-expanded', String(opening));
    };

    row.appendChild(info);
    row.appendChild(cat);
    row.appendChild(btn);
    row.appendChild(instrPanel); // wraps to full width via flex-wrap
    return row;
}

async function handleCompleteExercise(key, btn, row) {
    btn.disabled    = true;
    btn.textContent = '✓';

    if (row) row.classList.add('completing');

    if (isGuest) {
        guestCompleted.add(key);
        setTimeout(function () { loadDailyExercises(); }, 480);
        return;
    }

    // Captured before the request: whether this user has never completed a
    // mission before, so a genuine first-ever completion gets its own line
    // rather than the generic pool.
    var wasFirstMissionEver = !!(currentUser && currentUser.total_missions === 0);

    var result = await api('/api/daily/' + key + '/complete', 'POST');
    if (!result) return;

    if (result.status === 200) {
        var summary = _summarizeProgress(result.data);
        var isMilestoneMoment = result.data.completed_count === 5 || summary.leveledUp;
        var allowsReaction = _rickieAllowsReaction(isMilestoneMoment);
        if (summary.xp > 0 || summary.acorns > 0) {
            if (allowsReaction) {
                var poolKey = 'missionComplete';
                var wasFirstMission = result.data.completed_count === 5 && wasFirstMissionEver;
                var wasPerfectMission = result.data.completed_count === 5;
                if (wasFirstMission) {
                    poolKey = 'firstMission';
                } else if (wasPerfectMission) {
                    poolKey = 'perfectMission';
                }
                showRickieReaction(_pickRickieLine(poolKey), summary);
                currentRickieExpression = getRickieExpression({
                    type: 'mission_complete',
                    firstMissionEver: wasFirstMission,
                    leveledUp: summary.leveledUp,
                    perfectMission: wasPerfectMission
                });
            } else {
                currentRickieExpression = 'neutral';
            }
            _applyRickieExpression();
        }
        _applyCampfireUpdates(result.data.team_campfire_updates);
        // Let the flash animation play, then reload
        setTimeout(function () { loadDailyExercises(); }, 480);
    } else {
        if (row) row.classList.remove('completing');
        btn.disabled    = false;
        btn.textContent = 'I did this';
    }
}

async function handleSkillLevelChange(value) {
    var result = await api('/api/me', 'PATCH', { skill_level: value });
    if (!result) return;
    // Reload either way — on failure, reverts the select to server value
    await loadDailyExercises();
}

async function loadChallenges() {
    var result = await api('/api/challenges');
    if (!result) return;

    var list = document.getElementById('challenges-list');
    list.innerHTML = ''; // safe: content added via createElement below

    if (result.status !== 200) return;

    var challenges = result.data;

    if (challenges.length === 0) {
        var empty = document.createElement('div');
        empty.className = 'empty-state';

        var icon = document.createElement('div');
        icon.className = 'empty-icon';
        icon.textContent = '🏆'; // 🏆

        var title = document.createElement('p');
        title.className = 'empty-title';
        title.textContent = 'No side quests yet';

        var sub = document.createElement('p');
        sub.className = 'empty-sub';
        sub.textContent = 'Add a daily habit you want to track — reading, meditation, cold showers.';

        empty.appendChild(icon);
        empty.appendChild(title);
        empty.appendChild(sub);
        list.appendChild(empty);
        return;
    }

    challenges.forEach(function (c) {
        list.appendChild(renderChallenge(c));
    });
}

function renderChallenge(c) {
    var today     = new Date().toISOString().slice(0, 10);
    var yesterday = new Date(Date.now() - 864e5).toISOString().slice(0, 10);
    var alreadyDone = c.last_check_in === today;
    var atRisk      = !alreadyDone && c.last_check_in === yesterday && c.current_streak > 0;

    var card = document.createElement('div');
    card.className = 'challenge-card';
    if (alreadyDone)          card.classList.add('done-today');
    else if (atRisk)          card.classList.add('at-risk');
    else if (c.current_streak > 0) card.classList.add('has-streak');

    var info = document.createElement('div');
    info.className = 'challenge-info';

    var title = document.createElement('p');
    title.className = 'challenge-title';
    title.textContent = (c.current_streak > 0 ? '🔥 ' : '') + c.title; // 🔥

    var streakRow = document.createElement('div');
    streakRow.className = 'streak-row';

    var current = document.createElement('span');
    current.className = 'streak-current';
    current.textContent = c.current_streak + ' day' + (c.current_streak !== 1 ? 's' : '');

    var best = document.createElement('span');
    best.textContent = 'Best: ' + c.longest_streak;

    var lastIn = document.createElement('span');
    if (atRisk) {
        lastIn.textContent = '⚠️ Streak at risk';
        lastIn.style.color = '#dc2626';
    } else {
        lastIn.textContent = c.last_check_in ? 'Last: ' + c.last_check_in : 'Not started';
    }

    streakRow.appendChild(current);
    streakRow.appendChild(best);
    streakRow.appendChild(lastIn);
    info.appendChild(title);
    info.appendChild(streakRow);

    var btn = document.createElement('button');
    if (alreadyDone) {
        btn.textContent = '✓ Done today';
        btn.className   = 'btn-done';
        btn.disabled    = true;
    } else {
        btn.textContent = 'Check In';
        btn.className   = 'btn-primary';
        btn.onclick     = function () { handleCheckIn(c.id, btn); };
    }

    card.appendChild(info);
    card.appendChild(btn);
    return card;
}

async function handleCreateChallenge(event) {
    event.preventDefault();
    setError('create-error', '');

    var titleInput = document.getElementById('challenge-title');
    var title = titleInput.value.trim();
    if (!title) return;

    var result = await api('/api/challenges', 'POST', { title: title });
    if (!result) return;

    if (result.status === 201) {
        titleInput.value = '';
        await loadChallenges();
    } else {
        setError('create-error', result.data.error || 'Could not create challenge.');
    }
}

async function handleCheckIn(id, btn) {
    btn.disabled    = true;
    btn.textContent = '...';

    var result = await api('/api/challenges/' + id + '/checkin', 'POST');
    if (!result) return;

    if (result.status === 200) {
        await loadChallenges();
    } else {
        btn.disabled    = false;
        btn.textContent = 'Check In';
    }
}

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
    if (localStorage.getItem('streakfit_token')) {
        await loadDashboard();
        if (localStorage.getItem('streakfit_token')) {
            showView('dashboard');
        }
    } else {
        showView('auth');
    }
}

document.addEventListener('DOMContentLoaded', init);

// ── Exercise Coach Modal ──────────────────────────────────────────────────────

var _exModalOverlay = null;

function openExerciseModal(ex) {
    if (!_exModalOverlay) {
        _exModalOverlay = document.createElement('div');
        _exModalOverlay.className = 'ex-modal-overlay';
        _exModalOverlay.setAttribute('role', 'dialog');
        _exModalOverlay.setAttribute('aria-modal', 'true');
        _exModalOverlay.addEventListener('click', function (e) {
            if (e.target === _exModalOverlay) closeExerciseModal();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && _exModalOverlay.classList.contains('open')) {
                closeExerciseModal();
            }
        });
        document.body.appendChild(_exModalOverlay);
    }

    // Rebuild content each open
    _exModalOverlay.innerHTML = '';
    _exModalOverlay.setAttribute('aria-labelledby', 'ex-modal-name');

    var modal = document.createElement('div');
    modal.className = 'ex-modal';

    var closeBtn = document.createElement('button');
    closeBtn.className = 'ex-modal-close';
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.textContent = '✕';
    closeBtn.addEventListener('click', closeExerciseModal);

    if (ex.image_url) {
        var imgEl = document.createElement('img');
        imgEl.className = 'ex-modal-img';
        imgEl.src = ex.image_url;
        imgEl.alt = ex.name;
        imgEl.onerror = function () { this.style.display = 'none'; };
        modal.appendChild(imgEl);
    }

    var nameEl = document.createElement('h2');
    nameEl.className = 'ex-modal-name';
    nameEl.id = 'ex-modal-name';
    nameEl.textContent = ex.name;

    modal.appendChild(closeBtn);
    modal.appendChild(nameEl);

    var data = COACH_DATA[ex.key];
    if (data) {
        var whatSection = document.createElement('div');
        whatSection.className = 'ex-modal-section';

        var whatTitle = document.createElement('h3');
        whatTitle.className = 'ex-modal-section-title';
        whatTitle.textContent = 'What this is';

        var whatText = document.createElement('p');
        whatText.className = 'ex-modal-text';
        whatText.textContent = data.what;

        whatSection.appendChild(whatTitle);
        whatSection.appendChild(whatText);
        modal.appendChild(whatSection);

        var howSection = document.createElement('div');
        howSection.className = 'ex-modal-section';

        var howTitle = document.createElement('h3');
        howTitle.className = 'ex-modal-section-title';
        howTitle.textContent = 'How to do it';

        var howList = document.createElement('ol');
        howList.className = 'ex-modal-list';
        data.how.forEach(function (step) {
            var li = document.createElement('li');
            li.textContent = step;
            howList.appendChild(li);
        });

        howSection.appendChild(howTitle);
        howSection.appendChild(howList);
        modal.appendChild(howSection);

        var whySection = document.createElement('div');
        whySection.className = 'ex-modal-section';

        var whyTitle = document.createElement('h3');
        whyTitle.className = 'ex-modal-section-title';
        whyTitle.textContent = 'Why it matters';

        var whyText = document.createElement('p');
        whyText.className = 'ex-modal-text';
        whyText.textContent = data.why;

        whySection.appendChild(whyTitle);
        whySection.appendChild(whyText);
        modal.appendChild(whySection);

        var mistakesSection = document.createElement('div');
        mistakesSection.className = 'ex-modal-section';

        var mistakesTitle = document.createElement('h3');
        mistakesTitle.className = 'ex-modal-section-title';
        mistakesTitle.textContent = 'Common mistakes';

        var mistakesList = document.createElement('ul');
        mistakesList.className = 'ex-modal-list';
        data.mistakes.forEach(function (m) {
            var li = document.createElement('li');
            li.textContent = m;
            mistakesList.appendChild(li);
        });

        mistakesSection.appendChild(mistakesTitle);
        mistakesSection.appendChild(mistakesList);
        modal.appendChild(mistakesSection);

        var tipSection = document.createElement('div');
        tipSection.className = 'ex-modal-section';

        var tipTitle = document.createElement('h3');
        tipTitle.className = 'ex-modal-section-title';
        tipTitle.textContent = 'Beginner tip';

        var tipText = document.createElement('p');
        tipText.className = 'ex-modal-tip';
        tipText.textContent = data.tip;

        tipSection.appendChild(tipTitle);
        tipSection.appendChild(tipText);
        modal.appendChild(tipSection);
    }

    _exModalOverlay.appendChild(modal);
    _exModalOverlay.classList.add('open');
    document.body.style.overflow = 'hidden';
    closeBtn.focus();
}

function closeExerciseModal() {
    if (_exModalOverlay) {
        _exModalOverlay.classList.remove('open');
        document.body.style.overflow = '';
    }
}

// ── Coach panel ───────────────────────────────────────────────────────────────

var _coachPanel   = null;
var _coachThread  = null;
var _coachInput   = null;
var _coachSendBtn = null;

// ── Rickie mood (UI-only — derived from existing currentUser data, never sent to the backend) ──

var RICKIE_MOODS = {
    neutral:     { emoji: '🦝', title: 'Ready when you are' },
    'warming-up': { emoji: '🔥', title: 'Streak warming up' },
    'fired-up':   { emoji: '⚡', title: 'Streak is on fire' },
    celebrating: { emoji: '🎉', title: 'Milestone day!' }
};

function getRickieMood() {
    var streak = (currentUser && currentUser.current_streak) || 0;
    if ([7, 14, 30, 100].indexOf(streak) !== -1) return 'celebrating';
    if (streak >= 7) return 'fired-up';
    if (streak >= 1) return 'warming-up';
    return 'neutral';
}

function _updateRickieMoodBadge() {
    var badge = document.getElementById('coach-mood-badge');
    if (!badge) return;
    if (_rickieMode() === 'minimal') {
        badge.hidden = true;
        return;
    }
    badge.hidden = false;
    var mood = RICKIE_MOODS[getRickieMood()];
    badge.textContent = mood.emoji;
    badge.title = mood.title;
    badge.className = 'coach-mood-badge coach-mood-' + getRickieMood();
}

function openCoach(context) {
    if (!_coachPanel) {
        _coachPanel = document.createElement('section');
        _coachPanel.className = 'card coach-panel';

        var header = document.createElement('div');
        header.className = 'coach-header';

        var headerLeft = document.createElement('div');
        headerLeft.className = 'coach-header-left';

        var avatar = document.createElement('img');
        avatar.className = 'coach-avatar';
        avatar.src = '/static/rickie.svg';
        avatar.alt = 'Rickie';

        var title = document.createElement('p');
        title.className = 'coach-title';
        title.textContent = 'Rickie';

        var moodBadge = document.createElement('span');
        moodBadge.id = 'coach-mood-badge';
        moodBadge.className = 'coach-mood-badge';

        headerLeft.appendChild(avatar);
        headerLeft.appendChild(title);
        headerLeft.appendChild(moodBadge);

        var closeBtn = document.createElement('button');
        closeBtn.className = 'coach-close';
        closeBtn.textContent = 'Close';
        closeBtn.addEventListener('click', function () {
            _coachPanel.hidden = true;
        });

        header.appendChild(headerLeft);
        header.appendChild(closeBtn);

        _coachThread = document.createElement('div');
        _coachThread.className = 'coach-thread';

        var inputRow = document.createElement('div');
        inputRow.className = 'coach-input-row';

        _coachInput = document.createElement('input');
        _coachInput.type = 'text';
        _coachInput.className = 'coach-input';
        _coachInput.placeholder = 'Ask Rickie…';
        _coachInput.maxLength = 500;
        _coachInput.setAttribute('autocomplete', 'off');
        _coachInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') _submitCoachMessage();
        });

        _coachSendBtn = document.createElement('button');
        _coachSendBtn.className = 'btn-primary coach-send';
        _coachSendBtn.textContent = 'Ask';
        _coachSendBtn.addEventListener('click', _submitCoachMessage);

        inputRow.appendChild(_coachInput);
        inputRow.appendChild(_coachSendBtn);

        _coachPanel.appendChild(header);
        _coachPanel.appendChild(_coachThread);
        _coachPanel.appendChild(inputRow);

        var sideQuests = document.querySelector('.side-quests-section');
        sideQuests.parentNode.insertBefore(_coachPanel, sideQuests);
    }

    _coachPanel.hidden = false;
    _updateRickieMoodBadge();
    _applyRickieExpression();
    _coachPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });

    if (context && context.type === 'insight') {
        _sendCoachMessage('Tell me more about today’s insight', context);
    } else {
        _coachInput.focus();
    }
}

function _submitCoachMessage() {
    var msg = _coachInput.value.trim();
    if (!msg) return;
    _coachInput.value = '';
    _sendCoachMessage(msg, { type: 'general' });
}

async function _sendCoachMessage(message, context) {
    _coachInput.disabled   = true;
    _coachSendBtn.disabled = true;

    var isAutoTrigger = (context && context.type === 'insight');
    if (!isAutoTrigger) {
        _appendCoachMsg('user', message);
    }

    var loadingEl = _appendCoachMsg('coach', '…');

    var result = await api('/api/coach', 'POST', { message: message, context: context });

    _coachThread.removeChild(loadingEl);
    _coachInput.disabled   = false;
    _coachSendBtn.disabled = false;

    if (!result || result.status === 0) {
        _appendCoachMsg('coach', '🦝 Rickie stepped away from his burrow for a bit — try again in a little while.');
    } else if (result.status === 429) {
        _appendCoachMsg('coach', 'You’ve reached today’s question limit — come back tomorrow.');
    } else if (result.status !== 200) {
        _appendCoachMsg('coach', '🦝 Rickie stepped away from his burrow for a bit — try again in a little while.');
    } else {
        _appendCoachMsg('coach', result.data.reply);
    }

    _coachInput.focus();
}

function _appendCoachMsg(role, text) {
    var wrap = document.createElement('div');
    wrap.className = 'coach-msg coach-msg-' + role;

    var lines = text.split('\n').filter(function (line) { return line.trim() !== ''; });
    if (lines.length === 0) lines = [text];

    lines.forEach(function (line) {
        var p = document.createElement('p');
        p.className = 'coach-msg-line';
        p.textContent = line; // textContent — never innerHTML
        wrap.appendChild(p);
    });

    _coachThread.appendChild(wrap);
    _coachThread.scrollTop = _coachThread.scrollHeight;
    return wrap;
}
