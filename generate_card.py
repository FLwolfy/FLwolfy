from pathlib import Path
import base64
import html
import json
import os
import re
import subprocess
import sys
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"


def load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


def load_json_optional(json_path: Path) -> dict:
    if not json_path.exists():
        return {}
    return json.loads(json_path.read_text(encoding="utf-8"))


def render_about_template(text: str, data: dict) -> str:
    if not data:
        return text

    def repl(match: re.Match) -> str:
        key = match.group(1)
        return str(data.get(key, "N/A"))

    return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", repl, text)


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
color_theme = load_config(BASE_DIR / color_file)
color_blocks = color_theme.get("blocks", {})
if not isinstance(color_blocks, dict):
    raise KeyError(f"Missing blocks in {color_file}")

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
    node = color_blocks
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise KeyError(f"Missing color token: {path} (in {color_file})")
        node = node[key]
    if not isinstance(node, str):
        raise KeyError(f"Color token is not a string: {path} (in {color_file})")
    return node


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

txt_file = includes["ascii_art_file"]
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
EMOJI_CACHE_FILE = BASE_DIR / ".cache" / "github_emojis.json"
EMOJI_CACHE_MAX_AGE_SEC = 7 * 24 * 60 * 60


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


def parse_about_segments(line: str) -> list[tuple[str, str, float]]:
    # Inline color tags:
    # [#RRGGBB]text or [#RRGGBBAA]text
    # [@about_markup.token_name]text
    # Example:
    # [@about_markup.label]Skills: [@about_markup.value]C#, C++, Python
    color_tag_re = re.compile(
        r"\[((?:#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?)|(?:@[a-zA-Z0-9_.-]+))\]"
    )

    def decode_color(token: str) -> tuple[str, float]:
        if token.startswith("@"):
            mapped = color_token(token[1:])
            token = mapped
        if len(token) == 9:
            fill = token[:7]
            alpha = int(token[7:9], 16) / 255.0
            return fill, alpha
        return token, 1.0

    s = line.rstrip("\n\r")
    if s == "":
        return [("", ABOUT_DEFAULT_TEXT, 1.0)]

    segments: list[tuple[str, str, float]] = []
    current_fill = ABOUT_DEFAULT_TEXT
    current_opacity = 1.0
    cursor = 0

    for m in color_tag_re.finditer(s):
        if m.start() > cursor:
            text = s[cursor:m.start()]
            segments.append((text, current_fill, current_opacity))
        current_fill, current_opacity = decode_color(m.group(1))
        cursor = m.end()

    if cursor < len(s):
        segments.append((s[cursor:], current_fill, current_opacity))

    if not segments:
        return [("", ABOUT_DEFAULT_TEXT, 1.0)]
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
        for seg_text, seg_fill, seg_opacity in segs:
            if not seg_text:
                continue
            parts.append(
                f'<text x="{x_cursor}" y="{y}" '
                f'font-size="{size}" fill="{seg_fill}" fill-opacity="{seg_opacity}" '
                f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
                f'xml:space="preserve">{esc(svg_preserve_line(seg_text))}</text>'
            )
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


def _load_github_emoji_map() -> dict[str, str]:
    global _github_emoji_map_cache
    if _github_emoji_map_cache is not None:
        return _github_emoji_map_cache

    # 1) Prefer recent local cache (offline-friendly).
    try:
        if EMOJI_CACHE_FILE.exists():
            age = max(0.0, __import__("time").time() - EMOJI_CACHE_FILE.stat().st_mtime)
            if age <= EMOJI_CACHE_MAX_AGE_SEC:
                _github_emoji_map_cache = json.loads(EMOJI_CACHE_FILE.read_text(encoding="utf-8"))
                if isinstance(_github_emoji_map_cache, dict):
                    return _github_emoji_map_cache
    except Exception:
        pass

    # 2) Fetch from GitHub official endpoint.
    try:
        req = Request("https://api.github.com/emojis")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "profile-card-generator")
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if isinstance(payload, dict):
                _github_emoji_map_cache = payload
                try:
                    EMOJI_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    EMOJI_CACHE_FILE.write_text(
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                return _github_emoji_map_cache
    except (HTTPError, URLError, TimeoutError, ValueError):
        pass
    except Exception:
        pass

    # 3) Stale cache fallback if network fetch failed.
    try:
        if EMOJI_CACHE_FILE.exists():
            _github_emoji_map_cache = json.loads(EMOJI_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(_github_emoji_map_cache, dict):
                return _github_emoji_map_cache
    except Exception:
        pass

    _github_emoji_map_cache = {}
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
    key = m.group(1)

    emoji_map = _load_github_emoji_map()
    url = emoji_map.get(key) or emoji_map.get(key.lower())
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
    about_raw = render_about_template(about_raw, github_stats_data)
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

crop_x = inner_x + CROP_PAD_LEFT
crop_y = inner_y + CROP_PAD_Y
crop_w = max(1.0, inner_w - CROP_PAD_LEFT - CROP_PAD_RIGHT)
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
stats_animation_delay = intro_misc_begin + 0.42
stats_inner = delay_svg_animations(stats_inner, stats_animation_delay)
langs_inner = delay_svg_animations(langs_inner, stats_animation_delay)

wave_cycle_dur = 0.85
wave_repeat_count = max(1, int((name_typing_total + 0.2) / wave_cycle_dur + 0.5))
wave_cx = hi_x + 13.0
wave_cy = hi_y - 12.0
wave_text_x = hi_x - wave_cx
wave_text_y = hi_y - wave_cy
wave_scale_dur = max(0.55, name_typing_total + 0.2)

quote_line_height = 1.35
quote_line_px = quote_font_size * quote_line_height
quote_title_line = "꧁≺QUOTES OF THE DAY≻꧂"
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
            c3 = c2 + scramble_step
            c4 = c3 + scramble_step
            if c4 >= typing_deadline:
                break
            visible_end = end - 0.05
            x = x0 + x_cells * char_px_est
            r1, r2, r3, r4 = scramble_chars(seed=(qi * 131 + li * 37 + ci * 19))

            for ridx, (glyph, gs, ge) in enumerate(
                [
                    (r1, cstart, c1),
                    (r2, c1, c2),
                    (r3, c2, c3),
                    (r4, c3, c4),
                ]
            ):
                line_parts.append(
                    f'<text x="{x}" y="{y}" font-size="{quote_font_size}" fill="{QUOTES_SCRAMBLE_TEXT}" '
                    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" opacity="0">{esc(glyph)}'
                    f'<animate attributeName="opacity" dur="{quote_total_dur:.2f}s" repeatCount="indefinite" '
                    f'values="0;0;1;1;0;0" '
                    f'keyTimes="0.000000;{max(0.0, gs-0.0001)/quote_total_dur:.6f};{gs/quote_total_dur:.6f};{ge/quote_total_dur:.6f};{c4/quote_total_dur:.6f};1.000000" />'
                    f'</text>'
                )

            line_parts.append(
                f'<text x="{x}" y="{y}" font-size="{quote_font_size}" fill="{QUOTES_TEXT}" '
                f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" opacity="0">{esc(ch)}'
                f'<animate attributeName="opacity" dur="{quote_total_dur:.2f}s" repeatCount="indefinite" '
                f'values="0;0;1;1;0;0" '
                f'keyTimes="0.000000;{max(0.0, c4-0.0001)/quote_total_dur:.6f};{c4/quote_total_dur:.6f};{visible_end/quote_total_dur:.6f};{end/quote_total_dur:.6f};1.000000" />'
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
    f'<text x="{hi_x + 30}" y="{hi_y}" font-size="28" fill="{TEXT}" '
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
    f'<text x="{quotes_body_x + quotes_body_w / 2.0}" y="{quotes_body_y + quote_font_size}" font-size="{quote_font_size}" fill="{QUOTES_TITLE}" font-weight="700" text-anchor="middle" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" xml:space="preserve">{esc(svg_preserve_line(quote_title_line))}</text>'
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
    f'<rect x="{inner_x - 4}" y="{inner_y - 6}" width="{inner_w - 4}" height="{inner_h + 15}" fill="{ASCII_INNER_BACKGROUND}"/>'
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
        fill=ASCII_ART_TEXT,
        line_height=ascii_line_height,
        clip_id="asciiClip",
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
