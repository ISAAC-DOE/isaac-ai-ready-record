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


def render_header():
    """Render the ISAAC logo and inject the design system."""
    try:
        st.logo(_LOGO_PATH, size="large")
    except Exception:
        # Fallback for older Streamlit without st.logo()
        st.image(_LOGO_PATH, width=250)
    inject_theme()


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
# Design system (2026-06-12): "less is more" studio pass.
# One ink (#0B0F14), one accent (synchrotron teal #5EC8C0), Inter for UI,
# IBM Plex Mono for data, hairline borders, and a single flourish: the
# spectral line under the header.
# ---------------------------------------------------------------------------
def inject_theme():
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

html, body, [class*="css"], .stMarkdown, .stButton, .stSelectbox, .stTextInput {
    font-family: 'Inter', -apple-system, sans-serif;
}

/* Quiet the chrome */
#MainMenu, footer, header [data-testid="stToolbar"] { visibility: hidden; }
.block-container { max-width: 1180px; padding-top: 1.2rem; }

/* The one flourish: a spectral line under the header */
.isaac-spectral-line {
    height: 2px; border: 0; margin: 0.2rem 0 1.6rem 0;
    background: linear-gradient(90deg,
        #5EC8C0 0%, rgba(94,200,192,0.55) 28%,
        rgba(94,200,192,0.18) 55%, transparent 85%);
}

/* Typography rhythm */
h1 { font-weight: 600 !important; letter-spacing: -0.02em; font-size: 1.9rem !important; }
h2, h3 { font-weight: 600 !important; letter-spacing: -0.01em; color: #E6EAF0; }
.stCaption, small, [data-testid="stCaptionContainer"] { color: #8B94A3 !important; }

/* Metrics: quiet cards, mono numerals */
[data-testid="stMetric"] {
    background: #11161D;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 10px;
    padding: 0.9rem 1.1rem;
}
[data-testid="stMetricValue"] {
    font-family: 'IBM Plex Mono', monospace;
    font-feature-settings: 'tnum';
    font-size: 1.65rem;
}
[data-testid="stMetricLabel"] { color: #8B94A3; font-size: 0.78rem;
    text-transform: uppercase; letter-spacing: 0.06em; }

/* Tables and dataframes */
[data-testid="stDataFrame"] { border: 1px solid rgba(255,255,255,0.07); border-radius: 10px; }
[data-testid="stDataFrame"] * { font-family: 'IBM Plex Mono', monospace; font-size: 0.82rem; }

/* Buttons: ghost style, accent on hover only */
.stButton > button {
    background: transparent; border: 1px solid rgba(255,255,255,0.16);
    border-radius: 8px; color: #E6EAF0; font-weight: 500;
    transition: border-color 0.15s ease, color 0.15s ease;
}
.stButton > button:hover { border-color: #5EC8C0; color: #5EC8C0; background: rgba(94,200,192,0.06); }

/* Inputs */
.stTextInput input, .stSelectbox [data-baseweb], .stTextArea textarea {
    border-radius: 8px !important;
}

/* Hairline dividers */
hr { border-color: rgba(255,255,255,0.07) !important; }

/* Code blocks: true ink wells */
.stCode, pre { background: #0D1219 !important; border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px; }

/* Links */
a { color: #5EC8C0 !important; text-decoration: none; }
a:hover { text-decoration: underline; }
</style>
<div class="isaac-spectral-line"></div>
""", unsafe_allow_html=True)
