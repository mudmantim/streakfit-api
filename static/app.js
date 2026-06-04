// ── Service Worker ────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(function () {
        // Registration failed — app still works without PWA features
    });
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
    applyTheme(result.data.display_mode);
    // Sync skill-level select in case it differs from /api/daily response
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
    spinner.innerHTML = '<div class="spinner"></div><p>Today\'s 5 is loading…</p>';
    list.appendChild(spinner);

    var result = await api('/api/daily');
    if (!result) return;

    list.innerHTML = ''; // clear spinner
    if (result.status !== 200) return;

    var daily = result.data;

    // Sync the skill-level select
    var select = document.getElementById('skill-level-select');
    if (select) select.value = daily.skill_level;

    // Populate mission date
    var dateEl = document.getElementById('daily-mission-date');
    if (dateEl) {
        var d = new Date();
        dateEl.textContent = d.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
    }

    // Populate streak badge (hidden when streak is 0)
    var streakBadge = document.getElementById('daily-streak-badge');
    if (streakBadge) {
        var streak = daily.daily5_streak || 0;
        if (streak > 0) {
            streakBadge.textContent = '🔥 ' + streak + (streak === 1 ? ' day' : ' days');
            streakBadge.hidden = false;
        } else {
            streakBadge.hidden = true;
        }
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

    // All-complete banner — copy is driven by milestone or current streak count
    if (daily.completed_count === 5) {
        var bannerStreak = daily.daily5_streak || 0;

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
        bannerSub.textContent = 'Come back tomorrow to keep it alive.';

        bannerText.appendChild(bannerTitle);
        bannerText.appendChild(bannerSub);
        banner.appendChild(bannerEmoji);
        banner.appendChild(bannerText);
        list.appendChild(banner);
    }

    // Render exercises
    daily.exercises.forEach(function (ex) {
        list.appendChild(renderDailyExercise(ex));
    });

    // Progress text
    var progressText = document.getElementById('daily-progress-text');
    if (progressText) {
        if (daily.completed_count === 5) {
            progressText.textContent = '5 \u2F 5 complete 🔥';
        } else {
            progressText.textContent = daily.completed_count + ' \u2F 5 completed';
        }
    }
}

// ── Streak milestone copy ─────────────────────────────────────────────────────

var STREAK_MILESTONES = {
    1:   'Day 1. The streak starts here.',
    3:   '3 days in a row. You\'re building something.',
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
        title.textContent = 'Create your first personal challenge';

        var sub = document.createElement('p');
        sub.className = 'empty-sub';
        sub.textContent = 'Track any habit — workouts, reading, cold showers.';

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
