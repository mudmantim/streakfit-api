"""Rickie's size is decided in exactly one place, and he cannot swallow a tap.

Rickie's size used to be written down twice: `.rickie-roam` in style.css is
what the browser renders, and `var SIZE` in rickie-roam.js is what every
bounds, clamp and overlap calculation is solving for. Nothing connected them —
a stylesheet cannot export a number to a script — so the only thing keeping
them equal was a comment on each side asking the next person to change both.

When they drift, nothing throws and nothing looks wrong in a screenshot.
Rickie is simply clamped to the wrong edge, judged to overlap the wrong
elements, and allowed to stand on text he was supposed to move away from.
This project has shipped that exact bug before: 112 passing UI checks did not
notice Rickie covering copy, because the checks and the bug shared a blind
spot. `scripts/uicheck.py` had a third copy of the number, hardcoded in the
harness that exists to police it.

The engine now reads `el.offsetWidth`, so the stylesheet is the single source
of truth and the drift is structurally impossible rather than merely
discouraged. These tests hold that property in place — the first would fail
the moment somebody reintroduces a literal, which is the only way back.

Plain source-text assertions on purpose: no browser, no server, no fixtures,
so a regression is caught in the normal suite the same day it lands.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / 'static'
SCRIPTS = Path(__file__).resolve().parent.parent / 'scripts'


def _block(selector):
    """The block's DECLARATIONS, with comments stripped.

    Stripping is not tidiness, it is the correctness of this file. The first
    version of `test_he_cannot_intercept_a_tap_at_any_size` searched the raw
    block text, and the block's own comment contains the sentence
    "`pointer-events: none` above is what keeps him from obstructing a
    control". Deleting the real declaration left the prose describing it, the
    regex matched the prose, and the test went green over the exact regression
    it exists to catch. Found by mutating the stylesheet and watching the test
    fail to notice — the only way that class of bug ever surfaces.
    """
    css = (STATIC / 'style.css').read_text(encoding='utf-8')
    match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css)
    assert match, f'no `{selector} {{ }}` block found in style.css'
    return re.sub(r'/\*.*?\*/', '', match.group(1), flags=re.S)


def _code(path):
    """Source with comments removed, so prose can never satisfy an assertion."""
    text = path.read_text(encoding='utf-8')
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'(?m)^\s*//.*$', '', text)


def test_the_engine_measures_rickie_instead_of_being_told_his_size():
    js = _code(STATIC / 'rickie-roam.js')

    assert re.search(r'SIZE\s*=\s*\w*\.offsetWidth', js), (
        'rickie-roam.js must derive SIZE from the rendered element '
        '(`SIZE = el.offsetWidth || SIZE`). Without that read the stylesheet '
        'and the engine are two independent copies of one number again.'
    )

    # A literal seed for the pre-measurement fallback is fine and necessary.
    # A literal that MATCHES the stylesheet is the smell: it means somebody
    # has started keeping them in sync by hand again.
    css_width = int(re.search(r'width\s*:\s*(\d+)px', _block('.rickie-roam')).group(1))
    for literal in re.findall(r'SIZE\s*=\s*(\d+)\s*;', js):
        assert int(literal) != css_width, (
            f'rickie-roam.js hardcodes SIZE = {literal}, the same number as '
            f'`.rickie-roam` in style.css. That is the duplicated constant '
            f'this design removed; the fallback seed must not track the CSS.'
        )


def test_the_ui_harness_does_not_keep_its_own_copy_of_his_size():
    """The third copy, and the worst one.

    `scripts/uicheck.py` places Rickie where the engine says is clear and then
    measures what he landed on. It did that arithmetic with a hardcoded `56`.
    A harness holding a stale copy of the number under test does not fail when
    that number changes — it reports clear, from the wrong position, which is
    worse than having no check at all.
    """
    src = _code(SCRIPTS / 'uicheck.py')
    placement = re.search(r'_somewhereClear\(\).{0,700}?transform', src, re.S)
    assert placement, 'could not find the Rickie placement snippet in uicheck.py'
    assert 'offsetWidth' in placement.group(0), (
        'the uicheck placement must size Rickie from `e.offsetWidth`'
    )
    assert not re.search(r'(width|height)\s*-\s*\d+', placement.group(0)), (
        'the uicheck placement still subtracts a literal size — it must read '
        'the element, or it measures overlap at the wrong coordinates'
    )


def test_he_cannot_intercept_a_tap_at_any_size():
    """The one property that makes his size safe rather than merely tuned.

    Growing him grows what he can sit on top of. `pointer-events: none` on
    both the band and the character is why that is a legibility question and
    never a "the button did not respond" question — which is what makes the
    size a free parameter the stylesheet is allowed to vary by viewport.
    """
    for selector in ('.rickie-roam-band', '.rickie-roam'):
        assert re.search(r'pointer-events\s*:\s*none', _block(selector)), (
            f'{selector} must keep `pointer-events: none` — without it Rickie '
            f'can swallow a tap meant for a control underneath him'
        )
