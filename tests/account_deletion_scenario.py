"""A user tangled into every table that points at `user`, and the checks that
deleting them through DELETE /api/me leaves nothing behind.

Shared by two tests:

* tests/test_account_deletion_fks.py runs it in-process against SQLite with
  foreign keys switched ON (SQLite ignores them by default, which is how
  DELETE /api/me shipped returning 500 on PostgreSQL for anyone who had ever
  set a daily effort level);
* tests/test_account_deletion_postgres.py runs this file as a script against a
  PostgreSQL database built by `flask db upgrade`, because PostgreSQL is what
  production runs and the only engine whose answer settles it.

Run as a script it prints one JSON object and exits 0 only if every check held.
"""
import datetime
import json
import os
import sys
import uuid

PASSWORD = 'WalkTest123!'


def _register(client, username):
    client.post('/api/register', json={'username': username, 'password': PASSWORD})
    tok = client.post('/api/login', json={'username': username,
                                          'password': PASSWORD}).get_json()['access_token']
    return {'Authorization': f'Bearer {tok}'}


def _uid(A, username):
    return A.User.query.filter_by(username=username).one().id


def row_counts(A):
    """Every row in every table, so 'nothing changed' is checked, not assumed."""
    out = {}
    for table in A.db.metadata.sorted_tables:
        out[table.name] = A.db.session.execute(
            A.db.select(A.db.func.count()).select_from(table)).scalar()
    return out


def references_to(A, user_id):
    """{(table, column): rows still pointing at this user}, for every FK into user."""
    out = {}
    for table in A.db.metadata.sorted_tables:
        for col in table.columns:
            if any(fk.column.table.name == 'user' for fk in col.foreign_keys):
                out[(table.name, col.name)] = A.db.session.execute(
                    A.db.select(A.db.func.count()).select_from(table)
                    .where(col == user_id)).scalar()
    return out


def build_tangled_user(A, client, tag):
    """`leaver` touches every delete and link-null table; `friend` owns the team."""
    leaver_h = _register(client, f'leaver_{tag}')
    _register(client, f'friend_{tag}')
    leaver, friend = _uid(A, f'leaver_{tag}'), _uid(A, f'friend_{tag}')
    db = A.db
    team = A.Team(name=f'T {tag}', created_by_user_id=friend)
    db.session.add(team)
    db.session.flush()
    ch_by_leaver = A.TeamChallenge(public_id=uuid.uuid4().hex, team_id=team.id,
                                   preset_key='pushups', created_by_user_id=leaver,
                                   target_user_id=friend)
    ch_for_leaver = A.TeamChallenge(public_id=uuid.uuid4().hex, team_id=team.id,
                                    preset_key='squats', created_by_user_id=friend,
                                    target_user_id=leaver)
    db.session.add_all([ch_by_leaver, ch_for_leaver])
    db.session.flush()
    # Closed: a PENDING report about the leaver would (rightly) block them.
    report = A.Report(public_id=uuid.uuid4().hex, reporter_user_id=friend,
                      reported_user_id=leaver, category='harassment',
                      subject_type='user', team_id=team.id, status='closed',
                      disposition='dismissed', reviewed_at=datetime.datetime(2026, 9, 1))
    held_other = A.Report(public_id=uuid.uuid4().hex, reporter_user_id=friend,
                          category='harassment', subject_type='challenge',
                          subject_ref='c' * 32, team_id=team.id, status='closed',
                          disposition='dismissed', legal_hold=True, legal_hold_reason='x',
                          reviewed_at=datetime.datetime(2026, 9, 1))
    db.session.add_all([report, held_other])
    db.session.flush()
    db.session.add_all([
        A.ReportEvidence(report_id=held_other.id, content_type='challenge',
                         author_user_id=friend, context_json=json.dumps(
                             {'preset_key': 'x', 'target_user_id': leaver, 'team_id': team.id})),
        A.TeamMembership(team_id=team.id, user_id=friend),
        A.TeamMembership(team_id=team.id, user_id=leaver),
        A.DailyEffort(user_id=leaver, date=datetime.date(2026, 9, 1), level='easy'),
        A.DailyCompletion(user_id=leaver, date=datetime.date(2026, 9, 1),
                          exercise_key='pushups'),
        A.ProgressEvent(user_id=leaver, event_type='mission_complete', xp_delta=25),
        A.TeamChallengeCompletion(challenge_id=ch_for_leaver.id, user_id=leaver),
        A.TeamMessage(team_id=team.id, sender_user_id=leaver, sender_type='user',
                      body='hello'),
        A.UserBlock(blocker_user_id=leaver, blocked_user_id=friend),
        A.UserBlock(blocker_user_id=friend, blocked_user_id=leaver),
        A.ReportEvidence(report_id=report.id, content_type='user',
                         author_user_id=leaver),
        A.ModerationAction(report_id=report.id, action='dismiss',
                           target_user_id=leaver),
        A.UserRestriction(user_id=leaver, kind='social_suspended'),
        # Evidence of a reported challenge addressed to the leaver records
        # their id in context_json; one on the closed report, one on a held
        # report (someone else's) that must keep it.
        A.ReportEvidence(report_id=report.id, content_type='challenge',
                         author_user_id=friend, context_json=json.dumps(
                             {'preset_key': 'x', 'target_user_id': leaver, 'team_id': team.id})),
        A.PermissionAudit(subject_user_id=friend, actor_user_id=leaver,
                          capability=sorted(A.CAPABILITIES)[0], decision='deny',
                          reason='test'),
    ])
    db.session.commit()
    return {'leaver': leaver, 'friend': friend, 'leaver_h': leaver_h,
            'team': team.id, 'report': report.id, 'held_other': held_other.id,
            'challenges': [ch_by_leaver.id, ch_for_leaver.id]}


def _blocker_rows(A, code, user_id, other_id, team_id):
    """The smallest row that makes `code` block deletion of user_id."""
    if code == 'team_owned':
        return [A.Team(name='mine', created_by_user_id=user_id)]
    if code == 'guardian_link_child':
        return [A.GuardianLink(child_user_id=user_id, guardian_user_id=other_id,
                               method='test')]
    if code == 'guardian_link_guardian':
        return [A.GuardianLink(child_user_id=other_id, guardian_user_id=user_id,
                               method='test')]
    if code == 'permission_audit_subject':
        return [A.PermissionAudit(subject_user_id=user_id,
                                  capability=sorted(A.CAPABILITIES)[0],
                                  decision='deny', reason='test')]
    if code == 'report_open_about':          # pending, names them as reported
        return [A.Report(public_id=uuid.uuid4().hex, reporter_user_id=other_id,
                         reported_user_id=user_id, category='harassment',
                         subject_type='user', team_id=team_id)]
    if code == 'report_held_about':          # closed but held, names them
        return [A.Report(public_id=uuid.uuid4().hex, reporter_user_id=other_id,
                         reported_user_id=user_id, category='child_safety',
                         subject_type='user', status='closed', legal_hold=True,
                         legal_hold_reason='test', reviewed_at=datetime.datetime(2026, 9, 1))]
    if code == 'report_held_filed':          # they filed it; it is held
        return [A.Report(public_id=uuid.uuid4().hex, reporter_user_id=user_id,
                         reported_user_id=other_id, category='harassment',
                         subject_type='user', status='closed', legal_hold=True,
                         legal_hold_reason='test', reviewed_at=datetime.datetime(2026, 9, 1))]
    raise KeyError(code)


def _add_blocker(A, code, user_id, other_id, team_id):
    db = A.db
    if code == 'report_open_evidence':       # pending, quotes their message
        rep = A.Report(public_id=uuid.uuid4().hex, reporter_user_id=other_id,
                       category='inappropriate_content', subject_type='message',
                       subject_ref='e' * 32, team_id=team_id)
        db.session.add(rep)
        db.session.flush()
        db.session.add(A.ReportEvidence(report_id=rep.id, content_type='message',
                                        content_text='x', author_user_id=user_id))
    elif code == 'consent_child':
        link = A.GuardianLink(child_user_id=user_id, guardian_user_id=other_id,
                              method='test')
        db.session.add(link)
        db.session.flush()
        db.session.add(A.Consent(child_user_id=user_id, guardian_link_id=link.id,
                                 capability=sorted(A.CAPABILITIES)[0], method='test'))
    else:
        db.session.add_all(_blocker_rows(A, code, user_id, other_id, team_id))
    db.session.commit()


def run(A, client):
    """Every check, as {name: (ok, detail)}."""
    results = {}
    s = build_tangled_user(A, client, 'main')
    before = row_counts(A)
    resp = client.delete('/api/me', json={'password': PASSWORD}, headers=s['leaver_h'])
    A.db.session.remove()
    results['tangled user deletes with 200'] = (resp.status_code == 200,
                                                f'{resp.status_code} {resp.get_json()}')
    left = {k: v for k, v in references_to(A, s['leaver']).items() if v}
    results['no row points at the deleted user'] = (not left, str(left))
    after = row_counts(A)
    kept = {
        'report survives': after['report'] == before['report'],
        'report evidence survives': after['report_evidence'] == before['report_evidence'],
        'moderation action survives': after['moderation_action'] == before['moderation_action'],
        'team challenges survive': after['team_challenge'] == before['team_challenge'],
        'team message survives': after['team_message'] == before['team_message'],
        'permission audit survives': after['permission_audit'] == before['permission_audit'],
        'team survives': A.db.session.get(A.Team, s['team']) is not None,
        'friend survives': A.db.session.get(A.User, s['friend']) is not None,
        "friend's membership survives": A.TeamMembership.query.filter_by(
            user_id=s['friend']).count() == 1,
    }
    for name, ok in kept.items():
        results[name] = (ok, '')
    ctx = [json.loads(e.context_json) for e in A.ReportEvidence.query.filter_by(
        report_id=s['report'], content_type='challenge').all()]
    results["challenge evidence no longer records the deleted person's id"] = (
        ctx and all(c.get('target_user_id') is None for c in ctx)
        and all(c.get('preset_key') == 'x' for c in ctx), str(ctx))
    held_ctx = json.loads(A.ReportEvidence.query.filter_by(
        report_id=s['held_other']).one().context_json)
    results['a legal hold keeps even that id'] = (
        held_ctx.get('target_user_id') == s['leaver'], str(held_ctx))
    by_leaver, for_leaver = (A.db.session.get(A.TeamChallenge, i) for i in s['challenges'])
    results["a challenge addressed to them is expired, not reopened to everyone"] = (
        for_leaver.target_user_id is None and not A._challenge_is_open(for_leaver),
        str(for_leaver.expires_at))
    results["a challenge they set for someone else keeps running"] = (
        by_leaver.expires_at is None and by_leaver.target_user_id == s['friend'],
        str(by_leaver.expires_at))
    gone = {t: after[t] for t in ('daily_effort', 'user_block', 'user_restriction',
                                  'team_challenge_completion') if after[t]}
    results['private rows are deleted'] = (not gone, str(gone))

    # Each blocker refuses and changes nothing. The PLAN names the reason
    # exactly; the RESPONSE names only team ownership, and collapses every
    # other reason -- above all a report about them -- into 'safety_record'.
    internal = {'report_held_about': 'report_open_about',
                'report_open_evidence': 'report_open_about'}
    for i, code in enumerate(['team_owned', 'guardian_link_child',
                              'guardian_link_guardian', 'consent_child',
                              'permission_audit_subject', 'report_open_about',
                              'report_held_about', 'report_open_evidence',
                              'report_held_filed']):
        h = _register(client, f'blocked{i}')
        uid, other = _uid(A, f'blocked{i}'), s['friend']
        A.db.session.add(A.DailyEffort(user_id=uid, date=datetime.date(2026, 9, 2),
                                       level='easy'))
        A.db.session.commit()
        _add_blocker(A, code, uid, other, s['team'])
        want = internal.get(code, code)
        want_plan = (['guardian_link_child', 'consent_child'] if code == 'consent_child'
                     else [want])
        plan_codes = A.delete_user_account(uid, dry_run=True)['blocker_codes']
        snap = row_counts(A)
        resp = client.delete('/api/me', json={'password': PASSWORD}, headers=h)
        A.db.session.remove()
        body = resp.get_json() or {}
        shown = ['team_owned'] if code == 'team_owned' else ['safety_record']
        ok = (resp.status_code == 409 and plan_codes == want_plan
              and body.get('blocker_codes') == shown
              and (code == 'team_owned' or body.get('blockers') == [])
              and bool(body.get('message')) and row_counts(A) == snap)
        results[f'{code} blocks with 409 and changes nothing'] = (
            ok, f"{resp.status_code} plan={plan_codes} shown={body.get('blocker_codes')}")
        if code != 'team_owned':
            blob = json.dumps(body).lower()
            results[f'{code} refusal names no report'] = (
                not any(w in blob for w in ('report', 'legal', 'hold', 'moderation')), blob)

    results.update(run_reporter_and_appellant(A, client, s['friend'], s['team']))
    return results


ADMIN = 'scenario-admin-secret'


def run_reporter_and_appellant(A, client, other, team_id):
    """A reporter and an appellant delete their accounts; the records stay.

    Kept: category, dates, status, disposition / outcome. Gone: the link to
    the person, the appellant's words. The reporter's note and the evidence
    stay on the evidence clock and go when the retention sweep reaches them."""
    db, results = A.db, {}
    now = datetime.datetime.utcnow()
    h = _register(client, 'reporter')
    reporter = _uid(A, 'reporter')
    pending = A.Report(public_id=uuid.uuid4().hex, reporter_user_id=reporter,
                       reported_user_id=other, team_id=team_id, category='harassment',
                       subject_type='user', note='my own words', due_at=now)
    closed = A.Report(public_id=uuid.uuid4().hex, reporter_user_id=reporter,
                      reported_user_id=other, team_id=team_id,
                      category='inappropriate_content', subject_type='message',
                      subject_ref='m' * 32, note='more of my words', status='closed',
                      disposition='dismissed',
                      created_at=now - datetime.timedelta(days=40),
                      reviewed_at=now - datetime.timedelta(days=35))
    # (A report under legal hold that they filed would block the deletion:
    # see 'report_held_filed' above.)
    db.session.add_all([pending, closed])
    db.session.flush()
    db.session.add_all([
        A.ReportEvidence(report_id=closed.id, content_type='message',
                         content_text='what they wrote', author_user_id=other,
                         context_json='{"team_id": %d}' % team_id),
        A.ModerationAction(report_id=closed.id, action='dismiss', target_user_id=other),
    ])
    db.session.commit()
    before = {r.public_id: (r.category, r.created_at, r.due_at, r.reviewed_at,
                            r.status, r.disposition, r.team_id, r.reported_user_id)
              for r in (pending, closed)}
    ids = {pending.public_id: pending.id, closed.public_id: closed.id}
    pend_pid, closed_pid = pending.public_id, closed.public_id

    resp = client.delete('/api/me', json={'password': PASSWORD}, headers=h)
    db.session.remove()
    results['a reporter can delete their account'] = (resp.status_code == 200,
                                                      str(resp.status_code))
    after = {r.public_id: r for r in A.Report.query.filter(
        A.Report.id.in_(ids.values())).all()}
    results['both reports survive'] = (len(after) == 2, str(len(after)))
    results['neither report names the reporter any more'] = (
        all(r.reporter_user_id is None for r in after.values()), '')
    results['category, dates, status and outcome are unchanged'] = (
        all((r.category, r.created_at, r.due_at, r.reviewed_at, r.status,
             r.disposition, r.team_id, r.reported_user_id) == before[p]
            for p, r in after.items()), '')
    results['the pending report is still in the review queue'] = (
        after[pend_pid].status == 'pending', '')
    results["a pending report keeps the note for the investigator"] = (
        after[pend_pid].note == 'my own words', '')
    results["a closed report loses the reporter's note at deletion"] = (
        after[closed_pid].note is None, str(after[closed_pid].note))
    results['the note removal is on the trail'] = (
        A.ModerationAction.query.filter_by(report_id=ids[closed_pid], actor='system',
                                           action='reporter_note_removed').count() == 1
        and A.ModerationAction.query.filter_by(report_id=ids[pend_pid],
                                               action='reporter_note_removed').count() == 0, '')

    prev = os.environ.get('ADMIN_SECRET')
    os.environ['ADMIN_SECRET'] = ADMIN
    try:
        detail = client.get(f'/api/admin/reports/{pend_pid}',
                            headers={'X-Admin-Secret': ADMIN}).get_json() or {}
        results['the operator sees the reporter left, not who they were'] = (
            detail.get('reporter_account_deleted') is True
            and detail.get('reporter_user_id') is None, str(detail.get('reporter_user_id')))

        # The retention sweep then does what it does for every closed report.
        A._sweep_moderation_evidence(now=now)
        db.session.commit()
        c = db.session.get(A.Report, ids[closed_pid])
        ev = A.ReportEvidence.query.filter_by(report_id=c.id).one()
        results['the sweep removes note and evidence 30 days after closure'] = (
            c.note is None and ev.content_text is None and ev.context_json is None
            and c.evidence_purged_at is not None, '')
        results['what remains is category, dates and outcome'] = (
            (c.category, c.created_at, c.reviewed_at, c.status, c.disposition)
            == before[closed_pid][:2] + before[closed_pid][3:6], '')

        # --- the appellant ---
        h = _register(client, 'appellant')
        appellant = _uid(A, 'appellant')
        act_open = A.ModerationAction(report_id=ids[closed_pid],
                                      action='suspend_social', target_user_id=appellant,
                                      note='operator reasoning')
        act_done = A.ModerationAction(action='restrict_reporting',
                                      target_user_id=appellant, note='why')
        db.session.add_all([act_open, act_done])
        db.session.flush()
        open_ap = A.Appeal(public_id=uuid.uuid4().hex, user_id=appellant,
                           action_id=act_open.id, reason='please look again')
        done_ap = A.Appeal(public_id=uuid.uuid4().hex, user_id=appellant,
                           action_id=act_done.id, reason='also this', status='closed',
                           outcome='upheld', outcome_note='we looked, it stands',
                           decided_at=now)
        db.session.add_all([open_ap, done_ap,
                            A.UserRestriction(user_id=appellant, kind='social_suspended')])
        db.session.commit()
        ap_ids = (open_ap.id, done_ap.id)
        act_ids = (act_open.id, act_done.id)
        open_before = client.get('/api/admin/reports?status=all',
                                 headers={'X-Admin-Secret': ADMIN}).get_json()['counts']

        resp = client.delete('/api/me', json={'password': PASSWORD}, headers=h)
        db.session.remove()
        results['an appellant can delete their account'] = (resp.status_code == 200,
                                                            str(resp.status_code))
        o, d = (db.session.get(A.Appeal, i) for i in ap_ids)
        results['the open appeal ends as withdrawn'] = (
            (o.status, o.outcome) == ('closed', 'withdrawn') and o.decided_at is not None, '')
        results['the decided appeal keeps its outcome'] = (
            (d.status, d.outcome, d.decided_at) == ('closed', 'upheld', now), '')
        results["neither appeal keeps the person or their words"] = (
            all(a.user_id is None and a.reason is None and a.outcome_note is None
                for a in (o, d)), '')
        results['the appealed decisions keep their audit rows'] = (
            db.session.get(A.ModerationAction, act_ids[0]).note == 'operator reasoning'
            and db.session.get(A.ModerationAction, act_ids[1]) is not None, '')
        results['the withdrawal is on the trail'] = (
            A.ModerationAction.query.filter_by(action='appeal_withdrawn',
                                               actor='system').count() == 1, '')
        counts = client.get('/api/admin/reports?status=all',
                            headers={'X-Admin-Secret': ADMIN}).get_json()['counts']
        results['the open-appeals count drops by one'] = (
            counts['open_appeals'] == open_before['open_appeals'] - 1,
            f"{open_before['open_appeals']} -> {counts['open_appeals']}")
        r = client.post(f'/api/admin/appeals/{o.public_id}/decide',
                        json={'outcome': 'overturned'}, headers={'X-Admin-Secret': ADMIN})
        results['a withdrawn appeal cannot be decided'] = (
            r.status_code == 409 and (r.get_json() or {}).get('code') == 'appeal_withdrawn',
            str(r.status_code))
        actions_before = A.ModerationAction.query.count()
        r = client.post(f'/api/admin/appeals/{d.public_id}/decide',
                        json={'outcome': 'overturned', 'note': 'lifting'},
                        headers={'X-Admin-Secret': ADMIN})
        db.session.remove()
        d = db.session.get(A.Appeal, ap_ids[1])
        results['nor can one decided before its appellant left'] = (
            r.status_code == 409 and (d.outcome, d.outcome_note) == ('upheld', None)
            and A.ModerationAction.query.count() == actions_before,
            f'{r.status_code} {d.outcome} {d.outcome_note!r}')
    finally:
        if prev is None:
            os.environ.pop('ADMIN_SECRET', None)
        else:
            os.environ['ADMIN_SECRET'] = prev
    return results


def main():
    import app as A
    A.limiter.enabled = False
    A.app.config['TESTING'] = True
    with A.app.app_context():
        results = run(A, A.app.test_client())
    out = {name: {'ok': ok, 'detail': detail} for name, (ok, detail) in results.items()}
    print(json.dumps(out, indent=1))
    return 0 if all(ok for ok, _ in results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
