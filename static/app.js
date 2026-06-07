// ── Service Worker ────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(function () {
        // Registration failed — app still works without PWA features
    });
}

// ── PWA Install Prompt ────────────────────────────────────────────────────────
var _deferredInstallPrompt = null;

window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    _deferredInstallPrompt = e;
    // Show banner after the user reaches the dashboard (not on the auth screen)
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

// ── Retention prompts ─────────────────────────────────────────────────────────
// Called once per dashboard session. Shows install banner and/or notification
// ask. Never shown to guests. Each prompt stores a localStorage flag after
// being acted on so it never appears again.

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
    if (_isStandalone()) {
        // Already installed — skip install, go straight to notification ask
        _maybeShowNotificationBanner();
        return;
    }
    _maybeShowInstallBanner();
    _maybeShowNotificationBanner();
}

// ── Install banner ────────────────────────────────────────────────────────────

function _maybeShowInstallBanner() {
    if (localStorage.getItem('sf_install_dismissed')) return;

    if (_deferredInstallPrompt) {
        _showAndroidInstallBanner();
    } else if (_isIOSSafari()) {
        _showIOSInstallBanner();
    }
    // On other browsers with no prompt: do nothing
}

function _buildInstallBannerShell(container) {
    var banner = document.createElement('div');
    banner.className = 'retention-banner';
    banner.id = 'install-banner';

    var dismiss = document.createElement('button');
    dismiss.className = 'retention-dismiss';
    dismiss.setAttribute('aria-label', 'Dismiss');
    dismiss.textContent = '×';
    dismiss.onclick = function () {
        localStorage.setItem('sf_install_dismissed', '1');
        fireEvent('install_prompt_dismissed');
        banner.remove();
    };
    banner.appendChild(dismiss);
    container.insertBefore(banner, container.firstChild);
    return banner;
}

function _showAndroidInstallBanner() {
    var container = _getOrCreatePromptsContainer();
    var banner = _buildInstallBannerShell(container);

    var text = document.createElement('p');
    text.className = 'retention-text';
    text.textContent = 'Add StreakFit to your home screen for quick access to your daily mission.';

    var installBtn = document.createElement('button');
    installBtn.className = 'retention-btn';
    installBtn.textContent = 'Add to Home Screen';
    installBtn.onclick = function () {
        if (!_deferredInstallPrompt) {
            // Prompt already used once — browser consumed it; explain state
            installBtn.textContent = 'Open browser menu to install';
            installBtn.disabled = true;
            return;
        }
        installBtn.disabled = true;
        fireEvent('install_prompt_accepted');
        var captured = _deferredInstallPrompt;
        _deferredInstallPrompt = null; // browsers only allow one call
        captured.prompt();
        captured.userChoice.then(function (choice) {
            if (choice.outcome === 'accepted') {
                // User accepted — dismiss permanently
                localStorage.setItem('sf_install_dismissed', '1');
                banner.remove();
            } else {
                // User dismissed the OS dialog — keep banner, re-enable button
                installBtn.textContent = 'Add to Home Screen';
                installBtn.disabled = false;
                // Browsers don't re-fire beforeinstallprompt after a dismiss;
                // guide user to browser menu instead
                text.textContent = 'To install, use your browser menu (⋮ → Add to Home Screen).';
            }
        }).catch(function () {
            installBtn.textContent = 'Add to Home Screen';
            installBtn.disabled = false;
        });
    };

    banner.appendChild(text);
    banner.appendChild(installBtn);
    fireEvent('install_prompt_shown');
}

function _showIOSInstallBanner() {
    var container = _getOrCreatePromptsContainer();
    var banner = _buildInstallBannerShell(container);

    var text = document.createElement('p');
    text.className = 'retention-text';
    text.innerHTML = 'Add StreakFit to your home screen: tap <strong>Share ↑</strong>, then <strong>Add to Home Screen</strong>.';

    banner.appendChild(text);
    fireEvent('install_prompt_shown');
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
    checkRetentionPrompts();
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

// ── Coach modal data ───────────────────────────────────────────────────────────
// Static content for every exercise in the library.
// mistakes: common form errors to correct. tip: one actionable beginner cue.

var COACH_DATA = {
    // Beginner / Upper Body
    wall_push_up: {
        mistakes: [
            'Letting the hips sag or push back instead of staying in a straight line.',
            'Placing the hands too high — they should be at shoulder height, not head height.',
            'Bouncing off the wall with momentum instead of pressing with control.',
        ],
        tip: 'Walk your feet further from the wall to increase the angle and difficulty over time.',
    },
    knee_push_up: {
        mistakes: [
            'Hips sagging so the back curves into a banana shape.',
            'Flaring the elbows out to 90 degrees — keep them closer to 45 degrees.',
            'Stopping at a partial range rather than lowering the chest to the floor.',
        ],
        tip: 'Place a folded towel under your knees if the floor is uncomfortable.',
    },
    arm_circles: {
        mistakes: [
            'Allowing the shoulders to shrug up toward the ears.',
            'Making circles too large and fast, which reduces shoulder activation.',
            'Letting the arms drop below shoulder height mid-set.',
        ],
        tip: 'Keeping your thumbs pointing upward throughout the circle helps engage the rotator cuff.',
    },
    shoulder_tap: {
        mistakes: [
            'Letting the hips twist and sway with each tap.',
            'Looking up or forward instead of down at the floor, which misaligns the spine.',
            'Widening the hand position to compensate for instability.',
        ],
        tip: 'Place a water bottle on your lower back — if it falls, your hips are rotating too much.',
    },
    chest_opener: {
        mistakes: [
            'Arching the lower back to force the arms higher rather than opening the chest.',
            'Holding tension in the neck or jaw instead of relaxing into the stretch.',
            'Releasing after only a few seconds — 30 seconds is needed for a lasting effect.',
        ],
        tip: 'Your shoulders should pull directly back, not lift up. Watch in a mirror if you can.',
    },
    floor_tricep_dip: {
        mistakes: [
            'Allowing the shoulders to roll forward rather than staying open and back.',
            'Bending the elbows outward instead of pointing them straight back.',
            'Placing the hands too far from the hips, which shifts strain to the wrists.',
        ],
        tip: 'Keep your feet flat and close to your body to shorten the range and make the start easier.',
    },
    // Beginner / Lower Body
    bodyweight_squat: {
        mistakes: [
            'Letting the knees cave inward as you lower — push them out over your toes.',
            'Rising onto the toes because the ankles lack mobility.',
            'Leaning so far forward that the weight shifts off the heels.',
        ],
        tip: 'Squat toward a chair to build confidence before doing unsupported reps.',
    },
    reverse_lunge: {
        mistakes: [
            'Leaning too far forward with the torso instead of keeping it upright.',
            'Letting the front knee track past the toes instead of over the ankle.',
            'Pushing off the back foot rather than the front heel to return.',
        ],
        tip: 'Fix your gaze on a spot straight ahead — it keeps your torso tall.',
    },
    glute_bridge: {
        mistakes: [
            'Placing the feet too far from the body, shifting work to the hamstrings.',
            'Only lifting partway — hips should form a straight line from shoulder to knee.',
            'Letting the knees fall apart at the top instead of staying hip-width.',
        ],
        tip: 'Squeeze your glutes at the top for a full second before lowering — it activates more fibers.',
    },
    calf_raise: {
        mistakes: [
            'Bouncing at the bottom rather than pausing and fully stretching the calf.',
            'Rolling the ankles outward to cheat extra range.',
            'Using momentum to rise rather than controlled muscular effort.',
        ],
        tip: 'Performing these single-leg from a stair edge adds a full stretch and dramatically increases difficulty.',
    },
    wall_sit: {
        mistakes: [
            'Thighs not parallel to the floor — sit at exactly 90 degrees.',
            'Pushing through the hands on the knees, which takes load off the legs.',
            'Sliding the feet toward the wall, reducing the joint angle.',
        ],
        tip: 'Keep arms relaxed at your sides or folded across your chest — no hands on thighs.',
    },
    step_up: {
        mistakes: [
            'Pushing off the back foot instead of driving entirely through the stepping leg.',
            'Using a step so high the thigh exceeds parallel.',
            'Leaning forward from the hip instead of staying tall.',
        ],
        tip: 'Pause for one second at the top with your leg straight to confirm full hip extension.',
    },
    // Beginner / Core
    dead_bug: {
        mistakes: [
            'Allowing the lower back to arch away from the floor as the limbs extend.',
            'Moving too quickly — the movement should be slow and deliberate.',
            'Holding the breath instead of exhaling as you extend each limb.',
        ],
        tip: 'Press your lower back firmly into the floor before each rep and maintain that contact throughout.',
    },
    bird_dog: {
        mistakes: [
            'Hiking the hip of the extending leg instead of keeping the pelvis level.',
            'Lifting the arm and leg too high, forcing the lower back to arch.',
            'Rushing the transition — pause briefly at full extension to build stability.',
        ],
        tip: 'Imagine balancing a cup of water on your lower back — keep it perfectly still.',
    },
    knee_plank: {
        mistakes: [
            'Hips rising too high into a downward-dog shape, reducing core demand.',
            'Looking up rather than down at the floor, which hyperextends the neck.',
            'Holding the breath — breathe normally throughout the hold.',
        ],
        tip: 'Draw your belly button toward your spine to actively engage the deep core muscles.',
    },
    crunch: {
        mistakes: [
            'Pulling on the neck with interlaced fingers instead of lightly supporting the head.',
            'Using momentum to swing up rather than contracting the abs.',
            'Not fully lowering between reps — shoulders should approach but not fully settle on the floor.',
        ],
        tip: 'Place your tongue on the roof of your mouth to help reduce neck tension during the movement.',
    },
    bent_knee_leg_raise: {
        mistakes: [
            'Allowing the lower back to arch off the floor as the feet lower.',
            'Dropping the feet too fast, removing the eccentric challenge.',
            'Using hip flexors alone rather than keeping the core braced throughout.',
        ],
        tip: 'Stop the descent the moment you feel your lower back begin to lift from the floor.',
    },
    superman: {
        mistakes: [
            'Jerking the limbs up with momentum rather than lifting slowly with control.',
            'Straining the neck by looking up — keep your gaze toward the floor.',
            'Only lifting the arms while forgetting to raise the legs, or vice versa.',
        ],
        tip: 'Squeeze your glutes and upper back simultaneously as you lift for maximum activation.',
    },
    // Beginner / Mobility
    cat_cow: {
        mistakes: [
            'Moving only the lower back instead of mobilizing the entire spine.',
            'Forcing the range with muscular tension instead of letting breath drive it.',
            'Rushing through cycles — each transition should take at least 2–3 seconds.',
        ],
        tip: 'Sync each movement with your breath: exhale into Cat, inhale into Cow.',
    },
    hip_flexor_kneeling: {
        mistakes: [
            'Allowing the lower back to hyperextend — keep a neutral spine throughout.',
            'Shifting the front knee forward past the toes.',
            'Not shifting the hips forward enough to actually feel the hip flexor stretch.',
        ],
        tip: 'Tuck your tailbone slightly under as you shift forward to deepen the stretch.',
    },
    standing_hamstring_stretch: {
        mistakes: [
            'Rounding the spine forward to reach the surface instead of hinging from the hip.',
            'Locking the knee — keep a very slight bend to protect the joint.',
            'Forcing the range aggressively — the stretch should be firm, never painful.',
        ],
        tip: 'If your hamstrings are very tight, start with the foot on a low surface like a step.',
    },
    childs_pose: {
        mistakes: [
            'Sitting so far forward that the hips lift off the heels, reducing the stretch.',
            'Holding tension in the shoulders instead of lengthening the arms forward.',
            'Breathing shallowly — deep belly breaths allow the hips to sink further.',
        ],
        tip: 'Widen your knees slightly if your belly prevents a comfortable forward fold.',
    },
    thoracic_rotation: {
        mistakes: [
            'Rotating from the lower back and hips rather than isolating the upper spine.',
            'Bringing the elbow forward instead of backward — the rotation must go behind you.',
            'Holding the breath during the rotation instead of exhaling into the twist.',
        ],
        tip: 'Keep your lower body completely still — only the ribs and above should move.',
    },
    ankle_circles: {
        mistakes: [
            'Moving the entire leg in a circle rather than isolating the ankle joint.',
            'Making circles too small to work through the full range of motion.',
            'Going too fast — slow circles improve joint awareness and control.',
        ],
        tip: 'Keep the working leg parallel to the ground and draw the largest circles you can control.',
    },
    // Beginner / Conditioning
    marching_in_place: {
        mistakes: [
            'Not lifting the knees high enough — aim for hip height on each step.',
            'Letting the arms hang still instead of pumping them in opposition.',
            'Leaning backward rather than keeping a tall, upright posture.',
        ],
        tip: 'Drive the knee up rather than just lifting the foot — it recruits the hip flexors more effectively.',
    },
    jumping_jack: {
        mistakes: [
            'Landing with straight knees — always land with a slight bend to absorb impact.',
            'Not fully extending the arms overhead on each rep.',
            'Letting the feet drift inward on the landing, which stresses the ankles.',
        ],
        tip: 'For lower impact, step side to side instead of jumping — same arms, same benefit.',
    },
    step_touch: {
        mistakes: [
            'Taking too small a step, which reduces cardiovascular demand.',
            'Looking down at the feet instead of keeping the gaze forward.',
            'Letting the trailing foot drag rather than actively bringing it to meet the lead foot.',
        ],
        tip: 'Add a small side arm raise as you step out to increase upper body engagement.',
    },
    standing_bicycle: {
        mistakes: [
            'Crunching the elbow down toward the knee rather than rotating the ribcage.',
            'Moving only the arms without lifting the knee high enough.',
            'Rushing the movement — rotation matters more than speed.',
        ],
        tip: 'Think "ribcage rotates" not "elbow touches knee" — the torso does the work.',
    },
    low_skip: {
        mistakes: [
            'Landing on the heels instead of the balls of the feet, which increases impact.',
            'Skipping so high that the impact becomes significant — keep it low and rhythmic.',
            'Neglecting arm swing, which helps maintain rhythm and balance.',
        ],
        tip: 'Count "left, right, left, right" out loud to stay light and consistent.',
    },
    boxer_shuffle: {
        mistakes: [
            'Bouncing on the heels rather than staying on the balls of the feet.',
            'Stiffening the upper body — keep arms relaxed and slightly raised.',
            'Making the hops too large, which increases impact and reduces speed.',
        ],
        tip: 'Keep your weight centered and imagine the floor is hot — stay light on your feet.',
    },
    // Intermediate / Upper Body
    push_up: {
        mistakes: [
            'Allowing the hips to sag — the body must form a rigid plank from head to heels.',
            'Flaring the elbows to 90 degrees — keep them at roughly 45 degrees.',
            'Cutting the range short — the chest should come within an inch of the floor.',
        ],
        tip: 'Squeeze your glutes during every rep — it creates full-body tension and prevents hip sag.',
    },
    diamond_push_up: {
        mistakes: [
            'Spreading the diamond too wide, shifting emphasis to the chest instead of the triceps.',
            'Flaring the elbows out — they must stay close and track backward.',
            'Allowing the wrists to roll outward under load.',
        ],
        tip: 'If wrist pain occurs, make fists and press on your knuckles to keep the wrist neutral.',
    },
    pike_push_up: {
        mistakes: [
            'Not getting the hips high enough — the goal is an inverted V shape.',
            'Looking forward instead of down between the hands.',
            'Collapsing at the top instead of maintaining shoulder elevation.',
        ],
        tip: 'Walk your feet closer to your hands to increase the angle and difficulty.',
    },
    decline_push_up: {
        mistakes: [
            'Using a surface so high that balance becomes the limiting factor, not strength.',
            'Allowing the hips to pike upward during the movement.',
            'Looking forward — keep the neck neutral and gaze slightly ahead of the hands.',
        ],
        tip: 'Start with a low surface like a step and graduate to higher objects as you get stronger.',
    },
    wide_push_up: {
        mistakes: [
            'Going so wide that the elbows splay beyond 90 degrees at the bottom.',
            'Allowing the hips to sag or the lower back to arch.',
            'Not reaching full arm extension at the top of every rep.',
        ],
        tip: 'Think "bend the floor apart" with your hands to engage the chest from the top.',
    },
    sphinx_push_up: {
        mistakes: [
            'Allowing the hips to shift laterally during the forearm-to-straight-arm transition.',
            'Looking up during the transition, which compresses the cervical spine.',
            'Going too quickly — this is a control drill, not a speed exercise.',
        ],
        tip: 'Press one arm up at a time and hold the plank tight between each transition.',
    },
    // Intermediate / Lower Body
    jump_squat: {
        mistakes: [
            'Landing with stiff, straight knees — absorb the impact through bent knees into a squat.',
            'Not reaching full depth before each jump.',
            'Leaning forward excessively on the landing, losing balance.',
        ],
        tip: 'Land as quietly as possible — silent landings equal safe landings.',
    },
    walking_lunge: {
        mistakes: [
            'Pushing off the back toes rather than driving through the front heel.',
            'Letting the torso lean forward, which overloads the lower back.',
            'Front knee caving inward on each step.',
        ],
        tip: 'Keep your chin up and focus on a spot ahead to maintain an upright posture.',
    },
    single_leg_glute_bridge: {
        mistakes: [
            'Allowing the pelvis to tilt toward the free side at the top.',
            'Keeping the non-working leg bent instead of extending it.',
            'Pushing through the toes rather than the heel of the grounded foot.',
        ],
        tip: 'Pause at the top and check that both hip points are at the same height.',
    },
    lateral_lunge: {
        mistakes: [
            'Allowing the working knee to track inward rather than over the second toe.',
            'Rounding the lower back — hinge from the hip with a flat back.',
            'Putting weight through the extended leg instead of keeping it straight.',
        ],
        tip: 'Push your hip back first before bending the knee to initiate the correct pattern.',
    },
    sumo_squat: {
        mistakes: [
            'Toes pointing straight forward — they must turn out to match the wide stance.',
            'Letting the knees buckle inward at the bottom.',
            'Not reaching sufficient depth — aim for thighs parallel to the floor.',
        ],
        tip: 'Drive your knees out throughout the entire movement to keep tension on the inner thighs.',
    },
    bodyweight_good_morning: {
        mistakes: [
            'Rounding the lower back as you fold forward instead of hinging from the hip.',
            'Locking out the knees completely — keep a soft bend throughout.',
            'Going past parallel, which removes tension from the hamstrings.',
        ],
        tip: 'If you feel it in your lower back instead of your hamstrings, stand up and reset your hinge.',
    },
    // Intermediate / Core
    plank: {
        mistakes: [
            'Hips piking up toward the ceiling, which reduces core demand.',
            'Letting the hips sag, compressing the lower back.',
            'Holding the breath instead of breathing steadily throughout.',
        ],
        tip: 'Squeeze your quads and glutes alongside your abs for a full-body brace.',
    },
    hollow_body_hold: {
        mistakes: [
            'Raising the legs too high, which allows the lower back to arch off the floor.',
            'Only holding briefly instead of maintaining the full time.',
            'Losing the rounded lower-back — the key is a curved, engaged spine against the floor.',
        ],
        tip: 'Start with knees bent if you cannot maintain the position with legs extended.',
    },
    russian_twist: {
        mistakes: [
            'Rotating only the arms while the torso stays stationary — the whole upper body must rotate.',
            'Letting the feet drift wide for balance instead of keeping them together.',
            'Leaning back so far that the hip flexors dominate instead of the obliques.',
        ],
        tip: 'Lift your feet off the floor to increase the difficulty without changing the movement.',
    },
    bicycle_crunch: {
        mistakes: [
            'Pulling the head forward with the hands instead of using the abs to rotate.',
            'Not fully extending the opposite leg on each rep.',
            'Moving so fast that rotation disappears and it becomes a simple crunch.',
        ],
        tip: 'Think "elbow to the ceiling" rather than "elbow to the knee" to get full rotation.',
    },
    straight_leg_raise: {
        mistakes: [
            'Allowing the lower back to arch and lift off the floor as the legs descend.',
            'Using momentum to swing the legs up rather than controlled lifting.',
            'Going below the point where lower back control is lost.',
        ],
        tip: 'Press your hands lightly under your lower back if you struggle to keep it flat.',
    },
    side_plank: {
        mistakes: [
            'Allowing the hip to sag toward the floor.',
            'Stacking the feet incorrectly — top foot rests on the bottom, not in front of it.',
            'Looking down at the floor instead of keeping the head neutral.',
        ],
        tip: 'Drop to your bottom knee to regress if needed — it is the same movement pattern.',
    },
    // Intermediate / Mobility
    worlds_greatest_stretch: {
        mistakes: [
            'Rushing through each position rather than breathing into it.',
            'Not fully straightening the back leg during the hamstring shift.',
            'Letting the front knee collapse inward during the lunge phase.',
        ],
        tip: 'Move slowly and use each exhale to deepen the position — this works best as a warm-up.',
    },
    deep_squat_hold: {
        mistakes: [
            'Heels rising off the floor — place a thin book under them if mobility is limited.',
            'Rounding the upper back instead of maintaining a tall spine.',
            'Gripping the floor with the toes instead of keeping the feet flat.',
        ],
        tip: 'Hold onto a door frame or pole to counterbalance your weight and sit deeper.',
    },
    pigeon_pose: {
        mistakes: [
            'Allowing the back hip to rotate upward — both hips stay square to the floor.',
            'Forcing the shin to 90 degrees before having the flexibility for it.',
            'Collapsing all weight into the grounded hip rather than distributing it evenly.',
        ],
        tip: 'Place a folded blanket or cushion under the hip of the bent leg for support.',
    },
    spinal_twist: {
        mistakes: [
            'Using force to press the knee further across instead of letting gravity do the work.',
            'Allowing the opposite shoulder to lift off the floor.',
            'Tensing the neck during the twist instead of keeping it long and relaxed.',
        ],
        tip: 'Exhale deeply with each breath and allow the knee to sink a little further naturally.',
    },
    doorway_pec_stretch: {
        mistakes: [
            'Placing the forearms above shoulder height, which overstresses the front of the shoulder.',
            'Leaning forward with the lower back arched instead of a neutral spine.',
            'Pushing aggressively through the doorway rather than allowing a passive stretch.',
        ],
        tip: 'Experiment with raising or lowering your elbows to find which angle stretches your chest most.',
    },
    downdog_calf_stretch: {
        mistakes: [
            'Not fully pressing the heel of the active leg toward the floor.',
            'Bending the knee of the pressing leg, which removes the calf stretch.',
            'Moving too quickly between legs — hold each for 2 full seconds.',
        ],
        tip: 'Keep the hips high and press the chest toward your thighs to combine a hamstring stretch.',
    },
    // Intermediate / Conditioning
    no_jump_burpee: {
        mistakes: [
            'Stepping the feet out one at a time instead of both together.',
            'Skipping the push-up portion entirely.',
            'Not fully extending the hips when standing back up.',
        ],
        tip: 'Control the pace — a slow, correct no-jump burpee is harder than a fast, sloppy one.',
    },
    mountain_climber: {
        mistakes: [
            'Letting the hips rise as each knee drives forward.',
            'Placing the foot flat instead of landing on the ball of the foot.',
            'Losing shoulder position — shoulders should stay directly over the wrists.',
        ],
        tip: 'Brace your core as if you are about to be punched — this keeps your hips level.',
    },
    high_knees: {
        mistakes: [
            'Barely lifting the knees — they must reach hip height on every rep.',
            'Landing flat-footed, which increases ground contact time and slows you down.',
            'Leaning backward instead of maintaining a slight forward lean.',
        ],
        tip: 'Hold your palms face-down at hip height and aim to slap them with your knees.',
    },
    skater_jump: {
        mistakes: [
            'Jumping straight up rather than laterally, removing the balance challenge.',
            'Landing with the knee locked straight instead of bent and soft.',
            'Taking tiny hops rather than reaching maximum lateral distance.',
        ],
        tip: 'Reach your opposite hand toward the landing foot to add a balance challenge.',
    },
    plank_to_downdog: {
        mistakes: [
            'Bending the knees during the transition, reducing the hamstring stretch.',
            'Looking up in downward dog, compressing the neck.',
            'Moving the hips to the side rather than directly up and back.',
        ],
        tip: 'Coordinate with your breath: exhale to downdog, inhale to plank.',
    },
    speed_squat: {
        mistakes: [
            'Sacrificing depth for speed — you must reach parallel on every rep.',
            'Allowing the knees to collapse inward under fatigue.',
            'Holding the breath throughout instead of rhythmically exhaling each rep.',
        ],
        tip: 'If form deteriorates, slow down immediately — speed only builds what you already have.',
    },
    // Advanced / Upper Body
    archer_push_up: {
        mistakes: [
            'Not keeping the extended arm fully straight — it acts as a lever, not a support.',
            'Allowing the hips to rotate as you shift laterally.',
            'Gripping the floor with the extended hand — it rests lightly.',
        ],
        tip: 'Start by shifting only 20% of the way to each side and gradually increase range over weeks.',
    },
    pseudo_planche_push_up: {
        mistakes: [
            'Not leaning far enough forward — the shoulders must pass the wrists.',
            'Allowing the elbows to flare out instead of staying close to the body.',
            'Placing the fingers pointing forward instead of backward.',
        ],
        tip: 'Even 30 degrees of lean is meaningful progress at first — build it gradually.',
    },
    typewriter_push_up: {
        mistakes: [
            'Not lowering all the way before beginning the horizontal shift.',
            'Twisting at the hips during the shift instead of keeping the body square.',
            'Moving through the shift too quickly to build real strength.',
        ],
        tip: 'This is a strength skill — treat it as slow, deliberate work, not a rep grinder.',
    },
    plyometric_push_up: {
        mistakes: [
            'Allowing the elbows to collapse on landing rather than absorbing with bent arms.',
            'Not generating enough force to actually clear the hands off the floor.',
            'Attempting these with fatigued form at the end of a long set.',
        ],
        tip: 'Start with hands on an elevated surface to reduce the load while building explosive power.',
    },
    wall_handstand_hold: {
        mistakes: [
            'Kicking up with too much force and smashing the feet into the wall.',
            'Arching the lower back excessively rather than holding a tight hollow position.',
            'Looking at the wall instead of at the floor between your hands.',
        ],
        tip: 'Practice the kick-up separately first — a controlled entry is safer and builds better technique.',
    },
    assisted_one_arm_push_up: {
        mistakes: [
            'Placing the assisting hand on a surface too low, turning it into a regular push-up.',
            'Allowing the body to rotate toward the working arm.',
            'Cutting range — lower until the chest nearly touches the floor.',
        ],
        tip: 'A fist resting on a basketball is a classic tool — it assists but still demands significant balance.',
    },
    // Advanced / Lower Body
    assisted_pistol_squat: {
        mistakes: [
            'Using the support to pull yourself up rather than just for balance.',
            'Extending the free leg to the side rather than straight forward.',
            'Not squatting deep enough — full depth means hip below the knee.',
        ],
        tip: 'Use a door frame and grip it with progressively less force over time to reduce the assist.',
    },
    plyometric_lunge: {
        mistakes: [
            'Landing in a lunge that is too shallow or too narrow.',
            'Letting the front knee cave inward on each landing.',
            'Pausing between reps instead of landing and immediately loading the next jump.',
        ],
        tip: 'Pump your arms aggressively — the arm drive significantly increases jump height and power.',
    },
    single_leg_good_morning: {
        mistakes: [
            'Allowing the hips to rotate outward on the free leg — keep them square.',
            'Locking the knee of the standing leg — keep a soft bend throughout.',
            'Using momentum to swing back up instead of driving through the standing glute.',
        ],
        tip: 'Fix your gaze on a point directly ahead and keep it there throughout the movement.',
    },
    shrimp_squat: {
        mistakes: [
            'Letting the rear knee drop and touch the floor rather than lowering with control.',
            'Leaning excessively forward with the torso instead of staying tall.',
            'Using the held foot for balance rather than pure single-leg work.',
        ],
        tip: 'Place a folded mat where the knee will land so you have a target and a buffer.',
    },
    broad_jump: {
        mistakes: [
            'Not fully loading the hips before the jump — use a deep countermovement.',
            'Landing with the knees straight, sending impact directly to the joints.',
            'Not using arm swing, which contributes significantly to jump distance.',
        ],
        tip: 'Swing your arms back forcefully on the load, then throw them forward explosively on the jump.',
    },
    sprint_intervals: {
        mistakes: [
            'Starting at less than maximum effort — Tabata requires true all-out sprinting.',
            'Drifting from the prescribed work-to-rest ratio, which defeats the protocol.',
            'Sprinting on a surface that allows slipping — use flat, dry ground only.',
        ],
        tip: 'Your pace in round 8 will naturally be slower than round 1 — that is expected and normal.',
    },
    // Advanced / Core
    straddle_v_up: {
        mistakes: [
            'Using momentum by swinging rather than performing a controlled simultaneous lift.',
            'Barely lifting the torso — it should rise until the spine is nearly vertical.',
            'Neglecting the slow descent — 3 seconds down is where the strength is built.',
        ],
        tip: 'If full straddle is too difficult, start with feet together in a regular V-up first.',
    },
    l_sit_hold: {
        mistakes: [
            'Bending the elbows instead of pressing down on straight arms.',
            'Allowing the shoulders to elevate — press them down away from the ears.',
            'Giving up on the leg position before time is up — hold a tuck if you have to.',
        ],
        tip: 'Parallel bars or gymnastics blocks allow deeper depression and make this significantly easier.',
    },
    pike_walk_out: {
        mistakes: [
            'Bending the knees as you walk out, reducing the hamstring and core demand.',
            'Allowing the hips to sag once in the plank position.',
            'Walking the hands so far forward that all tension leaves the posterior chain.',
        ],
        tip: 'Add a push-up in the plank position before walking back to significantly increase difficulty.',
    },
    tuck_to_straight_leg_raise: {
        mistakes: [
            'Losing lower back contact with the floor during the extension phase.',
            'Dropping the legs too fast on the descent — it should be slow and controlled.',
            'Not fully extending the legs at the top before beginning the lower.',
        ],
        tip: 'If the lower back lifts, reduce the range and only go as low as you can truly control.',
    },
    hollow_body_rock: {
        mistakes: [
            'Arching the lower back during the rock — any arch means the hollow is lost.',
            'Rocking at a random pace instead of a controlled rhythm.',
            'Extending the arms and legs too wide, which demands more flexibility than strength.',
        ],
        tip: 'Shorten your position (tuck the arms in, bend the knees slightly) to maintain hollow under fatigue.',
    },
    planche_lean: {
        mistakes: [
            'Bending the elbows instead of keeping the arms completely straight.',
            'Allowing the hips to sag rather than maintaining a rigid body line.',
            'Leaning too aggressively too soon — shoulder damage from excessive lean accumulates.',
        ],
        tip: 'Build your lean angle gradually, adding only a degree or two each week.',
    },
    // Advanced / Mobility
    pancake_stretch: {
        mistakes: [
            'Rounding the back to get the chest lower instead of hinging from the hips.',
            'Forcing the position aggressively — this stretch requires months of consistent work.',
            'Not breathing — slow, deep exhales allow the muscles to release further.',
        ],
        tip: 'Use a foam roller lengthwise along your spine to encourage a flat back as you fold.',
    },
    bodyweight_jefferson_curl: {
        mistakes: [
            'Moving too quickly — each vertebra should peel away from the previous one slowly.',
            'Locking the knees, which creates excessive strain on the hamstrings.',
            'Only going halfway and calling it done.',
        ],
        tip: 'Treat this as a spinal warm-up. Move with full respect and never force it.',
    },
    front_split_prep: {
        mistakes: [
            'Forcing the hips all the way down before they are ready.',
            'Allowing the pelvis to rotate open on the rear hip.',
            'Only stretching one side, which creates an imbalance.',
        ],
        tip: 'Progress is measured in weeks and months, not sessions — do this daily for consistent gains.',
    },
    cossack_squat: {
        mistakes: [
            'Not keeping the extended leg completely straight.',
            'Allowing the heel of the squatting leg to lift — keep both heels on the floor.',
            'Only squatting to a comfortable partial range rather than going as deep as possible.',
        ],
        tip: 'Holding a light weight in front of your chest helps counterbalance the movement.',
    },
    shoulder_cars: {
        mistakes: [
            'Allowing the opposite shoulder to participate — pin it firmly and isolate one side.',
            'Losing tension at the end ranges of the rotation.',
            'Moving through the range too quickly — CARs require maximum active control.',
        ],
        tip: 'Do these in front of a mirror to catch any compensatory shoulder elevation or trunk sway.',
    },
    wrist_prep: {
        mistakes: [
            'Skipping this before handstand or planche work — wrist injuries from this omission are common.',
            'Loading the wrists before they are fully warmed up.',
            'Performing the movements too quickly without feeling each position.',
        ],
        tip: 'Your wrist strength often limits handstand progress more than your shoulder strength does.',
    },
    // Advanced / Conditioning
    full_burpee: {
        mistakes: [
            'Skipping the push-up or barely bending the elbows.',
            'Landing the jump with stiff, straight legs instead of soft bent knees.',
            'Not fully extending the body overhead at the top of the jump.',
        ],
        tip: 'Breathe out on the push-up, breathe in on the jump — find that rhythm and it becomes sustainable.',
    },
    tabata_mountain_climber: {
        mistakes: [
            'Pacing the first few rounds to save energy — Tabata demands maximum effort each round.',
            'Allowing the hips to rise progressively higher with each rep.',
            'Resting beyond the prescribed 10 seconds.',
        ],
        tip: 'Count reps in round 1. If your count drops by more than 30% in round 8, you started too hard.',
    },
    tuck_jump: {
        mistakes: [
            'Achieving only a partial tuck — the knees must drive up toward the chest.',
            'Landing with the knees forward and straight instead of absorbing with a bend.',
            'Leaning backward during the jump instead of staying tall.',
        ],
        tip: 'Mark hip height on a wall with tape and try to tap it with your knees at the peak.',
    },
    tuck_jump_burpee: {
        mistakes: [
            'Rushing the push-up portion to get to the jump faster.',
            'Landing from the tuck jump and falling immediately into the next burpee without resetting.',
            'Not achieving full tuck height on the jump due to accumulated fatigue.',
        ],
        tip: 'This is a high-skill drill — rest fully between sets so technique does not break down.',
    },
    broad_jump_consecutive: {
        mistakes: [
            'Not absorbing each landing through a full squat position before the next jump.',
            'Reducing jump distance on later reps — maintain consistent maximum effort throughout.',
            'Landing on the toes only, which fails to transfer force into the next jump.',
        ],
        tip: 'Land in a quarter squat, pause for a single breath, then drive into the next jump.',
    },
    shuttle_run: {
        mistakes: [
            'Slowing to a jog at the end of each length instead of sprinting through the turn.',
            'Touching the line without properly lowering and changing direction sharply.',
            'Not taking the full 45-second rest, which compromises all subsequent rounds.',
        ],
        tip: 'Lead with your outside foot when changing direction to minimize the deceleration distance.',
    },
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
    coachLink.textContent = 'Open Coach';
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

    var result = await api('/api/daily/' + key + '/complete', 'POST');
    if (!result) return;

    if (result.status === 200) {
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

    var instrEl = document.createElement('p');
    instrEl.className = 'ex-modal-instructions';
    instrEl.textContent = ex.instructions;

    modal.appendChild(closeBtn);
    modal.appendChild(nameEl);
    modal.appendChild(instrEl);

    var data = COACH_DATA[ex.key];
    if (data) {
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
