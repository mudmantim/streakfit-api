// ── Service Worker ────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(function () {
        // Registration failed — app still works without PWA features
    });
}

// ── User state ────────────────────────────────────────────────────────────────
// Set by loadUserPreferences() on every dashboard load. Streak data lives here
// rather than in the /api/daily response.
var currentUser = null;

// ── Guest state ───────────────────────────────────────────────────────────────
// In-memory only — intentionally resets on page refresh.
var isGuest = false;
var guestCompleted = new Set();

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
    applyTheme('game');
    setGuestUI(true);
    loadDailyExercises();
    showView('dashboard');
}

function handleExitGuest() {
    isGuest = false;
    guestCompleted = new Set();
    setGuestUI(false);
    clearErrors();
    showTab('login');
    showView('auth');
}

function setGuestUI(guest) {
    var skillSel = document.getElementById('skill-level-select');
    if (skillSel) skillSel.hidden = guest;

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
}

async function loadUserPreferences() {
    var result = await api('/api/me');
    if (!result || result.status !== 200) return;
    currentUser = result.data;
    applyTheme(result.data.display_mode);
    var sel = document.getElementById('skill-level-select');
    if (sel && result.data.skill_level) sel.value = result.data.skill_level;
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

async function loadDailyExercises() {
    var list = document.getElementById('daily-exercises-list');
    list.innerHTML = '';

    // Show spinner while loading
    var spinner = document.createElement('div');
    spinner.className = 'state-loading';
    spinner.innerHTML = '<div class="spinner"></div><p>Today\'s Mission is loading…</p>';
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

    // Sync the skill-level select
    var select = document.getElementById('skill-level-select');
    if (select) select.value = daily.skill_level;

    // Populate mission subtitle: date · skill level · Refreshes tomorrow
    var dateEl = document.getElementById('daily-mission-date');
    if (dateEl) {
        var d = new Date();
        var dateStr = d.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
        var level = daily.skill_level.charAt(0).toUpperCase() + daily.skill_level.slice(1);
        dateEl.textContent = dateStr + ' · ' + level + ' · Refreshes tomorrow';
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

        var rEmoji = document.createElement('p');
        rEmoji.className = 'rise-again-emoji';
        rEmoji.textContent = '🌅';

        var rTitle = document.createElement('p');
        rTitle.className = 'rise-again-title';
        rTitle.textContent = 'Rise Again';

        var rLine1 = document.createElement('p');
        rLine1.className = 'rise-again-body';
        rLine1.textContent = 'You came back.';

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
            list.appendChild(renderGuestCompleteBanner());
        } else {
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

        if (daily.insight) {
            list.appendChild(renderInsightCard(daily.insight));
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

    var category = document.createElement('p');
    category.className = 'insight-category';
    category.textContent = insight.category;

    var text = document.createElement('p');
    text.className = 'insight-text';
    text.textContent = insight.text;

    card.appendChild(category);
    card.appendChild(text);

    if (!isGuest) {
        var tellMore = document.createElement('button');
        tellMore.className = 'insight-tell-more';
        tellMore.textContent = 'Tell me more →';
        tellMore.addEventListener('click', function () {
            openCoach({ type: 'insight', insight_text: insight.text, insight_category: insight.category });
        });
        card.appendChild(tellMore);
    }

    return card;
}

var CATEGORY_PILL = {
    upper_body:   'pill-upper-body',
    lower_body:   'pill-lower-body',
    core:         'pill-core',
    mobility:     'pill-mobility',
    conditioning: 'pill-conditioning',
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

    info.appendChild(name);
    info.appendChild(meta);
    info.appendChild(howBtn);

    // ── Category pill ──────────────────────────────────────────────────────────────────────
    var cat = document.createElement('span');
    var pillClass = CATEGORY_PILL[ex.category] || '';
    cat.className = 'daily-category-pill ' + pillClass;
    cat.textContent = ex.category.replace(/_/g, ' ');

    // ── Mark Done / Done button ──────────────────────────────────────────────────────────────────
    var btn = document.createElement('button');
    if (ex.completed) {
        btn.textContent = '✓';
        btn.className   = 'btn-daily-done';
        btn.disabled    = true;
    } else {
        btn.textContent = 'Mark Done';
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

    var result = await api('/api/daily/' + key + '/complete', 'POST');
    if (!result) return;

    if (result.status === 200) {
        // Let the flash animation play, then reload
        setTimeout(function () { loadDailyExercises(); }, 480);
    } else {
        if (row) row.classList.remove('completing');
        btn.disabled    = false;
        btn.textContent = 'Mark Done';
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

// ── Coach panel ───────────────────────────────────────────────────────────────

var _coachPanel   = null;
var _coachThread  = null;
var _coachInput   = null;
var _coachSendBtn = null;

function openCoach(context) {
    if (!_coachPanel) {
        _coachPanel = document.createElement('section');
        _coachPanel.className = 'card coach-panel';

        var header = document.createElement('div');
        header.className = 'coach-header';

        var title = document.createElement('p');
        title.className = 'coach-title';
        title.textContent = 'Coach';

        var closeBtn = document.createElement('button');
        closeBtn.className = 'coach-close';
        closeBtn.textContent = 'Close';
        closeBtn.addEventListener('click', function () {
            _coachPanel.hidden = true;
        });

        header.appendChild(title);
        header.appendChild(closeBtn);

        _coachThread = document.createElement('div');
        _coachThread.className = 'coach-thread';

        var inputRow = document.createElement('div');
        inputRow.className = 'coach-input-row';

        _coachInput = document.createElement('input');
        _coachInput.type = 'text';
        _coachInput.className = 'coach-input';
        _coachInput.placeholder = 'Ask about StreakFit…';
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
        _appendCoachMsg('coach', 'Coach isn’t available right now — try again later.');
    } else if (result.status === 429) {
        _appendCoachMsg('coach', 'You’ve reached today’s question limit — come back tomorrow.');
    } else if (result.status !== 200) {
        _appendCoachMsg('coach', 'Coach isn’t available right now — try again later.');
    } else {
        _appendCoachMsg('coach', result.data.reply);
    }

    _coachInput.focus();
}

function _appendCoachMsg(role, text) {
    var el = document.createElement('p');
    el.className = 'coach-msg coach-msg-' + role;
    el.textContent = text;
    _coachThread.appendChild(el);
    _coachThread.scrollTop = _coachThread.scrollHeight;
    return el;
}
