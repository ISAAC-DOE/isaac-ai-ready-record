import streamlit as st
import pandas as pd
import json
import requests
import ontology
import database
import record_authz
import validation
import branding
import agent
import discovery
import os
import re
import html
import streamlit.components.v1 as components
from datetime import datetime, timezone, timedelta

# Page Config — hide the default sidebar entirely
st.set_page_config(page_title="ISAAC Portal", layout="wide", initial_sidebar_state="collapsed")

# CSS: hide the native sidebar and its toggle button
st.markdown("""
<style>
[data-testid="stSidebar"] { display: none; }
[data-testid="collapsedControl"] { display: none; }
</style>
""", unsafe_allow_html=True)

# Theme (dark/light) — initialise before injecting the design system.
# Dark deep-ink is the canonical default; mirrored to the URL so the choice
# survives a page reload.
if "ui_theme" not in st.session_state:
    _qp_theme = st.query_params.get("theme")
    st.session_state.ui_theme = _qp_theme if _qp_theme in ("dark", "light") else "dark"

# Design system at the top of every page. The ISAAC logo now lives IN the top bar
# (built below, once db_connected / identity / PAGES are known) so the logo and the
# hamburger/theme/status/user controls all sit on ONE level.
branding.inject_theme(st.session_state.ui_theme)

# Initialize database tables on startup (if configured)
if database.is_db_configured():
    database.init_tables()
    ontology.sync_file_to_db()

# Initialize the isolated discovery DB on startup (if its env is configured).
# Independent of the records DB above; a no-op when DISCOVERY_* is unset.
if database.is_discovery_db_configured():
    database.init_discovery_tables()

# Check database status
db_connected = database.test_db_connection()

# Resolve identity from Authentik headers — trusted only when the request
# carries the edge-proxy secret (ontology.trusted_identity / C1).
try:
    _headers = st.context.headers
except Exception:
    _headers = {}

current_username, user_is_admin = ontology.trusted_identity(_headers)


def _require_admin_action():
    """Re-validate admin from the live request headers at the point of a
    privileged action. Defense-in-depth beyond the page-level gate: a single
    server-side check, evaluated fresh, mirroring the Flask API's
    @_require_admin (H1). Halts the script run if the caller is not an admin."""
    try:
        _h = st.context.headers
    except Exception:
        _h = {}
    _, _is_admin = ontology.trusted_identity(_h)
    if not _is_admin:
        st.error("Admin privileges required.")
        st.stop()

# Log portal access (once per session)
if "access_logged" not in st.session_state:
    st.session_state.access_logged = True
    if db_connected:
        try:
            database.log_access(current_username)
        except Exception:
            pass

# Auto-sync from wiki on every page load if cache is stale (>5 min)
if db_connected and os.environ.get("WIKI_REPO_URL"):
    try:
        last_sync = database.get_last_sync()
        need_sync = True
        if last_sync and last_sync.get('synced_at'):
            age = datetime.now(timezone.utc) - last_sync['synced_at']
            if age.total_seconds() < 300:
                need_sync = False
        if need_sync:
            ontology.sync_file_to_db()
    except Exception:
        pass

# Initialize page state
if "current_page" not in st.session_state:
    st.session_state.current_page = "Dashboard"

PAGES = ["Dashboard", "Ontology Editor", "Record Form", "Record Validator", "Saved Records", "nano ISAAC", "API Keys", "API Documentation", "About"]
if user_is_admin:
    # Insert Admin Review after Ontology Editor
    PAGES.insert(2, "Admin Review")

# Discovery page (hypothesis-reasoning workbench) — visible to ANY authenticated
# portal user when the isolated discovery DB is configured, so projects can be
# shared with non-admin teammates (each user sees their own + projects shared with
# them; per-project access control is enforced in discovery.py). Set DISCOVERY_HIDDEN
# to hide it again if ever needed.
if database.is_discovery_db_configured() and \
        os.environ.get("DISCOVERY_HIDDEN", "").lower() not in ("1", "true", "yes", "on"):
    PAGES.insert(PAGES.index("About"), "Discovery")

# --- Top bar: one level — [logo  ☰]  ·····  [theme  ● DB Online  user | logout] ---
# Wrapped in st.container(key="isaac_topbar"); the design-system CSS pins that keyed
# wrapper `position:sticky; top:0` so the whole bar stays visible while scrolling.
# vertical_alignment="center" puts the logo, hamburger, theme toggle, DB status and
# user/logout all on the SAME horizontal line.
with st.container(key="isaac_topbar"):
    logo_col, burger_col, _spacer_col, theme_col, status_col, user_col = st.columns(
        [2.3, 0.7, 3.9, 0.55, 1.5, 2.4], vertical_alignment="center")
    with logo_col:
        branding.header_logo(st.session_state.ui_theme, width=185)
    with burger_col:
        with st.popover("☰", use_container_width=True, help="Menu"):
            for p in PAGES:
                label = p
                # Show pending count badge for Admin Review
                if p == "Admin Review" and db_connected:
                    try:
                        pending = database.count_pending_proposals()
                        if pending > 0:
                            label = f"{p} ({pending})"
                    except Exception:
                        pass
                if st.button(label, key=f"nav_{p}", use_container_width=True,
                             type="primary" if p == st.session_state.current_page else "secondary"):
                    st.session_state.current_page = p
                    st.rerun()
    with theme_col:
        _is_dark = st.session_state.ui_theme == "dark"
        # Sun in dark mode (click → light), moon in light mode (click → dark).
        if st.button("☀️" if _is_dark else "🌙", key="theme_toggle",
                     use_container_width=True,
                     help="Switch to light mode" if _is_dark else "Switch to dark mode"):
            st.session_state.ui_theme = "light" if _is_dark else "dark"
            st.query_params["theme"] = st.session_state.ui_theme
            st.rerun()
    with status_col:
        branding.status_dot(db_connected, "DB Online" if db_connected else "DB Offline")
    with user_col:
        _logout_url = "https://isaac.slac.stanford.edu/outpost.goauthentik.io/flows/logout/?rd=https://isaac.slac.stanford.edu/"
        st.markdown(
            f"**{current_username}** &nbsp;|&nbsp; [Logout]({_logout_url})"
        )

page = st.session_state.current_page


def _themed_bar(df, x, y):
    """Ink-and-teal Altair bar chart matching the active theme. None on failure."""
    try:
        import altair as alt
        pal = branding.palette(st.session_state.ui_theme)
        return (
            alt.Chart(df).mark_bar(color=pal["accent"], cornerRadiusEnd=2, size=46)
            .encode(
                x=alt.X(f"{x}:N", sort="-y", axis=alt.Axis(
                    labelAngle=0, labelColor=pal["muted"], titleColor=pal["muted"],
                    domainColor=pal["border_soft"], ticks=False, title=None)),
                y=alt.Y(f"{y}:Q", axis=alt.Axis(
                    labelColor=pal["muted"], titleColor=pal["muted"], grid=True,
                    gridColor=pal["border_soft"], domain=False, ticks=False, title=None)),
                tooltip=[x, y])
            .properties(height=300, background=pal["bg"])
            .configure_view(stroke=None)
        )
    except Exception:
        return None


def _themed_line(df, x, y):
    """Ink-and-teal Altair line chart matching the active theme. None on failure."""
    try:
        import altair as alt
        pal = branding.palette(st.session_state.ui_theme)
        base = alt.Chart(df).encode(
            x=alt.X(f"{x}:T", axis=alt.Axis(
                labelColor=pal["muted"], titleColor=pal["muted"], domainColor=pal["border_soft"],
                ticks=False, title=None)),
            y=alt.Y(f"{y}:Q", axis=alt.Axis(
                labelColor=pal["muted"], titleColor=pal["muted"], grid=True,
                gridColor=pal["border_soft"], domain=False, ticks=False, title=None)))
        return (
            (base.mark_line(color=pal["accent"], strokeWidth=2, interpolate="monotone")
             + base.mark_point(color=pal["accent"], size=28, filled=True))
            .properties(height=240, background=pal["bg"])
            .configure_view(stroke=None)
        )
    except Exception:
        return None

# --- CONFIG: Display Names (derived from SECTION_ORDER — single source, no hand-numbering) ---
# Cross-cutting vocabulary sections that are NOT record blocks:
CROSS_CUTTING = {"Units": "Units (cross-cutting: unit grammar + alias map)",
                  "Record Info": "Record Info (root fields)"}

def get_display_name(key):
    if key in CROSS_CUTTING:
        return CROSS_CUTTING[key]
    try:
        return f"{SECTION_ORDER.index(key) + 1}. {key}"
    except ValueError:
        return key

# --- CONFIG: Wiki Mapping ---
WIKI_BASE = "https://github.com/ISAAC-DOE/isaac-ai-ready-record/wiki"

WIKI_MAP = {
    "Record Info": "Record-Overview",
    "Computation": "Computation",
    "Units": "Controlled-Vocabulary",
    "Attribution": "Schema-Architecture",
    "Sample": "Sample",
    "Context": "Context",
    "System": "System",
    "Measurement": "Measurement",
    "Assets": "Assets",
    "Links": "Links",
    "Descriptors": "Descriptors"
}

# --- HELPER: Mermaid HTML Generator ---
def render_mermaid(code, height=600):
    """
    Renders Mermaid diagram using custom HTML to support Click Events.
    We need 'securityLevel': 'loose' for clicks to work.
    """
    html_code = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
        <script>
            mermaid.initialize({{
                startOnLoad: true,
                securityLevel: 'loose',
                theme: 'default'
            }});
        </script>
        <style>
            /* Ensure it fits */
            body {{ margin: 0; }}
            .mermaid {{ width: 100%; }}
        </style>
    </head>
    <body>
        <div class="mermaid">
        {code}
        </div>
    </body>
    </html>
    """
    components.html(html_code, height=height, scrolling=True)

SECTION_ORDER = [
    "Record Info", "Sample", "Context", "System",
    "Measurement", "Computation", "Descriptors",
    "Assets", "Links", "Units", "Attribution",
]

def generate_mermaid_code(active_section=None, active_category=None):
    """
    Generates Mermaid JS syntax for the ontology tree.
    Includes click events to open Wiki pages in new tab.
    """
    all_sections = ontology.get_sections()
    # Canonical order first, then any extras not in the predefined list
    sections = [s for s in SECTION_ORDER if s in all_sections]
    sections += [s for s in all_sections if s not in SECTION_ORDER]

    # Theme settings
    color_root = "#f9f9f9"
    color_section = "#e1f5fe"
    color_subblock = "#fff8e1"
    color_field = "#fff3e0"
    color_active = "#ffcccb"
    stroke_active = "#ff0000"

    mm = ["graph LR", "Record(ISAAC Record)"]
    click_events = []
    styles = []

    # Link Root to Home
    click_events.append(f'click Record "{WIKI_BASE}" "Go to Wiki Home" _blank')

    for sec in sections:
        disp_sec = get_display_name(sec)
        sec_id = sec.replace(" ", "_").replace(".", "_")

        # Node Label
        mm.append(f'Record --> {sec_id}("{disp_sec}")')

        # Click for Section
        wiki_page = WIKI_MAP.get(sec, "")
        if wiki_page:
            url = f"{WIKI_BASE}/{wiki_page}"
            click_events.append(f'click {sec_id} "{url}" "Open {wiki_page}" _blank')

        is_active_sec = (sec == active_section)

        if is_active_sec:
            styles.append(f"style {sec_id} fill:{color_active},stroke:{stroke_active},stroke-width:2px")
        else:
            styles.append(f"style {sec_id} fill:{color_section}")

        # Drill down if active section
        if is_active_sec:
            cats = ontology.get_categories(sec)
            subblocks = {}

            for cat_key in cats:
                parts = cat_key.split('.')
                if len(parts) > 1:
                    field_name = parts[-1]
                    path = ".".join(parts[:-1])
                else:
                    field_name = cat_key
                    path = "root"

                if path not in subblocks:
                    subblocks[path] = []
                subblocks[path].append((field_name, cat_key))

            # Render Subblocks
            for path, fields in subblocks.items():
                if path == "root":
                    parent_node = sec_id
                else:
                    path_parts = path.split('.')
                    sub_name = path_parts[-1]
                    sub_id = path.replace(".", "_").replace(" ", "_")

                    mm.append(f"{sec_id} --> {sub_id}({sub_name})")
                    styles.append(f"style {sub_id} fill:{color_subblock}")
                    parent_node = sub_id

                    if wiki_page:
                        anchor = sub_name.lower().replace("_", "-")
                        sub_url = f"{WIKI_BASE}/{wiki_page}#{anchor}"
                        click_events.append(f'click {sub_id} "{sub_url}" "Open Section" _blank')

                # Render Fields
                for field_name, full_key in fields:
                    field_id = full_key.replace(".", "_").replace(" ", "_")
                    mm.append(f"{parent_node} --> {field_id}[{field_name}]")

                    if wiki_page:
                         anchor = field_name.lower().replace("_", "-")
                         field_url = f"{WIKI_BASE}/{wiki_page}#{anchor}"
                         click_events.append(f'click {field_id} "{field_url}" "Def: {field_name}" _blank')

                    if full_key == active_category:
                        styles.append(f"style {field_id} fill:{color_active},stroke:{stroke_active},stroke-width:2px")

                        # Show Values
                        vals = cats[full_key]['values'][:5]
                        for val in vals:
                             val_clean = val.replace(" ", "_").replace("/", "_").replace(".", "_")
                             mm.append(f"{field_id} -.-> {val_clean}({val})")
                    else:
                        styles.append(f"style {field_id} fill:{color_field}")

    mm.extend(styles)
    mm.extend(click_events)
    return "\n".join(mm)


# =============================================================================
# PAGE: Dashboard
# =============================================================================
if page == "Dashboard":
    st.title("ISAAC AI-Ready Record Portal")
    st.markdown("### The Middleware for Scientific Semantics")

    if not db_connected:
        # Graceful offline state
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Database", "Offline")
        c2.metric("Total Records", "N/A")
        c3.metric("Last Indexed", "N/A")
        c4.metric("Portal Visits", "N/A")
        st.info("Database not connected. Configure PGHOST, PGUSER, PGPASSWORD, PGDATABASE environment variables.")
    else:
        try:
            stats = database.get_dashboard_stats()
            access = database.get_access_stats()

            # --- Row 1: Status Cards ---
            c1, c2, c3, c4 = st.columns(4)

            c1.metric("Database", "Online")

            c2.metric("Total Records", f"{stats['total']:,}")

            # Last indexed — relative time
            last_indexed = stats.get('last_indexed')
            if last_indexed:
                from datetime import timezone
                delta = datetime.now(timezone.utc) - last_indexed
                if delta.days > 0:
                    indexed_label = f"{delta.days}d ago"
                elif delta.seconds >= 3600:
                    indexed_label = f"{delta.seconds // 3600}h ago"
                elif delta.seconds >= 60:
                    indexed_label = f"{delta.seconds // 60}m ago"
                else:
                    indexed_label = "just now"
            else:
                indexed_label = "No records"
            c3.metric("Last Indexed", indexed_label)

            last_access = access.get('last_access')
            if last_access:
                visit_help = f"Last: {last_access.strftime('%Y-%m-%d %H:%M')}"
            else:
                visit_help = ""
            c4.metric("Portal Visits", f"{access['total_visits']:,}", help=visit_help)

            # --- Row 2: Records by Type ---
            by_type = stats.get('by_type', {})
            if by_type:
                st.subheader("Records by Type")
                type_df = pd.DataFrame(
                    list(by_type.items()),
                    columns=["Record Type", "Count"]
                )
                _bar = _themed_bar(type_df, "Record Type", "Count")
                if _bar is not None:
                    st.altair_chart(_bar, use_container_width=True)
                else:
                    st.bar_chart(type_df.set_index("Record Type"))
            else:
                st.info("No records yet. Use the Record Validator or Record Form to add data.")

        except Exception as e:
            st.error(f"Error loading dashboard: {e}")


# =============================================================================
# PAGE: Ontology Editor
# =============================================================================

# --- API Usage (Dimos dashboard, 2026-06-14) ---
if page == "Dashboard" and db_connected:
    st.markdown("---")
    st.subheader("API Usage")
    try:
        days = st.selectbox("Window", [7, 30, 90], index=1, format_func=lambda d: f"last {d} days")
        usage = database.get_api_usage_stats(days)
        u1, u2, u3, u4 = st.columns(4)
        u1.metric("API Requests", f"{usage['total_requests']:,}")
        u2.metric("Distinct Users", usage['distinct_users'])
        u3.metric("Rejections (4xx)", usage['rejection_count'],
                  help="Validation rejections, auth failures, not-found — mostly the defenses doing their job. A user with many 4xx needs help.")
        u4.metric("System Errors (5xx)", usage['server_error_count'],
                  help="The portal itself failed — the only genuinely alarming column.")
        if usage['daily']:
            df_daily = pd.DataFrame(usage['daily'])
            _line = _themed_line(df_daily, "day", "requests")
            if _line is not None:
                st.altair_chart(_line, use_container_width=True)
            else:
                st.line_chart(df_daily.set_index('day'), height=220)
        col_a, col_b = st.columns(2)
        with col_a:
            st.caption("Requests by user")
            if usage['by_user']:
                st.dataframe(pd.DataFrame(usage['by_user']), hide_index=True, use_container_width=True)
        with col_b:
            st.caption("Requests by endpoint (avg latency)")
            if usage['by_endpoint']:
                st.dataframe(pd.DataFrame(usage['by_endpoint']), hide_index=True, use_container_width=True)
        # Forensics: source IPs of any unauthenticated traffic (only shown when present)
        unauth = usage.get('unauth_by_ip') or []
        if unauth:
            st.caption("Unauthenticated requests by source IP")
            st.dataframe(pd.DataFrame(unauth), hide_index=True, use_container_width=True)
    except Exception as exc:
        st.info(f"Usage stats unavailable yet: {exc}")

elif page == "Ontology Editor":
    st.header("Living Ontology")
    st.info("Browse the ISAAC vocabulary below. Numbered sections mirror the record blocks; Units and Record Info are cross-cutting vocabularies (units and root fields appear inside records, not as blocks). Use the Propose form to suggest changes.")

    all_sections = ontology.get_sections()
    sections = [s for s in SECTION_ORDER if s in all_sections]
    sections += [s for s in all_sections if s not in SECTION_ORDER]

    col_nav, col_map = st.columns([1, 1.5])

    # -- LEFT: Controls --
    with col_nav:
        # Admin toolbar
        if user_is_admin:
            with st.container():
                admin_cols = st.columns([2, 1])
                with admin_cols[0]:
                    if db_connected:
                        last_sync = None
                        try:
                            last_sync = database.get_last_sync()
                        except Exception:
                            pass
                        if last_sync and last_sync.get('synced_at'):
                            st.caption(f"Last sync: {last_sync['synced_at'].strftime('%Y-%m-%d %H:%M')} by {last_sync.get('synced_by', '?')}")
                        else:
                            st.caption("Never synced from wiki")
                with admin_cols[1]:
                    if st.button("Re-sync from deployed file", type="secondary", help="vocabulary.json ships with the image and is the source of truth; the wiki is generated FROM it."):
                        with st.spinner("Syncing from vocabulary.json..."):
                            ok, msg = ontology.sync_file_to_db()
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
                st.divider()

        st.subheader("1. Browse")
        selected_section = st.selectbox("Select Schema Section", sections, format_func=get_display_name)

        categories_dict = ontology.get_categories(selected_section)
        categories = list(categories_dict.keys())

        if categories:
            selected_category = st.radio("Select Category", categories)
        else:
            selected_category = None
            st.warning("No categories found.")

        st.divider()

        if selected_category and selected_category in categories_dict:
            st.subheader(f"2. Details: {selected_category}")
            st.write(f"*{categories_dict[selected_category]['description']}*")
            values = categories_dict[selected_category]['values']
            df_vals = pd.DataFrame(values, columns=["Allowed Terms"])
            st.dataframe(df_vals, use_container_width=True, height=200)

        st.divider()

        # Propose changes (all users)
        st.subheader("3. Propose a Change")
        proposal_type = st.selectbox("Proposal Type", ["Add Term", "Add Category"], key="prop_type")

        if proposal_type == "Add Term":
            prop_section = st.selectbox("Section", sections, index=sections.index(selected_section) if selected_section in sections else 0, key="prop_sec_term")
            prop_cats = list(ontology.get_categories(prop_section).keys())
            prop_category = st.selectbox("Category", prop_cats, key="prop_cat_term") if prop_cats else None
            prop_term = st.text_input("New Term", placeholder="e.g. rotating_cylinder", key="prop_term_input")
            prop_term_desc = st.text_area(
                "Description (required)",
                placeholder="Explain what this term means and why it should be added. "
                            "This will be used to generate the wiki definition.",
                key="prop_term_desc",
                height=100,
            )
            if st.button("Submit Proposal", key="submit_add_term"):
                if prop_term and prop_category and prop_term_desc and prop_term_desc.strip() and db_connected:
                    try:
                        pid = database.create_proposal(
                            proposal_type="add_term",
                            section=prop_section,
                            category=prop_category,
                            term=prop_term,
                            description=prop_term_desc.strip(),
                            proposed_by=current_username
                        )
                        st.success(f"Proposal #{pid} submitted! An admin will review it.")
                    except Exception as e:
                        st.error(f"Failed to submit: {e}")
                elif not db_connected:
                    st.warning("Database not connected. Proposals require a database.")
                else:
                    st.warning("Please fill in all fields, including a description.")

        elif proposal_type == "Add Category":
            prop_section = st.selectbox("Section", sections, index=sections.index(selected_section) if selected_section in sections else 0, key="prop_sec_cat")
            prop_new_cat = st.text_input("New Category Key", placeholder="e.g. context.transport.viscosity", key="prop_cat_key")
            prop_desc = st.text_input("Description", key="prop_cat_desc")
            if st.button("Submit Proposal", key="submit_add_cat"):
                if prop_new_cat and db_connected:
                    try:
                        pid = database.create_proposal(
                            proposal_type="add_category",
                            section=prop_section,
                            category=prop_new_cat,
                            description=prop_desc,
                            proposed_by=current_username
                        )
                        st.success(f"Proposal #{pid} submitted! An admin will review it.")
                    except Exception as e:
                        st.error(f"Failed to submit: {e}")
                elif not db_connected:
                    st.warning("Database not connected. Proposals require a database.")
                else:
                    st.warning("Please provide a category key.")

        # My Proposals
        if db_connected:
            with st.expander("My Proposals"):
                try:
                    my_proposals = database.list_proposals(proposed_by=current_username)
                    if my_proposals:
                        for prop in my_proposals:
                            status_icon = {"pending": "...", "approved": "+", "rejected": "x"}.get(prop['status'], "?")
                            label = f"[{status_icon}] #{prop['id']} {prop['proposal_type']}: {prop.get('category', '')} {prop.get('term', '') or ''}"
                            st.write(label)
                            if prop.get('review_comment'):
                                st.caption(f"  Review: {prop['review_comment']}")
                    else:
                        st.write("No proposals yet.")
                except Exception as e:
                    st.error(f"Error loading proposals: {e}")

    # -- RIGHT: Concept Map --
    with col_map:
        st.subheader("Concept Map")
        st.caption("Visualizing: " + get_display_name(selected_section))

        mermaid_code = generate_mermaid_code(selected_section, selected_category)
        render_mermaid(mermaid_code, height=600)


# =============================================================================
# PAGE: Admin Review (admin-only)
# =============================================================================
elif page == "Admin Review":
    if not user_is_admin:
        st.error("Access denied. Admin privileges required.")
    elif not db_connected:
        st.warning("Database not connected. Admin review requires a database.")
    else:
        st.header("Vocabulary Proposal Review")

        tab_pending, tab_approved, tab_rejected = st.tabs(["Pending", "Approved", "Rejected"])

        with tab_pending:
            pending = database.list_proposals(status="pending")
            if not pending:
                st.info("No pending proposals.")

            # Session state to track which proposal is in the "review draft" step
            if "reviewing_proposal_id" not in st.session_state:
                st.session_state.reviewing_proposal_id = None
            if "draft_wiki_prose" not in st.session_state:
                st.session_state.draft_wiki_prose = ""
            if "draft_yaml_desc" not in st.session_state:
                st.session_state.draft_yaml_desc = ""

            for prop in pending:
                with st.container(border=True):
                    pid = prop['id']
                    st.markdown(f"**Proposal #{pid}** — `{prop['proposal_type']}`")
                    st.write(f"**Section:** {prop['section']}")
                    if prop.get('category'):
                        st.write(f"**Category:** {prop['category']}")
                    if prop.get('term'):
                        st.write(f"**Term:** {prop['term']}")
                    if prop.get('description'):
                        st.info(f"**Proposer's description:** {prop['description']}")
                    else:
                        st.warning("No description provided by proposer.")
                    st.caption(f"Proposed by {prop['proposed_by']} on {prop['proposed_at'].strftime('%Y-%m-%d %H:%M') if prop.get('proposed_at') else '?'}")

                    is_reviewing = (st.session_state.reviewing_proposal_id == pid)

                    if not is_reviewing:
                        # Step 1: Generate draft or quick actions
                        btn_cols = st.columns(3)
                        with btn_cols[0]:
                            if st.button("Generate Wiki Text", key=f"gen_{pid}", type="primary"):
                                _require_admin_action()
                                with st.spinner("Generating wiki prose with AI..."):
                                    result = ontology.generate_wiki_description(
                                        section=prop['section'],
                                        category=prop.get('category', ''),
                                        term=prop.get('term', ''),
                                        proposal_type=prop['proposal_type'],
                                        user_description=prop.get('description', '')
                                    )
                                if result['success']:
                                    st.session_state.reviewing_proposal_id = pid
                                    st.session_state.draft_wiki_prose = result['wiki_prose']
                                    st.session_state.draft_yaml_desc = result['yaml_description']
                                    st.rerun()
                                else:
                                    st.error(f"LLM error: {result['error']}")
                        with btn_cols[1]:
                            if st.button("Approve (no prose)", key=f"quick_approve_{pid}"):
                                _require_admin_action()
                                comment = ""
                                ok, msg = database.review_proposal(pid, "approved", current_username, comment)
                                if ok:
                                    apply_ok, apply_msg, wiki_ok = ontology.apply_approved_proposal(prop)
                                    if apply_ok:
                                        st.success(f"Approved and applied. {apply_msg}")
                                    else:
                                        st.warning(f"Approved but failed to apply: {apply_msg}")
                                    st.rerun()
                                else:
                                    st.error(msg)
                        with btn_cols[2]:
                            if st.button("Reject", key=f"reject_{pid}"):
                                _require_admin_action()
                                ok, msg = database.review_proposal(pid, "rejected", current_username, "")
                                if ok:
                                    st.success("Proposal rejected.")
                                    st.rerun()
                                else:
                                    st.error(msg)
                    else:
                        # Step 2: Review and edit the generated draft
                        st.divider()
                        st.markdown("**AI-Generated Wiki Text** — edit below before approving:")
                        edited_prose = st.text_area(
                            "Wiki prose (will be inserted into the wiki page)",
                            value=st.session_state.draft_wiki_prose,
                            height=150,
                            key=f"prose_{pid}"
                        )
                        edited_yaml_desc = st.text_input(
                            "YAML description (one-line for the vocabulary block)",
                            value=st.session_state.draft_yaml_desc,
                            key=f"yaml_desc_{pid}"
                        )
                        review_comment = st.text_input("Review comment (optional)", key=f"comment_{pid}")

                        confirm_cols = st.columns(3)
                        with confirm_cols[0]:
                            if st.button("Approve & Push to Wiki", key=f"confirm_{pid}", type="primary"):
                                _require_admin_action()
                                ok, msg = database.review_proposal(pid, "approved", current_username, review_comment)
                                if ok:
                                    # Update proposal description with the yaml_desc if provided
                                    enriched_prop = dict(prop)
                                    if edited_yaml_desc:
                                        enriched_prop['_yaml_description'] = edited_yaml_desc
                                    apply_ok, apply_msg, wiki_ok = ontology.apply_approved_proposal(
                                        enriched_prop, wiki_prose=edited_prose
                                    )
                                    if apply_ok:
                                        st.success(f"Approved, applied, and wiki updated. {apply_msg}")
                                        if not wiki_ok:
                                            st.warning(f"Wiki push issue: {apply_msg}")
                                    else:
                                        st.warning(f"Approved but failed to apply: {apply_msg}")
                                    st.session_state.reviewing_proposal_id = None
                                    st.session_state.draft_wiki_prose = ""
                                    st.session_state.draft_yaml_desc = ""
                                    st.rerun()
                                else:
                                    st.error(msg)
                        with confirm_cols[1]:
                            if st.button("Regenerate", key=f"regen_{pid}"):
                                _require_admin_action()
                                with st.spinner("Regenerating..."):
                                    result = ontology.generate_wiki_description(
                                        section=prop['section'],
                                        category=prop.get('category', ''),
                                        term=prop.get('term', ''),
                                        proposal_type=prop['proposal_type'],
                                        user_description=prop.get('description', '')
                                    )
                                if result['success']:
                                    st.session_state.draft_wiki_prose = result['wiki_prose']
                                    st.session_state.draft_yaml_desc = result['yaml_description']
                                    st.rerun()
                                else:
                                    st.error(f"LLM error: {result['error']}")
                        with confirm_cols[2]:
                            if st.button("Cancel", key=f"cancel_{pid}"):
                                st.session_state.reviewing_proposal_id = None
                                st.session_state.draft_wiki_prose = ""
                                st.session_state.draft_yaml_desc = ""
                                st.rerun()

        with tab_approved:
            approved = database.list_proposals(status="approved")
            if not approved:
                st.info("No approved proposals.")
            for prop in approved:
                with st.container(border=True):
                    st.markdown(f"**#{prop['id']}** `{prop['proposal_type']}` — {prop.get('category', '')} {prop.get('term', '') or ''}")
                    st.caption(f"By {prop['proposed_by']} | Approved by {prop.get('reviewed_by', '?')} on {prop['reviewed_at'].strftime('%Y-%m-%d %H:%M') if prop.get('reviewed_at') else '?'}")
                    if prop.get('review_comment'):
                        st.write(f"Comment: {prop['review_comment']}")

        with tab_rejected:
            rejected = database.list_proposals(status="rejected")
            if not rejected:
                st.info("No rejected proposals.")
            for prop in rejected:
                with st.container(border=True):
                    st.markdown(f"**#{prop['id']}** `{prop['proposal_type']}` — {prop.get('category', '')} {prop.get('term', '') or ''}")
                    st.caption(f"By {prop['proposed_by']} | Rejected by {prop.get('reviewed_by', '?')} on {prop['reviewed_at'].strftime('%Y-%m-%d %H:%M') if prop.get('reviewed_at') else '?'}")
                    if prop.get('review_comment'):
                        st.write(f"Comment: {prop['review_comment']}")


# =============================================================================
# PAGE: Record Validator
# =============================================================================
elif page == "Record Validator":
    st.header("Record Validator")
    st.info("Upload an ISAAC JSON record to validate against the schema **and** the living vocabulary.")

    # Persist validation results across reruns so the Save button works
    if "validator_result" not in st.session_state:
        st.session_state.validator_result = None
    if "validator_record" not in st.session_state:
        st.session_state.validator_record = None

    json_file = st.file_uploader("Upload JSON", type=["json"])

    # Clear validation results when file is removed or a different file is uploaded
    current_name = json_file.name if json_file else None
    if current_name != st.session_state.get("validator_filename"):
        st.session_state.validator_result = None
        st.session_state.validator_record = None
        st.session_state.validator_filename = current_name

    if json_file:
        try:
            raw_text = json_file.read().decode("utf-8")
            record_data = json.loads(raw_text)

            with st.expander("Record Preview", expanded=False):
                st.json(record_data)

            if st.button("Validate", type="primary"):
                # All three layers via the shared validation module — the
                # SAME code path the REST API and database chokepoint use.
                import validation
                full = validation.validate_record_full(record_data)

                # Store results in session state — including warnings/info, so the
                # UI surfaces exactly what the REST API and the chokepoint return
                # (they were previously dropped here, hiding accepted-but-improvable
                # feedback from anyone validating in the portal).
                st.session_state.validator_result = {
                    "schema_errors": full["schema_errors"],
                    "vocab_errors": full["vocabulary_errors"],
                    "semantic_errors": full["semantic_errors"],
                    "warnings": full.get("warnings", []),
                    "info": full.get("info", []),
                }
                st.session_state.validator_record = record_data

            # Display results from session state (persists across reruns)
            result = st.session_state.validator_result
            if result is not None:
                schema_errors = result["schema_errors"]
                vocab_errors = result["vocab_errors"]
                semantic_errors = result.get("semantic_errors", [])

                col_schema, col_vocab, col_semantic = st.columns(3)

                with col_schema:
                    if not schema_errors:
                        st.success("Schema: PASS")
                    else:
                        st.error(f"Schema: {len(schema_errors)} error(s)")
                        for e in schema_errors:
                            st.write(f"- **{e['path']}**: {e['message']}")

                with col_vocab:
                    if not vocab_errors:
                        st.success("Vocabulary: PASS")
                    else:
                        st.error(f"Vocabulary: {len(vocab_errors)} error(s)")
                        for e in vocab_errors:
                            st.write(f"- **{e['path']}**: {e['message']}")

                with col_semantic:
                    if not semantic_errors:
                        st.success("Integrity: PASS")
                    else:
                        st.error(f"Integrity: {len(semantic_errors)} error(s)")
                        for e in semantic_errors:
                            st.write(f"- **{e['path']}**: {e['message']}")

                # Warnings (accepted-but-improvable) and info — shown regardless of
                # error state, matching the API's 201 response.
                _warnings = result.get("warnings", [])
                _info = result.get("info", [])
                if _warnings:
                    st.warning(f"{len(_warnings)} warning(s) — record is accepted, but consider:")
                    for w in _warnings:
                        st.write(f"- **{w['code']}** ({w['path']}): {w['message']}")
                if _info:
                    with st.expander(f"{len(_info)} suggestion(s)"):
                        for i in _info:
                            st.write(f"- **{i['code']}** ({i['path']}): {i['message']}")

                if not schema_errors and not vocab_errors and not semantic_errors:
                    if _warnings:
                        st.success("This record is schema-valid (with the warnings above — they do not block saving).")
                    else:
                        st.success("This record is fully compliant with the ISAAC schema and vocabulary!")

                    if st.button("Save to Database", key="save_json_btn"):
                        if database.test_db_connection():
                            try:
                                # Save the CURRENT upload (record_data), not the
                                # session-state copy from validate time — a re-uploaded
                                # file with the same name would otherwise save stale
                                # content. save_record re-validates internally (the
                                # shared chokepoint), so a record that changed since
                                # the displayed PASS cannot slip through.
                                saved_id = database.save_record(record_data, uploaded_by=(current_username if current_username != "anonymous" else None), mode="insert")
                                st.success(f"Record saved! ID: `{saved_id}`")
                            except Exception as exc:
                                import validation
                                if isinstance(exc, validation.ValidationError):
                                    st.error(
                                        "Record failed validation at save time — it differs "
                                        "from the version that was validated. Click Validate "
                                        "again to see the errors."
                                    )
                                    for e in exc.result["errors"][:10]:
                                        st.write(f"- **{e['path']}**: {e['message']}")
                                else:
                                    st.error(f"Failed to save record: {exc}")
                        else:
                            st.error("Database not connected. Cannot save record.")

        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")
        except Exception as exc:
            st.error(f"Error reading file: {exc}")


# =============================================================================
# PAGE: Record Form
# =============================================================================
elif page == "Record Form":
    st.header("Manual Record Entry")
    st.info("Create ISAAC records manually using this form. Navigate to 'Record Form' page for full form.")

    # Import and run the form module
    try:
        import form
        form.render_form()
    except ImportError:
        st.warning("Record form module not found. Please ensure portal/form.py exists.")
        st.write("The full manual entry form is being developed.")


# =============================================================================
# PAGE: Saved Records
# =============================================================================
elif page == "Saved Records":
    st.header("Saved Records")

    if not db_connected:
        st.warning("Database not connected. Configure PGHOST, PGUSER, PGPASSWORD, PGDATABASE environment variables.")
    else:
        if st.button("Refresh"):
            st.rerun()

        # --- Filters ---
        with st.expander("Filters", expanded=False):
            fc1, fc2, fc3 = st.columns(3)
            f_type = fc1.text_input("Record type", key="sr_ftype").strip()
            f_domain = fc2.text_input("Domain", key="sr_fdomain").strip()
            f_mat = fc3.text_input("Material contains", key="sr_fmat").strip()
        filters = {}
        if f_type:
            filters["record_type"] = f_type
        if f_domain:
            filters["record_domain"] = f_domain
        if f_mat:
            filters["material_contains"] = f_mat

        # --- Pagination ---
        pc1, pc2, pc3 = st.columns([1, 1, 2])
        page_size = pc1.selectbox("Per page", [25, 50, 100], index=1, key="sr_psize")
        page_num = int(pc2.number_input("Page", min_value=1, value=1, step=1, key="sr_pnum"))
        offset = (page_num - 1) * int(page_size)

        try:
            rows, total = database.list_records(limit=int(page_size), offset=offset,
                                                filters=filters or None)
            total_pages = max(1, (total + int(page_size) - 1) // int(page_size))
            pc3.markdown(f"&nbsp;\n\n**{total}** records · page **{page_num}** of **{total_pages}**")

            if rows:
                df = pd.DataFrame(rows)
                df.columns = ["Record ID", "Type", "Domain", "Created At"]
                st.dataframe(df, width='stretch', hide_index=True)

                st.divider()
                st.subheader("Record detail")
                selected_id = st.selectbox("Select a record on this page",
                                           [r["record_id"] for r in rows], key="sr_sel")
                if selected_id:
                    record_data = database.get_record(selected_id)
                    if record_data:
                        owner = record_authz.record_owner(record_data)
                        acl_editors = database.acl_editor_usernames(selected_id)
                        can_edit, _why = record_authz.can_edit_record(
                            record_data, current_username, user_is_admin, acl_editors=acl_editors)
                        can_acl = record_authz.can_manage_acl(
                            record_data, current_username, user_is_admin)

                        st.caption(f"Owner: **{owner or 'unowned'}**"
                                   + ("  ·  ✏️ you can edit this record" if can_edit else ""))
                        st.json(record_data, expanded=False)
                        st.download_button("Download JSON", json.dumps(record_data, indent=2),
                                           file_name=f"isaac_record_{selected_id}.json",
                                           mime="application/json")

                        # --- Edit (owner / collaborator / admin) ---
                        if can_edit:
                            with st.expander("✏️ Edit this record"):
                                st.caption("Edit the JSON and save the **complete** record. The "
                                           "previous version is archived; ownership never changes.")
                                edited = st.text_area("Record JSON",
                                                      value=json.dumps(record_data, indent=2),
                                                      height=360, key=f"sr_edit_{selected_id}")
                                note = st.text_input("Change note (why you edited)",
                                                     key=f"sr_note_{selected_id}")
                                if st.button("💾 Save changes", key=f"sr_save_{selected_id}"):
                                    try:
                                        new_data = json.loads(edited)
                                    except json.JSONDecodeError as je:
                                        st.error(f"Not valid JSON: {je}")
                                    else:
                                        try:
                                            res = database.update_record_versioned(
                                                selected_id, new_data, actor=current_username,
                                                change_note=(note or None))
                                            st.success(f"Saved as version {res['version']} "
                                                       f"({res['change_class']} change).")
                                            st.rerun()
                                        except validation.ValidationError as ve:
                                            st.error("Validation failed — fix and retry:")
                                            st.json(ve.result)
                                        except database.VersionConflictError:
                                            st.warning("This record changed meanwhile — refresh and retry.")
                                        except Exception as ee:
                                            st.error(f"Could not save: {ee}")
                        else:
                            st.info("You can view this record. Only its owner, a collaborator, "
                                    "or an admin can edit it.")

                        # --- Collaborators (owner / admin) ---
                        if can_acl:
                            with st.expander("👥 Collaborators — let a co-author edit this record"):
                                for c in database.acl_list(selected_id):
                                    cc1, cc2 = st.columns([4, 1])
                                    cc1.write(f"**{c['grantee_identity']}** · editor "
                                              f"(added by {c.get('granted_by') or '?'})")
                                    if cc2.button("Remove",
                                                  key=f"sr_rm_{selected_id}_{c['grantee_identity']}"):
                                        database.acl_remove_editor(selected_id, c['grantee_identity'])
                                        st.rerun()
                                ng = st.text_input("Add collaborator (portal username)",
                                                   key=f"sr_addc_{selected_id}").strip()
                                if st.button("Add editor", key=f"sr_addbtn_{selected_id}"):
                                    ok, err = record_authz.validate_grant("editor", ng, owner)
                                    if not ok:
                                        st.error(f"Cannot add collaborator: {err}")
                                    else:
                                        database.acl_add_editor(selected_id, ng, current_username)
                                        st.rerun()

                        # --- Version history ---
                        with st.expander("🕓 Version history"):
                            hist = database.record_history(selected_id)
                            if hist:
                                st.dataframe(pd.DataFrame([{
                                    "When": h.get("archived_at"), "Action": h.get("action"),
                                    "By": h.get("actor"), "Version": h.get("version"),
                                    "Class": h.get("change_class"),
                                    "Note": h.get("change_note")} for h in hist]),
                                    width='stretch', hide_index=True)
                            else:
                                st.caption("No prior versions — this record hasn't been edited yet.")
                # Deletion is intentionally NOT in the web UI — it stays admin-API only
                # (DELETE /portal/api/records/<id>), archived to history. The proxy-header
                # web identity must never gate a destructive action.
            else:
                st.info("No records match these filters." if filters
                        else "No records found. Create one via the Record Validator or Record Form.")
        except Exception as e:
            st.error(f"Error loading records: {e}")


# =============================================================================
# PAGE: nano ISAAC
# =============================================================================
elif page == "nano ISAAC":
    # Re-opened to all users 2026-06-22. The 2026-06-20 admin-gate responded to a
    # suspected secret-exfil path (pg_read_file) that — verified live — does NOT
    # exist: the deployed DB role is `isaac` (NON-superuser), so file/credential
    # reads were never possible. nano-ISAAC's SQL now also runs in agent_mode
    # (records-table only; operational tables rejected by name), so the real
    # residual (cross-table reads) is closed in-code too.
    # Header row with title and Clear button
    title_col, btn_col = st.columns([5, 1])
    with title_col:
        st.header("nano ISAAC")
        st.caption("AI chat agent — ask questions about the ISAAC record database")
    with btn_col:
        st.markdown("")  # vertical spacing
        clear_chat = st.button("Clear Chat", use_container_width=True)

    # Check prerequisites
    if not db_connected:
        st.warning("Database not connected. nano ISAAC requires a live database.")
    elif not os.environ.get("ISAAC_LLM_API_KEY"):
        st.warning("LLM API key not configured. Set the ISAAC_LLM_API_KEY environment variable.")
    else:
        # Initialise session state
        if "agent_messages" not in st.session_state:
            st.session_state.agent_messages = agent.build_initial_messages()
        if "agent_display" not in st.session_state:
            st.session_state.agent_display = []

        if clear_chat:
            st.session_state.agent_messages = agent.build_initial_messages()
            st.session_state.agent_display = []
            st.rerun()

        # Scrollable chat window (fixed max height)
        chat_box = st.container(height=480)

        with chat_box:
            if not st.session_state.agent_display:
                st.markdown(
                    "*Ask me anything about the ISAAC database — e.g. "
                    "\"How many records are there?\" or "
                    "\"What materials have been measured?\"*"
                )
            for msg in st.session_state.agent_display:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

        # Input form directly below the chat box (not pinned to viewport)
        with st.form("nano_isaac_input", clear_on_submit=True):
            input_col, send_col = st.columns([6, 1])
            with input_col:
                prompt = st.text_input(
                    "Message", placeholder="Ask about the ISAAC database...",
                    label_visibility="collapsed",
                )
            with send_col:
                submitted = st.form_submit_button("Send", use_container_width=True)

        if submitted and prompt and prompt.strip():
            prompt = prompt.strip()

            # Append user message
            st.session_state.agent_display.append({"role": "user", "content": prompt})
            st.session_state.agent_messages.append({"role": "user", "content": prompt})

            # Run agent and append reply
            try:
                reply, updated = agent.run_agent_turn(st.session_state.agent_messages)
                st.session_state.agent_messages = updated
                st.session_state.agent_display.append({"role": "assistant", "content": reply})
            except Exception as exc:
                err = f"Agent error: {exc}"
                st.session_state.agent_display.append({"role": "assistant", "content": err})

            st.rerun()


# =============================================================================
# PAGE: API Documentation
# =============================================================================
# =============================================================================
# PAGE: API Keys
# =============================================================================
elif page == "API Keys":
    st.header("API Keys")
    st.markdown("Generate and manage API keys for programmatic access to the ISAAC Portal API.")

    authentik_api_url = os.environ.get(
        "AUTHENTIK_INTERNAL_URL",
        "http://authentik-server.authentik.svc.cluster.local:9000",
    )
    authentik_api_token = os.environ.get("AUTHENTIK_API_TOKEN", "")

    if not authentik_api_token:
        st.error("API key management is not configured. Contact an administrator.")
    else:
        admin_headers = {"Authorization": f"Bearer {authentik_api_token}"}

        # Look up current user's PK in Authentik
        user_pk = None
        try:
            user_resp = requests.get(
                f"{authentik_api_url}/api/v3/core/users/",
                headers=admin_headers,
                params={"username": current_username},
                timeout=5,
            )
            user_resp.raise_for_status()
            user_results = user_resp.json().get("results", [])
            if user_results:
                user_pk = user_results[0]["pk"]
        except Exception as exc:
            st.error("Could not look up your account. Please try again or contact an administrator.")

        if user_pk:
            # --- Generate new key ---
            st.subheader("Generate New Key")
            # Sanitize username for use in Authentik token identifiers (slug-compatible)
            _safe_username = re.sub(r'[^a-z0-9-]', '-', current_username.lower()).strip('-')

            if st.button("Generate API Key"):
                try:
                    import ulid
                    identifier = f"isaac-api-{_safe_username}-{str(ulid.ULID()).lower()}"

                    # Bounded TTL: keys expire so a leaked key cannot be used
                    # indefinitely. Identity (user_pk) is resolved from the
                    # edge-trusted username (C1), so a key can only be minted
                    # for the authenticated caller. (C3).
                    # NOTE: for intent="api" Authentik IGNORES any client "expires" and
                    # forces the lifetime to the tenant's Default token duration
                    # (TokenSerializer.validate: "expires cannot be overridden"). So we
                    # request an expiring token and read back the REAL expiry below rather
                    # than assert a TTL we don't control. The duration is configured in
                    # Authentik (tenant Default token duration).
                    create_resp = requests.post(
                        f"{authentik_api_url}/api/v3/core/tokens/",
                        headers=admin_headers,
                        json={
                            "identifier": identifier,
                            "intent": "api",
                            "user": user_pk,
                            "description": f"ISAAC Portal API key for {current_username}",
                            "expiring": True,
                        },
                        timeout=10,
                    )
                    if create_resp.status_code == 400:
                        detail = create_resp.json() if create_resp.headers.get("content-type", "").startswith("application/json") else {}
                        raise ValueError(f"Invalid request: {detail.get('identifier', detail.get('non_field_errors', 'unknown error'))}")
                    create_resp.raise_for_status()

                    key_resp = requests.get(
                        f"{authentik_api_url}/api/v3/core/tokens/{identifier}/view_key/",
                        headers=admin_headers,
                        timeout=5,
                    )
                    key_resp.raise_for_status()
                    key_value = key_resp.json()["key"]

                    # Report the ACTUAL expiry Authentik assigned (tenant default), so we
                    # never promise a lifetime the portal doesn't control.
                    _expires_display = "unknown — check the list below"
                    try:
                        info_resp = requests.get(
                            f"{authentik_api_url}/api/v3/core/tokens/{identifier}/",
                            headers=admin_headers,
                            timeout=5,
                        )
                        if info_resp.ok:
                            _exp = info_resp.json().get("expires")
                            if _exp:
                                _expires_display = str(_exp).replace("T", " ")[:16] + " UTC"
                    except Exception:
                        pass

                    st.success(f"API key created (expires {_expires_display}). Copy it now — it will not be shown again.")
                    st.code(key_value, language="text")
                    st.markdown("**Usage:**")
                    st.code(
                        f'curl -H "Authorization: Bearer {key_value}" \\\n'
                        f'  https://isaac.slac.stanford.edu/portal/api/records',
                        language="bash",
                    )
                except Exception as exc:
                    st.error(f"Failed to create API key. Please try again or contact an administrator.")

            # --- List existing keys ---
            st.divider()
            st.subheader("Your API Keys")
            try:
                # Authentik's token filter accepts `user__username` (NOT `user__pk`, which
                # it silently ignores — that ignored filter caused BOTH the old "showed
                # other users' keys" bug and this "no keys" bug). The list is paginated
                # (default 20/page) and includes EXPIRED tokens, so a user who regenerated
                # daily can have their newest key pushed past page 1: page through all of
                # them. Keep the identifier prefix as defense-in-depth so only this user's
                # ISAAC-minted keys are shown.
                keys = []
                _expired_hidden = 0
                _now = datetime.now(timezone.utc)
                _page = 1
                while _page and _page <= 50:  # hard cap (>5000 tokens is impossible per user)
                    list_resp = requests.get(
                        f"{authentik_api_url}/api/v3/core/tokens/",
                        headers=admin_headers,
                        params={
                            "user__username": current_username,
                            "intent": "api",
                            "page_size": 100,
                            "page": _page,
                            "ordering": "-expires,identifier",  # unique tiebreaker => stable paging
                        },
                        timeout=5,
                    )
                    list_resp.raise_for_status()
                    _body = list_resp.json()
                    for t in _body.get("results", []):
                        if not t.get("identifier", "").startswith(f"isaac-api-{_safe_username}-"):
                            continue
                        # Hide already-expired tokens: they can't authenticate and Authentik
                        # removes them on its own. The endpoint returns expired tokens too
                        # (including_expired), so a daily-regenerating user would otherwise see
                        # dozens of dead rows. Show only usable keys.
                        _exp = t.get("expires")
                        _active = True
                        if t.get("expiring", True) and _exp:
                            try:
                                _active = datetime.fromisoformat(str(_exp).replace("Z", "+00:00")) > _now
                            except Exception:
                                _active = True  # unparseable -> don't hide
                        if _active:
                            keys.append(t)
                        else:
                            _expired_hidden += 1
                    _next = _body.get("pagination", {}).get("next")  # page number, 0 on last page
                    if not _next:
                        break
                    _page = _next

                if not keys:
                    st.info("You have no API keys. Generate one above.")
                    if _expired_hidden:
                        st.caption(f"({_expired_hidden} expired key(s) hidden — expired keys "
                                   "can't be used and are removed automatically.)")
                else:
                    if _expired_hidden:
                        st.caption(f"{_expired_hidden} expired key(s) hidden.")
                    for key_info in keys:
                        ident = key_info["identifier"]
                        created = str(key_info.get("created", "unknown")).replace("T", " ")[:16]
                        # Show when each key expires (date + days left) so users can rotate
                        # before it dies; older keys created before the expiry change never
                        # expire.
                        if not key_info.get("expiring", True):
                            _exp_str = "never expires"
                        else:
                            _exp_str = "expiry unknown"
                            _e = key_info.get("expires")
                            if _e:
                                try:
                                    _edt = datetime.fromisoformat(str(_e).replace("Z", "+00:00"))
                                    _days = (_edt - datetime.now(timezone.utc)).days
                                    _exp_str = (f"expires {_edt.strftime('%Y-%m-%d')} "
                                                f"({_days} day{'' if _days == 1 else 's'} left)")
                                except Exception:
                                    _exp_str = f"expires {str(_e)[:10]}"
                        col1, col2 = st.columns([4, 1])
                        with col1:
                            st.text(ident)
                            st.caption(f"created {created} · {_exp_str}")
                        with col2:
                            if st.button("Revoke", key=f"revoke_{ident}"):
                                if not ident.startswith(f"isaac-api-{_safe_username}-"):
                                    st.error("You can only revoke your own keys.")
                                else:
                                    try:
                                        del_resp = requests.delete(
                                            f"{authentik_api_url}/api/v3/core/tokens/{ident}/",
                                            headers=admin_headers,
                                            timeout=5,
                                        )
                                        del_resp.raise_for_status()
                                        st.success(f"Revoked: {ident}")
                                        st.rerun()
                                    except Exception as exc:
                                        st.error(f"Failed to revoke: {exc}")
            except Exception as exc:
                st.error("Failed to list API keys. Please try again or contact an administrator.")


# =============================================================================
# PAGE: API Documentation
# =============================================================================
elif page == "API Documentation":
    st.header("API Documentation")
    st.info("The ISAAC Portal includes a REST API sidecar for programmatic record submission and validation.")

    st.subheader("Authentication")
    st.markdown("""
    All API endpoints (except health check) require authentication via a **Bearer token**.

    **How to get your token:**

    1. Go to the **API Keys** page in this portal (from the Menu)
    2. Click **Generate API Key**
    3. **Copy the key immediately** — it is only shown once

    Then pass it in the `Authorization` header of every API request:
    """)
    st.code('Authorization: Bearer <your-token-key>', language="text")

    st.subheader("Base URL")
    st.code("https://isaac.slac.stanford.edu/portal/api", language="text")

    st.divider()

    # --- Health ---
    st.subheader("Endpoints")

    st.markdown("#### Health Check")
    st.code("GET /portal/api/health", language="text")
    st.markdown("Returns `200` with `{\"status\": \"healthy\"}`. Use for connectivity checks.")

    st.divider()

    # --- Validate ---
    st.markdown("#### Validate a Record (dry-run)")
    st.code("POST /portal/api/validate", language="text")
    st.markdown("""
    Validates a JSON record against the ISAAC schema **without** saving to the database.
    Use this to check your data before committing it.
    """)
    st.markdown("**Example request:**")
    st.code('''curl -X POST https://isaac.slac.stanford.edu/portal/api/validate \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer <token>" \\
  -d '{
    "isaac_record_version": "1.05",
    "record_id": "01JFH3Q8Z1Q9F0XG3V7N4K2M8C",
    "record_type": "evidence",
    "record_domain": "characterization",
    "source_type": "facility",
    "tags": ["cuo-reference", "xps-2025"],
    "timestamps": { "created_utc": "2025-12-14T20:15:00Z" },
    "sample": {
      "material": { "name": "Copper(II) Oxide", "formula": "CuO2", "provenance": "commercial" },
      "sample_form": "pellet"
    }
  }' ''', language="bash")
    st.markdown("**Response fields:**")
    st.markdown("""
    | Field | Type | Description |
    |---|---|---|
    | `valid` | bool | `true` only if schema, vocabulary **and** semantic/integrity all pass |
    | `schema_valid` | bool | JSON Schema validation result |
    | `vocabulary_valid` | bool | Living-ontology vocabulary check result |
    | `semantic_valid` | bool | Semantic/integrity check result |
    | `schema_errors` | list | Schema validation errors |
    | `vocabulary_errors` | list | Vocabulary validation errors |
    | `semantic_errors` | list | Semantic/integrity errors |
    | `errors` | list | Combined list (schema + vocabulary + semantic) |
    | `warnings` | list | Accepted-but-improvable feedback (does not block) |
    | `info` | list | Suggestions (does not block) |
    """)
    st.markdown("**Responses:**")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("*Valid:*")
        st.code('''{ "valid": true,
  "schema_valid": true,
  "vocabulary_valid": true,
  "schema_errors": [],
  "vocabulary_errors": [],
  "errors": [] }''', language="json")
    with col2:
        st.markdown("*Invalid vocabulary:*")
        st.code('''{ "valid": false,
  "schema_valid": true,
  "vocabulary_valid": false,
  "schema_errors": [],
  "vocabulary_errors": [
    { "path": "system.domain",
      "message": "'empirical_wrong' is not in the vocabulary..." }
  ],
  "errors": [...] }''', language="json")

    st.divider()

    # --- Create Record ---
    st.markdown("#### Create a Record (validate + write)")
    st.code("POST /portal/api/records", language="text")
    st.markdown("""
    Validates the record against **both** the JSON Schema and the living vocabulary,
    and **if valid**, persists it to the database.
    This is the "write-if-valid" endpoint — invalid records are rejected without side effects.
    """)
    st.markdown("**Responses:**")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("*Success (201):*")
        st.code('{ "success": true, "record_id": "01JFH..." }', language="json")
    with col2:
        st.markdown("*Validation failure (400):*")
        st.code('''{ "success": false,
  "reason": "validation_failed",
  "schema_errors": [...],
  "vocabulary_errors": [...],
  "errors": [...] }''', language="json")

    st.divider()

    # --- List / Get ---
    st.markdown("#### List Records")
    st.code("GET /portal/api/records?limit=100&offset=0", language="text")
    st.markdown("Returns an array of record summaries (record ID, type, domain, creation timestamp).")

    st.markdown("#### Get a Single Record")
    st.code("GET /portal/api/records/<record_id>", language="text")
    st.markdown("Returns the full JSON for a specific record by its ULID.")

    st.divider()

    # --- Query records (read-only SQL) ---
    st.markdown("#### Query Records (read-only SQL)")
    st.code("POST /portal/api/records/query", language="text")
    st.markdown("""
    Run a **read-only** `SELECT`/`WITH` over the records (and other non-sensitive reference
    tables). Open to any authenticated user. Guardrails: a **single** statement, no writes/DDL,
    no system catalogs, a statement timeout, and `LIMIT` capped at **500**.
    Body: `{"sql": "SELECT ...", "max_rows": 100}`.

    The scientific content is a JSONB column `data`. Use `->` to get a JSON value and `->>`
    to get it **as text**:
    """)
    st.markdown("`data->'context'` (object) · "
                "`data->'context'->'electrochemistry'->>'reaction'` (text) · "
                "`data->'descriptors'->'faradaic_efficiency'->>'C2H4'` (text)")
    st.markdown("**Example queries:**")
    st.code("""-- columns + a nested JSONB value as text
SELECT record_id, record_type,
       data->'context'->'electrochemistry'->>'reaction' AS reaction
FROM records LIMIT 20;

-- filter on a nested JSONB field
SELECT record_id FROM records
WHERE data->'context'->'electrochemistry'->>'reaction' = 'CO2RR'
LIMIT 50;

-- count records by type
SELECT record_type, COUNT(*) AS n FROM records GROUP BY record_type ORDER BY n DESC;""",
            language="sql")
    st.markdown("**curl.** Heads-up: JSONB keys need single quotes *inside* the SQL, which "
                "collide with a single-quoted shell `-d '...'`. Simplest fix — put the body in "
                "a file:")
    st.code("""cat > q.json <<'EOF'
{"sql": "SELECT record_id, data->'context'->'electrochemistry'->>'reaction' AS reaction FROM records LIMIT 5"}
EOF
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -X POST https://isaac.slac.stanford.edu/portal/api/records/query -d @q.json""",
            language="bash")
    st.markdown("Returns `{\"rows\": [...], \"row_count\": N, \"truncated_at\": <max|null>}`. "
                "A malformed query returns a clear `400 {\"error\": \"SQL error: ...\"}`. "
                "Sensitive tables (usage/access logs, ACLs, proposals, and record_history — "
                "the edit audit log) are admin-only.")

    st.divider()

    # --- Edit a record you own ---
    st.markdown("#### Edit a Record (your own)")
    st.code("PUT /portal/api/records/<record_id>", language="text")
    st.markdown("""
    Update an **existing** record by re-submitting the **complete** record JSON.
    `POST` cannot overwrite (it is insert-only); editing always goes through `PUT`.

    **Who may edit:** the record's **owner** (whoever submitted it — your identity is
    stamped automatically on upload), an explicit **collaborator** (see below), or an
    **admin**. Anyone else gets `403`.

    - Ownership never changes on an edit. The previous version is **archived** (recoverable)
      and the record's `version` is bumped.
    - Optional `If-Match: <version>` header → safe against concurrent edits
      (`412` if the version moved, `409` on a concurrent write).
    - Optional `change_note` (in the body or `?change_note=`) records *why* you edited.
    """)
    st.code('''curl -X PUT https://isaac.slac.stanford.edu/portal/api/records/<record_id> \\
  -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \\
  -H "If-Match: 1" \\
  -d '<the full, updated record JSON>' ''', language="bash")
    st.markdown("Returns `200 {success, version, content_hash, change_class}`.")

    st.divider()

    # --- Collaborators (co-author ACL) ---
    st.markdown("#### Collaborators — let a co-author edit your record")
    st.markdown("""
    The record **owner** (or an admin) can grant another portal user editor access, so
    co-authors can edit without being an admin. Collaborators are keyed on portal username.
    """)
    st.code("""GET    /portal/api/records/<record_id>/collaborators
POST   /portal/api/records/<record_id>/collaborators      {"identity": "<username>"}
DELETE /portal/api/records/<record_id>/collaborators/<username>""", language="text")

    st.divider()

    # --- History / diff ---
    st.markdown("#### Version History & Diff")
    st.code("""GET /portal/api/records/<record_id>/history
GET /portal/api/records/<record_id>/diff?from=<v>&to=<v|current>""", language="text")
    st.markdown("Every edit is versioned and archived; `history` lists versions (actor, "
                "when, change note), `diff` shows the field-level changes between two versions.")

    st.divider()

    # --- Admin: owner reassignment ---
    st.markdown("#### Correct a Record's Owner (admin only)")
    st.code("""PATCH /portal/api/records/<record_id>/owner   {"uploaded_by": "<username>", "reason": "..."}
PATCH /portal/api/records/owner               {"record_ids": [...], "uploaded_by": "<username>", "reason": "..."}""", language="text")
    st.markdown("""
    **Admin only.** Corrects who owns a record (e.g. a batch ingested under the wrong
    identity). Archived + audited; the new owner can then self-serve edits. This is the
    **only** way ownership changes — a normal edit never transfers it.
    """)

    st.divider()

    # --- Python example ---
    st.subheader("Python Example")
    st.markdown("List records and fetch a single record using `requests`:")
    st.code('''import requests

API_URL = "https://isaac.slac.stanford.edu/portal/api"
TOKEN = "your-api-key-here"

headers = {"Authorization": f"Bearer {TOKEN}"}

# List records (paginated)
resp = requests.get(f"{API_URL}/records", headers=headers, params={"limit": 10})
resp.raise_for_status()
records = resp.json()
print(f"Found {len(records)} records")

# Fetch a single record by ID
if records:
    record_id = records[0]["record_id"]
    resp = requests.get(f"{API_URL}/records/{record_id}", headers=headers)
    resp.raise_for_status()
    record = resp.json()
    print(f"Record {record_id}: {record['record_type']} / {record['record_domain']}")''', language="python")

    st.divider()

    # --- Simplest curl example ---
    st.subheader("Simplest Curl Example")
    st.markdown("Fetch all records with a single `curl` command:")
    st.code(
        'curl -H "Authorization: Bearer <token>" \\\n'
        '  https://isaac.slac.stanford.edu/portal/api/records',
        language="bash",
    )

    st.divider()
    st.markdown(f"**Schema version: ISAAC AI-Ready Record v1.05**")


# =============================================================================
# PAGE: Discovery (hypothesis-driven reasoning workbench)
# =============================================================================
elif page == "Discovery":
    st.header("🔬 Discovery")
    st.caption("Hypothesis-driven reasoning workbench. Projects, competing "
               "hypotheses, their predictions and verdicts, a live ranking, and "
               "the ISAAC agent's reasoning transcript — with full provenance.")

    if not database.is_discovery_db_configured():
        st.info("Discovery database is not configured in this environment.")
    else:
        _DISC_OWNER = current_username
        if "discovery_project" not in st.session_state:
            st.session_state.discovery_project = None

        # ONE autumn/fall palette, assigned per HYPOTHESIS (by identity, not status),
        # and used IDENTICALLY across the ranking bars, the constellation dots, and
        # the belief river — so a given hypothesis is the same colour everywhere.
        # Ordered so the first few are maximally distinct (hue + lightness varied);
        # deliberately avoids the cool cyan/pink used for evidence verdicts.
        _HYP_PALETTE = ["#E8941F", "#5F7A34", "#B5462B", "#D8B02A", "#7A4A2B",
                        "#C97A3C", "#8C6E2A", "#9C3B30"]

        def _hyp_colors(hyps):
            return {h["label"]: _HYP_PALETTE[i % len(_HYP_PALETTE)]
                    for i, h in enumerate(hyps)}

        _VERDICT_ICON = {"supports": "✅", "contradicts": "❌",
                         "neutral": "➖", "insufficient": "❓", "blocked": "🚫"}

        def _bar(label, statement, confidence, status, color, score=None):
            c = float(confidence or 0.0)
            pct = max(0, min(100, int(round(c * 100))))
            dead = status in ("eliminated", "superseded")
            op = 0.5 if dead else 1.0
            pal = branding.palette(st.session_state.ui_theme)
            # The confidence bar is a LEVEL (0–1), not a verdict — so every live bar uses
            # the SAME neutral brand accent (theme-aware: teal on both light & dark).
            # Hypothesis identity stays on the colored dot + title (matching the
            # constellation); a per-hypothesis red/amber bar would read as "warning/wrong"
            # when it only means "lower confidence". Eliminated/superseded → muted fill.
            bar_fill = pal["muted"] if dead else pal["accent"]
            # Right-hand metadata, kept compact so it never crowds the statement:
            # status · confidence · reliability note.
            meta = (f"<span style='color:{pal['muted']}'>{html.escape(str(status))}</span>"
                    f" · <b style='color:{pal['text']}'>{c:.2f}</b>")
            if score is not None:
                _n = score.get("n_decisive", score.get("n_scored", 0))
                _nb = score.get("n_blocked", 0)
                if not score.get("reliable") and not dead:
                    meta += (f" · <span style='color:{pal['error']}'>⚠ {_n} decisive"
                             " — unreliable</span>")
                else:
                    _blk = f", {_nb} blocked" if _nb else ""
                    meta += (f" · <span style='color:{pal['muted']}'>{_n} decisive{_blk}, "
                             f"{score.get('coverage', 0):.0%} cov</span>")
            # Flexbox header: three zones that CANNOT overlap — identity (fixed),
            # statement (flexes + ellipsis-truncates in the space left), metadata
            # (fixed, pinned right). This replaces the old float:right, whose metadata
            # dropped onto the bar whenever the statement wrapped.
            return (
                f"<div style='margin:11px 0;opacity:{op}'>"
                f"<div style='display:flex;align-items:baseline;gap:9px;"
                f"font-size:0.85em;line-height:1.4'>"
                f"<span style='flex-shrink:0;white-space:nowrap'>"
                f"<span style='display:inline-block;width:9px;height:9px;border-radius:50%;"
                f"background:{color};margin-right:7px;vertical-align:middle'></span>"
                f"<b style='color:{color}'>{html.escape(str(label or ''))}</b></span>"
                f"<span style='flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;"
                f"white-space:nowrap;color:{pal['muted']}'>{html.escape(str(statement or ''))}</span>"
                f"<span style='flex-shrink:0;white-space:nowrap'>{meta}</span>"
                f"</div>"
                f"<div style='background:{pal['surface']};border:1px solid {pal['border_soft']};"
                f"border-radius:5px;height:9px;width:100%;margin-top:5px;overflow:hidden'>"
                f"<div style='background:{bar_fill};width:{pct}%;height:9px;"
                f"border-radius:5px'></div></div></div>")

        def _genesis_html(payload, theme="dark"):
            # GENESIS — generic, per-project: how the hypotheses were conceived. The SEED is
            # the project's real GOAL (what ISAAC was actually asked). From it, the competing
            # mechanisms are revealed ONE AT A TIME, each a receipt: its mechanism, WHY it was
            # posed (the real origin.reasoning), and its grounding + source. The null is set
            # apart. NO fabricated chart — everything is per-project data from the record.
            # Staged reveal; autoplay + replay; reduced-motion shows the final frame.
            pal = branding.palette(theme)
            P = json.dumps({"text": pal["text"], "muted": pal["muted"], "accent": pal["accent"]})
            data = json.dumps(payload)
            tmpl = r"""<html><head><style>
html,body{margin:0;background:transparent;font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:__TEXT__;}
#g{padding:4px 4px 2px;}
.k{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:__MUTED__;}
#seedwrap{border-left:3px solid __ACCENT__;padding:2px 0 2px 14px;margin:2px 0 4px;
 opacity:0;transform:translateX(-6px);transition:opacity .6s ease,transform .6s ease;}
#seed{font-size:17px;font-weight:600;color:__TEXT__;margin-top:3px;line-height:1.35;}
#bridge{margin:14px 0 8px;font-size:13px;color:__MUTED__;opacity:0;transition:opacity .6s ease;}
#bridge b{color:__TEXT__;font-weight:600;}
#cards{display:flex;flex-wrap:wrap;gap:10px;}
.card{flex:1 1 220px;min-width:200px;border:1px solid __BORDER__;border-left:3px solid var(--c);
 border-radius:10px;padding:10px 13px;background:__SURFACE__;opacity:0;transform:translateY(8px);
 transition:opacity .5s ease,transform .5s ease;}
.card.null{border-style:dashed;border-left-style:dashed;}
.card .nm{font-weight:700;font-size:13px;color:var(--c);}
.card .mech{font-size:12px;color:__TEXT__;margin:4px 0 6px;line-height:1.4;}
.card .why{font-size:11px;color:__MUTED__;line-height:1.4;margin-bottom:6px;}
.card .src{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:10px;color:__MUTED__;}
#replay{margin-top:12px;font-size:11px;color:__MUTED__;background:none;border:1px solid __BORDER_SOFT__;
 border-radius:14px;padding:3px 12px;cursor:pointer;}
#replay:hover{color:__ACCENT__;border-color:__ACCENT__;}
</style></head><body><div id="g">
<div id="seedwrap"><div class="k">The seed · what ISAAC was asked</div><div id="seed"></div></div>
<div id="bridge"></div>
<div id="cards"></div>
<button id="replay">↻ replay</button>
</div>
<script>
const D=__DATA__, P=__PAL__;
const cards=document.getElementById('cards');
const seedwrap=document.getElementById('seedwrap'), bridge=document.getElementById('bridge');
function esc(s){return (s||'').replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
document.getElementById('seed').textContent=D.goal;
bridge.innerHTML='From it, ISAAC posed <b>'+D.n+' competing mechanism'+(D.n===1?'':'s')
 +'</b> the field actually debates — each grounded, and stated so it can be broken.';
D.hyps.forEach(function(h){
 var d=document.createElement('div');d.className='card'+(h.is_null?' null':'');d.style.setProperty('--c',h.color);
 var foot=h.is_null?'⌜ the null — kept on the board to rule out'
   :('⌜ '+esc(h.grounding)+(h.source?' · '+esc(h.source):''));
 d.innerHTML='<div class="nm">'+esc(h.label)+'</div>'
  +'<div class="mech">'+esc(h.mech)+'</div>'
  +(h.why?'<div class="why">posed because: '+esc(h.why)+'</div>':'')
  +'<div class="src">'+foot+'</div>';
 cards.appendChild(d);
});
const cardEls=cards.querySelectorAll('.card');
var timers=[];
function clearT(){timers.forEach(clearTimeout);timers=[];}
function at(ms,fn){timers.push(setTimeout(fn,ms));}
function show(){
 seedwrap.style.opacity=1;seedwrap.style.transform='none';bridge.style.opacity=1;
 cardEls.forEach(function(c){c.style.opacity=1;c.style.transform='none';});}
function play(){
 clearT();
 seedwrap.style.opacity=0;seedwrap.style.transform='translateX(-6px)';bridge.style.opacity=0;
 cardEls.forEach(function(c){c.style.opacity=0;c.style.transform='translateY(8px)';});
 at(150,function(){seedwrap.style.opacity=1;seedwrap.style.transform='none';});  // the seed
 at(1100,function(){bridge.style.opacity=1;});                                   // the bridge
 cardEls.forEach(function(c,i){at(1700+i*380,function(){c.style.opacity=1;c.style.transform='none';});});
}
document.getElementById('replay').addEventListener('click',play);
if(window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches){show();}else{play();}
</script></body></html>"""
            return (tmpl.replace("__DATA__", data).replace("__PAL__", P)
                    .replace("__TEXT__", pal["text"]).replace("__MUTED__", pal["muted"])
                    .replace("__ACCENT__", pal["accent"]).replace("__SURFACE__", pal["surface"])
                    .replace("__BORDER_SOFT__", pal["border_soft"]).replace("__BORDER__", pal["border"]))

        def _ranking_html(rows, theme="dark", height=320, note=""):
            # Master-detail ranking: confidence bars on the left; hovering one fills a side
            # panel with that hypothesis's full prediction breakdown (validated / invalidated
            # / inconclusive / pending), and for each evaluated prediction the admissibility
            # gates (cited / falsifiable / structured / explained) + whether it counts toward
            # n_decisive (and if not, why). The `note` (what 'decisive' means) is tucked behind
            # an ⓘ hover, not shown as a wall of text. Panel resets when the mouse leaves.
            pal = branding.palette(theme)
            P = json.dumps({"text": pal["text"], "muted": pal["muted"],
                            "accent": pal["accent"], "err": pal["error"]})
            data = json.dumps(rows)
            note_j = json.dumps(note)
            _hh = height
            tmpl = r"""<html><head><style>
html,body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:transparent;}
#hdr{display:flex;justify-content:flex-end;align-items:center;margin:0 0 6px;}
#info{font-size:11px;color:__MUTED__;border:1px solid __BORDERSOFT__;border-radius:14px;
 padding:2px 10px;cursor:help;}
#info:hover{color:__ACCENT__;border-color:__ACCENT__;}
#wrap{display:flex;gap:14px;align-items:flex-start;}
#bars{flex:1 1 54%;min-width:0;}
#panel{flex:1 1 46%;background:__SURF__;border:1px solid __BORDER__;border-radius:10px;
 padding:12px 14px;overflow:auto;max-height:__HH__px;color:__TEXT__;font-size:12.5px;
 line-height:1.5;box-sizing:border-box;}
.row{padding:7px 8px;border-radius:8px;}
.row:hover,.row.active{background:__ACCSOFT__;}
.head{display:flex;align-items:baseline;gap:8px;font-size:12.5px;}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex:none;}
.lab{font-weight:700;white-space:nowrap;}
.stmt{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:__MUTED__;}
.meta{white-space:nowrap;color:__MUTED__;font-size:11.5px;}
.barbg{background:__SURF__;border:1px solid __BORDERSOFT__;border-radius:5px;height:9px;
 margin-top:5px;overflow:hidden;}
.barfill{height:9px;border-radius:5px;}
.unrel{color:__ERR__;}
.ph{color:__MUTED__;font-style:italic;}
.pgrp{margin-top:9px;}
.pitem{margin:5px 0 2px 2px;}
.gates{margin:3px 0 7px 18px;display:flex;flex-wrap:wrap;gap:4px;align-items:center;}
.g{font-size:9.5px;padding:1px 6px;border-radius:7px;border:1px solid;white-space:nowrap;}
.why{font-size:10px;}
</style></head><body>
<div id="hdr"><span id="info">ⓘ what counts as “decisive”?</span></div>
<div id="wrap"><div id="bars"></div>
<div id="panel"></div></div>
<script>
const R=__DATA__, P=__PAL__, NOTE=__NOTE__;
const GREEN='#3a9d6b';
// Broadcast the hovered hypothesis to the constellation below (same-origin iframes),
// so it can spotlight that hypothesis's whole network. No-ops if unsupported.
var BC=null; try{BC=new BroadcastChannel('isaac-focus');}catch(e){}
function broadcast(id){if(BC)BC.postMessage({focus:id,origin:'ranking'});}
const bars=document.getElementById('bars'), panel=document.getElementById('panel');
const CAT={validated:['✅',GREEN],invalidated:['❌',P.err],
           inconclusive:['◌',P.muted],pending:['⏳',P.muted]};
const REASON={accommodation:'circular — parameters fit to this same data',
 cross_system:'borrowed from a different system (suggestive only)',
 low_reliability:'weak-provenance source',
 shared_evidence:'rests on the same evidence as another (counted once)',
 robustness:'same observable, different method (robustness, not independence)',
 uncited:'not yet cited to a record or computation',
 unfalsifiable:'no falsification criterion stated',
 unstructured:'missing direction / reference condition',
 unexplained:'no rationale recorded'};
function esc(s){return (s||'').replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
function chip(label, ok){var c=ok?GREEN:P.muted;
 return '<span class="g" style="border-color:'+c+';color:'+c+';opacity:'+(ok?1:0.65)+'">'
   +(ok?'✓':'✗')+' '+label+'</span>';}
const maxConf=Math.max.apply(null,R.map(function(x){return x.conf||0;}).concat([0.01]));
R.forEach(function(h,i){
 const d=document.createElement('div'); d.className='row'; d.dataset.i=i; d.style.opacity=h.dead?0.5:1;
 // TWO honest encodings, no red: bar LENGTH + BRIGHTNESS = confidence (the leader pops);
 // bar TEXTURE = decisiveness — SOLID once reliable, HATCHED/ghostly while still
 // provisional. The ghost bar itself says "confident but not yet proven".
 const col=h.dead?P.muted:P.accent;
 const op=(0.40+0.60*((h.conf||0)/maxConf)).toFixed(2);
 const bg=h.reliable?col:('repeating-linear-gradient(45deg,'+col+' 0 3px,rgba(0,0,0,0) 3px 7px)');
 const nd=Math.max(0,Math.min(2,h.n_decisive||0));
 const dcol=(h.n_decisive>0)?P.accent:P.muted;
 const dots='●'.repeat(nd)+'○'.repeat(2-nd);
 let meta='<span style="color:'+P.muted+'">'+esc(h.status)+'</span> · '
   +'<b style="color:'+P.text+'">'+h.conf.toFixed(2)+'</b> · '
   +'<span style="color:'+dcol+'" title="decisive tests passed, of 2 needed to confirm">'
   +dots+' decisive</span>';
 d.innerHTML='<div class="head"><span class="dot" style="background:'+h.color+'"></span>'
  +'<span class="lab" style="color:'+h.color+'">'+esc(h.label)+'</span>'
  +'<span class="stmt">'+esc(h.statement)+'</span><span class="meta">'+meta+'</span></div>'
  +'<div class="barbg"><div class="barfill" style="width:'+Math.round(h.conf*100)+'%;background:'+bg+';opacity:'+op+'"></div></div>';
 d.addEventListener('mouseenter',function(){cancelHide();show(i);broadcast(h.id);});
 bars.appendChild(d);
});
function show(i){
 document.querySelectorAll('.row').forEach(function(r){r.classList.toggle('active', r.dataset.i==String(i));});
 const h=R[i];
 let s='<div style="font-weight:700;color:'+h.color+';font-size:13.5px">'+esc(h.label)+' · '+h.conf.toFixed(2)+'</div>';
 s+='<div style="color:'+P.text+';margin:4px 0 8px">'+esc(h.statement)+'</div>';
 s+='<div style="color:'+P.muted+';font-size:11.5px;margin-bottom:4px">'+h.preds.length
   +' prediction(s) · ✅ '+h.nv+'  ❌ '+h.ni+'  ⏳ '+h.npd
   +(h.reliable?' · <span style="color:'+GREEN+'">reliable</span>'
              :' · <span style="color:'+P.muted+'">'+h.n_decisive+'/2 decisive → provisional</span>')+'</div>';
 [['validated','Validated (supports)'],['invalidated','Invalidated (contradicts)'],
  ['inconclusive','Ran, inconclusive'],['pending','Pending (not yet run)']].forEach(function(g){
  const items=h.preds.filter(function(p){return p.cat===g[0];}); if(!items.length) return;
  const ic=CAT[g[0]][0], col=CAT[g[0]][1];
  s+='<div class="pgrp"><b style="color:'+col+';font-size:11px;text-transform:uppercase;letter-spacing:.04em">'
    +ic+' '+g[1]+' ('+items.length+')</b>';
  items.forEach(function(p){
   s+='<div class="pitem">'+ic+' '+esc(p.name)
     +(p.strength?' <span style="color:'+P.muted+'">· '+esc(p.strength)+'</span>':'')+'</div>';
   if(p.evaluated){
    s+='<div class="gates">'+chip('cited',p.cited)+chip('falsifiable',p.falsifiable)
      +chip('structured',p.structured)+chip('explained',p.explained);
    if(p.counted) s+='<span class="why" style="color:'+GREEN+'">→ counts as an independent decisive test</span>';
    else if(p.cat==='inconclusive') s+='<span class="why" style="color:'+P.muted+'">→ ran, no decisive effect</span>';
    else { var r=REASON[p.reason]||(p.admissible?'shares evidence with another test':'missing a criterion above');
           s+='<span class="why" style="color:'+P.muted+'">→ not decisive yet: '+r+'</span>'; }
    s+='</div>';
   }
  });
  s+='</div>';
 });
 if(!h.preds.length) s+='<div class="ph">No predictions attached yet — this hypothesis has no falsifiers.</div>';
 panel.innerHTML=s;
}
const PLACEHOLDER='<div class="ph">Hover a hypothesis to see its predictions — which are '
 +'validated / invalidated / pending, and for each, which decisiveness criteria '
 +'(cited · falsifiable · structured · explained · independent) it already meets.</div>';
function resetPanel(){document.querySelectorAll('.row').forEach(function(r){r.classList.remove('active');});
 panel.innerHTML=PLACEHOLDER;broadcast(null);}
// HOVER INTENT: the panel follows the mouse but with a grace delay, so you can travel
// from a hypothesis INTO the panel and scroll it. Entering bars/panel/ⓘ cancels the
// pending hide; leaving any of them schedules a hide that the next enter cancels.
var hideTimer=null;
function cancelHide(){if(hideTimer){clearTimeout(hideTimer);hideTimer=null;}}
function scheduleHide(){cancelHide();hideTimer=setTimeout(resetPanel,650);}
bars.addEventListener('mouseenter',cancelHide);
bars.addEventListener('mouseleave',scheduleHide);
panel.addEventListener('mouseenter',cancelHide);
panel.addEventListener('mouseleave',scheduleHide);
var info=document.getElementById('info');
info.addEventListener('mouseenter',function(){cancelHide();panel.innerHTML=
 '<div style="color:'+P.text+'">'+esc(NOTE)+'</div>';});
info.addEventListener('mouseleave',scheduleHide);
resetPanel();
</script></body></html>"""
            return (tmpl.replace("__DATA__", data).replace("__PAL__", P)
                    .replace("__NOTE__", note_j)
                    .replace("__SURF__", pal["surface"])
                    .replace("__BORDER__", pal["border"])
                    .replace("__BORDERSOFT__", pal["border_soft"])
                    .replace("__TEXT__", pal["text"]).replace("__MUTED__", pal["muted"])
                    .replace("__ACCENT__", pal["accent"])
                    .replace("__ERR__", pal["error"]).replace("__ACCSOFT__", pal["accent_soft"])
                    .replace("__HH__", str(_hh - 50)))

        def _constellation_html(payload, theme="dark"):
            dark = theme != "light"
            pal = json.dumps({
                "bg1": "#0c1226" if dark else "#eef3fa",
                "bg2": "#04050a" if dark else "#dbe6f3",
                "ring": "#24324f" if dark else "#c6d3e4",
                "ringlab": "#46587e" if dark else "#90a4c0",
                "badge": "#8bbad2" if dark else "#3d6885",
                "label": "#eef3ff" if dark else "#10243a",
                "labshadow": "rgba(0,0,0,0.6)" if dark else "rgba(255,255,255,0.85)",
                "screened": "#33446a" if dark else "#a9bad4",
                "evid": "#d6dee6" if dark else "#5f7081",
                "relrest": "#7e8aa0" if dark else "#9aa7bd",
                "tipbg": "rgba(16,22,40,0.96)" if dark else "rgba(255,255,255,0.98)",
                "tiptext": "#eaf0ff" if dark else "#13243a",
                "tipborder": "#2a3a5e" if dark else "#c2d0e4",
            })
            data = json.dumps(payload)
            tmpl = r"""
<html><head><script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>html,body{margin:0;overflow:hidden;}text{font-family:-apple-system,Segoe UI,Roboto,sans-serif;}
#c{cursor:grab;}#c:active{cursor:grabbing;}
#tt{position:fixed;pointer-events:none;opacity:0;transition:opacity .12s;max-width:300px;
 font:12px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;padding:8px 10px;border-radius:8px;
 box-shadow:0 4px 18px rgba(0,0,0,0.35);z-index:9;}
#tt code{font-size:10px;opacity:.8;}</style></head>
<body><div id="tt"></div><svg id="c" width="100%" height="560"></svg><script>
const DATA=__DATA__, P=__PAL__;
const tt=document.getElementById('tt');
tt.style.background=P.tipbg; tt.style.color=P.tiptext; tt.style.border='1px solid '+P.tipborder;
document.body.style.background='radial-gradient(circle at 50% 45%,'+P.bg1+','+P.bg2+')';
const el=document.getElementById('c'); const W=el.clientWidth||820, H=560; const cx=W/2, cy=H/2-2;
const svg=d3.select('#c').attr('width',W).attr('height',H);
const defs=svg.append('defs');
const glow=defs.append('filter').attr('id','g').attr('x','-80%').attr('y','-80%').attr('width','260%').attr('height','260%');
glow.append('feGaussianBlur').attr('stdDeviation','3.6').attr('result','b');
const fm=glow.append('feMerge');fm.append('feMergeNode').attr('in','b');fm.append('feMergeNode').attr('in','SourceGraphic');
const SC={supported:'#ffca28',eliminated:'#6f6f6f',needs_more_data:'#ffa726',proposed:'#4aa3ff',superseded:'#5a5a5a'};
const VC={supports:'#26c6da',contradicts:'#ec407a',neutral:'#90a4ae',insufficient:'#5c6b7a',blocked:'#7e6a4e'};
const CALC='#ab47bc'; const INVOLVED='#5b7da6';
function rT(d){return d.kind==='hyp'?56+(1-(d.conf||0))*150:d.kind==='pred'?250:(d.kind==='evid'||d.kind==='calc')?330:d.kind==='involved'?366:392;}
function nR(d){return d.kind==='hyp'?8+(d.conf||0)*22:d.kind==='pred'?({strong:7,moderate:5,weak:3}[d.strength]||4):d.kind==='calc'?4.5:d.kind==='evid'?3:d.kind==='involved'?1.9:1.3+Math.min(3.4,Math.log((d.n||1)+1));}
function nC(d){return d.kind==='hyp'?(d.color||SC[d.status]||'#90caf9'):d.kind==='pred'?(VC[d.verdict]||'#455a64'):d.kind==='calc'?CALC:d.kind==='involved'?INVOLVED:d.kind==='evid'?P.evid:P.screened;}
function nO(d){return d.kind==='screened'?0.4:d.kind==='involved'?0.62:d.kind==='evid'?0.85:d.kind==='calc'?0.95:(d.status==='eliminated'||d.status==='superseded')?0.45:1;}
[[56,'leading'],[250,'predictions'],[330,'evidence'],[366,'in-scope'],[392,'screened']].forEach(function(p){
 svg.append('circle').attr('cx',cx).attr('cy',cy).attr('r',p[0]).attr('fill','none').attr('stroke',P.ring).attr('stroke-dasharray','2,7').attr('opacity',0.7);
 svg.append('text').attr('x',cx).attr('y',cy-p[0]-3).attr('fill',P.ringlab).attr('font-size',9).attr('text-anchor','middle').attr('opacity',0.85).text(p[1]);});
svg.append('text').attr('x',16).attr('y',26).attr('fill',P.badge).attr('font-size',12).attr('font-weight',600)
 .text(DATA.corpus.records.toLocaleString()+'  records   →   '+DATA.corpus.screened+'  screened   →   '+(DATA.corpus.involved||0)+'  in-scope   →   '+DATA.corpus.cited+'  cited');
const nodes=DATA.nodes.map(function(d){return Object.assign({},d);});
const links=DATA.links.map(function(d){return Object.assign({},d);});
// SPREAD the hypotheses evenly AROUND the circle (an angular target each), so they fan
// across the real estate instead of clustering wherever their evidence pulls them. Their
// predictions/evidence then follow each hypothesis via the link force.
const _hn=nodes.filter(function(n){return n.kind==='hyp';});
_hn.forEach(function(n,i){n.theta=(-Math.PI/2)+(2*Math.PI*i/Math.max(1,_hn.length));});
function hX(d){return d.kind==='hyp'&&d.theta!=null?cx+rT(d)*Math.cos(d.theta):cx;}
function hY(d){return d.kind==='hyp'&&d.theta!=null?cy+rT(d)*Math.sin(d.theta):cy;}
const cont=svg.append('g');
const link=cont.append('g').selectAll('line').data(links).join('line')
 .attr('stroke',function(d){return d.rel==='pred'?(VC[d.verdict]||'#37474f'):d.rel==='calc'?'#ab47bc':d.rel==='evid'?P.screened:d.rel==='competes_with'?'#ef5350':d.rel==='co_operating'?'#66bb6a':P.relrest;})
 .attr('stroke-opacity',function(d){return d.rel==='evid'?0.42:d.rel==='calc'?0.6:d.rel==='pred'?0.72:0.78;})
 .attr('stroke-width',function(d){return d.rel==='pred'?({strong:2.8,moderate:1.9,weak:1.2}[d.strength]||1.4):d.rel==='calc'?1.6:(d.rel==='competes_with'||d.rel==='co_operating')?1.8:1.1;})
 .attr('stroke-linecap','round')
 .attr('stroke-dasharray',function(d){return d.rel==='competes_with'?'4,3':null;});
const node=cont.append('g').selectAll('circle').data(nodes).join('circle')
 .attr('r',nR).attr('fill',nC).attr('opacity',nO)
 .attr('filter',function(d){return d.kind==='hyp'?'url(#g)':null;})
 .attr('stroke',function(d){return d.kind==='hyp'?'#0006':'none';}).attr('stroke-width',0.5);
function showTip(e,d){if(!d.tip)return;tt.innerHTML=d.tip;tt.style.opacity=1;moveTip(e);}
function moveTip(e){var x=e.clientX+14,y=e.clientY+14;
 if(x+310>window.innerWidth)x=e.clientX-310;if(y+120>window.innerHeight)y=e.clientY-110;
 tt.style.left=x+'px';tt.style.top=y+'px';}
function hideTip(){tt.style.opacity=0;}
node.on('mouseover',showTip).on('mousemove',moveTip).on('mouseout',hideTip)
 .on('mouseenter',function(d){d3.select(this).attr('stroke',P.tiptext).attr('stroke-width',1.5);})
 .on('mouseleave',function(d){d3.select(this).attr('stroke',function(d){return d.kind==='hyp'?'#0006':'none';}).attr('stroke-width',0.5);});
const labLayer=svg.append('g');
const lab=labLayer.selectAll('text').data(nodes.filter(function(d){return d.kind==='hyp';})).join('text')
 .attr('fill',P.label).attr('font-size',11.5).attr('font-weight',700).attr('text-anchor','middle')
 .style('paint-order','stroke').style('stroke',P.labshadow).style('stroke-width','3px').style('stroke-linejoin','round')
 .text(function(d){return d.label+'  '+Math.round((d.conf||0)*100)+'%';});
// ---- LINKED FOCUS: spotlight one hypothesis's whole network, fade the rest ----
// Per hypothesis, the set of related node ids = the hyp + its predictions + the
// evidence/compute attached to those predictions. (Built from links: at this point
// source/target may be id-strings; after forceLink they're node objects — (x.id||x)
// handles both.) Driven by hovering a hyp node here AND by a BroadcastChannel the
// ranking posts to (the two iframes share origin), so hovering a ranking row lights
// up its constellation subgraph.
const _eid=function(x){return (x&&x.id!=null)?x.id:x;};
const relById={};
nodes.forEach(function(n){if(n.kind==='hyp')relById[n.id]=new Set([n.id]);});
links.forEach(function(l){if(l.rel==='pred'&&relById[_eid(l.target)])relById[_eid(l.target)].add(_eid(l.source));});
links.forEach(function(l){if(l.rel==='evid'||l.rel==='calc'){var s=_eid(l.source),t=_eid(l.target);
 for(var hid in relById){if(relById[hid].has(t))relById[hid].add(s);}}});
function _linkBase(d){return d.rel==='evid'?0.42:d.rel==='calc'?0.6:d.rel==='pred'?0.72:0.78;}
// focusSet = node ids kept lit (the UNION of one-or-more hypotheses' subgraphs);
// heroIds = the hypothesis ids that are the CAUSE (glow + one-shot pulse — the flare).
let focusSet=null, heroIds=[];
function _unionFor(ids){if(!ids||!ids.length)return null;var S=new Set();
 ids.forEach(function(h){var r=relById[h];if(r)r.forEach(function(x){S.add(x);});});return S.size?S:null;}
function applyFocus(){
 var S=focusSet, heroSet=new Set(heroIds);
 // Asymmetric: emphasis-IN is snappy (200ms), the RESTORE to "show everything" is a
 // gentle, longer fade-up (560ms ease-out) so dropping the focus melts back instead of
 // popping — no dizzy snap when the cursor leaves. ("fast in, slow out".)
 var dur=S?200:560, ez=S?d3.easeCubicInOut:d3.easeCubicOut;
 // One transition per selection (a second node.transition() would cancel the first) —
 // opacity AND the hero outline ride the same curve, so restore fades back together.
 node.transition().duration(dur).ease(ez)
     .attr('opacity',function(d){return S?(S.has(d.id)?nO(d):0.05):nO(d);})
     .attr('stroke',function(d){return heroSet.has(d.id)?P.tiptext:(d.kind==='hyp'?'#0006':'none');})
     .attr('stroke-width',function(d){return heroSet.has(d.id)?2.2:0.5;});
 lab.transition().duration(dur).ease(ez).attr('opacity',function(d){return S?(S.has(d.id)?1:0.08):1;});
 link.transition().duration(dur).ease(ez).attr('stroke-opacity',function(d){
  if(!S)return _linkBase(d);
  return (S.has(_eid(d.source))&&S.has(_eid(d.target)))?Math.min(1,_linkBase(d)+0.22):0.03;});
}
function pulseHeroes(){var heroSet=new Set(heroIds);
 node.filter(function(d){return heroSet.has(d.id);}).each(function(d){var n=d3.select(this),r0=nR(d);
  n.interrupt('pulse').transition('pulse').duration(170).attr('r',r0*1.55)
   .transition('pulse').duration(430).ease(d3.easeQuadOut).attr('r',r0);});}
function setFocusIds(ids,pulse){focusSet=_unionFor(ids);
 heroIds=(ids||[]).filter(function(h){return relById[h];});applyFocus();if(pulse)pulseHeroes();}
var _bc=null;
try{_bc=new BroadcastChannel('isaac-focus');
 _bc.onmessage=function(ev){var m=ev&&ev.data;if(!m||m.origin==='constellation')return;
  var f=m.focus,ids=(f==null)?null:(Array.isArray(f)?f:[f]);setFocusIds(ids,!!m.pulse);};}catch(e){}
// NB d3 v7: listener args are (event, datum) — earlier code used function(d) and so
// silently never fired (d was the event). Use the datum (the second arg) explicitly.
node.on('mouseenter.focus',function(event,d){if(d.kind==='hyp'){setFocusIds([d.id],false);
  if(_bc)_bc.postMessage({focus:[d.id],origin:'constellation'});}})
    .on('mouseleave.focus',function(event,d){if(d.kind==='hyp'){setFocusIds(null,false);
  if(_bc)_bc.postMessage({focus:null,origin:'constellation'});}});
let rot=0, ds=null;
function rp(x,y){var a=rot*Math.PI/180,ca=Math.cos(a),sa=Math.sin(a),dx=x-cx,dy=y-cy;return [cx+dx*ca-dy*sa, cy+dx*sa+dy*ca];}
function placeLabels(){lab.attr('x',function(d){return rp(d.x,d.y)[0];}).attr('y',function(d){return rp(d.x,d.y)[1]-nR(d)-5;});}
const sim=d3.forceSimulation(nodes)
 .force('link',d3.forceLink(links).id(function(d){return d.id;}).distance(function(d){return d.rel==='pred'?66:d.rel==='evid'?40:120;}).strength(function(d){return d.rel==='pred'?0.45:0.18;}))
 .force('charge',d3.forceManyBody().strength(function(d){return d.kind==='screened'?-12:d.kind==='hyp'?-220:-72;}))
 .force('r',d3.forceRadial(rT,cx,cy).strength(function(d){return d.kind==='hyp'?0.18:0.92;}))
 .force('x',d3.forceX(hX).strength(function(d){return d.kind==='hyp'?0.22:0.045;}))
 .force('y',d3.forceY(hY).strength(function(d){return d.kind==='hyp'?0.22:0.045;}))
 .force('collide',d3.forceCollide().radius(function(d){return nR(d)+(d.kind==='screened'?1.4:2.6);}))
 .on('tick',function(){
  link.attr('x1',function(d){return d.source.x;}).attr('y1',function(d){return d.source.y;}).attr('x2',function(d){return d.target.x;}).attr('y2',function(d){return d.target.y;});
  node.attr('cx',function(d){return d.x;}).attr('cy',function(d){return d.y;});
  placeLabels();});
svg.call(d3.drag()
 .on('start',function(e){ds={a:Math.atan2(e.y-cy,e.x-cx),r:rot};})
 .on('drag',function(e){if(!ds)return;rot=ds.r+(Math.atan2(e.y-cy,e.x-cx)-ds.a)*180/Math.PI;cont.attr('transform','rotate('+rot+','+cx+','+cy+')');placeLabels();}));
</script></body></html>
"""
            return tmpl.replace("__DATA__", data).replace("__PAL__", pal)

        def _constellation_replay_html(payload, theme="dark"):
            # The SAME constellation as the Decision-journey page (identical rings, node
            # kinds, colours, radii, fan layout) — but played over the run's timeline:
            # nodes/links fade in at the step they appeared, hypotheses migrate inward as
            # they get ranked, decision points surface as captions, and the FINAL frame is
            # byte-for-byte the live main-page constellation.
            dark = theme != "light"
            pal = json.dumps({
                "bg1": "#0c1226" if dark else "#eef3fa",
                "bg2": "#04050a" if dark else "#dbe6f3",
                "ring": "#24324f" if dark else "#c6d3e4",
                "ringlab": "#46587e" if dark else "#90a4c0",
                "badge": "#8bbad2" if dark else "#3d6885",
                "label": "#eef3ff" if dark else "#10243a",
                "labshadow": "rgba(0,0,0,0.6)" if dark else "rgba(255,255,255,0.85)",
                "screened": "#33446a" if dark else "#a9bad4",
                "evid": "#d6dee6" if dark else "#5f7081",
                "relrest": "#7e8aa0" if dark else "#9aa7bd",
                "annbg": "rgba(16,22,40,0.94)" if dark else "rgba(255,255,255,0.97)",
                "annborder": "#2a3a5e" if dark else "#c2d0e4",
                "capink": "#dfe7f7" if dark else "#1a2a44",
                "ctlbg": "#0a0e16" if dark else "#e6ecf5",
                "ctlborder": "#1d2738" if dark else "#c6d3e4",
                "playbg": "#111a2b" if dark else "#dbe6f3",
                "playink": "#cfe" if dark else "#22344e",
                "playborder": "#2a3a5e" if dark else "#aebfd6",
                "dim": "#7f8aa3" if dark else "#5c6b86",
            })
            data = json.dumps(payload)
            tmpl = r"""
<html><head><script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>html,body{margin:0;overflow:hidden;font-family:-apple-system,Segoe UI,Roboto,sans-serif;}
text{font-family:-apple-system,Segoe UI,Roboto,sans-serif;}
#wrap{position:relative;width:100%;height:520px;}
#ann{position:absolute;left:50%;top:38px;transform:translateX(-50%);max-width:70%;
 padding:7px 13px;border-radius:9px;border:1px solid;font-size:12.5px;font-weight:600;
 opacity:0;transition:opacity .25s;pointer-events:none;text-align:center;
 box-shadow:0 5px 20px rgba(0,0,0,0.4);}
#cap{position:absolute;left:16px;right:16px;bottom:8px;font-size:12px;line-height:1.4;
 pointer-events:none;}
#ctl{height:40px;display:flex;align-items:center;gap:12px;padding:0 14px;border-top:1px solid;}
#play{cursor:pointer;border:1px solid;width:34px;height:26px;border-radius:6px;font-size:13px;}
#scrub{flex:1;accent-color:#5EC8C0;cursor:pointer;}
#tl{font-size:11px;min-width:66px;text-align:right;}</style></head>
<body><div id="wrap"><svg id="c" width="100%" height="520"></svg>
<div id="ann"></div><div id="cap"></div></div>
<div id="ctl"><button id="play">▶</button><input id="scrub" type="range" min="0" max="1000" value="0">
<span id="tl">0 / 0</span></div>
<script>
const DATA=__DATA__, P=__PAL__;
document.body.style.background='radial-gradient(circle at 50% 45%,'+P.bg1+','+P.bg2+')';
const ann=document.getElementById('ann'),cap=document.getElementById('cap');
const playB=document.getElementById('play'),scrub=document.getElementById('scrub'),tlab=document.getElementById('tl');
ann.style.background=P.annbg;ann.style.borderColor=P.annborder;
document.getElementById('ctl').style.background=P.ctlbg;document.getElementById('ctl').style.borderTopColor=P.ctlborder;
playB.style.background=P.playbg;playB.style.color=P.playink;playB.style.borderColor=P.playborder;
tlab.style.color=P.dim;
const el=document.getElementById('c'); const W=el.clientWidth||820, H=520; const cx=W/2, cy=H/2-2;
const svg=d3.select('#c').attr('width',W).attr('height',H);
const defs=svg.append('defs');
const glow=defs.append('filter').attr('id','g').attr('x','-80%').attr('y','-80%').attr('width','260%').attr('height','260%');
glow.append('feGaussianBlur').attr('stdDeviation','3.6').attr('result','b');
const fm=glow.append('feMerge');fm.append('feMergeNode').attr('in','b');fm.append('feMergeNode').attr('in','SourceGraphic');
const SC={supported:'#ffca28',eliminated:'#6f6f6f',needs_more_data:'#ffa726',proposed:'#4aa3ff',superseded:'#5a5a5a'};
const VC={supports:'#26c6da',contradicts:'#ec407a',neutral:'#90a4ae',insufficient:'#5c6b7a',blocked:'#7e6a4e'};
const CALC='#ab47bc'; const INVOLVED='#5b7da6';
const N=DATA.N||1; let K=0, playing=false, fr=0;
function rT(d){return d.kind==='hyp'?56+(1-(d.conf||0))*150:d.kind==='pred'?250:(d.kind==='evid'||d.kind==='calc')?330:d.kind==='involved'?366:392;}
function nR(d){return d.kind==='hyp'?8+(d.conf||0)*22:d.kind==='pred'?({strong:7,moderate:5,weak:3}[d.strength]||4):d.kind==='calc'?4.5:d.kind==='evid'?3:d.kind==='involved'?1.9:1.3+Math.min(3.4,Math.log((d.n||1)+1));}
function nC(d){return d.kind==='hyp'?(d.color||SC[d.status]||'#90caf9'):d.kind==='pred'?(VC[d.verdict]||'#455a64'):d.kind==='calc'?CALC:d.kind==='involved'?INVOLVED:d.kind==='evid'?P.evid:P.screened;}
function nO(d){return d.kind==='screened'?0.4:d.kind==='involved'?0.62:d.kind==='evid'?0.85:d.kind==='calc'?0.95:(d.status==='eliminated'||d.status==='superseded')?0.45:1;}
function fade(d){return Math.max(0,Math.min(1,(K-(d.t0||0)+1)/2));}
[[56,'leading'],[250,'predictions'],[330,'evidence'],[366,'in-scope'],[392,'screened']].forEach(function(p){
 svg.append('circle').attr('cx',cx).attr('cy',cy).attr('r',p[0]).attr('fill','none').attr('stroke',P.ring).attr('stroke-dasharray','2,7').attr('opacity',0.7);
 svg.append('text').attr('x',cx).attr('y',cy-p[0]-3).attr('fill',P.ringlab).attr('font-size',9).attr('text-anchor','middle').attr('opacity',0.85).text(p[1]);});
svg.append('text').attr('x',16).attr('y',26).attr('fill',P.badge).attr('font-size',12).attr('font-weight',600)
 .text(DATA.corpus.records.toLocaleString()+'  records   →   '+DATA.corpus.screened+'  screened   →   '+(DATA.corpus.involved||0)+'  in-scope   →   '+DATA.corpus.cited+'  cited');
const nodes=DATA.nodes.map(function(d){return Object.assign({},d);});
const links=DATA.links.map(function(d){return Object.assign({},d);});
const _hn=nodes.filter(function(n){return n.kind==='hyp';});
_hn.forEach(function(n,i){n.theta=(-Math.PI/2)+(2*Math.PI*i/Math.max(1,_hn.length));});
function hX(d){return d.kind==='hyp'&&d.theta!=null?cx+rT(d)*Math.cos(d.theta):cx;}
function hY(d){return d.kind==='hyp'&&d.theta!=null?cy+rT(d)*Math.sin(d.theta):cy;}
const cont=svg.append('g');
const link=cont.append('g').selectAll('line').data(links).join('line')
 .attr('stroke',function(d){return d.rel==='pred'?(VC[d.verdict]||'#37474f'):d.rel==='calc'?'#ab47bc':d.rel==='evid'?P.screened:d.rel==='competes_with'?'#ef5350':d.rel==='co_operating'?'#66bb6a':P.relrest;})
 .attr('stroke-width',function(d){return d.rel==='pred'?({strong:2.8,moderate:1.9,weak:1.2}[d.strength]||1.4):d.rel==='calc'?1.6:(d.rel==='competes_with'||d.rel==='co_operating')?1.8:1.1;})
 .attr('stroke-linecap','round')
 .attr('stroke-dasharray',function(d){return d.rel==='competes_with'?'4,3':null;});
const node=cont.append('g').selectAll('circle').data(nodes).join('circle')
 .attr('r',nR).attr('fill',nC)
 .attr('filter',function(d){return d.kind==='hyp'?'url(#g)':null;})
 .attr('stroke',function(d){return d.kind==='hyp'?'#0006':'none';}).attr('stroke-width',0.5);
const labLayer=svg.append('g');
const lab=labLayer.selectAll('text').data(nodes.filter(function(d){return d.kind==='hyp';})).join('text')
 .attr('fill',P.label).attr('font-size',11.5).attr('font-weight',700).attr('text-anchor','middle')
 .style('paint-order','stroke').style('stroke',P.labshadow).style('stroke-width','3px').style('stroke-linejoin','round');
function linkBaseOp(d){return d.rel==='evid'?0.42:d.rel==='calc'?0.6:d.rel==='pred'?0.72:0.78;}
function linkVis(d){var s=d.source,t=d.target;return Math.min(fade(d),(s&&s.t0!=null?fade(s):1),(t&&t.t0!=null?fade(t):1));}
function placeLabels(){lab.attr('x',function(d){return d.x;}).attr('y',function(d){return d.y-nR(d)-5;});}
const sim=d3.forceSimulation(nodes)
 .force('link',d3.forceLink(links).id(function(d){return d.id;}).distance(function(d){return d.rel==='pred'?66:d.rel==='evid'?40:120;}).strength(function(d){return d.rel==='pred'?0.45:0.18;}))
 .force('charge',d3.forceManyBody().strength(function(d){return d.kind==='screened'?-12:d.kind==='hyp'?-220:-72;}))
 .force('r',d3.forceRadial(rT,cx,cy).strength(function(d){return d.kind==='hyp'?0.18:0.92;}))
 .force('x',d3.forceX(hX).strength(function(d){return d.kind==='hyp'?0.22:0.045;}))
 .force('y',d3.forceY(hY).strength(function(d){return d.kind==='hyp'?0.22:0.045;}))
 .force('collide',d3.forceCollide().radius(function(d){return nR(d)+(d.kind==='screened'?1.4:2.6);}))
 .on('tick',function(){
  link.attr('x1',function(d){return d.source.x;}).attr('y1',function(d){return d.source.y;}).attr('x2',function(d){return d.target.x;}).attr('y2',function(d){return d.target.y;});
  node.attr('cx',function(d){return d.x;}).attr('cy',function(d){return d.y;});
  placeLabels();});
function updateAnn(){var best=null;for(var i=0;i<(DATA.annotations||[]).length;i++){var a=DATA.annotations[i];if(a.k<=K&&(best==null||a.k>=best.k))best=a;}
 if(best&&(K-best.k)<=4){ann.style.opacity=Math.max(0.15,1-(K-best.k)/6);ann.style.color=best.color||P.label;ann.innerHTML=best.text;}else ann.style.opacity=0;}
function updateCap(){var c=(DATA.captions||[])[Math.min(K,N-1)];if(!c){cap.innerHTML='';return;}
 cap.innerHTML='<span style="color:'+(c.color||P.dim)+'">['+(c.cls||'step')+']</span> <span style="color:'+P.capink+'">'+(c.s||'').replace(/</g,'&lt;')+'</span><span style="color:'+P.dim+'"> &nbsp;'+(K+1)+'/'+N+'</span>';}
function applyStep(){
 nodes.forEach(function(d){if(d.kind==='hyp'&&d.cs)d.conf=(d.cs[Math.min(K,d.cs.length-1)]!=null)?d.cs[Math.min(K,d.cs.length-1)]:d.conf;});
 node.attr('r',nR).attr('fill',nC).attr('opacity',function(d){return (d.t0||0)>K?0:fade(d)*nO(d);});
 link.attr('opacity',function(d){return linkVis(d)*linkBaseOp(d);});
 lab.text(function(d){return d.label+'  '+Math.round((d.conf||0)*100)+'%';})
    .attr('opacity',function(d){return (d.t0||0)>K?0:fade(d);});
 sim.force('r').radius(rT);sim.force('x').x(hX);sim.force('y').y(hY);
 sim.alpha(0.32).restart();
 updateAnn();updateCap();
 scrub.value=N>1?Math.round(K/(N-1)*1000):0;tlab.textContent=(K+1)+' / '+N;}
function setK(k){K=Math.max(0,Math.min(N-1,k|0));applyStep();}
playB.onclick=function(){if(K>=N-1){setK(0);}playing=!playing;playB.textContent=playing?'⏸':'▶';};
scrub.oninput=function(){playing=false;playB.textContent='▶';setK(Math.round(this.value/1000*(N-1)));};
function loop(){fr++;if(playing&&fr%16===0){if(K<N-1)setK(K+1);else{playing=false;playB.textContent='▶';}}requestAnimationFrame(loop);}
window.__seek=function(frac){playing=false;setK(Math.round(frac*(N-1)));for(var i=0;i<240;i++)sim.tick();sim.on('tick')();};
setK(0);loop();
</script></body></html>
"""
            return tmpl.replace("__DATA__", data).replace("__PAL__", pal)

        def _river_html(payload, theme="dark"):
            dark = theme != "light"
            pal = json.dumps({
                "bg1": "#0c1226" if dark else "#eef3fa",
                "bg2": "#070b16" if dark else "#dbe6f3",
                "axis": "#5e7290" if dark else "#5a6e8a",
                "grid": "#34456a" if dark else "#b7c5db",
                "label": "#eef3ff" if dark else "#10243a",
                "labshadow": "rgba(0,0,0,0.55)" if dark else "rgba(255,255,255,0.85)",
                "tipbg": "rgba(10,16,32,0.97)" if dark else "rgba(255,255,255,0.98)",
                "tipbd": "#2a3a5e" if dark else "#bcccdf",
                "tipink": "#e7eefc" if dark else "#13243a",
                "tipdim": "#8aa0c4" if dark else "#5c6b86",
                "up": "#5fd08a" if dark else "#1a9a55",
                "down": "#ff7a90" if dark else "#c2384e",
            })
            data = json.dumps(payload)
            tmpl = r"""
<html><head><script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>html,body{margin:0;overflow:hidden;}text{font-family:-apple-system,Segoe UI,Roboto,sans-serif;}
#tip{position:fixed;pointer-events:none;z-index:9;max-width:320px;padding:9px 11px;
 border-radius:9px;border:1px solid;font-size:11.5px;line-height:1.45;opacity:0;
 transition:opacity .08s;box-shadow:0 6px 22px rgba(0,0,0,0.45);
 font-family:-apple-system,Segoe UI,Roboto,sans-serif;}
#tip .th{font-weight:700;font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px;}
#tip .ts{margin-bottom:6px;opacity:0.92;}
#tip .tr{display:flex;align-items:center;gap:6px;margin-top:3px;white-space:nowrap;}
#tip .dot{width:8px;height:8px;border-radius:50%;flex:none;}
#tip .lb{font-weight:700;min-width:72px;}
#tip .vv{opacity:0.82;}
#tip .dd{font-weight:700;margin-left:auto;padding-left:10px;}
#tip .tn{opacity:0.6;font-style:italic;}</style></head>
<body><svg id="r" width="100%" height="300"></svg><div id="tip"></div><script>
const D=__DATA__, P=__PAL__;
document.body.style.background='linear-gradient(180deg,'+P.bg1+','+P.bg2+')';
const el=document.getElementById('r'); const W=el.clientWidth||820,H=300,m={t:18,r:138,b:24,l:16};
const svg=d3.select('#r').attr('width',W).attr('height',H);
const keys=D.hyps.map(function(h){return h.label;});
const colorOf={}, deadOf={}, leadOf={};
D.hyps.forEach(function(h){colorOf[h.label]=h.color||'#C97A3C';
 deadOf[h.label]=(h.status==='eliminated'||h.status==='superseded');
 leadOf[h.label]=!!h.lead;});
if(!D.steps.length||!keys.length){svg.append('text').attr('x',16).attr('y',30).attr('fill',P.axis).attr('font-size',12).text('No confidence history yet.');}
else{
const stack=d3.stack().keys(keys).offset(d3.stackOffsetWiggle).order(d3.stackOrderInsideOut);
const series=stack(D.steps);
const x=d3.scaleLinear().domain([0,1]).range([m.l,W-m.r]);
const y=d3.scaleLinear().domain([d3.min(series,function(s){return d3.min(s,function(d){return d[0];});}),
                                 d3.max(series,function(s){return d3.max(s,function(d){return d[1];});})]).range([H-m.b,m.t]);
const area=d3.area().x(function(d){return x(d.data.t);}).y0(function(d){return y(d[0]);}).y1(function(d){return y(d[1]);}).curve(d3.curveBasis);
const defs=svg.append('defs');
const gl=defs.append('filter').attr('id','rg').attr('x','-40%').attr('y','-40%').attr('width','180%').attr('height','180%');
gl.append('feGaussianBlur').attr('stdDeviation','2.4');
const mlines=svg.append('g').selectAll('line').data(D.markers).join('line')
 .attr('x1',function(d){return x(d.t);}).attr('x2',function(d){return x(d.t);}).attr('y1',m.t-4).attr('y2',H-m.b)
 .attr('stroke',P.grid).attr('stroke-dasharray','2,4').attr('opacity',0.4);
const ribbons=svg.append('g').selectAll('path').data(series).join('path')
 .attr('d',area).attr('fill',function(s){return colorOf[s.key];})
 .attr('opacity',function(s){return deadOf[s.key]?0.5:0.95;})
 .attr('stroke','#00000022').attr('stroke-width',0.4)
 .attr('filter',function(s){return leadOf[s.key]?'url(#rg)':null;});
svg.append('g').selectAll('text').data(series).join('text')
 .attr('x',W-m.r+8).attr('y',function(s){var d=s[s.length-1];return y((d[0]+d[1])/2)+3;})
 .attr('fill',function(s){return deadOf[s.key]?P.axis:colorOf[s.key];}).attr('font-size',10.5).attr('font-weight',700)
 .style('paint-order','stroke').style('stroke',P.labshadow).style('stroke-width','2.5px').style('stroke-linejoin','round')
 .text(function(s){var h=D.hyps.find(function(z){return z.label===s.key;});return s.key+'  '+Math.round((h?h.conf:0)*100)+'%';});
svg.append('text').attr('x',m.l).attr('y',H-7).attr('fill',P.axis).attr('font-size',10).text('run start');
svg.append('text').attr('x',W-m.r).attr('y',H-7).attr('text-anchor','end').attr('fill',P.axis).attr('font-size',10).text('now →');
// ---- hover popups on the dashed event marks: what landed + which beliefs moved ----
const tip=document.getElementById('tip');
tip.style.background=P.tipbg;tip.style.borderColor=P.tipbd;tip.style.color=P.tipink;
function esc(s){return (s||'').replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function tipHTML(d){
 var t=(d.type||'event').replace(/_/g,' ');
 var s='<div class="th" style="color:'+P.tipdim+'">'+esc(t)+'</div>';
 if(d.summary)s+='<div class="ts">'+esc(d.summary)+'</div>';
 if(d.deltas&&d.deltas.length){
  d.deltas.forEach(function(z){var up=z.delta>0;
   s+='<div class="tr"><span class="dot" style="background:'+(colorOf[z.label]||P.tipink)+'"></span>'
    +'<span class="lb">'+esc(z.label)+'</span>'
    +'<span class="vv">'+Math.round(z.from*100)+'% → '+Math.round(z.to*100)+'%</span>'
    +'<span class="dd" style="color:'+(up?P.up:P.down)+'">'+(up?'▲+':'▼')+Math.round(z.delta*100)+'</span></div>';});
 }else{s+='<div class="tn">no belief shift recorded here</div>';}
 return s;
}
function moveTip(ev){var tw=tip.offsetWidth||300,th=tip.offsetHeight||80;
 var lx=ev.clientX+14;if(lx+tw>W-6)lx=ev.clientX-tw-14;if(lx<6)lx=6;
 var ty=ev.clientY-th-10;if(ty<6)ty=ev.clientY+16;if(ty+th>H-2)ty=Math.max(6,H-th-4);
 tip.style.left=lx+'px';tip.style.top=ty+'px';}
// ---- LINKED BRUSHING: river moment <-> constellation subgraph ----------------
// Hovering a moment broadcasts the hypotheses it MOVED so the constellation lights
// up exactly those subgraphs (with a one-shot pulse); receiving a focus from the
// constellation/ranking highlights this hypothesis's ribbon + its moments here.
var RBC=null; try{RBC=new BroadcastChannel('isaac-focus');}catch(e){}
const hidToLabel={}; (D.hyps||[]).forEach(function(h){if(h.id)hidToLabel[h.id]=h.label;});
function setRiverFocus(ids){
 var labset=null;
 if(ids&&ids.length){labset=new Set(ids.map(function(i){return hidToLabel[i];}).filter(Boolean));}
 // Same "fast in, slow out" as the constellation: emphasis 200ms, restore a gentle
 // 560ms ease-out so the ribbons/marks melt back instead of snapping.
 var on=!!(ids&&ids.length), dur=on?200:560, ez=on?d3.easeCubicInOut:d3.easeCubicOut;
 ribbons.transition().duration(dur).ease(ez).attr('opacity',function(s){
  if(!labset)return deadOf[s.key]?0.5:0.95;
  return labset.has(s.key)?0.98:0.12;});
 mlines.transition().duration(dur).ease(ez).attr('opacity',function(d){
  if(!ids||!ids.length)return 0.4;
  var hit=(d.hyp_ids||[]).some(function(h){return ids.indexOf(h)>=0;});
  return hit?0.95:0.06;}).attr('stroke',function(d){
  if(!ids||!ids.length)return P.grid;
  var hit=(d.hyp_ids||[]).some(function(h){return ids.indexOf(h)>=0;});
  return hit?P.label:P.grid;});
}
if(RBC){RBC.onmessage=function(ev){var msg=ev&&ev.data;if(!msg||msg.origin==='river')return;
 var f=msg.focus,ids=(f==null)?null:(Array.isArray(f)?f:[f]);setRiverFocus(ids);};}
function showTip(ev,d){tip.innerHTML=tipHTML(d);tip.style.opacity=1;
 var c=(d.deltas&&d.deltas.length)?(colorOf[d.deltas[0].label]||P.label):P.label;
 hl.attr('x1',x(d.t)).attr('x2',x(d.t)).attr('stroke',c).attr('opacity',0.75);moveTip(ev,d);
 if(RBC)RBC.postMessage({focus:(d.hyp_ids&&d.hyp_ids.length)?d.hyp_ids:null,origin:'river',pulse:true});}
function hideTip(){tip.style.opacity=0;hl.attr('opacity',0);
 if(RBC)RBC.postMessage({focus:null,origin:'river'});}
const mk=svg.append('g');
const hl=mk.append('line').attr('y1',m.t-4).attr('y2',H-m.b).attr('stroke',P.label)
 .attr('stroke-width',1.4).attr('opacity',0).attr('pointer-events','none');
mk.selectAll('line.hit').data(D.markers).join('line').attr('class','hit')
 .attr('x1',function(d){return x(d.t);}).attr('x2',function(d){return x(d.t);}).attr('y1',m.t-4).attr('y2',H-m.b)
 .attr('stroke','transparent').attr('stroke-width',12).style('pointer-events','stroke').style('cursor','pointer')
 .on('mouseenter',showTip).on('mousemove',moveTip).on('mouseleave',hideTip);
}
</script></body></html>
"""
            return tmpl.replace("__DATA__", data).replace("__PAL__", pal)

        # ---- Replay Studio: a data-driven "video" of the whole discovery, four
        # cinematic modes, all driven by the project's own event timeline + the
        # confidence snapshots. Self-contained canvas animation with play/scrub. ----
        def _replay_html(payload, theme="dark", mode="matrix"):
            dark = theme != "light"
            _bg = "#05070d" if dark else "#eef3fa"
            _capink = "#dfe7f7" if dark else "#1a2a44"
            _capshadow = "0 1px 6px #000" if dark else "0 1px 4px rgba(255,255,255,0.9)"
            _ctlbg = "#0a0e16" if dark else "#e6ecf5"
            _ctlborder = "#1d2738" if dark else "#c6d3e4"
            _playbg = "#111a2b" if dark else "#dbe6f3"
            _playink = "#cfe" if dark else "#22344e"
            _playborder = "#2a3a5e" if dark else "#aebfd6"
            pal = json.dumps({
                "bg": _bg,
                "ink": "#e7eefc" if dark else "#13243a",
                "dim": "#7f8aa3" if dark else "#5c6b86",
                "org1": "#0b1224" if dark else "#eaf0fa",   # organism radial gradient
                "org2": "#05070d" if dark else "#dce6f4",
                "accent": "#5EC8C0", "rain": "#39d98a" if mode == "matrix" else "#5EC8C0",
                "hi": "#ffd479", "panel": "rgba(255,255,255,0.05)",
                "cls": {"hypothesis": "#E8941F", "prediction": "#7AD0FF",
                        "verdict": "#26c6da", "evidence": "#9aa7bd",
                        "compute": "#b48cff", "literature": "#ffd479",
                        "experiment": "#66e0a3", "rigor": "#ff7a90",
                        "directive": "#f72585",
                        "update": "#8aa0c4", "other": "#8aa0c4"},
            })
            data = json.dumps(payload)
            tmpl = r"""
<html><head><style>
html,body{margin:0;background:__BGC__;overflow:hidden;
 font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace;}
#wrap{position:relative;width:100%;height:524px;background:__BGC__;}
#cv{display:block;width:100%;height:524px;}
#cap{position:absolute;left:18px;right:18px;bottom:14px;color:__CAPINK__;
 font-size:13px;line-height:1.4;text-shadow:__CAPSHADOW__;pointer-events:none;}
#ctl{height:42px;display:flex;align-items:center;gap:12px;padding:2px 14px;
 background:__CTLBG__;border-top:1px solid __CTLBORDER__;}
#play{cursor:pointer;border:1px solid __PLAYBORDER__;background:__PLAYBG__;color:__PLAYINK__;
 width:34px;height:26px;border-radius:6px;font-size:13px;}
#scrub{flex:1;accent-color:#5EC8C0;}
#tl{color:#7f8aa3;font-size:11px;min-width:74px;text-align:right;}
select{background:__PLAYBG__;color:__PLAYINK__;border:1px solid __PLAYBORDER__;border-radius:6px;
 font-size:11px;padding:2px;}
</style></head><body>
<div id="wrap"><canvas id="cv"></canvas><div id="cap"></div></div>
<div id="ctl">
 <button id="play">▶</button>
 <input id="scrub" type="range" min="0" max="1000" value="0">
 <span id="tl">0 / 0</span>
 <select id="spd"><option value="1">1×</option><option value="2">2×</option>
  <option value="0.5">0.5×</option><option value="4">4×</option></select>
</div>
<script>
const D=__DATA__, P=__PAL__, MODE="__MODE__";
const cv=document.getElementById('cv'), ctx=cv.getContext('2d');
const cap=document.getElementById('cap'), playB=document.getElementById('play');
const scrub=document.getElementById('scrub'), tl=document.getElementById('tl'), spd=document.getElementById('spd');
let W=0,H=0,DPR=Math.min(2,window.devicePixelRatio||1);
function size(){W=cv.clientWidth;H=cv.clientHeight;cv.width=W*DPR;cv.height=H*DPR;ctx.setTransform(DPR,0,0,DPR,0,0);}
new ResizeObserver(size).observe(cv); size();
const N=Math.max(1,D.events.length);
let p=0, playing=false, speed=1, tick=0;
function clsColor(c){return P.cls[c]||P.cls.other;}
function evIndex(pp){return Math.min(N-1,Math.floor(pp*N));}
// matrix rain state
let cols=[], glyphs=(D.pool&&D.pool.length?D.pool:['ISAAC']);
function initRain(){const step=14;cols=[];for(let x=0;x<W;x+=step){cols.push({x:x,y:Math.random()*-H,ch:rch(),v:1+Math.random()*2});}}
function rch(){const g=glyphs[(Math.random()*glyphs.length)|0];return g.charAt((Math.random()*g.length)|0)||'0';}
function confAt(k){return (D.confSeries&&D.confSeries[k])?D.confSeries[k]:(D.confSeries?D.confSeries[D.confSeries.length-1]:[]);}

// ---------- MATRIX ----------
function drawMatrix(pp,k){
 ctx.fillStyle='rgba(5,7,13,0.20)';ctx.fillRect(0,0,W,H);
 if(!cols.length)initRain();
 ctx.font='13px IBM Plex Mono,monospace';
 for(const c of cols){
   ctx.fillStyle=P.rain;ctx.globalAlpha=0.85;ctx.fillText(c.ch,c.x,c.y);
   ctx.globalAlpha=0.25;ctx.fillStyle='#bfffe0';ctx.fillText(c.ch,c.x,c.y-14);
   c.y+=c.v*(playing?speed:0.4);if(Math.random()<0.04)c.ch=rch();
   if(c.y>H){c.y=Math.random()*-60;c.v=1+Math.random()*2;}
 }
 ctx.globalAlpha=1;
 // reasoning log panel
 const lx=18, lw=Math.min(560,W*0.62);
 ctx.fillStyle='rgba(4,8,16,0.62)';ctx.fillRect(lx-8,14,lw,Math.min(340,H-120));
 const start=Math.max(0,k-14);let yy=34;
 for(let i=start;i<=k;i++){const e=D.events[i];const a=(i===k)?1:0.35+0.5*((i-start)/Math.max(1,k-start));
   ctx.globalAlpha=a;ctx.fillStyle=clsColor(e.cls);ctx.font='12px IBM Plex Mono,monospace';
   ctx.fillText('▍'+e.cls.toUpperCase().slice(0,4),lx,yy);
   ctx.fillStyle=(i===k)?'#fff':'#cdd8ef';ctx.fillText(' '+e.s.slice(0,72),lx+54,yy);yy+=20;}
 ctx.globalAlpha=1;
 // confidence bars right
 const bx=W-188, cs=confAt(k);
 ctx.fillStyle='#8aa0c4';ctx.font='10px IBM Plex Mono,monospace';ctx.fillText('BELIEF',bx,28);
 D.hyps.forEach(function(h,j){const v=(cs[j]||0);const by=44+j*22;
   ctx.fillStyle='rgba(255,255,255,0.08)';ctx.fillRect(bx,by,170,12);
   ctx.fillStyle=h.color;ctx.fillRect(bx,by,170*v,12);
   ctx.fillStyle='#cdd8ef';ctx.font='10px IBM Plex Mono,monospace';
   ctx.fillText(h.label.slice(0,14)+' '+Math.round(v*100)+'%',bx,by-2);});
}

// ---------- CONSTELLATION (grows over time) ----------
function nodePos(){const cx=W/2,cy=H/2-6,R=Math.min(W,H)*0.32;const ps=[];
 D.hyps.forEach(function(h,j){const a=-Math.PI/2+j*2*Math.PI/Math.max(1,D.hyps.length);
   ps.push({x:cx+R*Math.cos(a),y:cy+R*Math.sin(a),h:h,j:j});});return {cx:cx,cy:cy,ps:ps};}
function drawConstellation(pp,k){
 ctx.fillStyle='rgba(5,7,13,1)';ctx.fillRect(0,0,W,H);
 const {cx,cy,ps}=nodePos(), cs=confAt(k);
 // central hub
 ctx.beginPath();ctx.arc(cx,cy,5,0,7);ctx.fillStyle=P.accent;ctx.fill();
 ps.forEach(function(n){const created=(D.hypCreatedAt&&D.hypCreatedAt[n.h.label]!=null)?D.hypCreatedAt[n.h.label]:0;
   if(k<created)return;const age=Math.min(1,(k-created+1)/3);const v=cs[n.j]||0;
   ctx.strokeStyle=n.h.color;ctx.globalAlpha=0.25+0.5*age;ctx.lineWidth=1+3*v;
   ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(n.x,n.y);ctx.stroke();ctx.globalAlpha=1;
   const r=6+26*v;ctx.beginPath();ctx.arc(n.x,n.y,r,0,7);
   ctx.fillStyle=n.h.color;ctx.globalAlpha=(n.h.status==='eliminated'||n.h.status==='superseded')?0.4:(0.55+0.4*age);ctx.fill();ctx.globalAlpha=1;
   ctx.fillStyle='#dfe7f7';ctx.font='11px IBM Plex Mono,monospace';ctx.textAlign='center';
   ctx.fillText(n.h.label.slice(0,16),n.x,n.y-r-6);
   ctx.fillStyle=n.h.color;ctx.fillText(Math.round(v*100)+'%',n.x,n.y+r+14);ctx.textAlign='left';});
 // evidence sparks flying to hub when this event cites records
 const e=D.events[k];if(e&&e.recN){for(let i=0;i<Math.min(e.recN,18);i++){const a=Math.random()*7,rr=Math.min(W,H)*0.42;
   const ex=cx+rr*Math.cos(a),ey=cy+rr*Math.sin(a);ctx.globalAlpha=0.5;ctx.strokeStyle=P.cls.evidence;
   ctx.beginPath();ctx.moveTo(ex,ey);ctx.lineTo(cx,cy);ctx.stroke();ctx.globalAlpha=1;}}
}

// ---------- RIVER (reveals left→right) ----------
function drawRiver(pp,k){
 ctx.fillStyle='rgba(5,7,13,1)';ctx.fillRect(0,0,W,H);
 const m={l:24,r:120,t:40,b:30};const nH=D.hyps.length;
 const xr=function(i){return m.l+(W-m.l-m.r)*(i/Math.max(1,N-1));};
 const mid=(H-m.b+m.t)/2;
 // streamgraph: stack bands centered, thickness = conf*scale, revealed up to k
 const maxTot=Math.max.apply(null,D.confSeries.map(function(r){return r.reduce(function(s,x){return s+(x||0);},0);}).concat([0.6]));
 const scale=(H-m.t-m.b)/maxTot;
 for(let j=0;j<nH;j++){const h=D.hyps[j];
   ctx.beginPath();
   for(let i=0;i<=k;i++){const cs=D.confSeries[i]||[];let below=0,tot=0;for(let q=0;q<nH;q++){tot+=(cs[q]||0);if(q<j)below+=(cs[q]||0);}
     const yTop=mid-(tot*scale/2)+below*scale;ctx[i===0?'moveTo':'lineTo'](xr(i),yTop);}
   for(let i=k;i>=0;i--){const cs=D.confSeries[i]||[];let below=0,tot=0;for(let q=0;q<nH;q++){tot+=(cs[q]||0);if(q<=j)below+=(cs[q]||0);}
     const yBot=mid-(tot*scale/2)+below*scale;ctx.lineTo(xr(i),yBot);}
   ctx.closePath();ctx.fillStyle=h.color;ctx.globalAlpha=(h.status==='eliminated'||h.status==='superseded')?0.5:0.92;ctx.fill();ctx.globalAlpha=1;
   // label at head
   const cs=D.confSeries[k]||[];const v=cs[j]||0;
   ctx.fillStyle=h.color;ctx.font='11px IBM Plex Mono,monospace';
   ctx.fillText(h.label.slice(0,16)+' '+Math.round(v*100)+'%',xr(k)+6,mid+(j-nH/2)*16);}
 // playhead line
 ctx.strokeStyle='rgba(255,255,255,0.3)';ctx.beginPath();ctx.moveTo(xr(k),m.t-8);ctx.lineTo(xr(k),H-m.b);ctx.stroke();
}

// ---------- MISSION CONTROL (multi-panel) ----------
function drawMission(pp,k){
 ctx.fillStyle='rgba(5,7,13,1)';ctx.fillRect(0,0,W,H);
 const e=D.events[k], cs=confAt(k);
 // top ticker
 ctx.fillStyle=P.panel;ctx.fillRect(10,10,W-20,52);
 ctx.fillStyle=clsColor(e.cls);ctx.font='11px IBM Plex Mono,monospace';ctx.fillText('● '+e.cls.toUpperCase(),22,30);
 ctx.fillStyle='#fff';ctx.font='15px IBM Plex Mono,monospace';ctx.fillText(e.s.slice(0,84),22,52);
 // left: ranking bars
 const lx=14,lw=W*0.42-20,ly=80;
 ctx.fillStyle='#8aa0c4';ctx.font='10px IBM Plex Mono,monospace';ctx.fillText('HYPOTHESIS RANKING',lx,ly);
 D.hyps.forEach(function(h,j){const v=cs[j]||0;const by=ly+16+j*24;
   ctx.fillStyle='rgba(255,255,255,0.07)';ctx.fillRect(lx,by,lw,14);
   ctx.fillStyle=h.color;ctx.globalAlpha=(h.status==='eliminated')?0.45:1;ctx.fillRect(lx,by,lw*v,14);ctx.globalAlpha=1;
   ctx.fillStyle='#cdd8ef';ctx.fillText(h.label.slice(0,18)+'  '+Math.round(v*100)+'%',lx+2,by-3);});
 // right: ISAAC records grid lighting up
 const gx=W*0.46,gy=80,cells=(D.pool||[]).slice(0,60);
 ctx.fillStyle='#8aa0c4';ctx.fillText('ISAAC DATA / RECORDS TOUCHED',gx,gy);
 const cw=16,perRow=Math.max(8,Math.floor((W-gx-20)/cw));
 const touched=(D.touchedBy&&D.touchedBy[k])||0;
 cells.forEach(function(c,i){const cx=gx+(i%perRow)*cw,cyy=gy+12+Math.floor(i/perRow)*cw;
   const lit=i<touched;ctx.fillStyle=lit?P.hi:'rgba(255,255,255,0.10)';ctx.globalAlpha=lit?0.9:0.5;
   ctx.fillRect(cx,cyy,cw-3,cw-3);ctx.globalAlpha=1;});
 // bottom: activity sparkline
 const sy=H-46;ctx.strokeStyle=P.accent;ctx.beginPath();
 for(let i=0;i<=k;i++){const x=14+(W-28)*(i/Math.max(1,N-1));const a=Math.min(1,(D.confSeries[i]||[]).reduce(function(s,x){return s+(x||0);},0));
   ctx[i===0?'moveTo':'lineTo'](x,sy-a*30);}ctx.stroke();
 ctx.fillStyle='#8aa0c4';ctx.font='9px IBM Plex Mono,monospace';ctx.fillText('total belief mass over time',16,H-8);
}

// ---------- LIVING ORGANISM (the hero) ----------
function lerp(a,b,t){return a+(b-a)*t;}
function shade(hex,amt){try{var c=hex.replace('#','');var r=parseInt(c.substr(0,2),16),g=parseInt(c.substr(2,2),16),bl=parseInt(c.substr(4,2),16);
 r=Math.max(0,Math.min(255,r+amt*255));g=Math.max(0,Math.min(255,g+amt*255));bl=Math.max(0,Math.min(255,bl+amt*255));
 return 'rgb('+(r|0)+','+(g|0)+','+(bl|0)+')';}catch(e){return hex;}}
let ORG=null;
function initOrg(){const cx=W/2,cy=H*0.5,n=D.hyps.length;
 // radial 360° fan from the CENTRE → always centred & symmetric, fills the frame
 ORG={cx:cx,cy:cy,t:0,lastK:-1,parts:[],motes:[],
  br:D.hyps.map(function(h,i){
   var ang=-Math.PI/2+i*2*Math.PI/Math.max(1,n)+Math.sin(i*12.9)*0.05;
   return {h:h,i:i,ang:ang,grow:0,thick:0,pulse:0,ph:i*1.7,len:Math.min(W,H)*0.40};})};
 for(var m=0;m<70;m++)ORG.motes.push({x:Math.random()*W,y:Math.random()*H,r:Math.random()*1.5+0.3,s:0.08+Math.random()*0.25});}
function branchTip(b){var r=b.len*b.grow;var sway=Math.sin(ORG.t*1.1+b.ph)*16*b.grow;
 var dead=(b.h.status==='eliminated'||b.h.status==='superseded');
 return {x:ORG.cx+Math.cos(b.ang)*r+Math.cos(b.ang+1.57)*sway,
         y:ORG.cy+Math.sin(b.ang)*r+Math.sin(b.ang+1.57)*sway+(dead?40:0)};}
function spawnNutrients(ev){var b=(ev.hi>=0&&ORG.br[ev.hi])?ORG.br[ev.hi]:null;
 var col=clsColor(ev.cls);var cnt=Math.min(18,5+(ev.recN||1)*2);
 for(var i=0;i<cnt;i++){var a=Math.random()*6.283,rr=Math.min(W,H)*0.6;
  ORG.parts.push({x:ORG.cx+Math.cos(a)*rr,y:ORG.cy+Math.sin(a)*rr*0.7,b:b,col:col,life:1,
   sp:0.012+Math.random()*0.022,sd:Math.random()*6.283});}}
function drawBranch(b,cs){
 var dead=(b.h.status==='eliminated'||b.h.status==='superseded');
 var steps=18,half=[],half2=[];var perpx=Math.cos(b.ang+1.57),perpy=Math.sin(b.ang+1.57);
 var maxW=4+30*b.thick;
 for(var s=0;s<=steps;s++){var f=s/steps;var r=b.len*b.grow*f;
  var curl=Math.sin(b.ph)*18*f*f;var sway=Math.sin(ORG.t*1.1+b.ph+f*2.2)*(4+10*f)*b.grow;
  var off=curl+sway;var droop=dead?f*f*40:0;
  var x=ORG.cx+Math.cos(b.ang)*r+perpx*off, y=ORG.cy+Math.sin(b.ang)*r+perpy*off+droop;
  var w=maxW*(1-0.75*f)+0.6;
  half.push([x+perpx*w*0.5,y+perpy*w*0.5]);half2.push([x-perpx*w*0.5,y-perpy*w*0.5]);}
 ctx.beginPath();ctx.moveTo(half[0][0],half[0][1]);
 for(var i=1;i<half.length;i++)ctx.lineTo(half[i][0],half[i][1]);
 for(i=half2.length-1;i>=0;i--)ctx.lineTo(half2[i][0],half2[i][1]);ctx.closePath();
 var tip=half[half.length-1];
 var grd=ctx.createLinearGradient(ORG.cx,ORG.cy,tip[0],tip[1]);
 grd.addColorStop(0,dead?'#2a2f3a':shade(b.h.color,-0.32));grd.addColorStop(1,dead?'#3a4150':b.h.color);
 ctx.fillStyle=grd;ctx.globalAlpha=dead?0.4:0.92;
 if(b.pulse>0.05||b.thick>0.45){ctx.save();ctx.shadowBlur=14*(b.pulse*1.6+b.thick);ctx.shadowColor=b.h.color;ctx.fill();ctx.restore();}else ctx.fill();
 ctx.globalAlpha=1;
 var tp=branchTip(b),bud=3+8*b.thick;
 ctx.save();ctx.shadowBlur=12+22*b.pulse;ctx.shadowColor=b.h.color;
 ctx.beginPath();ctx.arc(tp.x,tp.y,bud,0,6.283);ctx.fillStyle=dead?'#444c5c':b.h.color;ctx.globalAlpha=dead?0.5:1;ctx.fill();ctx.restore();ctx.globalAlpha=1;
 var np=b.h.preds||0;for(var l=0;l<Math.min(np,5);l++){var lf=0.4+0.5*(l/Math.max(1,np));var lr=b.len*b.grow*lf;
  var side=(l%2?1:-1);var lx=ORG.cx+Math.cos(b.ang)*lr+perpx*(side*(6+6*b.thick)),ly=ORG.cy+Math.sin(b.ang)*lr+perpy*(side*(6+6*b.thick));
  ctx.beginPath();ctx.arc(lx,ly,2+2*b.thick,0,6.283);ctx.fillStyle=dead?'#3a4150':b.h.color;ctx.globalAlpha=dead?0.4:0.7;ctx.fill();ctx.globalAlpha=1;}}
function drawOrganism(pp,k){
 if(W<2)return;
 if(!ORG||ORG.br.length!==D.hyps.length||Math.abs(ORG.cx-W/2)>4)initOrg();
 ORG.t+=0.016;
 var g=ctx.createRadialGradient(W/2,H*0.5,30,W/2,H*0.5,Math.max(W,H)*0.75);
 g.addColorStop(0,P.org1);g.addColorStop(1,P.org2);ctx.fillStyle=g;ctx.fillRect(0,0,W,H);
 ctx.fillStyle='rgba(150,180,230,0.10)';
 ORG.motes.forEach(function(m){m.y-=m.s;if(m.y<0){m.y=H;m.x=Math.random()*W;}ctx.beginPath();ctx.arc(m.x,m.y,m.r,0,6.283);ctx.fill();});
 if(playing&&k>ORG.lastK){for(var j=ORG.lastK+1;j<=k;j++){var e=D.events[j];if(e&&(e.recN>0||['evidence','literature','compute','verdict'].indexOf(e.cls)>=0))spawnNutrients(e);}}
 ORG.lastK=k;
 var cs=confAt(k);
 ORG.br.forEach(function(b){var cr=(D.hypCreatedAt&&D.hypCreatedAt[b.h.label]!=null)?D.hypCreatedAt[b.h.label]:0;
  b.grow=lerp(b.grow,(k>=cr)?1:0,0.05);b.thick=lerp(b.thick,(cs[b.i]||0),0.07);b.pulse*=0.92;});
 ORG.br.slice().sort(function(a,b){return a.thick-b.thick;}).forEach(function(b){if(b.grow>0.02)drawBranch(b,cs);});
 var cor=8+4*Math.sin(ORG.t*2);ctx.save();ctx.shadowBlur=32;ctx.shadowColor=P.accent;
 ctx.beginPath();ctx.arc(ORG.cx,ORG.cy,cor,0,6.283);ctx.fillStyle='#d6f2f4';ctx.fill();ctx.restore();
 ORG.parts=ORG.parts.filter(function(pt){var tgt=pt.b?branchTip(pt.b):{x:ORG.cx,y:ORG.cy};
  pt.x=lerp(pt.x,tgt.x,pt.sp);pt.y=lerp(pt.y,tgt.y,pt.sp);var d=Math.hypot(pt.x-tgt.x,pt.y-tgt.y);
  var wob=Math.sin(ORG.t*3+pt.sd)*2;ctx.globalAlpha=Math.min(0.9,pt.life);ctx.fillStyle=pt.col;ctx.shadowBlur=8;ctx.shadowColor=pt.col;
  ctx.beginPath();ctx.arc(pt.x+wob,pt.y+wob,2.2,0,6.283);ctx.fill();ctx.shadowBlur=0;ctx.globalAlpha=1;
  if(d<10){if(pt.b)pt.b.pulse=1;return false;}return true;});
 ORG.br.forEach(function(b){if(b.grow<0.4)return;var tp=branchTip(b);var v=cs[b.i]||0;
  var dead=(b.h.status==='eliminated'||b.h.status==='superseded');
  ctx.fillStyle=dead?'rgba(150,160,180,0.5)':b.h.color;ctx.font='11px IBM Plex Mono,monospace';ctx.textAlign='center';
  ctx.fillText(b.h.label.slice(0,16),tp.x,tp.y-20);ctx.fillStyle=P.ink;ctx.fillText(Math.round(v*100)+'%',tp.x,tp.y-6);ctx.textAlign='left';});}

function drawCaption(k){const e=D.events[k];if(!e){cap.textContent='';return;}
 cap.innerHTML='<span style="color:'+clsColor(e.cls)+'">['+e.cls+']</span> '+
   (e.s.replace(/</g,'&lt;'))+'<span style="color:#7f8aa3"> &nbsp;'+(k+1)+'/'+N+'</span>';}

function draw(){const k=evIndex(p);
 if(MODE==='organism')drawOrganism(p,k);
 else if(MODE==='matrix')drawMatrix(p,k);
 else if(MODE==='constellation')drawConstellation(p,k);
 else if(MODE==='river')drawRiver(p,k);
 else drawMission(p,k);
 drawCaption(k);tl.textContent=(k+1)+' / '+N;}
function loop(){tick++;if(playing){p+=(1/N)*0.10*speed;if(p>=1){p=1;playing=false;playB.textContent='▶';}scrub.value=p*1000;}
 draw();requestAnimationFrame(loop);}
playB.onclick=function(){playing=!playing;if(p>=1){p=0;}playB.textContent=playing?'❚❚':'▶';};
scrub.oninput=function(){p=scrub.value/1000;playing=false;playB.textContent='▶';};
spd.onchange=function(){speed=parseFloat(spd.value);};
requestAnimationFrame(loop);
</script></body></html>
"""
            return (tmpl.replace("__DATA__", data).replace("__PAL__", pal)
                    .replace("__MODE__", mode).replace("__BGC__", _bg)
                    .replace("__CAPINK__", _capink).replace("__CAPSHADOW__", _capshadow)
                    .replace("__CTLBG__", _ctlbg).replace("__CTLBORDER__", _ctlborder)
                    .replace("__PLAYBG__", _playbg).replace("__PLAYINK__", _playink)
                    .replace("__PLAYBORDER__", _playborder))

        def _funnel(stages):
            pal = branding.palette(st.session_state.ui_theme)
            n = len(stages)
            out = ["<div style='padding:4px 0'>"]
            for i, (label, count, sub) in enumerate(stages):
                w = 96 - i * (70 / max(1, n - 1))
                out.append(
                    "<div style='display:flex;justify-content:center;margin:3px 0'>"
                    f"<div style='width:{w:.0f}%;background:linear-gradient(90deg,"
                    f"{pal['accent']},{pal['accent_hover']});border-radius:7px;"
                    f"padding:7px 12px;color:{pal['on_accent']};text-align:center;"
                    "box-shadow:0 1px 5px rgba(0,0,0,0.18)'>"
                    f"<span style='font-size:1.25em;font-weight:800'>{count:,}</span> "
                    f"<span style='opacity:0.95'>{html.escape(str(label))}</span>"
                    + (f"<div style='font-size:0.78em;opacity:0.85'>{html.escape(str(sub))}</div>"
                       if sub else "")
                    + "</div></div>")
            out.append("</div>")
            return "".join(out)

        def _fmt(ts):
            return ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)

        def _pred_row(p, prov):
            ev = ", ".join(f"{rid} ({prov.get(rid, {}).get('material', '?')})"
                           for rid in (p.get("evidence_record_ids") or [])) or "—"
            return {"Label": p.get("label"), "Descriptor": p.get("descriptor_name"),
                    "Direction": p.get("direction"),
                    "Work status": p.get("work_status") or "awaiting_evidence",
                    "Falsification": p.get("falsification_criterion"),
                    "Verdict": f"{_VERDICT_ICON.get(p.get('verdict'), '')} "
                               f"{p.get('verdict') or '—'} ({p.get('strength') or '—'})",
                    "Evidence": ev, "MLflow": p.get("mlflow_run_url") or ""}

        def _board_section(title, items, prov, show_verdict=False):
            st.markdown(f"**{title}** ({len(items)})")
            if not items:
                st.caption("_none_")
                return
            rows = []
            for h, p in items:
                ev = ", ".join(f"{rid} ({prov.get(rid, {}).get('material', '?')})"
                               for rid in (p.get("evidence_record_ids") or [])) or "—"
                row = {"Hypothesis": h["label"], "Descriptor": p.get("descriptor_name"),
                       "Direction": p.get("direction")}
                if show_verdict:
                    row["Verdict"] = (f"{_VERDICT_ICON.get(p.get('verdict'), '')} "
                                      f"{p.get('verdict') or '—'} ({p.get('strength') or '—'})")
                    row["Evidence"] = ev
                else:
                    row["Falsification"] = p.get("falsification_criterion")
                    row["MLflow"] = p.get("mlflow_run_url") or ""
                rows.append(row)
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

        def _discovery_detail(pid, owner):
            data = discovery.get_project(pid, owner_identity=owner)
            if data is None:
                st.warning("Project not found (or not yours).")
                return
            proj = data["project"]
            hyps = data["hypotheses"]
            events = data["events"]
            relations = data.get("relations", [])
            _is_owner = proj.get("owner_identity") == owner
            _hlabel = {h["hypothesis_id"]: h["label"] for h in hyps}
            _hcolor = _hyp_colors(hyps)   # one autumn colour per hypothesis, used everywhere
            _alive = [h for h in hyps if h["status"] not in ("eliminated", "superseded")]
            _leader_label = (max(_alive, key=lambda h: float(h["confidence"] or 0))["label"]
                             if _alive else None)
            brief = discovery.get_briefing(pid, owner) or {}

            # ---------- EVIDENCE-DRIFT TAG ----------
            # Records CITED for a verdict (used to reason) were edited since. Advisory; never
            # changes a score. Records merely browsed in the dataset never appear here.
            _drift = brief.get("evidence_drift") or []
            if _drift:
                _dh = {}
                for _d in _drift:
                    _dh.setdefault(_d.get("hypothesis"), set()).add(_d.get("record_id"))
                _lines = "; ".join(
                    f"**{_h}** ({len(_r)} record{'s' if len(_r) != 1 else ''})"
                    for _h, _r in _dh.items())
                st.warning(
                    f"**Evidence revised since used** — {_lines}. A record cited on these "
                    "hypotheses was edited after you reasoned on it. Re-examine the affected "
                    "verdict(s) and re-evaluate to re-pin (this tag clears itself). It did "
                    "**not** change any score.", icon="⚠️")

            # ---------- BRIEFING HEADER (the universal-truth digest) ----------
            # Title + the project's unique DB id (so you can tell the agent "continue
            # <id>" — the human title alone isn't addressable) + a single quiet meta line.
            # The Goal now lives in the triptych below rather than a separate full-width box.
            st.markdown(f"### {proj['title']}")
            _tp = branding.palette(st.session_state.ui_theme)
            st.markdown(
                f"<div style=\"font-family:'IBM Plex Mono',monospace;font-size:0.72rem;"
                f"margin:-6px 0 4px;color:{_tp['muted']}\">"
                f"<span style='text-transform:uppercase;letter-spacing:.09em'>project id</span>"
                f"&nbsp;&nbsp;<code style='background:{_tp['code_bg']};padding:1px 8px;"
                f"border-radius:5px;color:{_tp['text']};font-size:0.95em'>{html.escape(str(pid))}</code>"
                f"</div>", unsafe_allow_html=True)
            meta = " · ".join(filter(None, [
                proj.get("material_system"), proj.get("reaction"),
                f"status: {proj.get('status')}"]))
            if meta:
                st.caption(meta)

            # --- Calm landing: the triptych (Goal → Leading explanation → Next move) ---
            # Three equal cards in ONE iframe: same width (grid 1fr), same FIXED height. Each
            # body SCROLLS with a soft bottom fade when it overflows — nothing is clipped and
            # nothing sits half-empty. The middle "Leading explanation" is the hero (sole teal
            # accent + the one big confidence number), so equal geometry never flattens the
            # hierarchy — the eye lands on the answer first. (Design panel consensus.)
            def _leader_inner():
                # Inner HTML for the hero card (eyebrow · anchor · scrolling statement · foot).
                if not _alive:
                    return ("<div class='eyebrow hero-eyebrow'>Leading explanation</div>"
                            "<div class='body'><span class='muted'>No live hypothesis yet — "
                            "the agent hasn't proposed one.</span></div>")
                _ld = max(_alive, key=lambda h: float(h["confidence"] or 0))
                _sc = discovery.compute_hypothesis_score(_ld)
                _conf = float(_ld["confidence"] or 0)
                _pal = branding.palette(st.session_state.ui_theme)
                _col = _hcolor.get(_ld["label"], _pal["accent"])
                _nd = _sc.get("n_decisive", 0)
                if _ld["status"] == "supported" or (_sc.get("reliable") and _nd >= 2):
                    _sym, _symcol, _word = "✓", "#5EC8C0", "confirmed"
                    _foot = f"{_nd} independent decisive tests agree."
                else:
                    _sym, _symcol, _word = "○", _pal["muted"], "provisional"
                    _foot = (f"{_nd}/2 independent decisive tests so far — promoted only at 2, "
                             f"by design.")
                # Owner-directed hierarchy: hypothesis NAME first (its own colour, the
                # identity a scientist refers to), then the confidence number (neutral, #2),
                # then a quiet status — newcomers shouldn't be shouted "confirmed".
                return (
                    f"<div class='eyebrow hero-eyebrow'>Leading explanation</div>"
                    f"<div class='hname' style='color:{_col}'>{html.escape(_ld['label'] or 'H')}</div>"
                    f"<div class='hrow'>"
                    f"<span class='hconf'>{_conf:.2f}</span>"
                    f"<span class='hconflab'>confidence</span>"
                    f"<span class='hstatus'>"
                    f"<span style='color:{_symcol}'>{_sym}</span>&nbsp;{_word}</span>"
                    f"</div>"
                    f"<div class='body'>{html.escape(_ld.get('statement') or '')}</div>"
                    f"<div class='foot'>{html.escape(_foot)}</div>")

            def _next_inner():
                # Inner HTML for the next-move card, or None if there's no move to show.
                _ne = brief.get("next_experiment") or data.get("next_experiment")
                _recs = brief.get("recommended_actions") or []
                if not (_ne and (_ne.get("rationale") or _ne.get("title"))) and not _recs:
                    return None
                _ld = (max(_alive, key=lambda h: float(h["confidence"] or 0))
                       if _alive else None)
                _sc = discovery.compute_hypothesis_score(_ld) if _ld else {}
                _rel = bool(_sc.get("reliable"))

                def _tag_for(_a):
                    _l = _a.lower()
                    if any(w in _l for w in ("structure", "cite", "falsif", "decisive",
                                             "predictions", "origin", "mlflow")):
                        return "🔧 makes a test count"
                    if any(w in _l for w in ("reconcile", "pending", "poll", "await")):
                        return "⏳ finish what's running"
                    if any(w in _l for w in ("coverage", "unused", "dataset")):
                        return "📊 use the data on hand"
                    if any(w in _l for w in ("review", "critic", "rigor", "independent")):
                        return "🔍 independent check"
                    return "• strengthens the case"
                if _ne and (_ne.get("rationale") or _ne.get("title")):
                    _title = _ne.get("title") or "Run the discriminating experiment"
                    _why = _ne.get("rationale") or ""
                    _tags = []
                    if _ne.get("discriminates"):
                        _tags.append("⚔️ decides between rivals")
                    _tags.append("🧪 hardens it" if _rel else "🔓 confirms it")
                    _tags.append("📉 could falsify it")
                else:
                    _title = _recs[0].split(" — ")[0].split(". ")[0].strip()
                    _why = ""
                    _tags = [_tag_for(_recs[0])]
                return (
                    f"<div class='eyebrow'>Next move · most valuable</div>"
                    f"<div class='tags'>{' · '.join(_tags)}</div>"
                    f"<div class='body'><div class='mtitle'>{html.escape(_title)}</div>"
                    + (f"<div class='mwhy'>{html.escape(_why)}</div>" if _why else "")
                    + "</div>")

            def _render_triptych():
                _p = branding.palette(st.session_state.ui_theme)
                _goal = proj.get("goal") or proj.get("title") or ""
                _cards = [
                    ("<div class='card goal'>"
                     "<div class='eyebrow'>🎯 The goal</div>"
                     f"<div class='body'>{html.escape(_goal)}</div></div>"),
                    f"<div class='card hero'>{_leader_inner()}</div>",
                ]
                _nin = _next_inner()
                if _nin:
                    _cards.append(f"<div class='card'>{_nin}</div>")
                # Token template (literal % in calc()/masks survives .replace untouched —
                # no %-format escaping to get wrong). Hover spec + name-first hero hierarchy
                # from the design panel; iframe is sized so the hover shadow isn't clipped.
                _tpl = (
                    "<html><head><style>"
                    "html,body{margin:0;background:transparent;"
                    "font-family:-apple-system,Segoe UI,Roboto,sans-serif;}"
                    ".grid{display:grid;grid-template-columns:repeat(__COLS__,1fr);gap:14px;"
                    "padding:8px 8px 22px;}"
                    ".card{box-sizing:border-box;display:flex;flex-direction:column;height:248px;"
                    "background:__SURF__;border:1px solid __BD__;border-left:2px solid __BD__;"
                    "border-radius:12px;padding:17px 18px;will-change:transform;"
                    "box-shadow:0 1px 2px rgba(0,0,0,0.30),0 0 0 1px rgba(94,200,192,0);"
                    "transform:translateY(0) scale(1);"
                    "transition:transform 220ms cubic-bezier(.22,1,.36,1),"
                    "box-shadow 260ms cubic-bezier(.22,1,.36,1),"
                    "border-color 180ms cubic-bezier(.4,0,.2,1);}"
                    ".card:hover{transform:translateY(-3px) scale(1.006);border-color:__BDSOFT__;"
                    "box-shadow:0 2px 4px rgba(0,0,0,0.40),0 12px 28px -8px rgba(0,0,0,0.55),"
                    "0 8px 32px -4px rgba(94,200,192,0.10),0 0 0 1px rgba(94,200,192,0.14),"
                    "inset 0 1px 0 0 rgba(255,255,255,0.04);}"
                    ".card.hero{border-left:2px solid __ACC__;background:__ACCSOFT__;}"
                    ".eyebrow{font-size:11px;letter-spacing:.12em;text-transform:uppercase;"
                    "color:__MUT__;font-weight:600;flex:0 0 auto;margin-bottom:11px;"
                    "transition:color 200ms cubic-bezier(.22,1,.36,1);}"
                    ".card:hover .eyebrow{color:__TXT__;}"
                    ".hero-eyebrow{color:__ACC__;}"
                    ".card:hover .hero-eyebrow{color:__ACC__;}"
                    # name-first hero hierarchy
                    ".hname{font-size:22px;font-weight:700;line-height:1.1;letter-spacing:.04em;"
                    "text-transform:uppercase;flex:0 0 auto;margin-bottom:9px;}"
                    ".hrow{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap;flex:0 0 auto;"
                    "margin-bottom:11px;}"
                    ".hconf{font-size:20px;font-weight:600;color:__TXT__;"
                    "font-variant-numeric:tabular-nums;line-height:1;}"
                    ".hconflab{font-size:10.5px;font-weight:500;color:__MUT__;text-transform:uppercase;"
                    "letter-spacing:.06em;}"
                    ".hstatus{margin-left:auto;font-size:11.5px;font-weight:600;color:__MUT__;"
                    "white-space:nowrap;align-self:baseline;}"
                    ".tags{font-size:10.5px;color:__ACC__;text-transform:uppercase;"
                    "letter-spacing:.04em;font-weight:700;flex:0 0 auto;margin-bottom:9px;"
                    "line-height:1.55;}"
                    ".body{flex:1 1 auto;min-height:0;overflow-y:auto;color:__TXT__;"
                    "font-size:14px;line-height:1.5;scrollbar-width:none;"
                    "-webkit-mask-image:linear-gradient(to bottom,#000 0,"
                    "#000 calc(100% - 18px),transparent 100%);"
                    "mask-image:linear-gradient(to bottom,#000 0,"
                    "#000 calc(100% - 18px),transparent 100%);}"
                    ".goal .body{font-size:13.5px;line-height:1.52;}"
                    ".body::-webkit-scrollbar{width:0;height:0;}"
                    ".muted{color:__MUT__;}"
                    ".mtitle{font-weight:700;color:__TXT__;font-size:15px;line-height:1.35;"
                    "margin-bottom:7px;}"
                    ".mwhy{color:__MUT__;font-size:13px;line-height:1.5;}"
                    ".foot{flex:0 0 auto;margin-top:9px;color:__MUT__;font-size:11.5px;"
                    "line-height:1.4;}"
                    "@media (prefers-reduced-motion: reduce){"
                    ".card{transition:border-color 120ms ease,box-shadow 120ms ease;}"
                    ".card:hover{transform:none;box-shadow:0 0 0 1px rgba(94,200,192,0.12),"
                    "0 4px 16px -6px rgba(0,0,0,0.45);}}"
                    "</style></head><body><div class='grid'>__CARDS__</div></body></html>")
                _doc = (_tpl.replace("__SURF__", _p["surface"]).replace("__BDSOFT__", _p["border_soft"])
                        .replace("__BD__", _p["border"]).replace("__MUT__", _p["muted"])
                        .replace("__TXT__", _p["text"]).replace("__ACCSOFT__", _p["accent_soft"])
                        .replace("__ACC__", _p["accent"]).replace("__COLS__", str(len(_cards)))
                        .replace("__CARDS__", "".join(_cards)))
                components.html(_doc, height=286)

            def _ranking_block():
                st.markdown("#### How the explanations rank")
                if not hyps:
                    st.markdown("_No hypotheses yet._")
                    return
                # The long "what's decisive" explanation is tucked behind the ⓘ hover in the
                # iframe (not shown as a wall of text); hovering a row shows its predictions.
                _note = ("Confidence is COMPUTED from the prediction verdicts, never authored. "
                         "DECISIVE = a verdict on a complete, auditable, INDEPENDENT test — "
                         "cited to data/compute, falsifiable, structured, explained. Belief can "
                         "climb high on supporting evidence, but a hypothesis stays PROVISIONAL "
                         "until at least 2 independent decisive verdicts agree — so 0.93 with 0 "
                         "decisive means the evidence points strongly this way, but no single "
                         "piece is yet a fully auditable, independent test.")
                _rows = []
                for h in hyps:
                    _sc = discovery.compute_hypothesis_score(h)
                    _pd = _sc.get("predictions") or {}
                    _ps, _nv, _ni, _npd = [], 0, 0, 0
                    for p in h["predictions"]:
                        _ev = p.get("work_status") == "evaluated"
                        if _ev:
                            _v = discovery.normalize_verdict(p.get("verdict"))
                            if _v == "supports":
                                _cat = "validated"; _nv += 1
                            elif _v == "contradicts":
                                _cat = "invalidated"; _ni += 1
                            else:
                                _cat = "inconclusive"; _npd += 1
                        else:
                            _cat = "pending"; _npd += 1
                        _g = _pd.get(p.get("prediction_id") or p.get("id"), {})
                        _ps.append({
                            "name": p.get("descriptor_name") or p.get("label") or "—",
                            "strength": p.get("strength") or "", "cat": _cat, "evaluated": _ev,
                            "cited": bool(_g.get("cited")), "falsifiable": bool(_g.get("falsifiable")),
                            "structured": bool(_g.get("structured")), "explained": bool(_g.get("explained")),
                            "counted": bool(_g.get("counted")), "admissible": bool(_g.get("admissible")),
                            "reason": _g.get("reason")})
                    _rows.append({
                        "id": h["hypothesis_id"],
                        "label": h["label"] or "H",
                        "color": _hcolor.get(h["label"], "#C97A3C"),
                        "statement": h.get("statement") or "",
                        "conf": float(h["confidence"] or 0), "status": h["status"],
                        "dead": h["status"] in ("eliminated", "superseded"),
                        "n_decisive": _sc.get("n_decisive", 0),
                        "reliable": bool(_sc.get("reliable")),
                        "nv": _nv, "ni": _ni, "npd": _npd, "preds": _ps})

                # Height follows the BARS (left column) plus the ⓘ header row; the detail
                # panel SCROLLS within it (overflow:auto) instead of stretching the iframe —
                # so a long prediction list never leaves a tall empty gap under the bars.
                _H = max(250, 46 * len(_rows) + 56)
                components.html(_ranking_html(_rows, st.session_state.ui_theme, _H, _note),
                                height=_H)

            def _status_line():
                _st = brief.get("settled", {"supported": [], "eliminated": []})
                _pal = branding.palette(st.session_state.ui_theme)
                _parts = [f"{len(hyps)} hypotheses",
                          f"{len(brief.get('validated_predictions', []))} prediction(s) validated",
                          f"{len(brief.get('open_questions', []))} open"]
                _run = len(brief.get("pending_compute", []))
                if _run:
                    _parts.append(f"{_run} compute running")
                _pw = brief.get("pending_work", {})
                if _pw.get("items"):
                    _parts.append(f"⟳ {_pw['count']} resumable")
                st.markdown(
                    f"<div style='color:{_pal['muted']};font-size:0.85em;margin:6px 0 2px'>"
                    + "  ·  ".join(_parts) + "</div>", unsafe_allow_html=True)
                if _pw.get("items"):
                    with st.expander(f"⟳ Resumable work — {_pw['count']} pending step(s)"):
                        st.caption("An agent kicked these off and couldn't wait. Resume the "
                                   "project with an agent to poll & ingest the results.")
                        for _it in _pw["items"]:
                            _ki = {"literature": "📚", "compute": "🖥️"}.get(_it.get("kind"), "⏳")
                            st.markdown(f"- {_ki} **{_it.get('kind')}** · "
                                        f"{(_it.get('summary') or _it.get('ref') or '')[:80]} "
                                        f"· {_it.get('status')}")

            def _diagnostics_compact():
                _recs = brief.get("recommended_actions") or []
                _rr = brief.get("rigor_review", {})
                _oc = _rr.get("open_critical", 0)
                _lbl = "🔬 Rigor & what the agent does next"
                if _recs:
                    _lbl += f" — {len(_recs)} action(s)"
                if _oc:
                    _lbl = f"⚠ {_lbl} · {_oc} critical"
                with st.expander(_lbl, expanded=False):
                    st.caption("The platform audits its own rigor and derives the to-do; the "
                               "agent reads it from the briefing — no human prompt needed.")
                    for _a in _recs[:14]:
                        st.markdown(f"- {_a}")
                    if not _recs:
                        st.caption("All clear — no open actions.")
                with st.expander("🧭 Briefing — exactly what the agent reads as ground truth",
                                 expanded=False):
                    st.json(brief)

            # --- Calm landing (the chosen single view) ---
            # Top of page flows cleanly into the decision journey: goal → leader → ranking
            # → the tabs (constellation + river). The diagnostic expanders (status,
            # resumable, rigor, briefing) are rendered AFTER the tabs (see end of function)
            # so they don't break that flow.
            _render_triptych()
            _ranking_block()

            preds = [p for h in hyps for p in h["predictions"]]
            evidence_ids = sorted({rid for p in preds
                                   for rid in (p.get("evidence_record_ids") or [])})
            prov = discovery.resolve_record_summaries(evidence_ids)

            def _render_scoreboard(hyps, _hcolor):
                # Per-hypothesis PREDICTION SCOREBOARD: one row per hypothesis — how many
                # predictions it carries (vs the >=2 minimum) and how they resolve
                # (validated / invalidated / not-enough-data), with a composition mini-bar +
                # confidence + status. The at-a-glance oversight of the whole project.
                _spal = branding.palette(st.session_state.ui_theme)
                _MINP = discovery.MIN_PREDICTIONS_PER_HYPOTHESIS
                _GREEN, _RED, _GREY = "#3a9d6b", _spal["error"], _spal["muted"]
                st.markdown("#### Prediction scoreboard")
                st.caption(f"Each hypothesis needs a SET of falsifiable predictions "
                           f"(≥{_MINP}). A row shows how its predictions resolve — and a "
                           "thin or untested hypothesis can't be trusted no matter its "
                           "confidence.")
                if not hyps:
                    st.caption("_No hypotheses yet._")
                    return
                _rows = []
                for h in hyps:
                    _preds = h["predictions"]
                    _tot = len(_preds)
                    _val = sum(1 for p in _preds if p.get("work_status") == "evaluated"
                               and discovery.normalize_verdict(p.get("verdict")) == "supports")
                    _inv = sum(1 for p in _preds if p.get("work_status") == "evaluated"
                               and discovery.normalize_verdict(p.get("verdict")) == "contradicts")
                    _nd = _tot - _val - _inv
                    _sc = discovery.compute_hypothesis_score(h)
                    _conf, _rel = _sc["computed_confidence"], _sc["reliable"]
                    _dead = h["status"] in ("eliminated", "superseded")
                    if _tot == 0:
                        _stat = "◌ Untested"
                    elif _dead or (_inv > _val and (_val + _inv) >= 1):
                        _stat = "✗ Refuted"
                    elif _rel and _val > _inv:
                        _stat = "✓ Reliable"
                    elif _tot < _MINP:
                        _stat = "◌ Thin"
                    elif _val > 0 and _inv > 0:
                        _stat = "~ Mixed"
                    elif _val > 0:
                        _stat = "Supported"
                    else:
                        _stat = "Pending"
                    _thin = "<span style='color:%s'> !</span>" % _RED if _tot < _MINP else ""
                    _den = max(_tot, 1)
                    def _seg(n, col, _den=_den):
                        return (f"<span style='display:inline-block;height:9px;"
                                f"width:{(n/_den)*90:.0f}px;background:{col}'></span>") if n else ""
                    _bar_html = (_seg(_val, _GREEN) + _seg(_inv, _RED) + _seg(_nd, _GREY)) \
                        or f"<span style='color:{_GREY};font-size:0.8em'>none</span>"
                    _dot = _hcolor.get(h["label"], "#C97A3C")
                    _rows.append(
                        "<tr style='border-bottom:1px solid %s'>" % _spal["border_soft"]
                        + f"<td style='padding:6px 10px 6px 0;white-space:nowrap'>"
                        f"<span style='display:inline-block;width:8px;height:8px;border-radius:50%;"
                        f"background:{_dot};margin-right:6px'></span><b>{html.escape(h['label'])}</b></td>"
                        + f"<td style='text-align:center;color:{_spal['text']}'>{_tot} / {_MINP}{_thin}</td>"
                        + f"<td style='text-align:center;color:{_GREEN};font-weight:600'>{_val}</td>"
                        + f"<td style='text-align:center;color:{_RED};font-weight:600'>{_inv}</td>"
                        + f"<td style='text-align:center;color:{_GREY};font-weight:600'>{_nd}</td>"
                        + f"<td style='padding:0 10px'>{_bar_html}</td>"
                        + f"<td style='text-align:right;color:{_spal['text']};font-variant-numeric:tabular-nums'>{_conf:.2f}</td>"
                        + f"<td style='padding-left:10px;color:{_spal['muted']};white-space:nowrap'>{_stat}</td></tr>")
                st.markdown(
                    f"<table style='width:100%;border-collapse:collapse;font-size:0.86em'>"
                    f"<thead><tr style='color:{_spal['muted']};text-align:center;"
                    f"font-size:0.82em;text-transform:uppercase;letter-spacing:0.04em'>"
                    f"<th style='text-align:left'>Hypothesis</th><th>Preds</th>"
                    f"<th>✅</th><th>❌</th><th>⏳</th>"
                    f"<th style='text-align:left;padding-left:10px'>Composition</th>"
                    f"<th style='text-align:right'>Conf</th>"
                    f"<th style='text-align:left;padding-left:10px'>Status</th></tr></thead>"
                    f"<tbody>{''.join(_rows)}</tbody></table>"
                    f"<div style='color:{_spal['muted']};font-size:0.78em;margin-top:8px'>"
                    f"✅ validated (supports) · ❌ invalidated (contradicts) · ⏳ not enough data "
                    f"· <span style='color:{_RED}'>!</span> below the ≥{_MINP} minimum · "
                    f"✓ Reliable = ≥2 cited, falsifiable, structured, explained verdicts</div>",
                    unsafe_allow_html=True)

            tabImpact, tabGenesis, tabReplay, tabA, tabB, tabE, tabC, tabJ = st.tabs([
                "🌊 Decision journey", "✨ Genesis", "🎬 Replay",
                "🧪 Hypotheses & provenance", "✅ Validation board",
                "🔎 Evidence & matrix", "📊 Compute ledger", "📓 Journal"])

            # ---- ✨ Genesis: how the hypotheses were conceived (observation → contenders) ----
            with tabGenesis:
                st.markdown("#### How these explanations were conceived")
                if not hyps:
                    st.caption("_No hypotheses yet._")
                else:
                    def _src_of(_h):
                        _o = _h.get("origin") or {}
                        for _s in (_o.get("sources") or []):
                            _d = _s.get("doi") or _s.get("record_id") or _s.get("hypothesis")
                            if _d:
                                return _d
                        return (_h.get("grounding") or "reasoning")

                    def _is_null(_h):
                        _t = ((_h.get("mechanism") or "") + " "
                              + ((_h.get("origin") or {}).get("reasoning") or "")).lower()
                        return ("no downturn" in _t or "monotonic" in _t
                                or "null" in _t or "ensemble" in (_h.get("label") or "").lower())

                    def _mech_of(_h):
                        _m = _h.get("mechanism")
                        if isinstance(_m, dict):
                            _m = _m.get("summary") or _m.get("text") or json.dumps(_m)
                        return (str(_m or _h.get("statement") or "")[:140])

                    def _why_of(_h):
                        _o = _h.get("origin") or {}
                        return str(_o.get("reasoning") or _o.get("summary") or "")[:150]

                    def _ground_label(_h):
                        _g = (_h.get("grounding") or "").lower()
                        if _g == "standing_prior":
                            return "from the literature"
                        if _g == "ad_hoc":
                            return "from this dataset"
                        return (_g.replace("_", " ") or "reasoning")
                    _gh = []
                    for _h in hyps:
                        _gh.append({"label": _h["label"] or "H",
                                    "color": _hcolor.get(_h["label"], "#C97A3C"),
                                    "mech": _mech_of(_h), "why": _why_of(_h),
                                    "grounding": _ground_label(_h),
                                    "source": _src_of(_h), "is_null": _is_null(_h)})
                    # The SEED is the project's real goal — what ISAAC was actually asked. No
                    # fabricated chart; everything below is per-project, from the record.
                    _gpay = {"goal": (proj.get("goal") or proj.get("title") or
                                      "the question ISAAC was given"),
                             "n": len(_gh), "hyps": _gh}
                    components.html(_genesis_html(_gpay, st.session_state.ui_theme),
                                    height=max(300, 150 + 96 * ((len(_gh) + 1) // 2)))
                    st.caption("The seed is the goal you set; from it, ISAAC posed the "
                               "mechanisms the field debates — each grounded (literature or "
                               "this dataset) and stated so it can be broken. A dashed card is "
                               "a **null** kept on the board to rule out. ↻ replay the reasoning.")

            # ---- 🎬 Replay Studio: a data-driven "video" of the whole discovery ----
            with tabReplay:
                st.markdown("#### 🎬 Replay — watch the discovery happen")
                st.caption("A generative 'video' built entirely from this project's "
                           "stored timeline: every reasoning step, hypothesis swing, "
                           "evidence/literature touch and compute job, played in order. "
                           "Pick a cinematic style, hit ▶, or scrub the timeline.")
                _emap = {"hypothesis_created": "hypothesis", "prediction_added": "prediction",
                         "prediction_evaluated": "verdict", "evidence_ingested": "evidence",
                         "compute_submitted": "compute", "compute_running": "compute",
                         "next_experiment_proposed": "experiment", "status_changed": "update",
                         "project_created": "other", "agent_message": "literature",
                         "human_directive": "directive"}

                def _ecls(et, summ):
                    s = (summ or "").lower()
                    if "literat" in s or "edison" in s or "paperqa" in s:
                        return "literature"
                    if "rigor" in s or "finding" in s:
                        return "rigor"
                    return _emap.get(et, "other")

                _chrono = [e for e in reversed(events)]
                _rhyps = [{"label": h["label"], "color": _hcolor.get(h["label"], "#C97A3C"),
                           "status": h["status"], "preds": len(h["predictions"])}
                          for h in hyps]
                _lab2idx = {h["label"]: _j for _j, h in enumerate(hyps)}
                # confidence at each event (snapshot ≤ event time), and creation index
                _rsnaps = discovery.get_confidence_history(pid, owner)
                _rsb = {h["label"]: [] for h in hyps}
                _hid2lab = {h["hypothesis_id"]: h["label"] for h in hyps}

                def _ep(dt):
                    return dt.timestamp() if hasattr(dt, "timestamp") else 0.0
                for _sn in _rsnaps:
                    _lab = _hid2lab.get(_sn["hypothesis_id"])
                    if _lab:
                        _rsb[_lab].append((_ep(_sn["created_at"]), float(_sn["confidence"] or 0)))
                for _l in _rsb:
                    _rsb[_l].sort()

                def _conf_at_t(lab, t):
                    c = 0.0
                    for st_, cv in _rsb.get(lab, []):
                        if st_ <= t:
                            c = cv
                        else:
                            break
                    return c
                _revents, _confseries, _touched, _cum = [], [], [], 0
                _created_at = {}
                _pool = []
                for _i, _e in enumerate(_chrono):
                    _et = _e["event_type"]
                    _lab = _hid2lab.get(_e.get("hypothesis_id"))
                    if _et == "hypothesis_created" and _lab and _lab not in _created_at:
                        _created_at[_lab] = _i
                    _recs = _e.get("evidence_record_ids") or []
                    _cum += len(_recs)
                    for _rid in _recs:
                        _pool.append(_rid)
                    _t = _ep(_e["created_at"]) if hasattr(_e.get("created_at"), "timestamp") else _i
                    _revents.append({"cls": _ecls(_et, _e.get("summary")),
                                     "s": (_e.get("summary") or "")[:120],
                                     "recN": len(_recs),
                                     "hi": _lab2idx.get(_lab) if _lab is not None else -1})
                    _confseries.append([round(_conf_at_t(h["label"], _t), 3) for h in hyps])
                    _touched.append(_cum)
                # glyph pool for the matrix rain / record grid: record ids + descriptors
                _pool = (_pool + [p_.get("descriptor_name", "") for h in hyps
                                  for p_ in h["predictions"]]
                         + [h["label"] for h in hyps] + ["CO2RR", "ISAAC", "ΔE", "Cu", "Au"])
                _pool = [str(x) for x in _pool if x][:120] or ["ISAAC"]
                _replay_payload = {
                    "events": _revents, "hyps": _rhyps, "confSeries": _confseries,
                    "hypCreatedAt": _created_at, "pool": _pool, "touchedBy": _touched,
                }
                if not _revents:
                    st.info("No timeline yet — once the agent logs events, the replay fills in.")
                else:
                    _style = st.radio(
                        "Replay style",
                        ["✦ Constellation evolution", "🌱 Living organism"],
                        horizontal=True, label_visibility="collapsed", key="replay_style")
                    if _style.startswith("✦"):
                        # The SAME constellation as the Decision-journey tab, but played over
                        # the timeline. Reuse its node/link vocabulary; add t0 (appearance
                        # step), per-hyp confidence series, and decision-point annotations.
                        import bisect as _bisect

                        def _esc2(s):
                            return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                                    .replace(">", "&gt;"))
                        _N = len(_revents)
                        _ev_times = [_ep(e["created_at"]) for e in _chrono]

                        def _step_of(ts):
                            if not _ev_times:
                                return 0
                            return max(0, min(_N - 1, _bisect.bisect_right(_ev_times, ts) - 1))
                        _cn, _cl = [], []
                        for _j, _h in enumerate(hyps):
                            _cs = ([_confseries[_k][_j] for _k in range(_N)] if _N
                                   else [float(_h["confidence"] or 0)])
                            _tip = (f"<b>{_esc2(_h['label'])}</b> · {_esc2(_h['status'])} · "
                                    f"{round(float(_h['confidence'] or 0)*100)}%<br>"
                                    f"<span style='opacity:.85'>"
                                    f"{_esc2((_h.get('statement') or '')[:150])}</span>")
                            _cn.append({"id": _h["hypothesis_id"], "label": _h["label"] or "H",
                                        "kind": "hyp", "conf": float(_h["confidence"] or 0),
                                        "status": _h["status"],
                                        "color": _hcolor.get(_h["label"], "#C97A3C"),
                                        "tip": _tip, "t0": _created_at.get(_h["label"], 0),
                                        "cs": [round(_c, 3) for _c in _cs]})
                        for _h in hyps:
                            for _p in _h["predictions"]:
                                _pid = _p["prediction_id"]
                                _pt0 = (_step_of(_ep(_p.get("created_at")))
                                        if _p.get("created_at")
                                        else _created_at.get(_h["label"], 0))
                                _vt0 = (_step_of(_ep(_p.get("updated_at")))
                                        if _p.get("updated_at") else _pt0)
                                _ptip = (f"<b>{_esc2(_p.get('descriptor_name'))}</b> "
                                         f"<span style='opacity:.7'>(prediction · "
                                         f"{_esc2(_h['label'])})</span><br>verdict: "
                                         f"{_esc2(_p.get('verdict') or '—')} "
                                         f"({_esc2(_p.get('strength') or '—')})")
                                _cn.append({"id": _pid,
                                            "label": _p.get("descriptor_name") or "",
                                            "kind": "pred", "verdict": _p.get("verdict"),
                                            "strength": _p.get("strength"), "tip": _ptip,
                                            "t0": _pt0})
                                _cl.append({"source": _pid, "target": _h["hypothesis_id"],
                                            "rel": "pred", "verdict": _p.get("verdict"),
                                            "strength": _p.get("strength"), "t0": _vt0})
                                for _rid in (_p.get("evidence_record_ids") or [])[:6]:
                                    _enid = _rid + "|" + _pid
                                    _mat = prov.get(_rid, {}).get("material", "")
                                    _etip = (f"<b>evidence record</b><br>{_esc2(_mat[:60])}"
                                             f"<br><code>{_esc2(_rid)}</code>")
                                    _cn.append({"id": _enid, "label": _rid, "kind": "evid",
                                                "tip": _etip, "t0": _vt0})
                                    _cl.append({"source": _enid, "target": _pid,
                                                "rel": "evid", "t0": _vt0})
                                for _ci, _cr in enumerate((_p.get("compute_runs") or [])[:4]):
                                    _eng = _cr.get("engine") or _cr.get("backend") or "compute"
                                    _cid = f"calc{_ci}|{_pid}"
                                    _crt0 = (_step_of(_ep(_cr.get("created_at")))
                                             if _cr.get("created_at") else _vt0)
                                    _ctip = (f"<b>computation</b><br>{_esc2(_eng)}"
                                             f"<br>status: {_esc2(_cr.get('status') or '?')}")
                                    _cn.append({"id": _cid, "label": _eng, "kind": "calc",
                                                "tip": _ctip, "t0": _crt0})
                                    _cl.append({"source": _cid, "target": _pid,
                                                "rel": "calc", "t0": _crt0})
                        for _r in relations:
                            _rt0 = (_step_of(_ep(_r.get("created_at")))
                                    if _r.get("created_at") else 0)
                            _cl.append({"source": _r["from_hypothesis_id"],
                                        "target": _r["to_hypothesis_id"],
                                        "rel": _r["relation_type"], "t0": _rt0})
                        _ev_index = brief.get("evidence_index", {})
                        _ds_ids = ((data["project"].get("dataset") or {})
                                   .get("record_ids")) or []
                        _cited_ids = {_rid for _h in hyps for _p in _h["predictions"]
                                      for _rid in (_p.get("evidence_record_ids") or [])}
                        for _rid in _ds_ids[:140]:
                            if _rid in _cited_ids:
                                continue
                            _cn.append({"id": "inv|" + _rid, "label": _rid,
                                        "kind": "involved", "t0": 0,
                                        "tip": f"<b>in-scope record</b><br><code>"
                                               f"{_esc2(_rid)}</code>"})
                        for _name, _v in list(_ev_index.items())[:200]:
                            _cn.append({"id": "scr|" + _name, "label": _name,
                                        "kind": "screened", "n": (_v or {}).get("n", 1),
                                        "t0": 0, "tip": f"<b>screened descriptor</b><br>"
                                                        f"{_esc2(_name)}"})
                        try:
                            _corpus = database.count_records()
                        except Exception:
                            _corpus = 0
                        # decision-point annotations: notable confidence swings + status flips
                        _annos = []
                        for _k in range(1, _N):
                            for _j, _h in enumerate(hyps):
                                _d = _confseries[_k][_j] - _confseries[_k - 1][_j]
                                if abs(_d) >= 0.06:
                                    _dir = "▲ ranked up" if _d > 0 else "▼ ranked down"
                                    _annos.append({
                                        "k": _k, "mag": abs(_d),
                                        "color": _hcolor.get(_h["label"], "#C97A3C"),
                                        "text": f"{_dir} · {_esc2(_h['label'])} → "
                                                f"{round(_confseries[_k][_j]*100)}%"})
                        for _i, _e in enumerate(_chrono):
                            if _e["event_type"] == "status_changed":
                                _lab = _hid2lab.get(_e.get("hypothesis_id"))
                                if _lab:
                                    _annos.append({
                                        "k": _i, "mag": 1.0,
                                        "color": _hcolor.get(_lab, "#C97A3C"),
                                        "text": f"✦ {_esc2(_lab)} — "
                                                f"{_esc2((_e.get('summary') or 'status changed')[:60])}"})
                        _by_k = {}
                        for _a in _annos:
                            if _a["k"] not in _by_k or _a["mag"] > _by_k[_a["k"]]["mag"]:
                                _by_k[_a["k"]] = _a
                        _annos = sorted(_by_k.values(), key=lambda a: a["k"])
                        _creplay = {"nodes": _cn, "links": _cl, "N": _N,
                                    "annotations": _annos, "captions": _revents,
                                    "corpus": {"records": _corpus or 0,
                                               "screened": len(_ev_index),
                                               "involved": len(set(_ds_ids)),
                                               "cited": len(_cited_ids)}}
                        components.html(_constellation_replay_html(
                            _creplay, st.session_state.ui_theme), height=600)
                        st.caption(f"{_N} timeline steps · ✦ **Constellation evolution** — the "
                                   "SAME map as the Decision-journey tab, played from the first "
                                   "hypothesis to now: data sits in the outer rings from the "
                                   "start, predictions/evidence/compute light up as they land, "
                                   "and each hypothesis migrates **inward as it ranks up**. The "
                                   "final frame is the live constellation. ▶ or scrub; the "
                                   "callouts mark the decision points.")
                    else:
                        _mode = "organism"
                        components.html(_replay_html(_replay_payload,
                                                     st.session_state.ui_theme, _mode),
                                        height=600)
                        st.caption(f"{len(_revents)} timeline steps · 🌱 **Living organism** — "
                                   "the discovery grows & prunes itself. Hit ▶ and let it grow. "
                                   "push further.")

            # ---- Decision journey — the scale & complexity of the reasoning ----
            with tabImpact:
                st.markdown("#### How the machine ranked these mechanisms")
                st.caption("In a single autonomous run, the agent screened the ISAAC "
                           "evidence corpus, posed competing mechanisms, tested falsifiable "
                           "predictions against real data **and** fresh supercomputer "
                           "calculations, and converged on a ranked answer — the full detail "
                           "is in the tabs to the right.")
                if not hyps:
                    st.caption("_No hypotheses yet._")
                else:
                    ev_idx = brief.get("evidence_index", {})
                    n_desc = len(ev_idx)
                    n_ev = len({rid for _h in hyps for _p in _h["predictions"]
                                for rid in (_p.get("evidence_record_ids") or [])})
                    try:
                        corpus = database.count_records()
                    except Exception:
                        corpus = 0
                    def _esc(s):
                        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                                .replace(">", "&gt;"))
                    _cnodes, _clinks = [], []
                    for _h in hyps:
                        _tip = (f"<b>{_esc(_h['label'])}</b> · {_esc(_h['status'])} · "
                                f"{round(float(_h['confidence'] or 0)*100)}%<br>"
                                f"<span style='opacity:.85'>{_esc((_h.get('statement') or '')[:150])}</span>")
                        _cnodes.append({"id": _h["hypothesis_id"], "label": _h["label"] or "H",
                                        "kind": "hyp", "conf": float(_h["confidence"] or 0),
                                        "status": _h["status"],
                                        "color": _hcolor.get(_h["label"], "#C97A3C"),
                                        "tip": _tip})
                    for _h in hyps:
                        for _p in _h["predictions"]:
                            _pid = _p["prediction_id"]
                            _ptip = (f"<b>{_esc(_p.get('descriptor_name'))}</b> "
                                     f"<span style='opacity:.7'>(prediction · {_esc(_h['label'])})</span>"
                                     f"<br>verdict: {_esc(_p.get('verdict') or '—')} "
                                     f"({_esc(_p.get('strength') or '—')})"
                                     f"<br>falsified if: {_esc((_p.get('falsification_criterion') or '—')[:120])}")
                            _cnodes.append({"id": _pid, "label": _p.get("descriptor_name") or "",
                                            "kind": "pred", "verdict": _p.get("verdict"),
                                            "strength": _p.get("strength"), "tip": _ptip})
                            _clinks.append({"source": _pid, "target": _h["hypothesis_id"],
                                            "rel": "pred", "verdict": _p.get("verdict"),
                                            "strength": _p.get("strength")})
                            for _rid in (_p.get("evidence_record_ids") or [])[:6]:
                                _enid = _rid + "|" + _pid
                                _mat = prov.get(_rid, {}).get("material", "")
                                _etip = (f"<b>evidence record</b><br>{_esc(_mat[:60])}"
                                         f"<br><code>{_esc(_rid)}</code>")
                                _cnodes.append({"id": _enid, "label": _rid, "kind": "evid",
                                                "tip": _etip})
                                _clinks.append({"source": _enid, "target": _pid, "rel": "evid"})
                            # COMPUTE provenance — a verdict grounded in a calc (UMA/VASP)
                            # is real evidence too; draw it so it isn't invisible.
                            for _ci, _cr in enumerate((_p.get("compute_runs") or [])[:4]):
                                _eng = _cr.get("engine") or _cr.get("backend") or "compute"
                                _cid = f"calc{_ci}|{_pid}"
                                _ctip = (f"<b>computation</b><br>{_esc(_eng)}"
                                         f"<br>status: {_esc(_cr.get('status') or '?')}")
                                _cnodes.append({"id": _cid, "label": _eng, "kind": "calc",
                                                "tip": _ctip})
                                _clinks.append({"source": _cid, "target": _pid, "rel": "calc"})
                    for _r in relations:
                        _clinks.append({"source": _r["from_hypothesis_id"],
                                        "target": _r["to_hypothesis_id"], "rel": _r["relation_type"]})
                    # INNER faint ring — records actually INVOLVED in this project (the
                    # declared dataset-of-interest scope), distinct from the full screened
                    # corpus (outer) and the few CITED ones (bright). Communicates the funnel:
                    # all records → screened → in-scope/involved → cited.
                    _ds_ids = ((data["project"].get("dataset") or {}).get("record_ids")) or []
                    _cited_ids = {_rid for _h in hyps for _p in _h["predictions"]
                                  for _rid in (_p.get("evidence_record_ids") or [])}
                    _n_involved = len(set(_ds_ids))
                    for _rid in _ds_ids[:140]:
                        if _rid in _cited_ids:
                            continue   # cited records are already bright nodes
                        _itip = (f"<b>in-scope record</b> (declared dataset)"
                                 f"<br><code>{_esc(_rid)}</code>")
                        _cnodes.append({"id": "inv|" + _rid, "label": _rid,
                                        "kind": "involved", "tip": _itip})
                    # the dense screened-descriptor field — the outer-ring complexity
                    for _name, _v in list(ev_idx.items())[:200]:
                        _stip = (f"<b>screened descriptor</b><br>{_esc(_name)}"
                                 f"<br>{(_v or {}).get('n', 1)} records in the corpus")
                        _cnodes.append({"id": "scr|" + _name, "label": _name, "kind": "screened",
                                        "n": (_v or {}).get("n", 1), "tip": _stip})
                    components.html(_constellation_html(
                        {"nodes": _cnodes, "links": _clinks,
                         "corpus": {"records": corpus or 0, "screened": n_desc,
                                    "involved": _n_involved, "cited": n_ev}},
                        st.session_state.ui_theme), height=580)
                    st.caption("**Drag to rotate.** Concentric narrowing: the faint OUTER "
                               "field is every descriptor screened → the **in-scope ring** "
                               "(blue) is the declared dataset records this project is about "
                               "→ bright nodes are CITED evidence (grey) and computations "
                               "(purple) → predictions → the hypothesis stars, each drawn "
                               "toward the centre by its confidence (`competes_with` ties "
                               "push losers out). A prediction with NO grey/purple node is a "
                               "verdict not yet cited to data. Every position, size and "
                               "colour is a real value.")

                    # ---- The river of belief: confidence evolution over the run ----
                    # Real confidence history (first-class snapshots; legacy projects
                    # are backfilled from the event log on first read).
                    _hmap = {h["hypothesis_id"]: h for h in hyps}

                    def _epoch(_dt):
                        return _dt.timestamp() if hasattr(_dt, "timestamp") else 0.0
                    _snaps = discovery.get_confidence_history(pid, owner)
                    # Per-hypothesis confidence as a step function over real time.
                    _snap_by_h = {hid: [] for hid in _hmap}
                    for _sn in _snaps:
                        if _sn["hypothesis_id"] in _snap_by_h:
                            _snap_by_h[_sn["hypothesis_id"]].append(
                                (_epoch(_sn["created_at"]), float(_sn["confidence"] or 0)))
                    for _hid in _snap_by_h:
                        _snap_by_h[_hid].sort()

                    def _conf_at(_hid, _t):
                        # last snapshot value at-or-before _t; None => hypothesis not
                        # yet born at that point (band absent, so it grows in on birth)
                        _c = None
                        for (_st, _cv) in _snap_by_h[_hid]:
                            if _st <= _t:
                                _c = _cv
                            else:
                                break
                        return _c

                    # x-axis is ORDINAL over every change (events + confidence
                    # snapshots), NOT wall-clock — so runs days apart don't squash the
                    # within-session changes, and the river advances on every change.
                    _chrono = [e for e in reversed(events)
                               if hasattr(e.get("created_at"), "timestamp")]
                    _ev_times = [_epoch(e["created_at"]) for e in _chrono]
                    _snap_times = [t for pts in _snap_by_h.values() for (t, _c) in pts]
                    _ticks = sorted(set(_ev_times) | set(_snap_times))
                    if not _ticks:
                        _ticks = [0.0]
                    _N = len(_ticks)
                    _steps = []
                    for _i, _t in enumerate(_ticks):
                        _row = {"t": _i / (_N - 1) if _N > 1 else 0.0}
                        for _hid, _h in _hmap.items():
                            _cv = _conf_at(_hid, _t)
                            _row[_h["label"]] = _cv if _cv is not None else 0.0
                        _steps.append(_row)
                    _tick_index = {round(_t, 6): _i for _i, _t in enumerate(_ticks)}
                    _marker_types = ("prediction_evaluated", "compute_running",
                                     "compute_submitted", "evidence_ingested",
                                     "next_experiment_proposed")
                    # label → hypothesis_id, so each river moment can name the constellation
                    # subgraphs it moved (linked-brushing join key).
                    _label2hid = {_h["label"]: _h["hypothesis_id"] for _h in hyps}
                    _markers = []
                    for e in _chrono:
                        if e["event_type"] not in _marker_types:
                            continue
                        _ti = _tick_index.get(round(_epoch(e["created_at"]), 6), 0)
                        _t_pos = _ti / (_N - 1) if _N > 1 else 0.0
                        # Which hypotheses moved AT this tick (vs the one before) — this is
                        # exactly what the hover popup explains: who shifted and by how much.
                        _deltas = []
                        if _ti > 0:
                            for _h in hyps:
                                _before = _steps[_ti - 1].get(_h["label"], 0.0)
                                _after = _steps[_ti].get(_h["label"], 0.0)
                                _d = _after - _before
                                if abs(_d) >= 0.005:
                                    _deltas.append({"label": _h["label"], "from": _before,
                                                    "to": _after, "delta": _d})
                        _deltas.sort(key=lambda z: -abs(z["delta"]))
                        _summary = (e.get("summary") or e.get("detail") or "").strip()
                        if len(_summary) > 240:
                            _summary = _summary[:237] + "…"
                        # The hypotheses this moment moved — the constellation lights up
                        # exactly these subgraphs when the moment is hovered.
                        _hyp_ids = [_label2hid[z["label"]] for z in _deltas
                                    if z.get("label") in _label2hid]
                        _markers.append({"t": _t_pos, "type": e["event_type"],
                                         "summary": _summary, "deltas": _deltas,
                                         "hyp_ids": _hyp_ids})
                    st.markdown("**Confidence trajectories** — how confidence in each mechanism "
                                "evolved as evidence arrived")
                    components.html(_river_html(
                        {"steps": _steps,
                         "hyps": [{"id": _h["hypothesis_id"], "label": _h["label"],
                                   "status": _h["status"],
                                   "conf": float(_h["confidence"] or 0),
                                   "color": _hcolor.get(_h["label"], "#C97A3C"),
                                   "lead": _h["label"] == _leader_label} for _h in hyps],
                         "markers": _markers},
                        st.session_state.ui_theme), height=320)
                    st.caption("Each ribbon is a hypothesis; thickness = confidence over the "
                               "run — the leader swells, eliminated mechanisms thin out. Dashed "
                               "marks are where evidence or compute landed — **hover one** to see "
                               "what happened and which beliefs moved (and by how much). On "
                               "resume, new evidence extends each trajectory: the discovery's "
                               "evolution, live.")

            # ---- E: Evidence index (by descriptor) + discrimination matrix ----
            with tabE:
                st.markdown("**Discrimination matrix** — what each hypothesis "
                            "predicts for a measurable (drives next-experiment choice)")
                matrix = brief.get("discrimination_matrix", [])
                if matrix:
                    # `expected_by_hypothesis` is the agent's `discriminates` — defend
                    # against malformed shapes (strings / missing keys) so a bad write
                    # can't crash the project view.
                    def _disc_entries(m):
                        return [e for e in (m.get("expected_by_hypothesis") or [])
                                if isinstance(e, dict) and e.get("hypothesis_label")]
                    labels = sorted({e["hypothesis_label"]
                                     for m in matrix for e in _disc_entries(m)})
                    rows = []
                    for m in matrix:
                        row = {"Prediction": m.get("prediction"),
                               "Descriptor": m.get("descriptor")}
                        exp = {e["hypothesis_label"]: e.get("expected")
                               for e in _disc_entries(m)}
                        for lb in labels:
                            row[lb] = exp.get(lb, "—")
                        rows.append(row)
                    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
                else:
                    st.caption("_No discriminating predictions yet — the agent adds "
                               "`discriminates` when it proposes a prediction._")

                st.divider()
                ev_idx = brief.get("evidence_index", {})
                st.markdown(f"**Evidence index** — what already exists for "
                            f"`{', '.join(brief.get('elements', []))}` in the records DB, "
                            f"keyed by descriptor ({len(ev_idx)} descriptors). "
                            f"Reaction is *annotated*, not filtered.")
                if ev_idx:
                    erows = [{"Descriptor": k, "Records": v["n"],
                              "exact/baseline/analog":
                                  f"{v['by_role'].get('exact_system',0)}/"
                                  f"{v['by_role'].get('baseline',0)}/"
                                  f"{v['by_role'].get('analog',0)}",
                              "Reactions": ", ".join(v["reactions"]) or "—",
                              "Methods": ", ".join(v["methods"][:4]) or "—"}
                             for k, v in sorted(ev_idx.items(), key=lambda kv: -kv[1]["n"])]
                    st.dataframe(pd.DataFrame(erows), width='stretch', hide_index=True)
                    st.caption("The agent queries `GET /projects/{id}/evidence?"
                               "descriptor=<name>` for the full per-record list before "
                               "ever concluding 'no data'. `output_quantity`/`functional` "
                               "per record = the methodological-compatibility ledger.")
                else:
                    st.caption("_No element-matched evidence found (or records DB "
                               "unavailable)._")

            # ---- A: Hypotheses, how they were formed, their predictions ----
            with tabA:
                # Lead with the at-a-glance scoreboard — every hypothesis, its predictions,
                # and how they resolve — then the relations graph and per-hypothesis detail.
                _render_scoreboard(hyps, _hcolor)
                st.divider()
                if relations:
                    st.markdown("**Hypothesis relations** (the graph)")
                    st.dataframe(pd.DataFrame([
                        {"From": _hlabel.get(r["from_hypothesis_id"], "?"),
                         "Relation": r["relation_type"],
                         "To": _hlabel.get(r["to_hypothesis_id"], "?"),
                         "Discriminating observable": r.get("discriminating_observable"),
                         "Note": r.get("note")} for r in relations]),
                        width='stretch', hide_index=True)
                    st.caption("A `supersedes` should name the observable on which the "
                               "new hypothesis predicts differently — that's what makes "
                               "it a new hypothesis, not a refinement of the old one.")
                    st.divider()
                if not hyps:
                    st.caption("_No hypotheses yet._")
                for h in hyps:
                    _ver = h.get("version") or 1
                    _vtag = f" · v{_ver}" if _ver > 1 else ""
                    with st.expander(f"{h['label'] or 'H'} · {h['status']} · "
                                     f"conf {float(h['confidence'] or 0):.2f}{_vtag}"):
                        st.write(h["statement"])
                        if _ver > 1:
                            st.caption(f"Refined in place to version {_ver} "
                                       "(same hypothesis, sharpened — not a new node).")
                        _hsc = discovery.compute_hypothesis_score(h)
                        st.caption(f"Confidence {float(h['confidence'] or 0):.2f} — "
                                   f"computed from verdicts: {_hsc['note']}")
                        st.markdown("**How it was formed / provenance**")
                        org = h.get("origin")
                        if isinstance(org, dict) and org:
                            if org.get("type"):
                                st.write(f"Source type: `{org['type']}`")
                            if org.get("summary"):
                                st.write(org["summary"])
                            if org.get("reasoning"):
                                st.caption(org["reasoning"])
                            if org.get("sources"):
                                st.markdown("Sources:")
                                for s in org["sources"]:
                                    st.markdown(f"- {s}")
                        elif isinstance(org, (list, dict)) and org:
                            st.json(org)
                        elif org:
                            st.write(str(org))
                        else:
                            st.caption("_Origin not documented yet (the agent fills this)._")
                        if h.get("mechanism"):
                            st.markdown("**Mechanism**")
                            mech = h["mechanism"]
                            if isinstance(mech, (list, dict)):
                                st.json(mech, expanded=False)
                            else:
                                st.write(str(mech))
                        st.markdown(f"**Falsifying predictions** "
                                    f"({len(h['predictions'])}) — each would, if it failed, "
                                    f"weaken this hypothesis")
                        if not h["predictions"]:
                            st.caption("_No predictions yet._")
                        for p in h["predictions"]:
                            ws = p.get("work_status") or "awaiting_evidence"
                            vd = p.get("verdict")
                            icon = _VERDICT_ICON.get(vd, "•")
                            nev = len(p.get("evidence_record_ids") or [])
                            cr = p.get("compute_runs") or []
                            st.markdown(
                                f"{icon} **{p.get('descriptor_name')}** — "
                                f"{vd or ws}"
                                f"{(' (' + p['strength'] + ')') if p.get('strength') else ''} "
                                f"· evidence: {nev} · compute: {len(cr)} · `{ws}`")
                            if p.get("direction") or p.get("falsification_criterion"):
                                st.caption(f"↳ expects **{p.get('direction') or '—'}**; "
                                           f"**falsified if:** {p.get('falsification_criterion') or '—'}")
                            # how this prediction was produced (provenance)
                            _po = p.get("origin")
                            if isinstance(_po, dict) and _po:
                                _bits = []
                                if _po.get("type"):
                                    _bits.append(f"`{_po['type']}`")
                                if _po.get("summary"):
                                    _bits.append(_po["summary"])
                                st.caption("↳ **how produced:** " + " — ".join(_bits)
                                           + (f"  ·  {_po['reasoning']}" if _po.get("reasoning") else "")
                                           + ("  ·  sources: " + ", ".join(str(s) for s in _po["sources"])
                                              if _po.get("sources") else ""))
                            elif _po:
                                st.caption(f"↳ **how produced:** {_po}")
                            if p.get("rationale"):
                                st.caption(f"↳ **verdict reasoning:** {p['rationale']}")
                            if nev:
                                ev_txt = ", ".join(
                                    f"`{rid}`·{prov.get(rid, {}).get('material', '?')[:18]}"
                                    for rid in (p.get("evidence_record_ids") or [])[:6])
                                st.caption(f"↳ evidence: {ev_txt}")
                            for r in cr:
                                met = ", ".join(f"{k}={v2}" for k, v2 in (r.get("metrics") or {}).items())
                                jid = f"job {r['slurm_job_id']}" if r.get("slurm_job_id") else ""
                                st.caption(f"   • {r.get('backend') or 'compute'} "
                                           f"[{r.get('status')}] {jid} {met}")

            # ---- B: Validation board — predictions by workflow state ----
            with tabB:
                # The per-hypothesis scoreboard now leads the Hypotheses & provenance tab;
                # here the board drills into predictions by workflow state.
                groups = {k: [] for k in ["evaluated", "compute_running",
                                          "compute_submitted", "more_work_pending",
                                          "awaiting_evidence"]}
                for h in hyps:
                    for p in h["predictions"]:
                        groups.setdefault(p.get("work_status") or "awaiting_evidence",
                                          []).append((h, p))
                _board_section("✅ Evaluated — validated / invalidated by data",
                               groups["evaluated"], prov, show_verdict=True)
                _board_section("⏳ Compute running", groups["compute_running"], prov)
                _board_section("📤 Compute submitted (queued)",
                               groups["compute_submitted"], prov)
                _board_section("🔧 More work pending", groups["more_work_pending"], prov)
                _board_section("📥 Awaiting evidence", groups["awaiting_evidence"], prov)
                nx = proj.get("next_experiment")
                if nx:
                    st.divider()
                    st.markdown("#### 🧪 Next experiment (proposed)")
                    _pal = branding.palette(st.session_state.ui_theme)
                    # The next_experiment payload is free-form — agents use EITHER a single
                    # `title` OR structured descriptor/method/facility (both seen live). Build
                    # the headline from whichever is present; never emit an empty markdown
                    # skeleton ('**** — @'), and fall back to the rationale prose when no
                    # headline field is set. Themed accent panel (not st.success) so it's
                    # elegant on light AND dark and doesn't read as a green 'success'.
                    _title = (nx.get("title") or "").strip()
                    _desc = (nx.get("descriptor") or "").strip()
                    _method = (nx.get("method") or "").strip()
                    _facility = (nx.get("facility") or "").strip()
                    _rationale = (nx.get("rationale") or "").strip()
                    _where = " @ ".join(x for x in [_method, _facility] if x)
                    _headline = _title or " — ".join(x for x in [_desc, _where] if x)
                    if _headline:
                        _body = f"<div style='font-weight:600'>{html.escape(_headline)}</div>"
                        if _rationale:
                            _body += (f"<div style='color:{_pal['muted']};font-size:0.92em;"
                                      f"margin-top:0.45rem;line-height:1.55'>"
                                      f"{html.escape(_rationale)}</div>")
                    elif _rationale:
                        _body = (f"<div style='line-height:1.6'>"
                                 f"{html.escape(_rationale)}</div>")
                    else:
                        _body = (f"<div style='color:{_pal['muted']}'>A next experiment is "
                                 "proposed (no details recorded yet).</div>")
                    st.markdown(
                        f"<div style='background:{_pal['accent_soft']};"
                        f"border:1px solid {_pal['border_soft']};"
                        f"border-left:3px solid {_pal['accent']};border-radius:8px;"
                        f"padding:0.8rem 1rem;color:{_pal['text']}'>{_body}</div>",
                        unsafe_allow_html=True)
                    if nx.get("predicted_outcomes"):
                        st.dataframe(pd.DataFrame(nx["predicted_outcomes"]),
                                     width='stretch', hide_index=True)

            # ---- C: Compute ledger — every MLflow run, what & why ----
            with tabC:
                runs = []
                for e in events:
                    if e.get("mlflow_run_url"):
                        runs.append({"When": _fmt(e.get("created_at")),
                                     "Event": e["event_type"], "Summary": e["summary"],
                                     "MLflow": e["mlflow_run_url"]})
                # Compute runs (multi-run per prediction; the real lifecycle)
                crows = []
                for h in hyps:
                    for p in h["predictions"]:
                        for r in (p.get("compute_runs") or []):
                            met = r.get("metrics") or {}
                            crows.append({
                                "Prediction": f"{h['label']} / {p.get('descriptor_name')}",
                                "Backend": r.get("backend"), "Status": r.get("status"),
                                "Resource": r.get("resource"),
                                "Slurm": r.get("slurm_job_id"),
                                "Metrics": ", ".join(f"{k}={v}" for k, v in met.items())[:60],
                                "MLflow": r.get("mlflow_run_url") or ""})
                if crows:
                    st.markdown("**Compute runs**")
                    st.dataframe(pd.DataFrame(crows), width='stretch', hide_index=True,
                                 column_config={"MLflow": st.column_config.LinkColumn("MLflow")})
                if runs:
                    st.markdown("**MLflow-linked events**")
                    st.dataframe(pd.DataFrame(runs), width='stretch', hide_index=True,
                                 column_config={"MLflow": st.column_config.LinkColumn("MLflow")})
                if not crows and not runs:
                    st.caption("_No compute runs yet. The agent registers runs "
                               "(POST /predictions/{id}/runs) as it submits/finishes._")

            # ---- Journal — compact, scrollable; detail on demand ----
            with tabJ:
                if not events:
                    st.caption("_No activity yet._")
                else:
                    st.caption(f"{len(events)} reasoning steps — newest first. "
                               "Scroll the log; pick a step to read its full detail.")
                    jdf = pd.DataFrame([
                        {"#": len(events) - i, "time": _fmt(e.get("created_at")),
                         "type": e["event_type"], "summary": e["summary"]}
                        for i, e in enumerate(events)])
                    st.dataframe(jdf, height=340, width='stretch', hide_index=True)
                    opts = [f"{len(events) - i} · {e['event_type']} · {e['summary'][:50]}"
                            for i, e in enumerate(events)]
                    sel = st.selectbox("Inspect step", opts, key=f"jsel_{pid}")
                    e = events[opts.index(sel)] if sel in opts else events[0]
                    if e.get("detail"):
                        st.markdown(e["detail"])
                    if e.get("evidence_record_ids"):
                        st.caption("Evidence: " + ", ".join(e["evidence_record_ids"]))
                    if e.get("mlflow_run_url"):
                        st.markdown(f"[MLflow run]({e['mlflow_run_url']})")
                    if not (e.get("detail") or e.get("evidence_record_ids")
                            or e.get("mlflow_run_url")):
                        st.caption("_(no extra detail recorded for this step)_")

            # ---- Diagnostics, BELOW the decision journey + river of belief ----
            # (status line + resumable, rigor & next steps, raw briefing) — kept out of the
            # top flow so the goal → leader → ranking → constellation reads as one piece.
            st.divider()
            _status_line()
            _diagnostics_compact()

        # ---- Project list vs detail ----
        if st.session_state.discovery_project is None:
            st.subheader("My Projects")
            if st.button("🔄 Refresh"):
                st.rerun()
            projects = discovery.list_projects(_DISC_OWNER)

            # First-landing onboarding: how to point YOUR agent at the platform.
            _manifest = discovery.get_manifest()
            _gs = _manifest.get("getting_started", {})
            with st.expander("🔌 Connect your agent — start here",
                             expanded=(not projects)):
                st.write(_gs.get("what", ""))
                for _i, _s in enumerate(_gs.get("steps", []), 1):
                    st.markdown(f"**{_i}.** {_s}")
                st.caption("Copy this into your agent (any LLM with web access), "
                           "replacing the token placeholder:")
                st.code(_gs.get("agent_prompt", ""), language="text")
                st.caption("Your agent reads the self-describing manifest and "
                           "configures itself — you don't need to know the API. "
                           "Get a token from the **API Keys** page.")

            # Transparency: show EXACTLY what the agent is instructed with on connect.
            # Rendered from the same get_manifest() the agent fetches — never a
            # paraphrase, so what you read here is verbatim what the agent receives.
            _man_url = _manifest.get("base_path", "") + "/discovery/manifest"
            with st.expander("🔎 What the agent is told — the full operating manual"):
                st.caption("Transparency: this is rendered live from the **same manifest "
                           "your agent fetches** on connect (no auth needed to read it: "
                           f"`GET {_man_url}`). Nothing the agent receives is hidden from "
                           "you here.")
                st.markdown(f"**{_manifest.get('name','')}** · manifest "
                            f"`v{_manifest.get('version','')}`")

                _method = _manifest.get("method", {})
                if _method:
                    st.markdown("##### 🧭 The method — the scientific contract it must follow")
                    st.caption(_method.get("_what", ""))
                    for _step in _method.get("loop", []):
                        st.markdown(f"- {_step}")
                    if _method.get("non_negotiables"):
                        st.markdown("**Non-negotiables:**")
                        for _nn in _method["non_negotiables"]:
                            st.markdown(f"- {_nn}")

                if _manifest.get("prime_directive"):
                    st.markdown("##### ⚖️ Prime directive")
                    for _pd in _manifest["prime_directive"]:
                        st.markdown(f"- {_pd}")

                if _manifest.get("resume_protocol"):
                    st.markdown("##### 🔁 How a resuming agent rebuilds context")
                    st.caption(_manifest["resume_protocol"])

                _eps = _manifest.get("endpoints", [])
                if _eps:
                    st.markdown("##### 🔌 Endpoints it is given")
                    st.table([{"method": e.get("m"), "path": e.get("path"),
                               "purpose": e.get("purpose")} for e in _eps])

                _integ = _manifest.get("integrations", {})
                if _integ:
                    st.markdown("##### 🧰 Integrations it can reach")
                    for _k, _v in _integ.items():
                        if not isinstance(_v, dict):
                            st.markdown(f"- **{_k}** — {_v}")
                            continue
                        # Show a meaningful headline even when there's no `purpose`
                        # key (e.g. literature_search describes itself via provider /
                        # use_when), so the user sees WHAT it is and WHO provides it.
                        _prov = _v.get("provider")
                        _head = _v.get("purpose") or _v.get("use_when") or ""
                        _title = f"**{_k}**" + (f" — via {_prov}" if _prov else "")
                        st.markdown(f"- {_title}")
                        if _head:
                            st.caption(_head)
                        # surface the call shape if present (literature proxy)
                        _calls = [f"`{_v[_kk]}`" for _kk in ("submit", "poll")
                                  if _v.get(_kk)]
                        if _calls:
                            st.caption("How the agent calls it: " + " · ".join(_calls))

                st.markdown("##### 📦 The raw manifest (verbatim machine contract)")
                st.caption("Exactly the JSON the agent parses — every field, vocabulary "
                           "and shape. The companion narrative is "
                           "`portal/DISCOVERY_AGENT_PROTOCOL.md` in the repo.")
                st.json(_manifest, expanded=False)
                st.markdown(f"[Open the live manifest JSON ↗]({_man_url})")

            if not projects:
                st.caption("No projects yet. Create one below, or connect your agent above.")
            for p in projects:
                with st.container(border=True):
                    cols = st.columns([4, 1])
                    with cols[0]:
                        st.markdown(f"**{p['title']}**")
                        if p.get("goal"):
                            st.caption(p["goal"])
                        lead = p.get("leading_hypothesis")
                        leadtxt = (f"Leader: {lead['label']} "
                                   f"(conf {float(lead['confidence'] or 0):.2f})"
                                   if lead else "No hypotheses yet")
                        share_badge = ("" if p.get("is_owner", True)
                                       else f" · 🔗 shared by {p.get('owner_identity')}")
                        st.caption(f"{p['n_hypotheses']} hypotheses · {leadtxt} · "
                                   f"{p['status']}{share_badge}")
                    with cols[1]:
                        if st.button("Open", key=f"open_{p['project_id']}"):
                            st.session_state.discovery_project = p["project_id"]
                            st.rerun()

            st.divider()
            with st.expander("➕ New Project"):
                with st.form("new_discovery_project"):
                    t = st.text_input("Title *")
                    g = st.text_area("Goal")
                    ms = st.text_input("Material system", placeholder="e.g. Cu-Au")
                    rx = st.text_input("Reaction", placeholder="e.g. CO2RR")
                    if st.form_submit_button("Create project"):
                        if not t.strip():
                            st.error("Title is required.")
                        else:
                            new_id = discovery.create_project(
                                _DISC_OWNER, t.strip(), goal=g.strip() or None,
                                material_system=ms.strip() or None,
                                reaction=rx.strip() or None)
                            st.session_state.discovery_project = new_id
                            st.rerun()
        else:
            top_l, top_r = st.columns([4, 1])
            with top_l:
                if st.button("← Back to projects"):
                    st.session_state.discovery_project = None
                    st.rerun()
            pid = st.session_state.discovery_project
            _meta = discovery.get_project(pid, owner_identity=_DISC_OWNER)
            _is_owner = bool(_meta) and _meta["project"]["owner_identity"] == _DISC_OWNER
            with top_r:
                if not _is_owner and _meta:
                    st.caption(f"🔗 shared by {_meta['project']['owner_identity']}")
                elif _is_owner:
                    with st.popover("⋯ Manage"):
                        st.markdown("**Share (read-only) with another portal user**")
                        for s in (_meta["project"].get("shared_with") or []):
                            sc1, sc2 = st.columns([3, 1])
                            sc1.caption(f"{s['identity']} · {s['access']}")
                            if sc2.button("✕", key=f"unshare_{s['identity']}"):
                                discovery.unshare_project(pid, s["identity"],
                                                          owner_identity=_DISC_OWNER)
                                st.rerun()
                        with st.form(f"share_form_{pid}"):
                            who = st.text_input("Portal username to share with",
                                                placeholder="their login name")
                            if st.form_submit_button("Share") and who.strip():
                                discovery.share_project(pid, who.strip(),
                                                        owner_identity=_DISC_OWNER)
                                st.rerun()
                        st.divider()
                        st.caption("Delete this project and all its history — cannot be undone.")
                        if st.button("🗑 Delete project", type="secondary"):
                            if discovery.delete_project(pid, owner_identity=_DISC_OWNER,
                                                        is_admin=user_is_admin):
                                st.session_state.discovery_project = None
                                st.rerun()
                            else:
                                st.error("Delete failed (not yours or not found).")
            # Live auto-refresh when the Streamlit build supports it; otherwise a
            # manual refresh button keeps it functional on older versions.
            if hasattr(st, "fragment"):
                st.caption("Live — auto-refreshing every 5s.")
                st.fragment(run_every=5)(lambda: _discovery_detail(pid, _DISC_OWNER))()
            else:
                if st.button("🔄 Refresh"):
                    st.rerun()
                _discovery_detail(pid, _DISC_OWNER)


# =============================================================================
# PAGE: About
# =============================================================================
elif page == "About":
    st.markdown("""
    Features:
    - **Dashboard**: Database health, record stats, and access metrics at a glance
    - **Ontology Editor**: Browse and edit the ISAAC vocabulary
    - **Record Validator**: Validate JSON records against the schema and save to database
    - **Record Form**: Manually create ISAAC records
    - **Saved Records**: View and manage records in the database
    - **API Keys**: Generate and manage API keys for programmatic access
    - **API Documentation**: REST API reference for programmatic access
    """)
    st.markdown("**Schema version: ISAAC AI-Ready Record v1.05**")

# =============================================================================
# FOOTER: Partner & DOE logos on every page
# =============================================================================
branding.render_footer(st.session_state.ui_theme)
