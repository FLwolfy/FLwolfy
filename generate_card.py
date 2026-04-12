from pathlib import Path
import html
import json
import re

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"


def load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


def load_svg_for_embed(svg_path: Path) -> tuple[str, float, float]:
    raw = svg_path.read_text(encoding="utf-8")

    # Strip XML declaration if present.
    raw = re.sub(r"^\s*<\?xml[^>]*>\s*", "", raw, flags=re.IGNORECASE)

    svg_open_match = re.search(r"<svg\b[^>]*>", raw, flags=re.IGNORECASE | re.DOTALL)
    if not svg_open_match:
        raise ValueError(f"Invalid SVG (missing <svg>): {svg_path}")
    svg_open = svg_open_match.group(0)

    viewbox_match = re.search(r'viewBox="([^"]+)"', svg_open, flags=re.IGNORECASE)
    if viewbox_match:
        vb = [float(x) for x in viewbox_match.group(1).replace(",", " ").split()]
        if len(vb) == 4:
            _, _, vb_w, vb_h = vb
        else:
            vb_w, vb_h = 300.0, 140.0
    else:
        w_match = re.search(r'width="([^"]+)"', svg_open, flags=re.IGNORECASE)
        h_match = re.search(r'height="([^"]+)"', svg_open, flags=re.IGNORECASE)
        vb_w = float(re.sub(r"[^\d.]+", "", w_match.group(1))) if w_match else 300.0
        vb_h = float(re.sub(r"[^\d.]+", "", h_match.group(1))) if h_match else 140.0

    inner_match = re.search(r"<svg\b[^>]*>(.*)</svg>\s*$", raw, flags=re.IGNORECASE | re.DOTALL)
    if not inner_match:
        raise ValueError(f"Invalid SVG content: {svg_path}")
    inner = inner_match.group(1)

    return inner, vb_w, vb_h


config = load_config(CONFIG_FILE)

files = config["files"]
colors = config["colors"]

txt_file = files["ascii_art_file"]
about_file = files["about_file"]
out_file = files["output_file"]
metadata_file = files["metadata_file"]
stats_svg_file = files["stats_svg_file"]
top_langs_svg_file = files["top_langs_svg_file"]

metadata = load_config(BASE_DIR / metadata_file)

name = metadata["name"]
username = metadata["username"]
email = metadata["email"]
tagline = metadata["tagline"]

W, H = 1400, 860

BG = colors["background"]
CARD = colors["card"]
BORDER = colors["border"]
TEXT = colors["text"]
MUTED = colors["muted"]
FLAG = colors["flag"]
ACCENT_SECONDARY = colors["accent_secondary"]
EDGE_HIGHLIGHT = colors["edge_highlight"]

CODE_BG = colors["code_background"]
CODE_BORDER = colors["code_border"]
CODE_HEADER = colors["code_header"]
CODE_TEXT = colors["code_text"]
CODE_MUTED = colors["code_muted"]
INFO_STRIP = colors["info_strip"]


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
ascii_raw = (BASE_DIR / txt_file).read_text(encoding="utf-8")
ascii_lines = [normalize_tabs(line.rstrip("\n\r")) for line in ascii_raw.splitlines()]
if not ascii_lines:
    ascii_lines = [""]

about_raw = (BASE_DIR / about_file).read_text(encoding="utf-8")
about_lines = [normalize_tabs(line.rstrip("\n\r")) for line in about_raw.splitlines()]
if not about_lines:
    about_lines = [""]

stats_inner, stats_vb_w, stats_vb_h = load_svg_for_embed(BASE_DIR / stats_svg_file)
langs_inner, langs_vb_w, langs_vb_h = load_svg_for_embed(BASE_DIR / top_langs_svg_file)
stats_aspect = stats_vb_w / stats_vb_h
langs_aspect = langs_vb_w / langs_vb_h

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
frame_top_padding = 34
shared_bottom_gap = 28
ascii_to_stats_gap = 4
stats_to_info_gap = shared_bottom_gap

FIXED_COLS = 48
cols = FIXED_COLS

frame_w = cols * char_px
frame_x = card_x + card_w - frame_right_margin - frame_w
frame_y = card_y + frame_top_padding

# Stats pair layout:
# 1) fixed total width,
# 2) equal height,
# 3) height solved from width and intrinsic aspect ratios.
stats_gap = 8
stats_left_padding = 0.0
stats_right_padding = 10.0
stats_total_w = frame_w - stats_left_padding - stats_right_padding
stats_section_h = (stats_total_w - stats_gap) / (stats_aspect + langs_aspect)
stats_box_w1 = stats_aspect * stats_section_h
stats_box_w2 = langs_aspect * stats_section_h
stats_x1 = frame_x + stats_left_padding
stats_x2 = stats_x1 + stats_box_w1 + stats_gap
frame_bottom_gap = ascii_to_stats_gap + stats_section_h + stats_to_info_gap

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
left_w = frame_x - left_x - 28
left_h = info_y - left_y - shared_bottom_gap

code_header_h = 36
code_pad_x = 18
code_pad_y = 16

code_body_x = left_x + code_pad_x
code_body_y = left_y + code_header_h + code_pad_y
code_body_w = left_w - code_pad_x * 2
code_body_h = left_h - code_header_h - code_pad_y * 2

# Keep exact lines/blank lines; only clip visually by SVG clipPath.
code_first_y = code_body_y + code_font_size

# ----------------------------
# Stats row (between ASCII and bottom stripe)
# ----------------------------
stats_y = info_y - stats_to_info_gap - stats_section_h
stats_box_h = stats_section_h

parts = [
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
    "<defs>",
    f'<clipPath id="asciiClip"><rect x="{crop_x}" y="{crop_y}" width="{crop_w}" height="{crop_h}" /></clipPath>',
    f'<clipPath id="codeClip"><rect x="{code_body_x}" y="{code_body_y}" width="{code_body_w}" height="{code_body_h}" /></clipPath>',
    f'<clipPath id="statsClip"><rect x="{frame_x}" y="{stats_y}" width="{frame_w}" height="{stats_box_h}" /></clipPath>',
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
parts.append(
    f'<line x1="{flag_x1}" y1="{flag_y1}" x2="{flag_x3}" y2="{flag_y3}" '
    f'stroke="{EDGE_HIGHLIGHT}" stroke-opacity="0.24" stroke-width="2.2" stroke-linecap="round"/>'
)

# Decorative triangle (bottom-right mirror)
triangle_right_gap = stats_right_padding + 50.0
flag2_x1 = card_x + card_w - triangle_right_gap
flag2_y1 = card_y + card_h * 0.52
flag2_x2 = card_x + card_w - triangle_right_gap
flag2_y2 = card_y + card_h
# Match the left triangle's hypotenuse slope.
left_slope_abs = (flag_y1 - flag_y3) / (flag_x3 - flag_x1)
flag2_x3 = flag2_x1 - (flag2_y2 - flag2_y1) / left_slope_abs
flag2_y3 = card_y + card_h

parts.append(
    f'<polygon points="{flag2_x1},{flag2_y1} {flag2_x2},{flag2_y2} {flag2_x3},{flag2_y3}" '
    f'fill="{ACCENT_SECONDARY}" opacity="0.22"/>'
)
parts.append(
    f'<line x1="{flag2_x1}" y1="{flag2_y1}" x2="{flag2_x3}" y2="{flag2_y3}" '
    f'stroke="{EDGE_HIGHLIGHT}" stroke-opacity="0.20" stroke-width="2.2" stroke-linecap="round"/>'
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

# Stats cards row
parts.append('<g clip-path="url(#statsClip)">')
parts.append(
    f'<svg x="{stats_x1}" y="{stats_y}" width="{stats_box_w1}" height="{stats_box_h}" '
    f'viewBox="0 0 {stats_vb_w} {stats_vb_h}" preserveAspectRatio="xMidYMid meet">{stats_inner}</svg>'
)
parts.append(
    f'<svg x="{stats_x2}" y="{stats_y}" width="{stats_box_w2}" height="{stats_box_h}" '
    f'viewBox="0 0 {langs_vb_w} {langs_vb_h}" preserveAspectRatio="xMidYMid meet">{langs_inner}</svg>'
)
parts.append('</g>')

# Bottom info strip
parts.append(
    f'<rect x="{card_x}" y="{info_y}" width="{card_w}" height="{info_h}" '
    f'rx="0" fill="{INFO_STRIP}" opacity="0.95"/>'
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

out_path = BASE_DIR / out_file
out_path.write_text("\n".join(parts), encoding="utf-8")
print(f"Generated {out_file}")
