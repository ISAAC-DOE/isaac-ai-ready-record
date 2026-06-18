"""
ISAAC Portal — Header & Footer branding + design system.

Uses st.logo() for the persistent header logo and st.image() for the
footer partner/DOE logos (reliable across all Streamlit versions).

Design system (2026-06-18 refinement): high-end academic/professional —
near-monochrome deep ink, ONE disciplined teal accent, a real modular type
scale (Inter for UI, IBM Plex Mono for figures/IDs/eyebrows), hairline
borders, generous rhythm. No emoji, no traffic-light colors, no gradients,
no motion. Appearance is driven entirely by injected CSS keyed to `mode`
(NOT .streamlit/config.toml, which the native charts read independently).
"""

import os
import streamlit as st

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

_LOGO_PATH = os.path.join(_STATIC_DIR, "ISAAC_full_horizontal_white.png")
_PARTNERS_PATH = os.path.join(_STATIC_DIR, "ISAAC_partners_footer_white.png")
_DOE_PATH = os.path.join(_STATIC_DIR, "DOE_White_Seal_White_Lettering_Horizontal.png")


def render_header(mode: str = "dark"):
    """Render the ISAAC logo and inject the design system for the given mode."""
    try:
        st.logo(_LOGO_PATH, size="large")
    except Exception:
        st.image(_LOGO_PATH, width=250)
    inject_theme(mode)


def render_footer():
    """Render partner logos and DOE logo at the bottom of the page."""
    st.divider()
    col1, col2, col3 = st.columns([1, 3, 1])
    with col2:
        st.image(_PARTNERS_PATH, use_container_width=True)
        subcol1, subcol2, subcol3 = st.columns([2, 1, 2])
        with subcol2:
            st.image(_DOE_PATH, width=150)


# ---------------------------------------------------------------------------
# Design tokens. Dark deep-ink is canonical; light is the toggle alternate.
# One accent per surface, full stop.
# ---------------------------------------------------------------------------
PALETTES = {
    "dark": {
        "bg": "#0B0F14", "surface": "#11161D", "surface_raised": "#161C24",
        "text": "#E6EAF0", "muted": "#8B94A3",
        "border": "rgba(255,255,255,0.10)", "border_soft": "rgba(255,255,255,0.06)",
        "accent": "#5EC8C0", "accent_hover": "#7AD6CF", "accent_soft": "rgba(94,200,192,0.06)",
        "on_accent": "#0B0F14", "error": "#E0726A",
        "code_bg": "#0D1219", "logo_chip": "transparent",
    },
    "light": {
        "bg": "#FFFFFF", "surface": "#F4F6F8", "surface_raised": "#FFFFFF",
        "text": "#0B0F14", "muted": "#5A6472",
        "border": "rgba(0,0,0,0.10)", "border_soft": "rgba(0,0,0,0.06)",
        "accent": "#0E8C84", "accent_hover": "#0B736C", "accent_soft": "rgba(14,140,132,0.08)",
        "on_accent": "#FFFFFF", "error": "#C0392B",
        "code_bg": "#F4F6F8", "logo_chip": "#0B0F14",
    },
}


def palette(mode: str = "dark") -> dict:
    """Exposed so charts (Altair) can match the active theme exactly."""
    return PALETTES.get(mode, PALETTES["dark"])


def inject_theme(mode: str = "dark"):
    """Inject the design-system CSS for the given mode ('dark' | 'light')."""
    p = PALETTES.get(mode, PALETTES["dark"])
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

html, body, [class*="css"], .stMarkdown, .stButton, .stSelectbox, .stTextInput {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}}

/* App canvas */
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {{ background: {p['bg']}; }}
.stApp, .stMarkdown, p, li, label, [data-testid="stWidgetLabel"],
[data-testid="stMarkdownContainer"] {{ color: {p['text']}; }}

/* Quiet the chrome; calmer measure + generous bottom rhythm */
#MainMenu, footer, header [data-testid="stToolbar"] {{ visibility: hidden; }}
.block-container {{ max-width: 1080px; padding: 2.4rem 2rem 4rem; }}

/* Logo legibility (white asset → dark chip on light bg only) */
[data-testid="stLogo"], [data-testid="stSidebarHeader"] img {{
    background: {p['logo_chip']}; border-radius: 8px; padding: 2px 6px;
}}

/* Header rule: a single restrained hairline (no gradient flourish) */
.isaac-spectral-line {{
    height: 0; border: 0; border-top: 1px solid {p['border_soft']};
    margin: 0.4rem 0 1.8rem 0;
}}

/* ---- Type scale (modular ~1.18; every level sized) ---- */
h1, [data-testid="stHeading"] h1 {{ font-weight: 700 !important; font-size: 2.0rem !important;
    line-height: 1.15; letter-spacing: -0.025em; color: {p['text']}; margin: 0 0 1.25rem; }}
h2 {{ font-weight: 600 !important; font-size: 1.45rem !important; line-height: 1.25;
    letter-spacing: -0.015em; color: {p['text']}; margin: 2.25rem 0 1rem; }}
h3 {{ font-weight: 600 !important; font-size: 1.15rem !important; line-height: 1.3;
    letter-spacing: -0.01em; color: {p['text']}; margin: 1.75rem 0 0.75rem; }}
h4 {{ font-weight: 600 !important; font-size: 0.78rem !important; text-transform: uppercase;
    letter-spacing: 0.08em; color: {p['muted']}; margin: 1.25rem 0 0.5rem;
    font-family: 'IBM Plex Mono', monospace; }}
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li {{
    font-size: 0.95rem; line-height: 1.65; }}
/* Constrain PROSE measure only (not tables/charts/columns) */
[data-testid="stMarkdownContainer"] {{ max-width: 74ch; }}
[data-testid="stMarkdownContainer"]:has(table), [data-testid="stMarkdownContainer"]:has(pre) {{ max-width: none; }}
.stCaption, small, [data-testid="stCaptionContainer"] {{ color: {p['muted']} !important; font-size: 0.8rem; }}

/* Reusable mono eyebrow */
.isaac-eyebrow {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem;
    text-transform: uppercase; letter-spacing: 0.09em; color: {p['muted']}; font-weight: 500; }}

/* Status dot (ambient state — replaces traffic-light pills) */
.isaac-dot {{ display: inline-block; width: 7px; height: 7px; border-radius: 50%;
    margin-right: 7px; vertical-align: middle; }}
.isaac-dot.up {{ background: {p['accent']}; }}
.isaac-dot.down {{ background: {p['muted']}; }}
.isaac-status {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.74rem;
    text-transform: uppercase; letter-spacing: 0.06em; color: {p['muted']}; }}

/* Metrics: quiet cards, mono tabular numerals, eyebrow labels */
[data-testid="stMetric"] {{ background: {p['surface']}; border: 1px solid {p['border_soft']};
    border-radius: 10px; padding: 0.85rem 1.1rem; }}
[data-testid="stMetricValue"] {{ font-family: 'IBM Plex Mono', monospace;
    font-variant-numeric: tabular-nums lining-nums; font-weight: 500;
    font-size: 1.5rem; letter-spacing: -0.01em; color: {p['text']}; }}
[data-testid="stMetricLabel"] {{ color: {p['muted']}; font-size: 0.72rem;
    text-transform: uppercase; letter-spacing: 0.08em; font-family: 'IBM Plex Mono', monospace; }}

/* Dataframes: Inter for text, mono+tabular for figures, uppercase muted headers */
[data-testid="stDataFrame"] {{ border: 1px solid {p['border_soft']}; border-radius: 10px;
    font-family: 'Inter', sans-serif; font-size: 0.85rem; }}
[data-testid="stDataFrame"] [role="columnheader"] {{ font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.05em; font-size: 0.7rem; color: {p['muted']}; }}
[data-testid="stDataFrame"] [role="gridcell"] {{ font-variant-numeric: tabular-nums lining-nums; }}

/* Neutralize native alerts — quiet panels with a muted left bar. True red
   reserved for st.error only. Kills the green/yellow/blue traffic-light leak. */
[data-testid="stAlert"] {{ background: {p['surface']} !important; color: {p['text']} !important;
    border: 1px solid {p['border_soft']} !important; border-left: 3px solid {p['muted']} !important;
    border-radius: 8px; }}
[data-testid="stAlert"] * {{ color: {p['text']} !important; }}
[data-testid="stAlertContentError"], div[data-baseweb="notification"][kind="negative"] {{
    border-left-color: {p['error']} !important; }}

/* Buttons: ghost; active page filled with accent */
.stButton > button, [data-testid^="stBaseButton-"] {{
    background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 6px;
    color: {p['text']}; font-weight: 500;
    transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease; }}
.stButton > button:hover, [data-testid^="stBaseButton-"]:hover {{
    border-color: {p['accent']}; color: {p['accent']}; background: {p['accent_soft']}; }}
[data-testid="stBaseButton-primary"], .stButton > button[kind="primary"] {{
    background: {p['accent']}; color: {p['on_accent']}; border-color: {p['accent']}; }}
[data-testid="stBaseButton-primary"]:hover, .stButton > button[kind="primary"]:hover {{
    background: {p['accent_hover']}; color: {p['on_accent']}; border-color: {p['accent_hover']}; }}

/* Popover menu (portals outside .stApp — set bg explicitly for dark mode) */
[data-testid="stPopover"], [data-testid="stPopoverBody"],
[data-testid="stPopoverBody"] [data-testid="stVerticalBlock"],
[data-testid="stPopoverBody"] [data-testid="stElementContainer"] {{ background: {p['bg']}; }}
[data-testid="stPopoverBody"] {{ border: 1px solid {p['border_soft']}; border-radius: 10px; }}
[data-testid="stPopoverButton"] {{ background: {p['surface']}; border: 1px solid {p['border']};
    color: {p['text']}; border-radius: 6px; }}
[data-testid="stPopoverButton"]:hover {{ border-color: {p['accent']}; color: {p['accent']};
    background: {p['accent_soft']}; }}

/* Inputs + selectbox dropdown */
.stTextInput input, .stTextArea textarea {{ border-radius: 6px !important;
    background: {p['surface']}; color: {p['text']}; }}
.stSelectbox [data-baseweb] {{ border-radius: 6px !important; }}
[data-baseweb="popover"] ul[role="listbox"], [data-baseweb="menu"] {{ background: {p['surface']}; }}
[role="option"] {{ color: {p['text']}; }}

/* Hairline dividers (used sparingly — whitespace separates sections) */
hr {{ border-color: {p['border_soft']} !important; }}

/* Code blocks */
.stCode, pre {{ background: {p['code_bg']} !important; border: 1px solid {p['border_soft']};
    border-radius: 10px; }}

/* Links */
a {{ color: {p['accent']} !important; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
<div class="isaac-spectral-line"></div>
""", unsafe_allow_html=True)


def status_dot(up: bool, label: str):
    """Render ambient status as a small dot + mono caption (no traffic-light pills)."""
    cls = "up" if up else "down"
    st.markdown(f'<span class="isaac-dot {cls}"></span><span class="isaac-status">{label}</span>',
                unsafe_allow_html=True)
