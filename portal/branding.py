"""
ISAAC Portal — Header & Footer branding components.

Uses st.logo() for the persistent header logo and st.image() for the
footer partner/DOE logos (reliable across all Streamlit versions).
"""

import os
import streamlit as st

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

_LOGO_PATH = os.path.join(_STATIC_DIR, "ISAAC_full_horizontal_white.png")
_PARTNERS_PATH = os.path.join(_STATIC_DIR, "ISAAC_partners_footer_white.png")
_DOE_PATH = os.path.join(_STATIC_DIR, "DOE_White_Seal_White_Lettering_Horizontal.png")


def render_header(mode: str = "light"):
    """Render the ISAAC logo and inject the design system for the given mode."""
    try:
        st.logo(_LOGO_PATH, size="large")
    except Exception:
        # Fallback for older Streamlit without st.logo()
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
# Design system (2026-06-12): "less is more" studio pass — now theme-aware.
# One accent (synchrotron teal), Inter for UI, IBM Plex Mono for data, hairline
# borders, and a single flourish: the spectral line under the header.
#
# Appearance is driven entirely by injected CSS keyed to `mode`, NOT by
# .streamlit/config.toml: that file is not copied into the container image
# (see Dockerfile), so the native Streamlit theme is always the light default.
# Setting backgrounds/text explicitly here makes either mode render correctly
# regardless, and lets the UI toggle switch themes at runtime.
# ---------------------------------------------------------------------------
PALETTES = {
    "dark": {
        "bg": "#0B0F14", "surface": "#11161D", "text": "#E6EAF0",
        "muted": "#8B94A3", "border": "rgba(255,255,255,0.16)",
        "border_soft": "rgba(255,255,255,0.07)", "accent": "#5EC8C0",
        "accent_soft": "rgba(94,200,192,0.06)", "on_accent": "#0B0F14",
        "code_bg": "#0D1219", "logo_chip": "transparent",
    },
    "light": {
        "bg": "#FFFFFF", "surface": "#F4F6F8", "text": "#0B0F14",
        "muted": "#5A6472", "border": "rgba(0,0,0,0.18)",
        "border_soft": "rgba(0,0,0,0.08)", "accent": "#0E8C84",
        "accent_soft": "rgba(14,140,132,0.08)", "on_accent": "#FFFFFF",
        "code_bg": "#F4F6F8", "logo_chip": "#0B0F14",
    },
}


def inject_theme(mode: str = "light"):
    """Inject the design-system CSS for the given mode ('dark' | 'light')."""
    p = PALETTES.get(mode, PALETTES["light"])
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

html, body, [class*="css"], .stMarkdown, .stButton, .stSelectbox, .stTextInput {{
    font-family: 'Inter', -apple-system, sans-serif;
}}

/* App canvas — set explicitly so the chosen mode applies even though the
   native Streamlit theme (config.toml) is never shipped to the container. */
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {{
    background: {p['bg']};
}}
.stApp, .stMarkdown, p, li, label, [data-testid="stWidgetLabel"],
[data-testid="stMarkdownContainer"] {{ color: {p['text']}; }}

/* Quiet the chrome */
#MainMenu, footer, header [data-testid="stToolbar"] {{ visibility: hidden; }}
.block-container {{ max-width: 1180px; padding-top: 1.2rem; }}

/* Logo legibility: the logo asset is white, so it needs a dark chip on light bg */
[data-testid="stLogo"], [data-testid="stSidebarHeader"] img {{
    background: {p['logo_chip']}; border-radius: 8px; padding: 2px 6px;
}}

/* The one flourish: a spectral line under the header */
.isaac-spectral-line {{
    height: 2px; border: 0; margin: 0.2rem 0 1.6rem 0;
    background: linear-gradient(90deg,
        {p['accent']} 0%, {p['accent']}8C 28%,
        {p['accent']}2E 55%, transparent 85%);
}}

/* Typography rhythm */
h1 {{ font-weight: 600 !important; letter-spacing: -0.02em; font-size: 1.9rem !important; color: {p['text']}; }}
h2, h3 {{ font-weight: 600 !important; letter-spacing: -0.01em; color: {p['text']}; }}
.stCaption, small, [data-testid="stCaptionContainer"] {{ color: {p['muted']} !important; }}

/* Metrics: quiet cards, mono numerals */
[data-testid="stMetric"] {{
    background: {p['surface']};
    border: 1px solid {p['border_soft']};
    border-radius: 10px;
    padding: 0.9rem 1.1rem;
}}
[data-testid="stMetricValue"] {{
    font-family: 'IBM Plex Mono', monospace;
    font-feature-settings: 'tnum';
    font-size: 1.65rem;
    color: {p['text']};
}}
[data-testid="stMetricLabel"] {{ color: {p['muted']}; font-size: 0.78rem;
    text-transform: uppercase; letter-spacing: 0.06em; }}

/* Tables and dataframes */
[data-testid="stDataFrame"] {{ border: 1px solid {p['border_soft']}; border-radius: 10px; }}
[data-testid="stDataFrame"] * {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.82rem; }}

/* Buttons: ghost style, accent on hover; active page (primary) is filled.
   Streamlit 1.50 marks buttons with data-testid="stBaseButton-<kind>"; we also
   match the legacy kind="..." attribute for forward/backward compatibility. */
.stButton > button, [data-testid^="stBaseButton-"] {{
    background: {p['surface']}; border: 1px solid {p['border']};
    border-radius: 8px; color: {p['text']}; font-weight: 500;
    transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
}}
.stButton > button:hover, [data-testid^="stBaseButton-"]:hover {{
    border-color: {p['accent']}; color: {p['accent']}; background: {p['accent_soft']};
}}
[data-testid="stBaseButton-primary"], .stButton > button[kind="primary"] {{
    background: {p['accent']}; color: {p['on_accent']}; border-color: {p['accent']};
}}
[data-testid="stBaseButton-primary"]:hover, .stButton > button[kind="primary"]:hover {{
    background: {p['accent']}; color: {p['on_accent']}; filter: brightness(1.06);
}}

/* Popover / hamburger-menu. The popover portals OUTSIDE .stApp, so it never
   inherits the canvas bg — without setting the body AND its inner containers
   explicitly, dark mode shows the native-light white between the menu items. */
[data-testid="stPopover"],
[data-testid="stPopoverBody"],
[data-testid="stPopoverBody"] [data-testid="stVerticalBlock"],
[data-testid="stPopoverBody"] [data-testid="stElementContainer"] {{
    background: {p['bg']};
}}
[data-testid="stPopoverBody"] {{ border: 1px solid {p['border_soft']}; }}
/* The "☰ Menu" trigger is a stPopoverButton (NOT stBaseButton) — style it too,
   or it renders as a bright native-white button in dark mode. */
[data-testid="stPopoverButton"] {{
    background: {p['surface']}; border: 1px solid {p['border']}; color: {p['text']};
}}
[data-testid="stPopoverButton"]:hover {{
    border-color: {p['accent']}; color: {p['accent']}; background: {p['accent_soft']};
}}

/* Inputs + selectbox dropdown (baseweb popover) */
.stTextInput input, .stTextArea textarea {{
    border-radius: 8px !important; background: {p['surface']}; color: {p['text']};
}}
.stSelectbox [data-baseweb] {{ border-radius: 8px !important; }}
[data-baseweb="popover"] ul[role="listbox"], [data-baseweb="menu"] {{ background: {p['surface']}; }}
[role="option"] {{ color: {p['text']}; }}

/* Hairline dividers */
hr {{ border-color: {p['border_soft']} !important; }}

/* Code blocks: true ink wells */
.stCode, pre {{ background: {p['code_bg']} !important; border: 1px solid {p['border_soft']};
    border-radius: 10px; }}

/* Links */
a {{ color: {p['accent']} !important; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
<div class="isaac-spectral-line"></div>
""", unsafe_allow_html=True)
