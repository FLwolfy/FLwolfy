from pathlib import Path
import html

TXT_FILE = "image.txt"
ABOUT_FILE = "about.md"
OUT_FILE = "card.svg"

name = "Aaron Liao"
username = "FLwolfy"
email = "hsuankailiao@gmail.com"
tagline = "Undergraduate Student · COMPSCI · Duke University"

W, H = 1400, 860

BG = "#0d1117"
CARD = "#161b22"
BORDER = "#30363d"
TEXT = "#e6edf3"
MUTED = "#8b949e"
FLAG = "#1f6feb"

CODE_BG = "#0f141b"
CODE_BORDER = "#2f3942"
CODE_HEADER = "#161b22"
CODE_TEXT = "#c9d1d9"
CODE_MUTED = "#7d8590"


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def normalize_tabs(s: str, tabsize: int = 4) -> str:
    return s.expandtabs(tabsize)


def svg_preserve_line(s: str) -> str:
    # Preserve spaces exactly for SVG text rendering.
    # Use NBSP so leading/trailing/multiple spaces survive reliably.
    if s == "":
        return "\u00A0"
    return s.replace(" ", "\u00A0")


def draw_text_lines(
    lines,
    x,
    first_baseline_y,
    size,
    fill,
    line_height,
    clip_id=None,
):
    parts = []
    if clip_id:
        parts.append(f'<g clip-path="url(#{clip_id})">')
    y = first_baseline_y
    for line in lines:
        line = svg_preserve_line(line)
        parts.append(
            f'<text x="{x}" y="{y}" '
            f'font-size="{size}" fill="{fill}" '
            f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
            f'xml:space="preserve">{esc(line)}</text>'
        )
        y += size * line_height
    if clip_id:
        parts.append('</g>')
    return "\n".join(parts)


# ----------------------------
# Load source files
# ----------------------------
ascii_raw = Path(TXT_FILE).read_text(encoding="utf-8")
ascii_lines = [normalize_tabs(line.rstrip("\n\r")) for line in ascii_raw.splitlines()]
if not ascii_lines:
    ascii_lines = [""]

about_raw = Path(ABOUT_FILE).read_text(encoding="utf-8")
about_lines = [normalize_tabs(line.rstrip("\n\r")) for line in about_raw.splitlines()]
if not about_lines:
    about_lines = [""]

# ----------------------------
# Card geometry
# ----------------------------
outer_pad = 36
card_x = outer_pad
card_y = outer_pad
card_w = W - outer_pad * 2
card_h = H - outer_pad * 2

info_h = 74
info_y = card_y + card_h - info_h

# ----------------------------
# Text metrics
# ----------------------------
ascii_font_size = 14
ascii_line_height = 1.18
char_px = ascii_font_size * 0.62
line_px = ascii_font_size * ascii_line_height

code_font_size = 16
code_line_height = 1.4
code_char_px = code_font_size * 0.61
code_line_px = code_font_size * code_line_height

# ----------------------------
# Right-side ASCII frame layout
# ----------------------------
frame_right_margin = 16
frame_top_padding = 48
frame_bottom_gap = 24

FIXED_COLS = 48
cols = FIXED_COLS

frame_w = cols * char_px
frame_x = card_x + card_w - frame_right_margin - frame_w
frame_y = card_y + frame_top_padding

frame_h = info_y - frame_y - frame_bottom_gap
rows = max(4, int(frame_h / line_px))
frame_h = rows * line_px

top_border = "+" + ("-" * (cols - 2)) + "+"
middle_border = "|" + (" " * (cols - 2)) + "|"
bottom_border = top_border

frame_lines = [top_border]
for _ in range(rows - 2):
    frame_lines.append(middle_border)
frame_lines.append(bottom_border)

inner_x = frame_x + char_px
inner_y = frame_y + line_px
inner_w = frame_w - char_px * 2
inner_h = frame_h - line_px * 2

CROP_PAD_X = char_px * 1.0
CROP_PAD_Y = line_px * 0.45

crop_x = inner_x + CROP_PAD_X
crop_y = inner_y + CROP_PAD_Y
crop_w = max(1.0, inner_w - CROP_PAD_X * 2)
crop_h = max(1.0, inner_h - CROP_PAD_Y * 2)

ascii_max_chars = max(len(line) for line in ascii_lines) if ascii_lines else 0
ascii_line_count = len(ascii_lines)

ascii_block_w = ascii_max_chars * char_px
ascii_block_h = ascii_line_count * line_px

# Full block centered first, then cropped by clip path.
ascii_left_x = crop_x + (crop_w - ascii_block_w) / 2.0
ascii_first_y = crop_y + (crop_h - ascii_block_h) / 2.0 + ascii_font_size

# ----------------------------
# Left-side raw markdown code block
# ----------------------------
left_x = card_x + 36
left_y = card_y + 150
left_w = frame_x - left_x - 48
left_h = info_y - left_y - 28

code_header_h = 36
code_pad_x = 18
code_pad_y = 16

code_body_x = left_x + code_pad_x
code_body_y = left_y + code_header_h + code_pad_y
code_body_w = left_w - code_pad_x * 2
code_body_h = left_h - code_header_h - code_pad_y * 2

# Keep exact lines/blank lines; only clip visually by SVG clipPath.
code_first_y = code_body_y + code_font_size

parts = [
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
    "<defs>",
    f'<clipPath id="asciiClip"><rect x="{crop_x}" y="{crop_y}" width="{crop_w}" height="{crop_h}" /></clipPath>',
    f'<clipPath id="codeClip"><rect x="{code_body_x}" y="{code_body_y}" width="{code_body_w}" height="{code_body_h}" /></clipPath>',
    "</defs>",
    f'<rect width="100%" height="100%" fill="{BG}"/>'
]

parts.append(
    f'<rect x="{card_x}" y="{card_y}" width="{card_w}" height="{card_h}" '
    f'rx="20" fill="{CARD}" stroke="{BORDER}"/>'
)

# Decorative triangle
flag_x1 = card_x
flag_y1 = card_y + card_h * 0.48
flag_x2 = card_x
flag_y2 = card_y
flag_x3 = card_x + card_w * 0.60
flag_y3 = card_y

parts.append(
    f'<polygon points="{flag_x1},{flag_y1} {flag_x2},{flag_y2} {flag_x3},{flag_y3}" '
    f'fill="{FLAG}" opacity="0.18"/>'
)

# Header
hi_x = card_x + 36
hi_y = card_y + 58
parts.append(
    f'<text x="{hi_x}" y="{hi_y}" font-size="28" fill="{TEXT}" '
    f'font-family="Inter, Arial, sans-serif" font-weight="700">👋 Hi, I&apos;m</text>'
)

name_x = hi_x
name_y = hi_y + 58
parts.append(
    f'<text x="{name_x}" y="{name_y}" font-size="46" fill="{TEXT}" '
    f'text-decoration="underline" '
    f'font-family="Inter, Arial, sans-serif" font-weight="900">{esc(name)}</text>'
)

# Code block shell
parts.append(
    f'<rect x="{left_x}" y="{left_y}" width="{left_w}" height="{left_h}" '
    f'rx="14" fill="{CODE_BG}" stroke="{CODE_BORDER}"/>'
)
parts.append(
    f'<rect x="{left_x}" y="{left_y}" width="{left_w}" height="{code_header_h}" '
    f'rx="14" fill="{CODE_HEADER}"/>'
)
parts.append(
    f'<circle cx="{left_x + 18}" cy="{left_y + 18}" r="4.5" fill="{CODE_MUTED}" opacity="0.85"/>'
)
parts.append(
    f'<circle cx="{left_x + 34}" cy="{left_y + 18}" r="4.5" fill="{CODE_MUTED}" opacity="0.65"/>'
)
parts.append(
    f'<circle cx="{left_x + 50}" cy="{left_y + 18}" r="4.5" fill="{CODE_MUTED}" opacity="0.45"/>'
)
parts.append(
    f'<text x="{left_x + 72}" y="{left_y + 23}" font-size="14" fill="{CODE_MUTED}" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">about.me</text>'
)

# Exact raw code text with preserved spaces and blank lines
parts.append(
    draw_text_lines(
        about_lines,
        code_body_x,
        code_first_y,
        size=code_font_size,
        fill=CODE_TEXT,
        line_height=code_line_height,
        clip_id="codeClip",
    )
)

# ASCII border frame
parts.append(
    draw_text_lines(
        frame_lines,
        frame_x,
        frame_y + ascii_font_size,
        size=ascii_font_size,
        fill=TEXT,
        line_height=ascii_line_height,
    )
)

# Full ASCII content, centered first, then cropped with padding.
parts.append(
    draw_text_lines(
        ascii_lines,
        ascii_left_x,
        ascii_first_y,
        size=ascii_font_size,
        fill=TEXT,
        line_height=ascii_line_height,
        clip_id="asciiClip",
    )
)

# Bottom info strip
parts.append(
    f'<rect x="{card_x}" y="{info_y}" width="{card_w}" height="{info_h}" '
    f'rx="0" fill="#11161d" opacity="0.95"/>'
)

info_text_y = info_y + 45
info_pad_x = 28

parts.append(
    f'<text x="{card_x + info_pad_x}" y="{info_text_y}" font-size="18" fill="{MUTED}" '
    f'font-family="Inter, Arial, sans-serif">@{esc(username)}</text>'
)

parts.append(
    f'<text x="{card_x + card_w * 0.34}" y="{info_text_y}" font-size="18" fill="{MUTED}" '
    f'font-family="Inter, Arial, sans-serif">{esc(email)}</text>'
)

parts.append(
    f'<text x="{card_x + card_w - info_pad_x}" y="{info_text_y}" font-size="18" fill="{MUTED}" '
    f'font-family="Inter, Arial, sans-serif" text-anchor="end">{esc(tagline)}</text>'
)

parts.append("</svg>")

Path(OUT_FILE).write_text("\n".join(parts), encoding="utf-8")
print(f"Generated {OUT_FILE}")
