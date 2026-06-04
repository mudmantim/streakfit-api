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
    ['login-error', 'register-error', 'create-error'].forEach(function(id) {
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
    await loadDailyExercises();
    await loadChallenges();
}

async function loadDailyExercises() {
    var result = await api('/api/daily');
    if (!result) return;

    var list     = document.getElementById('daily-exercises-list');
    var progress = document.getElementById('daily-progress');
    list.innerHTML = ''; // safe: cleared here, content added via createElement below

    if (result.status !== 200) return;

    var daily = result.data;

    var select = document.getElementById('skill-level-select');
    if (select) select.value = daily.skill_level;

    daily.exercises.forEach(function(ex) {
        list.appendChild(renderDailyExercise(ex));
    });

    progress.textContent = daily.completed_count + ' / 5 completed';
}

function renderDailyExercise(ex) {
    var row = document.createElement('div');
    row.className = 'daily-exercise-row' + (ex.completed ? ' daily-exercise-done' : '');

    var info = document.createElement('div');
    info.className = 'daily-exercise-info';

    var name = document.createElement('p');
    name.className = 'daily-exercise-name';
    name.textContent = ex.name; // textContent — never innerHTML

    var meta = document.createElement('span');
    meta.className = 'daily-exercise-meta';
    meta.textContent = ex.reps_or_duration;

    info.appendChild(name);
    info.appendChild(meta);

    var cat = document.createElement('span');
    cat.className = 'daily-category-pill';
    cat.textContent = ex.category.replace(/_/g, ' ');

    var btn = document.createElement('button');
    if (ex.completed) {
        btn.textContent = '✓';
        btn.className   = 'btn-daily-done';
        btn.disabled    = true;
    } else {
        btn.textContent = 'Mark Done';
        btn.className   = 'btn-daily-complete';
        btn.onclick     = function() { handleCompleteExercise(ex.key, btn); };
    }

    row.appendChild(info);
    row.appendChild(cat);
    row.appendChild(btn);
    return row;
}

async function handleCompleteExercise(key, btn) {
    btn.disabled    = true;
    btn.textContent = '...';

    var result = await api('/api/daily/' + key + '/complete', 'POST');
    if (!result) return;

    if (result.status === 200) {
        await loadDailyExercises();
    } else {
        btn.disabled    = false;
        btn.textContent = 'Mark Done';
    }
}

async function handleSkillLevelChange(value) {
    var result = await api('/api/me', 'PATCH', { skill_level: value });
    if (!result) return;

    if (result.status === 200) {
        await loadDailyExercises();
    } else {
        // Revert the select to the previous server value by reloading
        await loadDailyExercises();
    }
}

async function loadChallenges() {
    var result = await api('/api/challenges');
    if (!result) return;

    var list  = document.getElementById('challenges-list');
    var empty = document.getElementById('no-challenges');

    list.innerHTML = ''; // safe: only cleared here, content added via createElement below

    if (result.status !== 200) return;

    var challenges = result.data;
    empty.hidden = challenges.length > 0;
    challenges.forEach(function(c) {
        list.appendChild(renderChallenge(c));
    });
}

function renderChallenge(c) {
    var today      = new Date().toISOString().slice(0, 10);
    var alreadyDone = c.last_check_in === today;

    var card = document.createElement('div');
    card.className = 'challenge-card';

    var info = document.createElement('div');
    info.className = 'challenge-info';

    var title = document.createElement('p');
    title.className = 'challenge-title';
    title.textContent = c.title; // textContent — never innerHTML

    var streakRow = document.createElement('div');
    streakRow.className = 'streak-row';

    var current = document.createElement('span');
    current.className = 'streak-current';
    current.textContent = c.current_streak + ' day' + (c.current_streak !== 1 ? 's' : '');

    var best = document.createElement('span');
    best.textContent = 'Best: ' + c.longest_streak;

    var lastIn = document.createElement('span');
    lastIn.textContent = c.last_check_in ? 'Last: ' + c.last_check_in : 'Not started';

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
        btn.onclick     = function() { handleCheckIn(c.id, btn); };
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
