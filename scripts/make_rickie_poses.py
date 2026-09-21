#!/usr/bin/env python3
"""Draw Rickie's roaming poses from one shared construction.

Rickie is a RACCOON, and everything that makes him one is here in a single
place: the ringed tail, the mask, the round ears with their pale inners, the
pale muzzle and belly. Poses vary limb positions, tilt and face — never the
identity. That is the whole reason this is a generator and not eleven hand-drawn
files: a hand-drawn set drifts, and a raccoon whose mask moves between frames
stops reading as one character.

The existing four poses (neutral, happy, proud, curious) are NOT regenerated.
They were drawn by hand, they are good, and rebuilding them to match a script
would be a redesign nobody asked for.

    python scripts/make_rickie_poses.py
"""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "static"

FUR = "#E8A872"      # face and body
FUR_DARK = "#CF8E55"  # tail, ears, legs — the darker raccoon tone
PALE = "#F3F4F6"     # tail rings, belly, muzzle, inner ear
MASK = "#7C3AED"     # the mask. Not negotiable; it is how you know it is him.
INK = "#1F2937"
INDIGO = "#4F46E5"
INDIGO_DARK = "#4338CA"
ACORN = "#B45309"
ACORN_CAP = "#78350F"


# The tail always pivots at the point it joins the body. Rotating it about
# anything else swings the whole shape away and leaves a striped fragment
# floating beside him, which is precisely the crude-rotation failure to avoid.
# Angles are kept inside ±40°; past that the rings stop lining up with the path.
_TAIL_ROOT = (138, 142)


def tail(angle: float = 0.0) -> str:
    """The upright ringed tail. Three pale rings, always."""
    angle = max(-40.0, min(40.0, angle))
    g = f'<g transform="rotate({angle} {_TAIL_ROOT[0]} {_TAIL_ROOT[1]})">' if angle else "<g>"
    return f'''  {g}
    <path d="M138,142 Q170,130 162,95 Q158,72 138,68" stroke="{FUR_DARK}" stroke-width="26" fill="none" stroke-linecap="round"/>
    <line x1="155" y1="118" x2="172" y2="132" stroke="{PALE}" stroke-width="11" stroke-linecap="round"/>
    <line x1="148" y1="93" x2="170" y2="103" stroke="{PALE}" stroke-width="11" stroke-linecap="round"/>
    <line x1="133" y1="68" x2="152" y2="80" stroke="{PALE}" stroke-width="11" stroke-linecap="round"/>
  </g>'''


def tail_curled(root: tuple[int, int] = (130, 156)) -> str:
    """A tail drawn low and curled round, for sitting and lying poses.

    Drawn, not rotated. A curled tail is a different shape from an upright one
    and no amount of turning the upright one produces it.
    """
    x, y = root
    return f'''  <g>
    <path d="M{x},{y} Q{x + 42},{y + 2} {x + 40},{y - 26} Q{x + 38},{y - 46} {x + 16},{y - 44}"
          stroke="{FUR_DARK}" stroke-width="24" fill="none" stroke-linecap="round"/>
    <line x1="{x + 40}" y1="{y - 2}" x2="{x + 20}" y2="{y + 6}" stroke="{PALE}" stroke-width="10" stroke-linecap="round"/>
    <line x1="{x + 44}" y1="{y - 22}" x2="{x + 26}" y2="{y - 22}" stroke="{PALE}" stroke-width="10" stroke-linecap="round"/>
    <line x1="{x + 30}" y1="{y - 42}" x2="{x + 22}" y2="{y - 26}" stroke="{PALE}" stroke-width="10" stroke-linecap="round"/>
  </g>'''


def leg(hip: tuple[int, int], foot: tuple[int, int]) -> str:
    return (f'  <line x1="{hip[0]}" y1="{hip[1]}" x2="{foot[0]}" y2="{foot[1]}" '
            f'stroke="{FUR_DARK}" stroke-width="24" stroke-linecap="round"/>\n'
            f'  <circle cx="{foot[0]}" cy="{foot[1] + 4}" r="12" fill="{FUR_DARK}"/>')


def arm(shoulder: tuple[int, int], hand: tuple[int, int], pale_palm: bool = False) -> str:
    out = (f'  <line x1="{shoulder[0]}" y1="{shoulder[1]}" x2="{hand[0]}" y2="{hand[1]}" '
           f'stroke="{FUR}" stroke-width="20" stroke-linecap="round"/>\n'
           f'  <circle cx="{hand[0]}" cy="{hand[1]}" r="13" fill="{FUR}"/>')
    if pale_palm:
        out += f'\n  <circle cx="{hand[0]}" cy="{hand[1]}" r="8" fill="{PALE}"/>'
    return out


def body(cy: int = 120) -> str:
    return f'''  <ellipse cx="100" cy="{cy}" rx="42" ry="46" fill="{FUR}"/>
  <ellipse cx="100" cy="{cy + 8}" rx="26" ry="30" fill="{PALE}"/>
  <path d="M82,{cy - 22} L118,{cy - 22} L100,{cy - 2} Z" fill="{INDIGO}" stroke="{INDIGO_DARK}" stroke-width="1.5" stroke-linejoin="round"/>'''


def head(cy: int = 70, eyes: str = "open", mouth: str = "smile",
         tilt: float = 0.0) -> str:
    """Head, ears, mask, eyes, nose, mouth — the raccoon signature.

    `eyes`: open · closed (resting, yawning) · squint (stretching, laughing)
    `mouth`: smile · open (yawn) · small · grin
    """
    ear_y = cy - 30
    parts = [f'<g transform="rotate({tilt} 100 {cy})">' if tilt else "<g>"]
    parts.append(f'''    <circle cx="72" cy="{ear_y}" r="14" fill="{FUR_DARK}"/>
    <circle cx="72" cy="{ear_y + 2}" r="7" fill="{PALE}"/>
    <circle cx="128" cy="{ear_y}" r="14" fill="{FUR_DARK}"/>
    <circle cx="128" cy="{ear_y + 2}" r="7" fill="{PALE}"/>
    <circle cx="100" cy="{cy}" r="44" fill="{FUR}"/>
    <ellipse cx="100" cy="{cy + 15}" rx="22" ry="16" fill="{PALE}"/>
    <ellipse cx="82" cy="{cy - 2}" rx="20" ry="14" fill="{MASK}"/>
    <ellipse cx="118" cy="{cy - 2}" rx="20" ry="14" fill="{MASK}"/>''')

    if eyes == "closed":
        parts.append(f'''    <path d="M74,{cy - 2} Q82,{cy + 5} 90,{cy - 2}" stroke="{INK}" stroke-width="3" fill="none" stroke-linecap="round"/>
    <path d="M110,{cy - 2} Q118,{cy + 5} 126,{cy - 2}" stroke="{INK}" stroke-width="3" fill="none" stroke-linecap="round"/>''')
    elif eyes == "squint":
        parts.append(f'''    <path d="M74,{cy} Q82,{cy - 6} 90,{cy}" stroke="{INK}" stroke-width="3" fill="none" stroke-linecap="round"/>
    <path d="M110,{cy} Q118,{cy - 6} 126,{cy}" stroke="{INK}" stroke-width="3" fill="none" stroke-linecap="round"/>''')
    else:
        parts.append(f'''    <circle cx="82" cy="{cy - 2}" r="9" fill="#FFFFFF"/>
    <circle cx="118" cy="{cy - 2}" r="9" fill="#FFFFFF"/>
    <circle cx="84" cy="{cy}" r="4.5" fill="{INK}"/>
    <circle cx="116" cy="{cy}" r="4.5" fill="{INK}"/>
    <circle cx="86" cy="{cy - 3}" r="1.5" fill="#FFFFFF"/>
    <circle cx="118" cy="{cy - 3}" r="1.5" fill="#FFFFFF"/>''')

    parts.append(f'    <ellipse cx="100" cy="{cy + 12}" rx="5" ry="4" fill="{INK}"/>')
    if mouth == "open":
        parts.append(f'    <ellipse cx="100" cy="{cy + 24}" rx="9" ry="11" fill="{INK}"/>')
    elif mouth == "grin":
        parts.append(f'    <path d="M86,{cy + 20} Q100,{cy + 32} 114,{cy + 20}" stroke="#374151" stroke-width="3" fill="none" stroke-linecap="round"/>')
    elif mouth == "small":
        parts.append(f'    <path d="M94,{cy + 22} Q100,{cy + 26} 106,{cy + 22}" stroke="#374151" stroke-width="2.5" fill="none" stroke-linecap="round"/>')
    else:
        parts.append(f'    <path d="M90,{cy + 22} Q100,{cy + 28} 110,{cy + 22}" stroke="#374151" stroke-width="2.5" fill="none" stroke-linecap="round"/>')
    parts.append("  </g>")
    return "\n".join(parts)


def svg(*layers: str) -> str:
    inner = "\n".join(layer for layer in layers if layer)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" '
            f'width="200" height="200">\n{inner}\n</svg>\n')


POSES: dict[str, str] = {}

# ── Walking: two frames. Legs swap, arms swing opposite, tail counter-swings,
#    body drops a couple of pixels on the planted foot. Two frames is enough for
#    a walk at this scale, and four would only be four chances to look wrong.
POSES["walk_a"] = svg(
    tail(-14), leg((90, 148), (72, 176)), leg((110, 148), (124, 174)),
    body(121), arm((122, 108), (148, 126)), arm((78, 108), (52, 130), pale_palm=True),
    head(71, mouth="smile"))

POSES["walk_b"] = svg(
    tail(10), leg((90, 148), (78, 178)), leg((110, 148), (128, 168)),
    body(119), arm((122, 108), (146, 138)), arm((78, 108), (50, 112), pale_palm=True),
    head(69, mouth="smile"))

# ── Hop: both feet tucked, arms up, tail streaming. Mid-air, so everything
#    sits a little higher than it does on the ground.
POSES["hop"] = svg(
    tail(-34), leg((90, 142), (78, 162)), leg((110, 142), (122, 162)),
    body(114), arm((122, 102), (152, 78)), arm((78, 102), (48, 78), pale_palm=True),
    head(62, eyes="squint", mouth="grin"))

# ── Sitting: legs forward, tail curled round, hands in lap. His resting state,
#    and the one he should be in most of the time.
POSES["sit"] = svg(
    tail_curled(), leg((92, 164), (72, 176)), leg((108, 164), (128, 176)),
    body(128), arm((120, 122), (134, 150)), arm((80, 122), (66, 150), pale_palm=True),
    head(80, mouth="small"))

# ── Stretch: arms straight up, back arched, eyes squeezed shut. The pose every
#    stretching animal makes, which is the joke.
POSES["stretch"] = svg(
    tail(-22), leg((90, 150), (86, 180)), leg((110, 150), (114, 180)),
    body(122), arm((122, 106), (150, 58)), arm((78, 106), (50, 58), pale_palm=True),
    head(68, eyes="closed", mouth="open"))

# ── Yawn: eyes shut, mouth wide, one hand up near the face.
POSES["yawn"] = svg(
    tail(16), leg((90, 150), (86, 180)), leg((110, 150), (114, 180)),
    body(122), arm((122, 108), (140, 76)), arm((78, 108), (52, 128), pale_palm=True),
    head(70, eyes="closed", mouth="open", tilt=-6))

# ── Peeking: only the head and one hand, sitting at the bottom of the frame so
#    it can be positioned as if he is looking over an edge.
POSES["peek"] = svg(
    head(96, eyes="open", mouth="small"),
    # The hands come last so they sit ON the edge rather than behind it.
    f'''  <circle cx="58" cy="150" r="13" fill="{FUR}"/>
  <circle cx="58" cy="150" r="8" fill="{PALE}"/>
  <circle cx="142" cy="150" r="13" fill="{FUR}"/>
  <rect x="0" y="156" width="200" height="44" rx="6" fill="#D1D5DB"/>''')

# ── Holding an acorn, examining it. Raccoons have famously dextrous hands, and
#    acorns are already the app's currency.
POSES["acorn"] = svg(
    tail_curled(), leg((92, 164), (72, 176)), leg((108, 164), (128, 176)),
    body(128),
    arm((120, 120), (108, 144)), arm((80, 120), (92, 144), pale_palm=True),
    f'''  <ellipse cx="100" cy="146" rx="11" ry="13" fill="{ACORN}"/>
  <path d="M89,141 Q100,132 111,141 Z" fill="{ACORN_CAP}"/>
  <line x1="100" y1="132" x2="100" y2="127" stroke="{ACORN_CAP}" stroke-width="3" stroke-linecap="round"/>''',
    head(80, eyes="squint", mouth="small"))

# ── Resting: lying down, eyes shut, tail draped. Doing absolutely nothing, on
#    purpose — the brief asks for it and it is the most characterful pose here.
POSES["rest"] = svg(
    tail_curled((122, 168)),
    f'  <ellipse cx="96" cy="152" rx="52" ry="28" fill="{FUR}"/>',
    f'  <ellipse cx="96" cy="160" rx="32" ry="16" fill="{PALE}"/>',
    arm((116, 148), (150, 164)), arm((70, 148), (40, 164), pale_palm=True),
    head(112, eyes="closed", mouth="small", tilt=-8),
    # Asleep, and saying so.
    '''  <path d="M150,84 q9,-5 0,-11 q9,-5 0,-11" stroke="#9CA3AF" stroke-width="3.5"
        fill="none" stroke-linecap="round" stroke-linejoin="round"/>''')

# ── Celebrating: both arms thrown up, on tiptoe, tail high.
POSES["cheer"] = svg(
    tail(-40), leg((90, 148), (84, 178)), leg((110, 148), (116, 178)),
    body(120), arm((122, 104), (152, 66)), arm((78, 104), (48, 66), pale_palm=True),
    head(66, eyes="squint", mouth="grin"),
    '''  <path d="M30,50 L34,42 L38,50 L34,58 Z" fill="#F59E0B"/>
  <path d="M164,46 L168,38 L172,46 L168,54 Z" fill="#F59E0B"/>
  <circle cx="46" cy="38" r="2.5" fill="#F59E0B"/>
  <circle cx="154" cy="34" r="2.5" fill="#F59E0B"/>''')

def main() -> int:
    for name, markup in POSES.items():
        path = OUT / f"rickie_{name}.svg"
        path.write_text(markup, encoding="utf-8")
        print(f"  {path.name:26} {len(markup):5} bytes")
    print(f"{len(POSES)} poses written to static/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
