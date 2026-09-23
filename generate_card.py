from pathlib import Path
import base64
import html
import json
import os
import re
import subprocess
import sys
import unicodedata

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"


TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def resolve_key(root: dict, key: str):
    node = root
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def resolve_templates(value, context: dict):
    if isinstance(value, dict):
        return {k: resolve_templates(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_templates(v, context) for v in value]
    if not isinstance(value, str):
        return value

    def repl(match: re.Match) -> str:
        resolved = resolve_key(context, match.group(1))
        return "N/A" if resolved is None else str(resolved)

    return TEMPLATE_RE.sub(repl, value)


def load_json_with_templates(json_path: Path) -> dict:
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return raw
    resolved = raw
    for _ in range(5):
        next_resolved = resolve_templates(resolved, resolved)
        if next_resolved == resolved:
            break
        resolved = next_resolved
    return resolved


def load_config(config_path: Path) -> dict:
    return load_json_with_templates(config_path)


def load_json_optional(json_path: Path) -> dict:
    if not json_path.exists():
        return {}
    return load_json_with_templates(json_path)


def render_about_template(text: str, data: dict) -> str:
    if not data:
        return text

    def repl(match: re.Match) -> str:
        key = match.group(1)
        resolved = resolve_key(data, key)
        return "N/A" if resolved is None else str(resolved)

    return TEMPLATE_RE.sub(repl, text)


def token_path_to_css_var(path: str) -> str:
    css_name = path.replace("_", "-").replace(".", "-")
    return f"--{css_name}"


def load_theme_css_vars(css_path: Path) -> dict[str, str]:
    text = css_path.read_text(encoding="utf-8")
    matches = re.findall(r"(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);", text)
    out: dict[str, str] = {}
    for name, raw_val in matches:
        val = raw_val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[name] = val
    return out


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

includes = config.get("includes") or config.get("files")
if not isinstance(includes, dict):
    raise KeyError("Missing config.includes")

outputs_config = config.get("outputs", {})
output_base_name = outputs_config.get("base_name", "card")
output_directory = outputs_config.get("directory", ".")
output_dir = BASE_DIR / output_directory

color_themes = config.get("themes", {})
if not isinstance(color_themes, dict) or not color_themes:
    color_config = config.get("colors", {})
    color_themes = color_config.get("themes", {})
if not isinstance(color_themes, dict) or not color_themes:
    legacy_file = config.get("color_file")
    if not isinstance(legacy_file, str) or not legacy_file:
        legacy_file = config.get("colors", {}).get("file")
    if isinstance(legacy_file, str) and legacy_file:
        color_themes = {"default": legacy_file}
    else:
        raise KeyError("Missing themes")

selected_theme = os.environ.get("PROFILE_CARD_RENDER_THEME", "").strip()
selected_output = os.environ.get("PROFILE_CARD_RENDER_OUTPUT", "").strip()
suppress_single_output_log = os.environ.get("PROFILE_CARD_SUPPRESS_SINGLE_LOG", "").strip() == "1"

if not selected_theme:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_paths: list[str] = []
    script_path = str(Path(__file__).resolve())
    for theme_name in color_themes:
        target_path = output_dir / f"{output_base_name}-{theme_name}.svg"
        env = os.environ.copy()
        env["PROFILE_CARD_RENDER_THEME"] = theme_name
        env["PROFILE_CARD_RENDER_OUTPUT"] = str(target_path)
        env["PROFILE_CARD_SUPPRESS_SINGLE_LOG"] = "1"
        subprocess.run([sys.executable, script_path], check=True, env=env)
        try:
            generated_paths.append(str(target_path.relative_to(BASE_DIR)))
        except ValueError:
            generated_paths.append(str(target_path))
    print("Generated:", ", ".join(generated_paths))
    raise SystemExit(0)

if selected_theme not in color_themes:
    raise KeyError(f"Unknown theme '{selected_theme}'. Available: {', '.join(color_themes)}")

color_file = color_themes[selected_theme]
color_path = BASE_DIR / color_file
color_vars: dict[str, str]
if color_path.suffix.lower() == ".css":
    color_vars = load_theme_css_vars(color_path)
else:
    # Backward compatibility for legacy JSON themes.
    color_theme = load_config(color_path)
    color_blocks = color_theme.get("blocks", {})
    if not isinstance(color_blocks, dict):
        raise KeyError(f"Missing blocks in {color_file}")
    color_vars = {}

    def flatten_tokens(node: dict, prefix: str = ""):
        for k, v in node.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                flatten_tokens(v, key)
            elif isinstance(v, str):
                color_vars[token_path_to_css_var(key)] = v

    flatten_tokens(color_blocks)

if selected_output:
    output_path = Path(selected_output)
else:
    output_path = output_dir / f"{output_base_name}-{selected_theme}.svg"
output_path.parent.mkdir(parents=True, exist_ok=True)

try:
    output_label = str(output_path.relative_to(BASE_DIR))
except ValueError:
    output_label = str(output_path)


def color_token(path: str) -> str:
    css_var = token_path_to_css_var(path)
    if css_var not in color_vars:
        raise KeyError(f"Missing color token: {path} as {css_var} (in {color_file})")
    return color_vars[css_var]


def color_token_optional(path: str, fallback: str) -> str:
    try:
        return color_token(path)
    except KeyError:
        return fallback


def invert_hex_color(color: str) -> str:
    m = re.fullmatch(r"#([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?", color.strip())
    if not m:
        return color
    rgb = m.group(1)
    alpha = m.group(2) or ""
    r = 255 - int(rgb[0:2], 16)
    g = 255 - int(rgb[2:4], 16)
    b = 255 - int(rgb[4:6], 16)
    return f"#{r:02x}{g:02x}{b:02x}{alpha}"


def invert_braille_text(text: str) -> str:
    # Invert Unicode Braille patterns (U+2800..U+28FF) by flipping all 8 dot bits.
    out_chars: list[str] = []
    for ch in text:
        cp = ord(ch)
        if 0x2800 <= cp <= 0x28FF:
            out_chars.append(chr(0x2800 + ((cp - 0x2800) ^ 0xFF)))
        else:
            out_chars.append(ch)
    return "".join(out_chars)


def delay_svg_animations(svg_inner: str, delay_sec: float) -> str:
    if delay_sec <= 0:
        return svg_inner

    tag_re = re.compile(
        r"<(animate(?:Transform|Motion|Color)?|set)\b([^>]*)>",
        flags=re.IGNORECASE,
    )
    begin_attr_re = re.compile(r'(\sbegin\s*=\s*")([^"]*)(")', flags=re.IGNORECASE)
    clock_re = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)(ms|s)?\s*$", flags=re.IGNORECASE)

    def shift_begin_value(raw: str) -> str:
        parts = raw.split(";")
        shifted: list[str] = []
        for p in parts:
            m = clock_re.fullmatch(p)
            if not m:
                shifted.append(p)
                continue
            value = float(m.group(1))
            unit = (m.group(2) or "s").lower()
            if unit == "ms":
                delay_unit = delay_sec * 1000.0
                value += delay_unit
                shifted.append(f"{value:g}ms")
            else:
                value += delay_sec
                shifted.append(f"{value:g}s")
        return ";".join(shifted)

    def repl(match: re.Match) -> str:
        tag = match.group(1)
        attrs = match.group(2)
        begin_match = begin_attr_re.search(attrs)
        if begin_match:
            shifted = shift_begin_value(begin_match.group(2))
            attrs = begin_attr_re.sub(
                lambda m: f'{m.group(1)}{shifted}{m.group(3)}',
                attrs,
                count=1,
            )
        else:
            attrs = f'{attrs} begin="{delay_sec:.2f}s"'
        return f"<{tag}{attrs}>"

    return tag_re.sub(repl, svg_inner)


def _shift_time_value(raw: str, delay_sec: float) -> str:
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)(ms|s)?\s*", raw, flags=re.IGNORECASE)
    if not m:
        return raw
    value = float(m.group(1))
    unit = (m.group(2) or "s").lower()
    if unit == "ms":
        return f"{value + delay_sec * 1000.0:g}ms"
    return f"{value + delay_sec:g}s"


def delay_css_animations(svg_inner: str, delay_sec: float) -> str:
    if delay_sec <= 0:
        return svg_inner

    # 1) Shift explicit animation-delay values in inline style attributes.
    svg_inner = re.sub(
        r"(animation-delay\s*:\s*)([^;\"']+)",
        lambda m: f"{m.group(1)}{_shift_time_value(m.group(2), delay_sec)}",
        svg_inner,
        flags=re.IGNORECASE,
    )

    # 2) In each CSS declaration block, append base animation-delay when animation exists.
    def patch_block(match: re.Match) -> str:
        body = match.group(1)
        if re.search(r"\banimation\s*:", body, flags=re.IGNORECASE) and not re.search(
            r"\banimation-delay\s*:", body, flags=re.IGNORECASE
        ):
            body = body.rstrip() + f" animation-delay: {delay_sec:.2f}s;"
        return "{" + body + "}"

    svg_inner = re.sub(r"\{([^{}]*)\}", patch_block, svg_inner)
    return svg_inner

txt_file = includes.get("braille_art_file") or includes.get("ascii_art_file")
if not txt_file:
    raise KeyError("Missing includes.braille_art_file (or legacy includes.ascii_art_file)")
about_file = includes["about_file"]
metadata_file = includes["metadata_file"]
stats_svg_file = includes["stats_svg_file"]
top_langs_svg_file = includes["top_langs_svg_file"]
quotes_file = includes["quotes_file"]
github_stats_json_file = includes.get("github_stats_json_file")
status_file = includes.get("status_file", "info/status.md")

metadata = load_config(BASE_DIR / metadata_file)

name = metadata["name"]
username = metadata["username"]
email = metadata["email"]
tagline = metadata["tagline"]

W, H = 1400, 860

BG = color_token("app.background")
CARD = color_token("app.card_surface")
TEXT = color_token("app.text_primary")
ASCII_ART_TEXT_BASE = color_token_optional("app.ascii_art_text", TEXT)
ASCII_AUTO_INVERT = color_token_optional("app.ascii_auto_invert", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
ASCII_ART_TEXT = ASCII_ART_TEXT_BASE
ASCII_INNER_BACKGROUND = color_token_optional("app.ascii_inner_background", "none")
MUTED = color_token("app.text_muted")
FLAG = color_token("app.decorative_flag")
ACCENT_SECONDARY = color_token("app.decorative_accent_secondary")
EDGE_HIGHLIGHT = color_token("app.decorative_edge_highlight")
CARD_OUTLINE_STROKE = color_token("app.card_outline_stroke")
INFO_STRIP = color_token("app.info_strip_background")

CODE_BG = color_token("code_window.background")
CODE_BORDER = color_token("code_window.border")
CODE_HEADER = color_token("code_window.header_background")
CODE_TEXT = color_token("code_window.text_primary")
CODE_MUTED = color_token("code_window.text_muted")
CODE_TRAFFIC_1 = color_token("code_window.traffic_dot_1")
CODE_TRAFFIC_2 = color_token("code_window.traffic_dot_2")
CODE_TRAFFIC_3 = color_token("code_window.traffic_dot_3")
TERMINAL_BG = color_token("code_window.terminal_background")
TERMINAL_BORDER = color_token("code_window.terminal_border")
TERMINAL_PROMPT = color_token("code_window.terminal_prompt")
TERMINAL_TEXT = color_token("code_window.terminal_text")
TERMINAL_CURSOR = color_token("code_window.terminal_cursor")

QUOTES_PANEL_BG = color_token("quotes_window.panel_background")
QUOTES_PANEL_STROKE = color_token("quotes_window.panel_stroke")
QUOTES_HEADER_BG = color_token("quotes_window.header_background")
QUOTES_TAB_BG = color_token("quotes_window.tab_background")
QUOTES_TAB_STROKE = color_token("quotes_window.tab_stroke")
QUOTES_TAB_TEXT = color_token("quotes_window.tab_text")
QUOTES_STATUS_TEXT = color_token("quotes_window.status_text")
QUOTES_GUTTER_BG = color_token("quotes_window.gutter_background")
QUOTES_GUTTER_STROKE = color_token("quotes_window.gutter_stroke")
QUOTES_LINE_NUM = color_token("quotes_window.line_number_text")
QUOTES_SCRAMBLE_TEXT = color_token("quotes_window.scramble_text")
QUOTES_TEXT = color_token("quotes_window.quote_text")
QUOTES_TITLE = color_token("quotes_window.title_text")

STATUS_PANEL_BG = color_token("status_window.panel_background")
STATUS_PANEL_STROKE = color_token("status_window.panel_stroke")
STATUS_HEADER_BG = color_token("status_window.header_background")
STATUS_HEADER_ACCENT = color_token("status_window.header_accent")
STATUS_HEADER_BUBBLE_BG = color_token("status_window.header_bubble_background")
STATUS_HEADER_BUBBLE_STROKE = color_token("status_window.header_bubble_stroke")
STATUS_HEADER_BUBBLE_TEXT = color_token("status_window.header_bubble_text")
STATUS_HEADER_TITLE_TEXT = color_token("status_window.header_title_text")
STATUS_CLOSE_STROKE = color_token("status_window.close_button_stroke")
STATUS_CLOSE_TEXT = color_token("status_window.close_button_text")
STATUS_MARKDOWN_TEXT = color_token("status_window.markdown_text")
STATUS_MARKDOWN_ERROR = color_token("status_window.markdown_error_text")
ABOUT_DEFAULT_TEXT = color_token("about_markup.default_text")
EMOJI_CACHE_FILE = BASE_DIR / includes.get("emoji_cache_file", "status/github_emojis_cache.json")


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def esc_attr(s: str) -> str:
    return html.escape(s, quote=True)


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
    char_px=None,
):
    parts = []
    if clip_id:
        parts.append(f'<g clip-path="url(#{clip_id})">')
    y = first_baseline_y
    for line in lines:
        raw_line = line
        line = svg_preserve_line(line)
        width_attrs = ""
        if char_px is not None:
            target_w = text_cells(raw_line) * char_px
            width_attrs = f' textLength="{target_w}" lengthAdjust="spacingAndGlyphs"'
        parts.append(
            f'<text x="{x}" y="{y}" '
            f'font-size="{size}" fill="{fill}" '
            f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
            f'xml:space="preserve"{width_attrs}>{esc(line)}</text>'
        )
        y += size * line_height
    if clip_id:
        parts.append('</g>')
    return "\n".join(parts)


def parse_about_segments(line: str) -> list[tuple[str, str, float, str | None]]:
    # Inline color tags:
    # [--about-markup-token]text
    # Example:
    # [--about-markup-label]Skills: [--about-markup-value]C#, C++, Python

    def decode_color(token: str) -> tuple[str, float]:
        fill = color_vars.get(token)
        if not fill:
            return ABOUT_DEFAULT_TEXT, 1.0
        return fill, 1.0

    s = line.rstrip("\n\r")
    if s == "":
        return [("", ABOUT_DEFAULT_TEXT, 1.0, None)]

    segments: list[tuple[str, str, float, str | None]] = []
    current_fill = ABOUT_DEFAULT_TEXT
    current_opacity = 1.0
    cursor = 0

    token_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)|\[(--[a-zA-Z0-9-]+)\]")

    def sanitize_href(href: str) -> str | None:
        href = href.strip()
        if not href:
            return None
        h = href.lower()
        if h.startswith(("http://", "https://", "mailto:", "tel:")):
            return href
        return None

    for m in token_re.finditer(s):
        if m.start() > cursor:
            text = s[cursor:m.start()]
            segments.append((text, current_fill, current_opacity, None))
        if m.group(1) is not None:
            link_text = m.group(1)
            link_href = sanitize_href(m.group(2))
            segments.append((link_text, current_fill, current_opacity, link_href))
        else:
            current_fill, current_opacity = decode_color(m.group(3))
        cursor = m.end()

    if cursor < len(s):
        segments.append((s[cursor:], current_fill, current_opacity, None))

    if not segments:
        return [("", ABOUT_DEFAULT_TEXT, 1.0, None)]
    return segments


def draw_about_lines(
    lines,
    x,
    first_baseline_y,
    size,
    line_height,
    char_px,
    clip_id=None,
):
    parts = []
    if clip_id:
        parts.append(f'<g clip-path="url(#{clip_id})">')
    y = first_baseline_y
    for line in lines:
        segs = parse_about_segments(line)
        x_cursor = x
        for seg_text, seg_fill, seg_opacity, seg_href in segs:
            if not seg_text:
                continue
            link_style = ' style="cursor:pointer; text-decoration:underline;"' if seg_href else ""
            text_node = (
                f'<text x="{x_cursor}" y="{y}" '
                f'font-size="{size}" fill="{seg_fill}" fill-opacity="{seg_opacity}" '
                f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
                f'xml:space="preserve"{link_style}>{esc(svg_preserve_line(seg_text))}</text>'
            )
            if seg_href:
                safe_href = esc_attr(seg_href)
                text_node = (
                    f'<a href="{safe_href}" target="_blank">'
                    f'<title>{esc(seg_href)}</title>'
                    f'{text_node}'
                    f'</a>'
                )
            parts.append(text_node)
            x_cursor += text_cells(seg_text) * char_px
        y += size * line_height
    if clip_id:
        parts.append("</g>")
    return "\n".join(parts)


def load_quotes(quotes_path: Path) -> list[str]:
    lines = quotes_path.read_text(encoding="utf-8").splitlines()
    quotes: list[list[str]] = []
    current: list[str] = []
    in_quote = False

    for line in lines:
        s = line.strip()
        if s.startswith("## QUOTE"):
            if current:
                quotes.append(current)
                current = []
            in_quote = True
            continue
        if not in_quote:
            continue
        if not s or s.startswith("#####") or s.startswith("####") or s.startswith("## "):
            continue
        if s.startswith("---"):
            current.append("——— " + s[3:].strip())
        else:
            current.append(s.strip('"'))

    if current:
        quotes.append(current)

    merged: list[str] = []
    for q in quotes:
        body_parts = [line for line in q if line and not line.startswith("—")]
        author_parts = [line for line in q if line.startswith("—")]
        body = " ".join(body_parts).strip()
        author = " ".join(author_parts).strip()
        if body and author:
            merged.append(f"{body}\n{author}")
        elif body:
            merged.append(body)
        elif author:
            merged.append(author)
    merged = [q for q in merged if q]
    if not merged:
        merged = ["No quotes found."]
    return merged


def extract_quotes_update_time(quotes_path: Path) -> str:
    text = quotes_path.read_text(encoding="utf-8")
    m = re.search(r"\[\s*([^\]]+?)\s*\]\s+LATEST QUOTES UPDATE TIME", text)
    if not m:
        return "N/A"
    return m.group(1).strip()


_github_emoji_map_cache: dict[str, str] | None = None


def _load_github_emoji_map_from_cache() -> dict[str, str]:
    global _github_emoji_map_cache
    if _github_emoji_map_cache is not None:
        return _github_emoji_map_cache

    try:
        if EMOJI_CACHE_FILE.exists():
            payload = json.loads(EMOJI_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                _github_emoji_map_cache = payload
                return _github_emoji_map_cache
    except Exception:
        pass

    _github_emoji_map_cache = {}
    try:
        EMOJI_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not EMOJI_CACHE_FILE.exists():
            EMOJI_CACHE_FILE.write_text("{}", encoding="utf-8")
    except Exception:
        pass
    return _github_emoji_map_cache


def _github_emoji_url_to_unicode(url: str) -> str | None:
    # GitHub unicode emoji URLs look like:
    # .../images/icons/emoji/unicode/1f512.png?v8
    m = re.search(r"/unicode/([0-9a-fA-F\-]+)\.png", url)
    if not m:
        return None
    cps = m.group(1).split("-")
    try:
        return "".join(chr(int(cp, 16)) for cp in cps)
    except Exception:
        return None


def decode_github_emoji_shortcode(token: str) -> str:
    s = token.strip()
    m = re.fullmatch(r":([a-z0-9_+\-]+):", s, flags=re.IGNORECASE)
    if not m:
        return s
    key = m.group(1).lower()

    emoji_map = _load_github_emoji_map_from_cache()
    url = emoji_map.get(key)
    if isinstance(url, str):
        decoded = _github_emoji_url_to_unicode(url)
        if decoded:
            return decoded

    # Keep original shortcode for custom/non-unicode GitHub emojis.
    return s


def parse_status_header_tag(md_text: str) -> tuple[str, str]:
    lines = md_text.splitlines()
    header = ""
    cleaned: list[str] = []
    i = 0

    # Keep leading blank lines untouched.
    while i < len(lines) and not lines[i].strip():
        cleaned.append(lines[i])
        i += 1

    # Parse optional header tag at first non-empty line.
    if i < len(lines):
        m = re.match(r"^\[#header\s+(.+?)\]\s*$", lines[i].strip(), flags=re.IGNORECASE)
        if m:
            header = decode_github_emoji_shortcode(m.group(1))
            i += 1
            # Drop consecutive blank lines right after header tag.
            while i < len(lines) and not lines[i].strip():
                i += 1

    # Keep the rest.
    cleaned.extend(lines[i:])
    return header, "\n".join(cleaned)


def wrap_text(text: str, max_chars: int, max_lines: int) -> list[str]:
    segments = text.split("\n")
    out_lines: list[str] = []
    truncated = False
    for seg in segments:
        words = seg.split()
        if not words:
            continue
        curr = words[0]
        for w in words[1:]:
            test = f"{curr} {w}"
            if text_cells(test) <= max_chars:
                curr = test
            else:
                out_lines.append(curr)
                curr = w
                if len(out_lines) >= max_lines:
                    truncated = True
                    break
        if truncated:
            break
        out_lines.append(curr)
        if len(out_lines) >= max_lines:
            if seg != segments[-1]:
                truncated = True
            break
    if not out_lines:
        out_lines = [""]
    if truncated:
        out_lines[-1] = (out_lines[-1][:-1] + "…") if len(out_lines[-1]) > 1 else "…"
    return out_lines


def scramble_chars(seed: int) -> list[str]:
    charset = "01@$#%&*+=?abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return [charset[(seed + 3) % len(charset)], charset[(seed + 17) % len(charset)], charset[(seed + 29) % len(charset)], charset[(seed + 41) % len(charset)]]


def char_cells(ch: str) -> float:
    if not ch:
        return 0.0
    if unicodedata.combining(ch):
        return 0.0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2.0
    return 1.0


def text_cells(text: str) -> float:
    return sum(char_cells(ch) for ch in text)


def wrap_text_by_width(text: str, max_width_px: float, char_px: float, max_lines: int = 3) -> list[str]:
    def width_px(s: str) -> float:
        return text_cells(s) * char_px

    s = text.strip()
    if not s:
        return [""]

    words = s.split()
    out: list[str] = []

    if len(words) == 1:
        # Fallback for single long token (no spaces): hard wrap by characters.
        token = words[0]
        cur = ""
        for ch in token:
            test = cur + ch
            if cur and width_px(test) > max_width_px:
                out.append(cur)
                cur = ch
                if len(out) >= max_lines:
                    break
            else:
                cur = test
        if len(out) < max_lines and cur:
            out.append(cur)
    else:
        cur = words[0]
        for w in words[1:]:
            test = f"{cur} {w}"
            if width_px(test) <= max_width_px:
                cur = test
            else:
                out.append(cur)
                cur = w
                if len(out) >= max_lines:
                    break
        if len(out) < max_lines:
            out.append(cur)

    if not out:
        out = [s]

    # Clamp lines and add ellipsis if truncated.
    if len(out) > max_lines:
        out = out[:max_lines]
    if " ".join(out).strip() != s and out:
        last = out[-1]
        while last and width_px(last + "…") > max_width_px:
            last = last[:-1]
        out[-1] = (last + "…") if last else "…"
    return out


def strip_markdown_inline(text: str) -> str:
    # Links: [label](url) -> label
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Inline code: `code` -> code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Bold/italic markers
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)
    # Strikethrough
    text = re.sub(r"~~(.*?)~~", r"\1", text)
    return text


def resolve_markdown_image_href(src: str, md_path: Path) -> str | None:
    src = src.strip()
    if not src:
        return None
    if src.startswith("http://") or src.startswith("https://"):
        return src

    img_path = (md_path.parent / src).resolve()
    if not img_path.exists() or not img_path.is_file():
        return None

    ext = img_path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(ext, "application/octet-stream")

    data = base64.b64encode(img_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def markdown_to_render_lines(md_text: str, max_chars: int) -> list[tuple[str, float, str]]:
    # Returns tuples: (text, size_scale, weight)
    out: list[tuple[str, float, str]] = []
    in_code = False
    for raw in md_text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            continue

        if in_code:
            text = line
            scale = 0.92
            weight = "400"
        else:
            s = line.lstrip()
            if not s:
                out.append(("", 1.0, "400"))
                continue
            image_match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", s)
            if image_match:
                alt = image_match.group(1).strip()
                src = image_match.group(2).strip()
                out.append((f"__IMG__|{alt}|{src}", 1.0, "400"))
                continue
            if s.startswith("### "):
                text = strip_markdown_inline(s[4:])
                scale = 1.04
                weight = "700"
            elif s.startswith("## "):
                text = strip_markdown_inline(s[3:])
                scale = 1.08
                weight = "700"
            elif s.startswith("# "):
                text = strip_markdown_inline(s[2:])
                scale = 1.14
                weight = "700"
            elif re.match(r"^[-*]\s+", s):
                text = "• " + strip_markdown_inline(re.sub(r"^[-*]\s+", "", s))
                scale = 1.0
                weight = "400"
            elif re.match(r"^\d+\.\s+", s):
                text = strip_markdown_inline(s)
                scale = 1.0
                weight = "400"
            elif s.startswith("> "):
                text = "│ " + strip_markdown_inline(s[2:])
                scale = 0.98
                weight = "400"
            else:
                text = strip_markdown_inline(s)
                scale = 1.0
                weight = "400"

        words = text.split()
        if not words:
            out.append(("", scale, weight))
            continue
        curr = words[0]
        for w in words[1:]:
            candidate = f"{curr} {w}"
            if text_cells(candidate) <= max_chars:
                curr = candidate
            else:
                out.append((curr, scale, weight))
                curr = w
        out.append((curr, scale, weight))

    if not out:
        out = [("No status.md found.", 1.0, "400")]
    return out


# ----------------------------
# Load source files
# ----------------------------
ascii_raw = (BASE_DIR / txt_file).read_text(encoding="utf-8")
ascii_lines = [normalize_tabs(line.rstrip("\n\r")) for line in ascii_raw.splitlines()]
if not ascii_lines:
    ascii_lines = [""]
if ASCII_AUTO_INVERT:
    ascii_lines = [invert_braille_text(line) for line in ascii_lines]

about_raw = (BASE_DIR / about_file).read_text(encoding="utf-8")
if github_stats_json_file:
    github_stats_data = load_json_optional(BASE_DIR / github_stats_json_file)
    about_template_data = dict(github_stats_data)
    stats_scope_name = Path(github_stats_json_file).stem
    about_template_data[stats_scope_name] = github_stats_data
    about_raw = render_about_template(about_raw, about_template_data)
about_lines = [normalize_tabs(line.rstrip("\n\r")) for line in about_raw.splitlines()]
if not about_lines:
    about_lines = [""]

quotes = load_quotes(BASE_DIR / quotes_file)
quotes_update_time = extract_quotes_update_time(BASE_DIR / quotes_file)
status_md_path = BASE_DIR / status_file
status_raw = status_md_path.read_text(encoding="utf-8") if status_md_path.exists() else ""
status_header_token, status_raw = parse_status_header_tag(status_raw)

stats_inner, stats_vb_w, stats_vb_h = load_svg_for_embed(BASE_DIR / stats_svg_file)
langs_inner, langs_vb_w, langs_vb_h = load_svg_for_embed(BASE_DIR / top_langs_svg_file)
stats_aspect = stats_vb_w / stats_vb_h
langs_aspect = langs_vb_w / langs_vb_h

# ----------------------------
# Card geometry
# ----------------------------
outer_pad = 0
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

CROP_PAD_LEFT = char_px * 1.0
CROP_PAD_RIGHT = char_px * 1.0 + 10.0
CROP_PAD_Y = line_px * 0.45
ASCII_RIGHT_EXPAND_PX = 10.0

crop_x = inner_x + CROP_PAD_LEFT
crop_y = inner_y + CROP_PAD_Y
crop_w = max(1.0, inner_w - CROP_PAD_LEFT - CROP_PAD_RIGHT + ASCII_RIGHT_EXPAND_PX)
crop_h = max(1.0, inner_h - CROP_PAD_Y * 2)

ascii_max_chars = max(text_cells(line) for line in ascii_lines) if ascii_lines else 0
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
left_y = card_y + 190
left_w = frame_x - left_x - 28
left_h = info_y - left_y - shared_bottom_gap

code_header_h = 36
code_pad_x = 18
code_pad_y = 16
code_terminal_h = 28
code_terminal_gap = 8

code_body_x = left_x + code_pad_x
code_body_y = left_y + code_header_h + code_pad_y
code_body_w = left_w - code_pad_x * 2
code_body_h = left_h - code_header_h - code_pad_y * 2 - code_terminal_h - code_terminal_gap
code_terminal_x = left_x
code_terminal_w = left_w
code_terminal_y = left_y + left_h - code_terminal_h

# Keep exact lines/blank lines; only clip visually by SVG clipPath.
code_first_y = code_body_y + code_font_size

# ----------------------------
# Quotes window (IDE-style, decorative, above/about overlay)
# ----------------------------
hi_x = card_x + 36
hi_y = card_y + 58
name_x = hi_x
name_font_size = 46.0
# Keep name close to "Hi, I'm" with near-zero padding.
name_y = hi_y + name_font_size + 24.0

# Absolute placement for daily.quotes panel.
quotes_x = 320.0
quotes_y = 28.0
quotes_right_x = left_x + left_w + 15.0
quotes_bottom_y = left_y + left_h + 10.0
quotes_w = max(320.0, quotes_right_x - quotes_x)
quotes_h = max(180.0, quotes_bottom_y - quotes_y)
quotes_header_h = 30.0
quotes_pad_x = 14.0
quotes_pad_y = 12.0
quotes_gutter_w = 32.0
quotes_gutter_inner_pad = 7.0
quotes_text_left_pad = 10.0

quote_font_size = 21.0
quotes_tab_w = 122.0
quotes_tab_h = quotes_header_h - 9.0
quotes_tab_x = quotes_x + 10.0
quotes_tab_y = quotes_y + 6.0
quotes_title_y = quotes_y + 20.0
quotes_utf_y = quotes_y + 20.0
quotes_utf_text = "UTF-8"
quotes_close_r = 6.0
quotes_close_x = quotes_x + quotes_w - 12.0
quotes_close_y = quotes_utf_y - 4.5
quotes_update_gap = 14.0
quotes_utf_x = (quotes_close_x - quotes_close_r) - quotes_update_gap
quotes_update_time_w_est = len(quotes_update_time) * 11 * 0.58
quotes_update_time_x = quotes_utf_x - (len(quotes_utf_text) * 11 * 0.58) - quotes_update_gap - quotes_update_time_w_est

quotes_body_x = quotes_x + quotes_gutter_w + quotes_text_left_pad
quotes_body_y = quotes_y + quotes_header_h + quotes_pad_y
quotes_body_w = quotes_w - (quotes_gutter_w + quotes_text_left_pad + quotes_pad_x)
quotes_body_h = quotes_h - quotes_header_h - quotes_pad_y * 2

# Name wraps before colliding with the quotes window.
name_char_px = name_font_size * 0.58
name_safe_gap = 1.0
name_max_width = max(140.0, quotes_x - name_x - name_safe_gap)
name_lines = wrap_text_by_width(name, name_max_width, name_char_px, max_lines=3)
name_line_step = name_font_size * 1.02
name_wrap_offset_y = max(0.0, max(0, len(name_lines) - 1) * (name_line_step * 0.55) - 7.0)
name_y -= name_wrap_offset_y

intro_name_begin = 0.18
intro_name_char_sec = 0.046
intro_name_line_gap_sec = 0.16
intro_window_dur = 0.46
intro_window_rise = 14
intro_ascii_dur = 0.44
intro_ascii_shift_x = 10
intro_after_name_gap_sec = 0.06
intro_window_stagger = 0.24

name_defs: list[str] = []
name_text_parts: list[str] = []
name_elapsed = 0.0
for i, nline in enumerate(name_lines):
    line_id = f"nameTypeClip{i}"
    baseline_y = name_y + i * name_line_step
    line_w = max(1.0, text_cells(nline) * name_char_px * 1.04)
    line_h = name_font_size * 1.16
    line_top = baseline_y - name_font_size * 0.93
    line_start = intro_name_begin + name_elapsed
    line_dur = max(0.30, text_cells(nline) * intro_name_char_sec)
    name_defs.append(
        f'<clipPath id="{line_id}"><rect x="{name_x}" y="{line_top}" width="0" height="{line_h}">'
        f'<animate attributeName="width" begin="{line_start:.2f}s" dur="{line_dur:.2f}s" values="0;{line_w:.2f}" fill="freeze"/>'
        f'</rect></clipPath>'
    )
    name_text_parts.append(
        f'<text x="{name_x}" y="{baseline_y}" font-size="{name_font_size}" fill="{TEXT}" text-decoration="underline" '
        f'font-family="Inter, Arial, sans-serif" font-weight="900" clip-path="url(#{line_id})">{esc(nline)}</text>'
    )
    name_elapsed += line_dur + intro_name_line_gap_sec

name_typing_total = max(0.0, name_elapsed - intro_name_line_gap_sec)
intro_after_name_begin = intro_name_begin + name_typing_total + intro_after_name_gap_sec
intro_misc_begin = intro_after_name_begin
intro_quotes_begin = intro_after_name_begin
intro_code_begin = intro_quotes_begin + intro_window_stagger
intro_status_begin = intro_code_begin + intro_window_stagger
intro_ascii_begin = intro_after_name_begin
stats_animation_delay = intro_misc_begin
stats_inner = delay_svg_animations(stats_inner, stats_animation_delay)
langs_inner = delay_svg_animations(langs_inner, stats_animation_delay)
stats_inner = delay_css_animations(stats_inner, stats_animation_delay)
langs_inner = delay_css_animations(langs_inner, stats_animation_delay)

wave_cycle_dur = 0.85
wave_repeat_count = max(1, int((name_typing_total + 0.2) / wave_cycle_dur + 0.5))
wave_cx = hi_x + 13.0
wave_cy = hi_y - 12.0
wave_text_x = hi_x - wave_cx
wave_text_y = hi_y - wave_cy
wave_scale_dur = max(0.55, name_typing_total + 0.2)

quote_line_height = 1.35
quote_line_px = quote_font_size * quote_line_height
# Title flourish.
#
# Both the petals and the two chevrons are vector art now:
#
#   * the petals are the exact outlines of the Javanese rerenggan glyphs
#     U+A9C1 / U+A9C2 that Windows uses ("Javanese Text");
#   * the chevrons are the exact outlines of the "precedes" / "succeeds" glyphs
#     U+227A / U+227B as Cambria Math draws them - curved arms plus the little
#     horizontal bar at the tip - which is the face browsers fall back to, and
#     which is why a plain "<" chevron looked wrong.
#
# Everything is in font units (em = 100, y down, origin on the baseline).  Each
# piece is anchored on the point that has to meet the other one: the petal moves
# by its hub disc, the chevron by its apex, and both are put on the same
# coordinates, so tip and hub merge into a single continuous shape.  No glyph and
# no font metric takes part in the title any more.
quote_title_line = "QUOTES OF THE DAY"
quote_title_petal_left = (
    "M137.06,47.41C134.29,50.18 131.28,53.07 128.03,56.08C124.77,59.09 121.35,62.04 117.77,64.92C114.19,67.8 110.49,70.53 106.67,73.12C102.84,75.71 99,77.98 95.14,79.93C91.28,81.88 87.45,83.44 83.64,84.59C79.83,85.75 76.14,86.33 72.56,86.33C69.86,86.33 67.23,85.98 64.67,85.28C62.12,84.58 59.83,83.54 57.81,82.18C55.79,80.81 54.17,79.09 52.95,77.03C51.73,74.96 51.12,72.56 51.12,69.82C51.12,67.87 51.43,66.15 52.05,64.65C52.67,63.15 53.47,61.9 54.44,60.89C55.42,59.88 56.53,59.11 57.76,58.59C59,58.07 60.24,57.81 61.47,57.81C62.58,57.81 63.65,58.01 64.67,58.4C65.7,58.79 66.61,59.35 67.41,60.08C68.2,60.82 68.84,61.71 69.31,62.77C69.78,63.83 70.02,65.01 70.02,66.31C70.02,67.58 69.8,68.74 69.36,69.8C68.92,70.86 68.32,71.78 67.55,72.56C66.79,73.34 65.89,73.95 64.87,74.39C63.84,74.83 62.76,75.05 61.62,75.05C60.77,75.05 59.92,74.92 59.06,74.66C58.19,74.4 57.31,73.97 56.4,73.39L56.15,73.54C56.67,74.77 57.41,75.91 58.37,76.95C59.33,77.99 60.48,78.89 61.82,79.64C63.15,80.39 64.69,80.98 66.43,81.42C68.17,81.86 70.1,82.08 72.22,82.08C75.47,82.08 79.17,81.48 83.3,80.27C87.43,79.07 92.02,77.06 97.05,74.24C102.08,71.43 107.55,67.71 113.48,63.09C119.4,58.46 125.78,52.73 132.62,45.9C133.53,46.06 134.33,46.26 135.01,46.48C135.69,46.71 136.38,47.02 137.06,47.41ZM121.88,19.68C111.13,17.63 101.88,15.13 94.12,12.18C86.35,9.24 79.96,6.01 74.95,2.51C69.94,-0.98 66.24,-4.66 63.87,-8.52C61.49,-12.38 60.3,-16.24 60.3,-20.12C60.3,-22.46 60.69,-24.59 61.45,-26.51C62.22,-28.43 63.25,-30.09 64.55,-31.47C65.85,-32.85 67.34,-33.92 69.02,-34.67C70.69,-35.42 72.44,-35.79 74.27,-35.79C75.63,-35.79 76.91,-35.57 78.1,-35.13C79.29,-34.69 80.33,-34.08 81.23,-33.3C82.12,-32.52 82.82,-31.57 83.33,-30.44C83.83,-29.32 84.08,-28.08 84.08,-26.71C84.08,-25.31 83.81,-24.08 83.28,-23.02C82.74,-21.96 82.06,-21.09 81.23,-20.39C80.4,-19.69 79.47,-19.17 78.44,-18.82C77.42,-18.48 76.4,-18.31 75.39,-18.31C74.35,-18.31 73.33,-18.5 72.34,-18.87C71.35,-19.25 70.46,-19.8 69.68,-20.53C68.9,-21.26 68.25,-22.17 67.75,-23.24C67.24,-24.32 66.98,-25.57 66.94,-27L66.65,-27.1C66.03,-26.03 65.57,-24.87 65.26,-23.63C64.95,-22.4 64.79,-21.16 64.79,-19.92C64.79,-18 65.21,-15.97 66.04,-13.84C66.87,-11.71 68.22,-9.54 70.09,-7.32C71.96,-5.11 74.41,-2.91 77.42,-0.71C80.43,1.49 84.11,3.59 88.48,5.59C92.84,7.59 97.92,9.47 103.74,11.23C109.55,12.99 116.18,14.52 123.63,15.82C123.34,17.22 122.75,18.51 121.88,19.68ZM124.07,36.62C120.43,37.37 116.76,38 113.09,38.5C109.41,39.01 105.76,39.42 102.15,39.75C98.54,40.07 95,40.31 91.53,40.45C88.06,40.6 84.72,40.67 81.49,40.67C73.26,40.67 65.9,40.25 59.42,39.4C52.95,38.56 47.25,37.39 42.33,35.91C37.42,34.43 33.24,32.7 29.79,30.71C26.33,28.73 23.53,26.6 21.36,24.34C19.2,22.08 17.62,19.74 16.63,17.33C15.63,14.93 15.14,12.53 15.14,10.16C15.14,8.11 15.48,6.17 16.16,4.35C16.85,2.52 17.77,0.93 18.95,-0.44C20.12,-1.81 21.49,-2.89 23.07,-3.69C24.65,-4.48 26.35,-4.88 28.17,-4.88C29.44,-4.88 30.65,-4.68 31.79,-4.27C32.93,-3.87 33.93,-3.29 34.79,-2.54C35.65,-1.79 36.34,-0.87 36.87,0.22C37.39,1.31 37.65,2.54 37.65,3.91C37.65,5.27 37.39,6.49 36.89,7.57C36.39,8.64 35.73,9.55 34.94,10.28C34.14,11.01 33.22,11.56 32.18,11.94C31.14,12.31 30.08,12.5 29,12.5C28.12,12.5 27.25,12.36 26.37,12.08C25.49,11.81 24.67,11.38 23.93,10.79C23.18,10.21 22.52,9.46 21.95,8.57C21.38,7.67 20.95,6.61 20.65,5.37L20.36,5.32C20.1,6.04 19.91,6.8 19.8,7.62C19.69,8.43 19.63,9.21 19.63,9.96C19.63,11.88 20.07,13.88 20.95,15.94C21.83,18.01 23.23,20.04 25.17,22.02C27.11,24.01 29.65,25.88 32.79,27.64C35.93,29.39 39.77,30.94 44.31,32.28C48.85,33.61 54.13,34.67 60.16,35.45C66.18,36.23 73.06,36.62 80.81,36.62C87.35,36.62 94.1,36.31 101.05,35.69C108,35.07 114.99,34.08 122.02,32.71C122.51,33.3 122.91,33.89 123.22,34.47C123.53,35.06 123.81,35.77 124.07,36.62ZM132.96,26.71C132.96,25.44 133.2,24.24 133.69,23.12C134.18,22 134.84,21.02 135.67,20.19C136.5,19.36 137.48,18.7 138.6,18.21C139.72,17.72 140.92,17.48 142.19,17.48C143.46,17.48 144.65,17.72 145.78,18.21C146.9,18.7 147.88,19.36 148.71,20.19C149.54,21.02 150.2,22 150.68,23.12C151.17,24.24 151.42,25.44 151.42,26.71C151.42,27.98 151.17,29.17 150.68,30.3C150.2,31.42 149.54,32.4 148.71,33.23C147.88,34.06 146.9,34.72 145.78,35.21C144.65,35.69 143.46,35.94 142.19,35.94C140.92,35.94 139.72,35.69 138.6,35.21C137.48,34.72 136.5,34.06 135.67,33.23C134.84,32.4 134.18,31.42 133.69,30.3C133.2,29.17 132.96,27.98 132.96,26.71ZM136.72,5.86C133.69,3.12 130.92,0.32 128.42,-2.56C125.91,-5.44 123.76,-8.36 121.97,-11.3C120.18,-14.25 118.79,-17.2 117.8,-20.14C116.81,-23.09 116.31,-26.01 116.31,-28.91C116.31,-31.67 116.75,-34.16 117.63,-36.35C118.51,-38.55 119.68,-40.42 121.14,-41.97C122.61,-43.51 124.28,-44.69 126.17,-45.51C128.06,-46.32 130.01,-46.73 132.03,-46.73C133.72,-46.73 135.25,-46.44 136.62,-45.87C137.99,-45.3 139.14,-44.56 140.06,-43.63C140.99,-42.7 141.71,-41.63 142.21,-40.43C142.72,-39.23 142.97,-38 142.97,-36.77C142.97,-35.69 142.78,-34.66 142.41,-33.67C142.03,-32.67 141.49,-31.8 140.77,-31.03C140.06,-30.27 139.18,-29.65 138.13,-29.2C137.09,-28.74 135.89,-28.52 134.52,-28.52C133.38,-28.52 132.28,-28.69 131.23,-29.03C130.17,-29.37 129.23,-29.88 128.42,-30.57C127.6,-31.25 126.95,-32.1 126.46,-33.13C125.98,-34.16 125.73,-35.35 125.73,-36.72C125.73,-37.34 125.78,-37.92 125.88,-38.45C125.98,-38.99 126.16,-39.63 126.42,-40.38L126.17,-40.58C124.48,-39.21 123.14,-37.52 122.14,-35.52C121.15,-33.52 120.65,-31.18 120.65,-28.52C120.65,-26.4 121.02,-24.09 121.75,-21.58C122.49,-19.08 123.68,-16.41 125.34,-13.57C127,-10.74 129.18,-7.76 131.88,-4.61C134.59,-1.47 137.91,1.81 141.85,5.22C140.38,5.65 138.67,5.86 136.72,5.86ZM153.86,44.58C154.7,48.32 155.31,51.9 155.69,55.3C156.06,58.7 156.25,61.95 156.25,65.04C156.25,70.38 155.72,75.18 154.66,79.44C153.61,83.71 152.11,87.34 150.17,90.33C148.23,93.33 145.91,95.62 143.21,97.22C140.51,98.81 137.52,99.61 134.23,99.61C132.01,99.61 130.06,99.27 128.37,98.58C126.68,97.9 125.26,97.01 124.12,95.9C122.98,94.79 122.13,93.54 121.56,92.14C120.99,90.74 120.7,89.34 120.7,87.94C120.7,86.77 120.9,85.63 121.29,84.52C121.68,83.41 122.25,82.44 123,81.59C123.75,80.75 124.66,80.07 125.73,79.57C126.81,79.06 128.01,78.81 129.35,78.81C130.65,78.81 131.83,79.05 132.89,79.54C133.94,80.03 134.86,80.67 135.62,81.47C136.39,82.27 136.97,83.18 137.38,84.2C137.78,85.23 137.99,86.28 137.99,87.35C137.99,88.72 137.63,90.07 136.91,91.41C136.2,92.74 134.99,93.93 133.3,94.97L133.3,95.17C133.46,95.2 133.6,95.21 133.72,95.21C133.83,95.21 133.9,95.21 133.94,95.21C136.18,95.21 138.4,94.65 140.58,93.53C142.76,92.41 144.69,90.62 146.39,88.18C148.08,85.74 149.45,82.61 150.49,78.78C151.53,74.96 152.05,70.36 152.05,64.99C152.05,62.35 151.9,59.51 151.61,56.45C151.32,53.39 150.85,50.1 150.2,46.58C150.75,46.13 151.33,45.74 151.95,45.41C152.57,45.08 153.21,44.81 153.86,44.58Z"
)
quote_title_petal_right = (
    "M33.84,5.96C36.6,3.19 39.62,0.3 42.87,-2.71C46.13,-5.72 49.54,-8.67 53.12,-11.55C56.71,-14.43 60.41,-17.16 64.23,-19.75C68.06,-22.34 71.9,-24.61 75.76,-26.56C79.61,-28.52 83.45,-30.07 87.26,-31.23C91.06,-32.38 94.76,-32.96 98.34,-32.96C101.01,-32.96 103.63,-32.61 106.2,-31.91C108.77,-31.21 111.07,-30.18 113.09,-28.81C115.1,-27.44 116.72,-25.72 117.94,-23.66C119.17,-21.59 119.78,-19.19 119.78,-16.46C119.78,-14.5 119.47,-12.78 118.85,-11.28C118.23,-9.78 117.43,-8.53 116.46,-7.52C115.48,-6.51 114.37,-5.75 113.13,-5.22C111.9,-4.7 110.66,-4.44 109.42,-4.44C108.32,-4.44 107.25,-4.64 106.23,-5.03C105.2,-5.42 104.29,-5.98 103.49,-6.71C102.69,-7.45 102.06,-8.34 101.59,-9.4C101.11,-10.46 100.88,-11.64 100.88,-12.94C100.88,-14.21 101.1,-15.37 101.54,-16.43C101.98,-17.49 102.58,-18.41 103.34,-19.19C104.11,-19.97 105,-20.58 106.03,-21.02C107.06,-21.46 108.14,-21.68 109.28,-21.68C110.12,-21.68 110.98,-21.55 111.84,-21.29C112.7,-21.03 113.59,-20.61 114.5,-20.02L114.75,-20.17C114.19,-21.37 113.44,-22.5 112.5,-23.56C111.56,-24.62 110.42,-25.52 109.08,-26.27C107.75,-27.02 106.21,-27.61 104.47,-28.05C102.73,-28.49 100.8,-28.71 98.68,-28.71C95.43,-28.71 91.72,-28.11 87.57,-26.9C83.42,-25.7 78.83,-23.69 73.8,-20.87C68.77,-18.06 63.31,-14.34 57.4,-9.72C51.49,-5.09 45.12,0.63 38.28,7.47C37.37,7.31 36.57,7.11 35.89,6.88C35.21,6.66 34.52,6.35 33.84,5.96ZM49.02,33.69C59.77,35.74 69.02,38.24 76.78,41.19C84.55,44.13 90.93,47.36 95.95,50.85C100.96,54.35 104.65,58.03 107.03,61.89C109.41,65.75 110.6,69.61 110.6,73.49C110.6,75.83 110.21,77.96 109.45,79.88C108.68,81.8 107.65,83.46 106.35,84.84C105.05,86.22 103.56,87.29 101.88,88.04C100.2,88.79 98.45,89.16 96.63,89.16C95.26,89.16 93.99,88.94 92.8,88.5C91.61,88.06 90.57,87.45 89.67,86.67C88.78,85.89 88.08,84.94 87.57,83.81C87.07,82.69 86.82,81.45 86.82,80.08C86.82,78.68 87.08,77.45 87.62,76.39C88.16,75.33 88.84,74.45 89.67,73.75C90.5,73.06 91.43,72.53 92.46,72.19C93.48,71.85 94.5,71.68 95.51,71.68C96.55,71.68 97.57,71.87 98.56,72.24C99.55,72.62 100.44,73.17 101.22,73.9C102,74.63 102.64,75.54 103.12,76.61C103.61,77.69 103.89,78.94 103.96,80.37L104.25,80.47C104.87,79.39 105.33,78.24 105.64,77C105.95,75.76 106.1,74.53 106.1,73.29C106.1,71.37 105.69,69.34 104.86,67.21C104.03,65.08 102.68,62.91 100.81,60.69C98.93,58.48 96.49,56.27 93.48,54.08C90.47,51.88 86.78,49.78 82.42,47.78C78.06,45.78 72.97,43.9 67.16,42.16C61.35,40.42 54.72,38.88 47.27,37.55C47.56,36.15 48.14,34.86 49.02,33.69ZM46.83,16.75C50.47,16.03 54.13,15.41 57.81,14.89C61.49,14.37 65.14,13.95 68.75,13.62C72.36,13.3 75.9,13.06 79.37,12.92C82.84,12.77 86.18,12.7 89.4,12.7C97.64,12.7 105,13.12 111.47,13.96C117.95,14.81 123.65,15.97 128.56,17.46C133.48,18.94 137.66,20.67 141.11,22.66C144.56,24.64 147.37,26.77 149.54,29.03C151.7,31.29 153.28,33.63 154.27,36.06C155.27,38.48 155.76,40.87 155.76,43.21C155.76,45.26 155.42,47.2 154.74,49.02C154.05,50.85 153.12,52.44 151.95,53.81C150.78,55.18 149.4,56.26 147.8,57.06C146.21,57.85 144.51,58.25 142.72,58.25C141.46,58.25 140.25,58.05 139.11,57.64C137.97,57.23 136.97,56.66 136.11,55.91C135.25,55.16 134.55,54.24 134.03,53.15C133.51,52.06 133.25,50.83 133.25,49.46C133.25,48.1 133.5,46.88 134.01,45.83C134.51,44.77 135.16,43.87 135.96,43.14C136.76,42.41 137.68,41.85 138.72,41.46C139.76,41.06 140.82,40.87 141.89,40.87C142.77,40.87 143.65,41.01 144.53,41.28C145.41,41.56 146.22,41.99 146.97,42.58C147.72,43.16 148.38,43.9 148.95,44.8C149.52,45.69 149.95,46.76 150.24,48L150.54,48.05C150.8,47.33 150.98,46.57 151.1,45.75C151.21,44.94 151.27,44.16 151.27,43.41C151.27,41.49 150.83,39.49 149.95,37.43C149.07,35.36 147.66,33.33 145.73,31.35C143.79,29.36 141.25,27.49 138.11,25.73C134.97,23.97 131.13,22.43 126.59,21.09C122.05,19.76 116.76,18.7 110.74,17.92C104.72,17.14 97.84,16.75 90.09,16.75C83.54,16.75 76.8,17.06 69.85,17.68C62.9,18.29 55.91,19.29 48.88,20.65C48.39,20.07 47.99,19.48 47.68,18.9C47.37,18.31 47.09,17.59 46.83,16.75ZM37.94,26.66C37.94,27.93 37.7,29.13 37.21,30.25C36.72,31.37 36.06,32.35 35.23,33.18C34.4,34.01 33.42,34.67 32.3,35.16C31.18,35.64 29.98,35.89 28.71,35.89C27.44,35.89 26.25,35.64 25.12,35.16C24,34.67 23.02,34.01 22.19,33.18C21.36,32.35 20.7,31.37 20.21,30.25C19.73,29.13 19.48,27.93 19.48,26.66C19.48,25.39 19.73,24.19 20.21,23.07C20.7,21.95 21.36,20.97 22.19,20.14C23.02,19.31 24,18.65 25.12,18.16C26.25,17.68 27.44,17.43 28.71,17.43C29.98,17.43 31.18,17.68 32.3,18.16C33.42,18.65 34.4,19.31 35.23,20.14C36.06,20.97 36.72,21.95 37.21,23.07C37.7,24.19 37.94,25.39 37.94,26.66ZM34.18,47.51C37.21,50.24 39.97,53.05 42.48,55.93C44.99,58.81 47.14,61.73 48.93,64.67C50.72,67.62 52.11,70.57 53.1,73.54C54.09,76.5 54.59,79.41 54.59,82.28C54.59,85.04 54.14,87.52 53.25,89.72C52.35,91.92 51.17,93.79 49.71,95.34C48.24,96.88 46.57,98.06 44.68,98.88C42.79,99.69 40.85,100.1 38.87,100.1C37.14,100.1 35.6,99.81 34.25,99.24C32.9,98.67 31.76,97.92 30.83,97C29.91,96.07 29.19,95 28.69,93.8C28.18,92.59 27.93,91.37 27.93,90.14C27.93,89.06 28.12,88.03 28.49,87.04C28.87,86.04 29.41,85.16 30.13,84.4C30.84,83.63 31.72,83.02 32.76,82.57C33.81,82.11 35.01,81.88 36.38,81.88C37.52,81.88 38.61,82.06 39.67,82.4C40.73,82.74 41.67,83.25 42.48,83.94C43.29,84.62 43.95,85.47 44.43,86.5C44.92,87.52 45.17,88.72 45.17,90.09C45.17,90.71 45.12,91.28 45.02,91.82C44.92,92.36 44.74,93 44.48,93.75L44.73,93.95C46.42,92.58 47.76,90.89 48.75,88.89C49.75,86.89 50.24,84.55 50.24,81.88C50.24,79.77 49.88,77.46 49.15,74.95C48.41,72.44 47.22,69.78 45.56,66.94C43.9,64.11 41.72,61.12 39.01,57.98C36.31,54.84 32.99,51.56 29.05,48.14C30.52,47.72 32.23,47.51 34.18,47.51ZM17.04,8.79C16.19,5.05 15.58,1.47 15.21,-1.93C14.84,-5.33 14.65,-8.58 14.65,-11.67C14.65,-17.01 15.18,-21.81 16.24,-26.07C17.29,-30.34 18.78,-33.97 20.7,-36.96C22.62,-39.96 24.94,-42.25 27.66,-43.85C30.38,-45.44 33.38,-46.24 36.67,-46.24C38.88,-46.24 40.84,-45.9 42.53,-45.21C44.22,-44.53 45.64,-43.64 46.78,-42.53C47.92,-41.42 48.77,-40.17 49.34,-38.77C49.91,-37.37 50.2,-35.97 50.2,-34.57C50.2,-33.4 50,-32.26 49.61,-31.15C49.22,-30.05 48.65,-29.07 47.9,-28.22C47.15,-27.38 46.24,-26.7 45.17,-26.2C44.09,-25.69 42.89,-25.44 41.55,-25.44C40.25,-25.44 39.07,-25.68 38.01,-26.17C36.95,-26.66 36.04,-27.3 35.28,-28.1C34.51,-28.9 33.93,-29.81 33.52,-30.83C33.11,-31.86 32.91,-32.91 32.91,-33.98C32.91,-35.35 33.27,-36.7 33.98,-38.04C34.7,-39.37 35.9,-40.56 37.6,-41.6L37.6,-41.8C37.43,-41.83 37.3,-41.85 37.18,-41.85C37.07,-41.85 37,-41.85 36.96,-41.85C34.72,-41.85 32.5,-41.28 30.32,-40.16C28.14,-39.04 26.2,-37.26 24.51,-34.81C22.82,-32.37 21.45,-29.24 20.41,-25.42C19.37,-21.59 18.85,-16.99 18.85,-11.62C18.85,-8.98 18.99,-6.14 19.29,-3.08C19.58,-0.02 20.05,3.27 20.7,6.79C20.15,7.24 19.56,7.63 18.95,7.96C18.33,8.28 17.69,8.56 17.04,8.79Z"
)
quote_title_petal_hub_left = (142.19, 26.71)
quote_title_petal_hub_right = (28.71, 26.66)
quote_title_chevron_left = (
    "M7.28,-32.18L7.86,-32.18C17.46,-32.76 27.46,-34.9 37.84,-38.6C48.23,-42.29 55.73,-47.93 60.35,-55.52L66.02,-51.76C61.78,-45.21 56.13,-40.12 49.05,-36.47C41.97,-32.83 32.58,-30.31 20.9,-28.91L20.9,-28.37C32.55,-27.23 41.93,-24.86 49.02,-21.26C56.12,-17.67 61.78,-12.5 66.02,-5.76L60.35,-2C55.79,-9.78 48.31,-15.47 37.89,-19.07C27.47,-22.66 17.46,-24.76 7.86,-25.34L7.28,-25.34Z",
    (7.28, -32.18),
)
quote_title_chevron_right = (
    "M66.02,-25.34L65.43,-25.34C55.83,-24.76 45.82,-22.66 35.4,-19.07C24.98,-15.47 17.5,-9.78 12.94,-2L7.28,-5.76C11.51,-12.5 17.17,-17.67 24.27,-21.26C31.36,-24.86 40.74,-27.23 52.39,-28.37L52.39,-28.91C40.71,-30.31 31.32,-32.83 24.24,-36.47C17.16,-40.12 11.51,-45.21 7.28,-51.76L12.94,-55.52C17.56,-47.93 25.07,-42.29 35.45,-38.6C45.83,-34.9 55.83,-32.76 65.43,-32.18L66.02,-32.18Z",
    (66.02, -25.34),
)
quote_title_scale = quote_font_size / 100.0
quote_title_center_x = quotes_body_x + quotes_body_w / 2.0
quote_title_baseline = quotes_body_y + quote_font_size
quote_title_tip_y = quote_title_baseline - 7.0
# Width of the chevron ink, measured from its apex inwards (in font units).
quote_title_chevron_ink = 58.74 * quote_title_scale
# quote_title_char_w is the widest monospace advance we may meet (SF Mono/Menlo
# are ~0.60 em, Consolas ~0.55 em), so the gap only ever grows on a platform with
# a narrower face; quote_title_gap is the visible gap between word and chevron.
quote_title_char_w = quote_font_size * 0.61
quote_title_gap = 4.0
quote_title_tip_dx = (
    len(quote_title_line) * quote_title_char_w / 2.0
    + quote_title_gap
    + quote_title_chevron_ink
)


def quote_title_petal(petal: str, hub: tuple, tip_x: float, fill: str) -> str:
    """Draw one petal so its hub - the disc the curls radiate from - sits on the tip point."""
    scale = quote_title_scale
    hub_x, hub_y = hub
    return (
        f'<g transform="translate({tip_x:.2f},{quote_title_tip_y:.2f}) '
        f'scale({scale:.2f}) translate({-hub_x:.2f},{-hub_y:.2f})">'
        f'<path d="{petal}" fill="{fill}" fill-rule="nonzero"/>'
        f'</g>'
    )


def quote_title_chevron(chevron: tuple, tip_x: float, fill: str) -> str:
    """Draw the "precedes"/"succeeds" glyph with its apex on the very same tip point."""
    path_d, (apex_x, apex_y) = chevron
    scale = quote_title_scale
    return (
        f'<g transform="translate({tip_x:.2f},{quote_title_tip_y:.2f}) '
        f'scale({scale:.2f}) translate({-apex_x:.2f},{-apex_y:.2f})">'
        f'<path d="{path_d}" fill="{fill}" fill-rule="nonzero"/>'
        f'</g>'
    )


quote_max_lines = max(1, int(quotes_body_h / quote_line_px) - 1)
quote_max_chars = max(10, int(quotes_body_w / (quote_font_size * 0.58)))

# ----------------------------
# Status window (Safari-style, markdown-rendered)
# ----------------------------
# Absolute placement:
# Keep previous bottom-right fixed (~944.36, 778.0),
# and move top-left by (-100, -100), so window grows.
status_x = 644.36
status_y = 578.0
status_w = 300.0
status_h = 200.0

status_header_h = 30.0
status_pad_x = 12.0
status_pad_y = 10.0
status_body_x = status_x + status_pad_x
status_body_y = status_y + status_header_h + status_pad_y
status_body_w = status_w - status_pad_x * 2
status_body_h = status_h - status_header_h - status_pad_y * 2
status_bubble_x = status_x + 9.0
status_bubble_y = status_y + 7.0
status_bubble_w = 30.0
status_bubble_h = 16.0

status_base_font = 12.5
status_line_height = 1.32
status_max_chars = max(12, int(status_body_w / (status_base_font * 0.56)))
status_md_lines = markdown_to_render_lines(status_raw, status_max_chars)
status_required_lines = max(1, len(status_md_lines))
status_max_scale = max((scale for _, scale, _ in status_md_lines), default=1.0)
status_fit_font = min(
    status_base_font,
    status_body_h / (status_required_lines * status_line_height * status_max_scale),
)
status_font = max(8.2, status_fit_font)
status_line_px = status_font * status_line_height

quote_hold_dur = 1.25
char_step = 0.065
scramble_step = 0.055
line_pause = 0.22
char_px_est = quote_font_size * 0.58

quote_defs: list[str] = []
quote_groups: list[str] = []
wrapped_quotes: list[list[str]] = []
for q in quotes:
    if "\n" in q:
        body, author = q.split("\n", 1)
        body_lines = wrap_text(body, quote_max_chars, max(1, quote_max_lines - 1))
        author_lines = wrap_text(author, quote_max_chars, 1)
        wrapped_quotes.append(body_lines + author_lines)
    else:
        wrapped_quotes.append(wrap_text(q, quote_max_chars, quote_max_lines))
quote_durations: list[float] = []
for qlines in wrapped_quotes:
    dur = 0.0
    for line in qlines:
        dur += text_cells(line) * char_step + line_pause
    dur += quote_hold_dur
    quote_durations.append(max(6.8, dur))

quote_slot = max(quote_durations) if quote_durations else 6.8
quote_total_dur = max(quote_slot * len(wrapped_quotes), 6.8)

for qi, qlines in enumerate(wrapped_quotes):
    start = qi * quote_slot
    end = (qi + 1) * quote_slot
    typing_deadline = end - quote_hold_dur
    line_parts: list[str] = []
    cursor = start

    for li, line in enumerate(qlines):
        if cursor >= typing_deadline:
            break
        y = quotes_body_y + quote_font_size + (li + 1) * quote_line_px
        is_author_line = line.startswith("—")
        line_w = text_cells(line) * char_px_est
        x0 = quotes_body_x + (quotes_body_w - line_w if is_author_line else 0.0)
        x_cells = 0.0

        for ci, ch in enumerate(line):
            cw = char_cells(ch)
            if ch == " ":
                x_cells += cw
                continue
            cstart = cursor + x_cells * char_step
            c1 = cstart + scramble_step
            c2 = c1 + scramble_step
            if c2 >= typing_deadline:
                break
            visible_end = end - 0.05
            x = x0 + x_cells * char_px_est
            r1, r2, _, _ = scramble_chars(seed=(qi * 131 + li * 37 + ci * 19))

            for glyph, gs, ge in [
                (r1, cstart, c1),
                (r2, c1, c2),
            ]:
                line_parts.append(
                    f'<text x="{x}" y="{y}" font-size="{quote_font_size}" fill="{QUOTES_SCRAMBLE_TEXT}" '
                    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" opacity="0">{esc(glyph)}'
                    f'<animate attributeName="opacity" dur="{quote_total_dur:.2f}s" repeatCount="indefinite" '
                    f'values="0;0;1;1;0;0" '
                    f'keyTimes="0.000000;{max(0.0, gs-0.0001)/quote_total_dur:.6f};{gs/quote_total_dur:.6f};{ge/quote_total_dur:.6f};{c2/quote_total_dur:.6f};1.000000" />'
                    f'</text>'
                )

            line_parts.append(
                f'<text x="{x}" y="{y}" font-size="{quote_font_size}" fill="{QUOTES_TEXT}" '
                f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" opacity="0">{esc(ch)}'
                f'<animate attributeName="opacity" dur="{quote_total_dur:.2f}s" repeatCount="indefinite" '
                f'values="0;0;1;1;0;0" '
                f'keyTimes="0.000000;{max(0.0, c2-0.0001)/quote_total_dur:.6f};{c2/quote_total_dur:.6f};{visible_end/quote_total_dur:.6f};{end/quote_total_dur:.6f};1.000000" />'
                f'</text>'
            )
            x_cells += cw

        cursor += text_cells(line) * char_step + line_pause

    if line_parts:
        quote_groups.append("\n".join(line_parts))

# ----------------------------
# Stats row (between ASCII and bottom stripe)
# ----------------------------
stats_y = info_y - stats_to_info_gap - stats_section_h
stats_box_h = stats_section_h
card_radius = 20.0
card_border_w = 5.0
card_content_inset = card_border_w / 2.0 + 0.2
card_content_rx = max(0.0, card_radius - card_content_inset)
card_border_inset = card_border_w / 2.0
card_border_x = card_x + card_border_inset
card_border_y = card_y + card_border_inset
card_border_w_inner = card_w - card_border_w
card_border_h_inner = card_h - card_border_w
card_border_rx = max(0.0, card_radius - card_border_inset)

parts = [
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
    "<defs>",
    f'<clipPath id="cardContentClip"><rect x="{card_x + card_content_inset}" y="{card_y + card_content_inset}" width="{card_w - card_content_inset * 2}" height="{card_h - card_content_inset * 2}" rx="{card_content_rx}" /></clipPath>',
    f'<clipPath id="asciiClip"><rect x="{crop_x}" y="{crop_y}" width="{crop_w}" height="{crop_h}" /></clipPath>',
    f'<clipPath id="codeClip"><rect x="{code_body_x}" y="{code_body_y}" width="{code_body_w}" height="{code_body_h}" /></clipPath>',
    f'<clipPath id="statsClip"><rect x="{frame_x}" y="{stats_y}" width="{frame_w}" height="{stats_box_h}" /></clipPath>',
    f'<clipPath id="statusClip"><rect x="{status_body_x}" y="{status_body_y}" width="{status_body_w}" height="{status_body_h}" /></clipPath>',
    f'<clipPath id="statusHeaderBubbleClip"><rect x="{status_bubble_x}" y="{status_bubble_y}" width="{status_bubble_w}" height="{status_bubble_h}" rx="{status_bubble_h/2.0}" /></clipPath>',
    *name_defs,
    *quote_defs,
    "</defs>",
]

parts.append(
    f'<rect x="{card_x}" y="{card_y}" width="{card_w}" height="{card_h}" '
    f'rx="{card_radius}" fill="{CARD}"/>'
)
parts.append('<g clip-path="url(#cardContentClip)">')

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
    f'stroke="{EDGE_HIGHLIGHT}" stroke-opacity="0.24" stroke-width="3" stroke-linecap="round"/>'
)

# Decorative triangle (bottom-right mirror)
triangle_right_gap = stats_right_padding + 50.0
flag2_x1_old = card_x + card_w - triangle_right_gap
flag2_y1_old = card_y + card_h * 0.52
flag2_y2 = card_y + card_h
# Match the left triangle's hypotenuse slope.
left_slope_abs = (flag_y1 - flag_y3) / (flag_x3 - flag_x1)
flag2_x3 = flag2_x1_old - (flag2_y2 - flag2_y1_old) / left_slope_abs
flag2_y3 = card_y + card_h

# Keep slope and left-bottom corner fixed, then extend to right card edge.
flag2_x1 = card_x + card_w
flag2_x2 = flag2_x1
flag2_y1 = flag2_y2 - left_slope_abs * (flag2_x1 - flag2_x3)

parts.append(
    f'<polygon points="{flag2_x1},{flag2_y1} {flag2_x2},{flag2_y2} {flag2_x3},{flag2_y3}" '
    f'fill="{ACCENT_SECONDARY}" opacity="0.22"/>'
)
parts.append(
    f'<line x1="{flag2_x1}" y1="{flag2_y1}" x2="{flag2_x3}" y2="{flag2_y3}" '
    f'stroke="{EDGE_HIGHLIGHT}" stroke-opacity="0.20" stroke-width="3" stroke-linecap="round"/>'
)

# Header
parts.append(
    f'<text x="{hi_x + 44}" y="{hi_y}" font-size="28" fill="{TEXT}" '
    f'font-family="Inter, Arial, sans-serif" font-weight="700">Hi, I&apos;m</text>'
)
parts.append(
    f'<g transform="translate({wave_cx:.1f},{wave_cy:.1f})">'
    f'<g>'
    f'<animateTransform attributeName="transform" type="scale" begin="{intro_name_begin:.2f}s" dur="{wave_scale_dur:.2f}s" '
    f'values="1;2;2;1" keyTimes="0;0.15;0.85;1" fill="freeze" />'
    f'<text x="{wave_text_x:.1f}" y="{wave_text_y:.1f}" font-size="28" fill="{TEXT}" '
    f'font-family="Inter, Arial, sans-serif" font-weight="700">👋'
    f'<animateTransform attributeName="transform" additive="sum" type="rotate" begin="{intro_name_begin:.2f}s" '
    f'dur="{wave_cycle_dur:.2f}s" repeatCount="{wave_repeat_count}" '
    f'values="0;16;-10;16;-6;0" keyTimes="0;0.2;0.4;0.6;0.8;1" fill="freeze" />'
    f'</text>'
    f'</g>'
    f'</g>'
)

parts.extend(name_text_parts)

# Quotes shell
parts.append(
    f'<g opacity="0" transform="translate(0,{intro_window_rise})">'
    f'<animate attributeName="opacity" begin="{intro_quotes_begin:.2f}s" dur="{intro_window_dur:.2f}s" values="0;1" fill="freeze"/>'
    f'<animateTransform attributeName="transform" type="translate" begin="{intro_quotes_begin:.2f}s" dur="{intro_window_dur:.2f}s" values="0 {intro_window_rise};0 0" fill="freeze"/>'
)
parts.append(
    f'<rect x="{quotes_x}" y="{quotes_y}" width="{quotes_w}" height="{quotes_h}" '
    f'rx="12" fill="{QUOTES_PANEL_BG}" stroke="{QUOTES_PANEL_STROKE}" opacity="0.98"/>'
)
parts.append(
    f'<rect x="{quotes_x}" y="{quotes_y}" width="{quotes_w}" height="{quotes_header_h}" '
    f'rx="12" fill="{QUOTES_HEADER_BG}"/>'
)
parts.append(
    f'<rect x="{quotes_tab_x}" y="{quotes_tab_y}" width="{quotes_tab_w}" height="{quotes_tab_h}" '
    f'rx="6" fill="{QUOTES_TAB_BG}" stroke="{QUOTES_TAB_STROKE}"/>'
)
parts.append(
    f'<text x="{quotes_tab_x + 12}" y="{quotes_title_y}" font-size="13" fill="{QUOTES_TAB_TEXT}" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">daily.quotes</text>'
)
parts.append(
    f'<text x="{quotes_update_time_x}" y="{quotes_utf_y}" font-size="11" fill="{QUOTES_STATUS_TEXT}" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">{esc(quotes_update_time)}</text>'
)
parts.append(
    f'<text x="{quotes_utf_x}" y="{quotes_utf_y}" font-size="11" fill="{QUOTES_STATUS_TEXT}" text-anchor="end" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">{quotes_utf_text}</text>'
)
parts.append(
    f'<circle cx="{quotes_close_x}" cy="{quotes_close_y}" r="{quotes_close_r}" '
    f'fill="none" stroke="{QUOTES_STATUS_TEXT}" stroke-opacity="0.9" stroke-width="1"/>'
)
parts.append(
    f'<text x="{quotes_close_x}" y="{quotes_close_y + 3.4}" font-size="10" fill="{QUOTES_STATUS_TEXT}" text-anchor="middle" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">x</text>'
)
parts.append(
    f'<rect x="{quotes_x}" y="{quotes_y + quotes_header_h}" width="{quotes_gutter_w}" '
    f'height="{quotes_h - quotes_header_h}" fill="{QUOTES_GUTTER_BG}" opacity="0.98"/>'
)
parts.append(
    f'<line x1="{quotes_x + quotes_gutter_w}" y1="{quotes_y + quotes_header_h}" '
    f'x2="{quotes_x + quotes_gutter_w}" y2="{quotes_y + quotes_h}" '
    f'stroke="{QUOTES_GUTTER_STROKE}" stroke-width="1"/>'
)
parts.append(
    f'<text x="{quotes_x + quotes_gutter_inner_pad}" y="{quotes_body_y + quote_font_size}" font-size="{quote_font_size}" fill="{QUOTES_LINE_NUM}" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">1</text>'
)
parts.append(
    f'<text x="{quotes_x + quotes_gutter_inner_pad}" y="{quotes_body_y + quote_font_size + quote_line_px}" font-size="{quote_font_size}" fill="{QUOTES_LINE_NUM}" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">2</text>'
)
parts.append(
    f'<text x="{quotes_x + quotes_gutter_inner_pad}" y="{quotes_body_y + quote_font_size + quote_line_px * 2}" font-size="{quote_font_size}" fill="{QUOTES_LINE_NUM}" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">3</text>'
)
parts.append(
    f'<text x="{quotes_x + quotes_gutter_inner_pad}" y="{quotes_body_y + quote_font_size + quote_line_px * 3}" font-size="{quote_font_size}" fill="{QUOTES_LINE_NUM}" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">4</text>'
)
parts.append(
    f'<text x="{quotes_x + quotes_gutter_inner_pad}" y="{quotes_body_y + quote_font_size + quote_line_px * 4}" font-size="{quote_font_size}" fill="{QUOTES_LINE_NUM}" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">5</text>'
)
parts.append(
    quote_title_chevron(quote_title_chevron_left, quote_title_center_x - quote_title_tip_dx, QUOTES_TITLE)
)
parts.append(
    quote_title_petal(quote_title_petal_left, quote_title_petal_hub_left, quote_title_center_x - quote_title_tip_dx, QUOTES_TITLE)
)
parts.append(
    f'<text x="{quote_title_center_x:.2f}" y="{quote_title_baseline}" font-size="{quote_font_size}" fill="{QUOTES_TITLE}" font-weight="700" text-anchor="middle" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" xml:space="preserve">{esc(svg_preserve_line(quote_title_line))}</text>'
)
parts.append(
    quote_title_petal(quote_title_petal_right, quote_title_petal_hub_right, quote_title_center_x + quote_title_tip_dx, QUOTES_TITLE)
)
parts.append(
    quote_title_chevron(quote_title_chevron_right, quote_title_center_x + quote_title_tip_dx, QUOTES_TITLE)
)
parts.extend(quote_groups)
parts.append("</g>")

# status.md window (UE-style)
status_window_parts: list[str] = []
status_window_parts.append(
    f'<rect x="{status_x}" y="{status_y}" width="{status_w}" height="{status_h}" '
    f'rx="8" fill="{STATUS_PANEL_BG}" stroke="{STATUS_PANEL_STROKE}" stroke-width="1.4" opacity="0.98"/>'
)
status_window_parts.append(
    f'<rect x="{status_x}" y="{status_y}" width="{status_w}" height="{status_header_h}" '
    f'rx="8" fill="{STATUS_HEADER_BG}"/>'
)
status_window_parts.append(
    f'<rect x="{status_x}" y="{status_y + status_header_h - 2.0}" width="{status_w}" height="2" '
    f'fill="{STATUS_HEADER_ACCENT}" opacity="0.9"/>'
)
status_title_x = status_x + 26
if status_header_token:
    status_window_parts.append(
        f'<rect x="{status_bubble_x}" y="{status_bubble_y}" width="{status_bubble_w}" height="{status_bubble_h}" rx="{status_bubble_h/2.0}" '
        f'fill="{STATUS_HEADER_BUBBLE_BG}" fill-opacity="0.12" stroke="{STATUS_HEADER_BUBBLE_STROKE}" stroke-opacity="0.52" stroke-width="1.0"/>'
    )
    status_window_parts.append(
        f'<g clip-path="url(#statusHeaderBubbleClip)">'
        f'<text x="{status_bubble_x + status_bubble_w / 2.0}" y="{status_y + 20}" font-size="12" fill="{STATUS_HEADER_BUBBLE_TEXT}" text-anchor="middle" '
        f'font-family="Inter, Arial, sans-serif" font-weight="700">{esc(status_header_token)}</text>'
        f'</g>'
    )
    status_title_x = status_bubble_x + status_bubble_w + 12.0
else:
    status_window_parts.append(
        f'<rect x="{status_x + 12}" y="{status_y + 9}" width="12" height="12" rx="2" fill="{STATUS_HEADER_ACCENT}" opacity="0.9"/>'
    )
status_window_parts.append(
    f'<text x="{status_title_x}" y="{status_y + 20}" font-size="12" fill="{STATUS_HEADER_TITLE_TEXT}" '
    f'font-family="Inter, Arial, sans-serif" font-weight="700">Status.md</text>'
)
status_window_parts.append(
    f'<circle cx="{status_x + status_w - 16}" cy="{status_y + 15}" r="5.4" '
    f'fill="none" stroke="{STATUS_CLOSE_STROKE}" stroke-opacity="0.95" stroke-width="1"/>'
)
status_window_parts.append(
    f'<text x="{status_x + status_w - 16}" y="{status_y + 18}" font-size="10" fill="{STATUS_CLOSE_TEXT}" text-anchor="middle" '
    f'font-family="Inter, Arial, sans-serif" font-weight="700">-</text>'
)

status_render_parts: list[str] = ['<g clip-path="url(#statusClip)">']
status_y_cursor = status_body_y + status_font
for line_text, scale, weight in status_md_lines:
    if line_text.startswith("__IMG__|"):
        _, alt, src = (line_text.split("|", 2) + ["", ""])[:3]
        href = resolve_markdown_image_href(src, status_md_path)
        if href:
            img_h = max(32.0, min(status_body_h * 0.72, status_body_w * 0.7))
            if status_y_cursor + img_h > status_body_y + status_body_h:
                img_h = max(20.0, status_body_y + status_body_h - status_y_cursor)
            status_render_parts.append(
                f'<image x="{status_body_x}" y="{status_y_cursor}" width="{status_body_w}" height="{img_h}" '
                f'href="{esc_attr(href)}" preserveAspectRatio="xMidYMid meet" />'
            )
            status_y_cursor += img_h + status_line_px * 0.35
        else:
            status_render_parts.append(
                f'<text x="{status_body_x}" y="{status_y_cursor}" font-size="{status_font * 0.95}" fill="{STATUS_MARKDOWN_ERROR}" '
                f'font-family="IBM Plex Mono, SFMono-Regular, Menlo, Consolas, monospace">[missing image: {esc(src)}]</text>'
            )
            status_y_cursor += status_line_px
        continue
    status_render_parts.append(
        f'<text x="{status_body_x}" y="{status_y_cursor}" font-size="{status_font * scale}" fill="{STATUS_MARKDOWN_TEXT}" '
        f'font-family="IBM Plex Mono, SFMono-Regular, Menlo, Consolas, monospace" font-weight="{weight}" xml:space="preserve">{esc(svg_preserve_line(line_text))}</text>'
    )
    status_y_cursor += status_line_px
status_render_parts.append("</g>")
status_window_parts.extend(status_render_parts)

# Code block shell
parts.append(
    f'<g opacity="0" transform="translate(0,{intro_window_rise})">'
    f'<animate attributeName="opacity" begin="{intro_code_begin:.2f}s" dur="{intro_window_dur:.2f}s" values="0;1" fill="freeze"/>'
    f'<animateTransform attributeName="transform" type="translate" begin="{intro_code_begin:.2f}s" dur="{intro_window_dur:.2f}s" values="0 {intro_window_rise};0 0" fill="freeze"/>'
)
parts.append(
    f'<rect x="{left_x}" y="{left_y}" width="{left_w}" height="{left_h}" '
    f'rx="14" fill="{CODE_BG}" stroke="{CODE_BORDER}"/>'
)
parts.append(
    f'<rect x="{left_x}" y="{left_y}" width="{left_w}" height="{code_header_h}" '
    f'rx="14" fill="{CODE_HEADER}"/>'
)
parts.append(
    f'<circle cx="{left_x + 18}" cy="{left_y + 18}" r="4.5" fill="{CODE_TRAFFIC_1}"/>'
)
parts.append(
    f'<circle cx="{left_x + 34}" cy="{left_y + 18}" r="4.5" fill="{CODE_TRAFFIC_2}"/>'
)
parts.append(
    f'<circle cx="{left_x + 50}" cy="{left_y + 18}" r="4.5" fill="{CODE_TRAFFIC_3}"/>'
)
parts.append(
    f'<text x="{left_x + 72}" y="{left_y + 23}" font-size="14" fill="{CODE_MUTED}" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">about.me</text>'
)

# Exact raw code text with preserved spaces and blank lines
parts.append(
    draw_about_lines(
        about_lines,
        code_body_x,
        code_first_y,
        size=code_font_size,
        line_height=code_line_height,
        char_px=code_char_px,
        clip_id="codeClip",
    )
)

# about.me terminal input row
terminal_prompt = f"github@{username}: ~ $"
terminal_text_x = code_terminal_x + 16
terminal_text_y = code_terminal_y + 19
terminal_char_px = 8.7
cursor_x = terminal_text_x + text_cells(terminal_prompt) * terminal_char_px + 6
cursor_y = code_terminal_y + 8
cursor_h = 14
parts.append(
    f'<rect x="{code_terminal_x}" y="{code_terminal_y}" width="{code_terminal_w}" height="{code_terminal_h}" '
    f'rx="7" fill="{TERMINAL_BG}" stroke="{TERMINAL_BORDER}" stroke-width="1"/>'
)
parts.append(
    f'<text x="{terminal_text_x}" y="{terminal_text_y}" font-size="13" fill="{TERMINAL_PROMPT}" '
    f'font-family="IBM Plex Mono, SFMono-Regular, Menlo, Consolas, monospace">●</text>'
)
parts.append(
    f'<text x="{terminal_text_x + 14}" y="{terminal_text_y}" font-size="13" fill="{TERMINAL_TEXT}" '
    f'font-family="IBM Plex Mono, SFMono-Regular, Menlo, Consolas, monospace">{esc(terminal_prompt)}</text>'
)
parts.append(
    f'<rect x="{cursor_x}" y="{cursor_y}" width="7" height="{cursor_h}" rx="1.5" fill="{TERMINAL_CURSOR}">'
    f'<animate attributeName="opacity" values="1;1;0;0;1" keyTimes="0;0.45;0.5;0.95;1" dur="1.1s" repeatCount="indefinite"/>'
    f'</rect>'
)
parts.append("</g>")

# ASCII inner panel background
parts.append(
    f'<g opacity="0" transform="translate({intro_ascii_shift_x},0)">'
    f'<animate attributeName="opacity" begin="{intro_ascii_begin:.2f}s" dur="{intro_ascii_dur:.2f}s" values="0;1" fill="freeze"/>'
    f'<animateTransform attributeName="transform" type="translate" begin="{intro_ascii_begin:.2f}s" dur="{intro_ascii_dur:.2f}s" values="{intro_ascii_shift_x} 0;0 0" fill="freeze"/>'
)
parts.append(
    f'<rect x="{inner_x - 4}" y="{inner_y - 6}" width="{inner_w - 4 + ASCII_RIGHT_EXPAND_PX}" height="{inner_h + 15}" fill="{ASCII_INNER_BACKGROUND}"/>'
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
        char_px=char_px,
    )
)

# Full ASCII content, centered first, then cropped with padding.
parts.append(
    draw_text_lines(
        ascii_lines,
        ascii_left_x,
        ascii_first_y,
        size=ascii_font_size,
        fill=ASCII_ART_TEXT,
        line_height=ascii_line_height,
        clip_id="asciiClip",
        char_px=char_px,
    )
)
parts.append("</g>")

# Stats cards row
parts.append(
    f'<g opacity="0">'
    f'<animate attributeName="opacity" begin="{intro_misc_begin:.2f}s" dur="0.42s" values="0;1" fill="freeze"/>'
)
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

parts.append(
    f'<g opacity="0" transform="translate(0,{intro_window_rise})">'
    f'<animate attributeName="opacity" begin="{intro_status_begin:.2f}s" dur="{intro_window_dur:.2f}s" values="0;1" fill="freeze"/>'
    f'<animateTransform attributeName="transform" type="translate" begin="{intro_status_begin:.2f}s" dur="{intro_window_dur:.2f}s" values="0 {intro_window_rise};0 0" fill="freeze"/>'
)
parts.extend(status_window_parts)
parts.append("</g>")
parts.append('</g>')
parts.append(
    f'<rect x="{card_border_x}" y="{card_border_y}" width="{card_border_w_inner}" height="{card_border_h_inner}" '
    f'rx="{card_border_rx}" fill="none" stroke="{CARD_OUTLINE_STROKE}" stroke-width="{card_border_w}"/>'
)
parts.append("</svg>")

output_path.write_text("\n".join(parts), encoding="utf-8")
if not suppress_single_output_log:
    print(f"Generated {output_label}")
