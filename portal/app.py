import streamlit as st
import pandas as pd
import json
import requests
import ontology
import database
import branding
import agent
import discovery
import os
import re
import importlib
import streamlit.components.v1 as components
from datetime import datetime, timezone, timedelta

importlib.reload(ontology)

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

# ISAAC logo + design system at the top of every page
branding.render_header(st.session_state.ui_theme)

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

# Discovery page (hypothesis-reasoning workbench) — feature-gated: only shown when
# the isolated discovery DB is configured AND either the global DISCOVERY_ENABLED
# flag is set or the viewer is an admin. Lets us merge to main and demo to a
# limited audience before flipping it on for everyone.
if database.is_discovery_db_configured() and (
        os.environ.get("DISCOVERY_ENABLED", "").lower() in ("1", "true", "yes", "on")
        or user_is_admin):
    PAGES.insert(PAGES.index("About"), "Discovery")

# --- Top navigation bar: hamburger menu + theme toggle + DB status + user info ---
nav_col, theme_col, status_col, user_col = st.columns([5, 1, 1, 2])
with theme_col:
    _is_dark = st.session_state.ui_theme == "dark"
    if st.button("Light" if _is_dark else "Dark", key="theme_toggle",
                 use_container_width=True, help="Switch between dark and light mode"):
        st.session_state.ui_theme = "light" if _is_dark else "dark"
        st.query_params["theme"] = st.session_state.ui_theme
        st.rerun()
with nav_col:
    with st.popover("Menu"):
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
        # Refresh button
        if st.button("Refresh"):
            st.rerun()

        try:
            record_count = database.count_records()
            st.write(f"Total records: **{record_count}**")

            if record_count > 0:
                records, _total = database.list_records(limit=50)

                # Display as table
                df = pd.DataFrame(records)
                df.columns = ["Record ID", "Type", "Domain", "Created At"]
                st.dataframe(df, width='stretch')

                # View record detail
                st.divider()
                st.subheader("View Record Detail")

                record_ids = [r['record_id'] for r in records]
                selected_id = st.selectbox("Select Record", record_ids)

                if selected_id:
                    record_data = database.get_record(selected_id)
                    if record_data:
                        st.json(record_data)

                        # Download button
                        json_str = json.dumps(record_data, indent=2)
                        st.download_button(
                            label="Download JSON",
                            data=json_str,
                            file_name=f"isaac_record_{selected_id}.json",
                            mime="application/json"
                        )

                        # Record deletion is intentionally NOT available from the
                        # web interface. Deleting a record is an irreversible,
                        # high-trust operation; it is exposed ONLY through the
                        # admin-authenticated API (DELETE /portal/api/records/<id>,
                        # which validates the Bearer-token admin group and archives
                        # the record to history). The web identity is proxy-header
                        # derived and must never gate destructive actions.
            else:
                st.info("No records found. Create records using the Record Validator or Record Form.")

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
                    # for the authenticated caller. (C3)
                    _ttl_days = 90
                    _expires = (datetime.now(timezone.utc) + timedelta(days=_ttl_days)).isoformat()
                    create_resp = requests.post(
                        f"{authentik_api_url}/api/v3/core/tokens/",
                        headers=admin_headers,
                        json={
                            "identifier": identifier,
                            "intent": "api",
                            "user": user_pk,
                            "description": f"ISAAC Portal API key for {current_username}",
                            "expiring": True,
                            "expires": _expires,
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

                    st.success(f"API key created (expires in {_ttl_days} days). Copy it now — it will not be shown again.")
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
                list_resp = requests.get(
                    f"{authentik_api_url}/api/v3/core/tokens/",
                    headers=admin_headers,
                    params={"user__pk": user_pk, "intent": "api"},
                    timeout=5,
                )
                list_resp.raise_for_status()

                keys = [
                    t for t in list_resp.json().get("results", [])
                    if t.get("identifier", "").startswith(f"isaac-api-{_safe_username}-")
                ]

                if not keys:
                    st.info("You have no API keys. Generate one above.")
                else:
                    for key_info in keys:
                        ident = key_info["identifier"]
                        created = key_info.get("created", "unknown")
                        col1, col2 = st.columns([4, 1])
                        with col1:
                            st.text(f"{ident}  (created: {created})")
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

        _STATUS_COLORS = {
            "supported": "#2e7d32", "needs_more_data": "#f9a825",
            "eliminated": "#c62828", "proposed": "#90a4ae",
            "superseded": "#607d8b",
        }
        _VERDICT_ICON = {"supports": "✅", "contradicts": "❌",
                         "neutral": "➖", "insufficient": "❓"}

        def _bar(label, statement, confidence, status):
            c = float(confidence or 0.0)
            color = _STATUS_COLORS.get(status, "#90a4ae")
            pct = max(0, min(100, int(round(c * 100))))
            return (
                f"<div style='margin:4px 0'>"
                f"<div style='font-size:0.85em'><b>{label or ''}</b> "
                f"<span style='color:#666'>{(statement or '')[:90]}</span> "
                f"<span style='float:right;color:#666'>{status} · {c:.2f}</span></div>"
                f"<div style='background:#eee;border-radius:4px;height:14px;width:100%'>"
                f"<div style='background:{color};width:{pct}%;height:14px;"
                f"border-radius:4px'></div></div></div>")

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
            brief = discovery.get_briefing(pid, owner) or {}

            # ---------- BRIEFING HEADER (the universal-truth digest) ----------
            st.markdown(f"### {proj['title']}")
            if proj.get("goal"):
                st.info(f"🎯 **Goal:** {proj['goal']}")
            meta = " · ".join(filter(None, [
                proj.get("material_system"), proj.get("reaction"),
                f"status: {proj.get('status')}"]))
            if meta:
                st.caption(meta)

            settled = brief.get("settled", {"supported": [], "eliminated": []})
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Hypotheses", len(hyps))
            c2.metric("Supported", len(settled.get("supported", [])))
            c3.metric("Eliminated", len(settled.get("eliminated", [])))
            c4.metric("Validated preds", len(brief.get("validated_predictions", [])))
            c5.metric("Compute running", len(brief.get("pending_compute", [])))

            st.markdown("**Hypothesis ranking** — bar length = confidence, colour = status")
            st.markdown("".join(_bar(h["label"], h["statement"], h["confidence"],
                                     h["status"]) for h in hyps) or "_No hypotheses yet._",
                        unsafe_allow_html=True)

            with st.expander("🧭 Briefing — exactly what the agent reads as ground truth"):
                st.caption("Server-curated digest (not the full firehose). The agent "
                           "reconciles its reasoning to this at the start of every turn; "
                           "if a change isn't written back here, it didn't happen.")
                st.json(brief)

            preds = [p for h in hyps for p in h["predictions"]]
            evidence_ids = sorted({rid for p in preds
                                   for rid in (p.get("evidence_record_ids") or [])})
            prov = discovery.resolve_record_summaries(evidence_ids)

            tabA, tabB, tabC, tabJ = st.tabs([
                "🧪 Hypotheses & provenance", "✅ Validation board",
                "📊 Compute ledger", "📓 Journal"])

            # ---- A: Hypotheses, how they were formed, their predictions ----
            with tabA:
                if not hyps:
                    st.caption("_No hypotheses yet._")
                for h in hyps:
                    with st.expander(f"{h['label'] or 'H'} · {h['status']} · "
                                     f"conf {float(h['confidence'] or 0):.2f}"):
                        st.write(h["statement"])
                        if h.get("confidence_basis"):
                            st.caption(f"Confidence basis: {h['confidence_basis']}")
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
                        elif org:
                            st.json(org)
                        else:
                            st.caption("_Origin not documented yet (the agent fills this)._")
                        if h.get("mechanism"):
                            st.markdown("**Mechanism**")
                            st.json(h["mechanism"], expanded=False)
                        st.markdown("**Predictions**")
                        if h["predictions"]:
                            st.dataframe(pd.DataFrame(_pred_row(p, prov)
                                                      for p in h["predictions"]),
                                         width='stretch', hide_index=True)
                        else:
                            st.caption("_No predictions yet._")

            # ---- B: Validation board — predictions by workflow state ----
            with tabB:
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
                    st.success(f"**{nx.get('descriptor', '')}** — {nx.get('method', '')} "
                               f"@ {nx.get('facility', '')}")
                    if nx.get("rationale"):
                        st.write(nx["rationale"])
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
                for h in hyps:
                    for p in h["predictions"]:
                        if p.get("mlflow_run_url"):
                            runs.append({"When": _fmt(p.get("updated_at")),
                                         "Event": f"{h['label']} / {p.get('descriptor_name')}",
                                         "Summary": f"{p.get('work_status')} · "
                                                    f"verdict {p.get('verdict') or '—'}",
                                         "MLflow": p["mlflow_run_url"]})
                if runs:
                    st.dataframe(pd.DataFrame(runs), width='stretch', hide_index=True,
                                 column_config={"MLflow": st.column_config.LinkColumn("MLflow")})
                else:
                    st.caption("_No MLflow runs linked yet. The agent attaches run URLs "
                               "as it submits/finishes compute._")

            # ---- Journal — append-only history ----
            with tabJ:
                if not events:
                    st.caption("_No activity yet._")
                for e in events:
                    st.markdown(f"**{e['event_type']}** · {_fmt(e.get('created_at'))} · "
                                f"_{e.get('actor_identity') or 'agent'}_")
                    st.write(e["summary"])
                    detail_bits = []
                    if e.get("detail"):
                        detail_bits.append(e["detail"])
                    if e.get("evidence_record_ids"):
                        detail_bits.append("Evidence: " + ", ".join(e["evidence_record_ids"]))
                    if e.get("mlflow_run_url"):
                        detail_bits.append(f"[MLflow run]({e['mlflow_run_url']})")
                    if detail_bits:
                        with st.expander("detail"):
                            for b in detail_bits:
                                st.markdown(b)
                    st.divider()

        # ---- Project list vs detail ----
        if st.session_state.discovery_project is None:
            st.subheader("My Projects")
            if st.button("🔄 Refresh"):
                st.rerun()
            projects = discovery.list_projects(_DISC_OWNER)
            if not projects:
                st.caption("No projects yet. Create one below.")
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
                        st.caption(f"{p['n_hypotheses']} hypotheses · {leadtxt} · "
                                   f"{p['status']}")
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
            with top_r:
                with st.popover("⋯ Manage"):
                    st.caption("Delete this project and all its hypotheses, "
                               "predictions, and history. Cannot be undone.")
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
branding.render_footer()
