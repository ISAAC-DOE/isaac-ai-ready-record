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
<body><div id="tt"></div><svg id="c" width="100%" height="580"></svg><script>
const DATA=__DATA__, P=__PAL__;
const tt=document.getElementById('tt');
tt.style.background=P.tipbg; tt.style.color=P.tiptext; tt.style.border='1px solid '+P.tipborder;
document.body.style.background='radial-gradient(circle at 50% 45%,'+P.bg1+','+P.bg2+')';
const el=document.getElementById('c'); const W=el.clientWidth||820, H=580; const cx=W/2, cy=H/2-2;
const svg=d3.select('#c').attr('width',W).attr('height',H);
const defs=svg.append('defs');
const glow=defs.append('filter').attr('id','g').attr('x','-80%').attr('y','-80%').attr('width','260%').attr('height','260%');
glow.append('feGaussianBlur').attr('stdDeviation','3.6').attr('result','b');
const fm=glow.append('feMerge');fm.append('feMergeNode').attr('in','b');fm.append('feMergeNode').attr('in','SourceGraphic');
const SC={supported:'#ffca28',eliminated:'#6f6f6f',needs_more_data:'#ffa726',proposed:'#4aa3ff',superseded:'#5a5a5a'};
const VC={supports:'#26c6da',contradicts:'#ec407a',neutral:'#90a4ae',insufficient:'#5c6b7a'};
function rT(d){return d.kind==='hyp'?56+(1-(d.conf||0))*150:d.kind==='pred'?250:d.kind==='evid'?330:392;}
function nR(d){return d.kind==='hyp'?8+(d.conf||0)*22:d.kind==='pred'?({strong:7,moderate:5,weak:3}[d.strength]||4):d.kind==='evid'?3:1.3+Math.min(3.4,Math.log((d.n||1)+1));}
function nC(d){return d.kind==='hyp'?(SC[d.status]||'#90caf9'):d.kind==='pred'?(VC[d.verdict]||'#455a64'):d.kind==='evid'?P.evid:P.screened;}
function nO(d){return d.kind==='screened'?0.45:d.kind==='evid'?0.85:(d.status==='eliminated'||d.status==='superseded')?0.45:1;}
[[56,'leading'],[250,'predictions'],[330,'evidence'],[392,'screened']].forEach(function(p){
 svg.append('circle').attr('cx',cx).attr('cy',cy).attr('r',p[0]).attr('fill','none').attr('stroke',P.ring).attr('stroke-dasharray','2,7').attr('opacity',0.7);
 svg.append('text').attr('x',cx).attr('y',cy-p[0]-3).attr('fill',P.ringlab).attr('font-size',9).attr('text-anchor','middle').attr('opacity',0.85).text(p[1]);});
svg.append('text').attr('x',16).attr('y',26).attr('fill',P.badge).attr('font-size',12).attr('font-weight',600)
 .text(DATA.corpus.records.toLocaleString()+'  records   →   '+DATA.corpus.screened+'  descriptors screened   →   '+DATA.corpus.cited+'  cited');
const nodes=DATA.nodes.map(function(d){return Object.assign({},d);});
const links=DATA.links.map(function(d){return Object.assign({},d);});
const cont=svg.append('g');
const link=cont.append('g').selectAll('line').data(links).join('line')
 .attr('stroke',function(d){return d.rel==='pred'?(VC[d.verdict]||'#37474f'):d.rel==='evid'?P.screened:d.rel==='competes_with'?'#ef5350':d.rel==='co_operating'?'#66bb6a':P.relrest;})
 .attr('stroke-opacity',function(d){return d.rel==='evid'?0.2:d.rel==='pred'?0.5:0.6;})
 .attr('stroke-width',function(d){return d.rel==='pred'?({strong:2.4,moderate:1.5,weak:0.8}[d.strength]||1):(d.rel==='competes_with'||d.rel==='co_operating')?1.6:0.6;})
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
let rot=0, ds=null;
function rp(x,y){var a=rot*Math.PI/180,ca=Math.cos(a),sa=Math.sin(a),dx=x-cx,dy=y-cy;return [cx+dx*ca-dy*sa, cy+dx*sa+dy*ca];}
function placeLabels(){lab.attr('x',function(d){return rp(d.x,d.y)[0];}).attr('y',function(d){return rp(d.x,d.y)[1]-nR(d)-5;});}
const sim=d3.forceSimulation(nodes)
 .force('link',d3.forceLink(links).id(function(d){return d.id;}).distance(function(d){return d.rel==='pred'?66:d.rel==='evid'?40:120;}).strength(function(d){return d.rel==='pred'?0.45:0.18;}))
 .force('charge',d3.forceManyBody().strength(function(d){return d.kind==='screened'?-12:-72;}))
 .force('r',d3.forceRadial(rT,cx,cy).strength(0.92))
 .force('x',d3.forceX(cx).strength(0.045)).force('y',d3.forceY(cy).strength(0.045))
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

        def _river_html(payload, theme="dark"):
            dark = theme != "light"
            pal = json.dumps({
                "bg1": "#0c1226" if dark else "#eef3fa",
                "bg2": "#070b16" if dark else "#dbe6f3",
                "axis": "#5e7290" if dark else "#5a6e8a",
                "grid": "#34456a" if dark else "#b7c5db",
                "label": "#eef3ff" if dark else "#10243a",
                "labshadow": "rgba(0,0,0,0.55)" if dark else "rgba(255,255,255,0.85)",
            })
            data = json.dumps(payload)
            tmpl = r"""
<html><head><script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>html,body{margin:0;overflow:hidden;}text{font-family:-apple-system,Segoe UI,Roboto,sans-serif;}</style></head>
<body><svg id="r" width="100%" height="300"></svg><script>
const D=__DATA__, P=__PAL__;
document.body.style.background='linear-gradient(180deg,'+P.bg1+','+P.bg2+')';
const el=document.getElementById('r'); const W=el.clientWidth||820,H=300,m={t:18,r:138,b:24,l:16};
const svg=d3.select('#r').attr('width',W).attr('height',H);
const SC={supported:'#ffca28',eliminated:'#6f6f6f',needs_more_data:'#ffa726',proposed:'#4aa3ff',superseded:'#5a5a5a'};
const keys=D.hyps.map(function(h){return h.label;});
const colorOf={}; D.hyps.forEach(function(h){colorOf[h.label]=SC[h.status]||'#90caf9';});
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
svg.append('g').selectAll('line').data(D.markers).join('line')
 .attr('x1',function(d){return x(d.t);}).attr('x2',function(d){return x(d.t);}).attr('y1',m.t-4).attr('y2',H-m.b)
 .attr('stroke',P.grid).attr('stroke-dasharray','2,4').attr('opacity',0.4);
svg.append('g').selectAll('path').data(series).join('path')
 .attr('d',area).attr('fill',function(s){return colorOf[s.key];}).attr('opacity',0.93)
 .attr('stroke','#00000022').attr('stroke-width',0.4)
 .attr('filter',function(s){return colorOf[s.key]==='#ffca28'?'url(#rg)':null;});
svg.append('g').selectAll('text').data(series).join('text')
 .attr('x',W-m.r+8).attr('y',function(s){var d=s[s.length-1];return y((d[0]+d[1])/2)+3;})
 .attr('fill',P.label).attr('font-size',10.5).attr('font-weight',600)
 .style('paint-order','stroke').style('stroke',P.labshadow).style('stroke-width','2.5px').style('stroke-linejoin','round')
 .text(function(s){var h=D.hyps.find(function(z){return z.label===s.key;});return s.key+'  '+Math.round((h?h.conf:0)*100)+'%';});
svg.append('text').attr('x',m.l).attr('y',H-7).attr('fill',P.axis).attr('font-size',10).text('run start');
svg.append('text').attr('x',W-m.r).attr('y',H-7).attr('text-anchor','end').attr('fill',P.axis).attr('font-size',10).text('now →');
}
</script></body></html>
"""
            return tmpl.replace("__DATA__", data).replace("__PAL__", pal)

        def _funnel(stages):
            n = len(stages)
            out = ["<div style='padding:4px 0'>"]
            for i, (label, count, sub) in enumerate(stages):
                w = 96 - i * (70 / max(1, n - 1))
                out.append(
                    "<div style='display:flex;justify-content:center;margin:3px 0'>"
                    f"<div style='width:{w:.0f}%;background:linear-gradient(90deg,"
                    "#0d47a1,#42a5f5);border-radius:7px;padding:7px 12px;color:white;"
                    "text-align:center;box-shadow:0 1px 5px rgba(0,0,0,0.35)'>"
                    f"<span style='font-size:1.25em;font-weight:800'>{count:,}</span> "
                    f"<span style='opacity:0.95'>{label}</span>"
                    + (f"<div style='font-size:0.78em;opacity:0.85'>{sub}</div>" if sub else "")
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
            _hlabel = {h["hypothesis_id"]: h["label"] for h in hyps}
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

            open_n = len(brief.get("open_questions", []))
            running = len(brief.get("pending_compute", []))
            needs = sum(1 for h in hyps if h["status"] == "needs_more_data")
            st.caption(f"⏳ **Pending** — {open_n} prediction(s) open · {running} compute "
                       f"running · {needs} hypothes(es) need more data")

            # ---------- Scientific-rigor check (live, from method_compliance) -------
            _mc = brief.get("method_compliance", {})
            _issues = []
            if not _mc.get("enough_competing_hypotheses", True):
                _issues.append(("Needs ≥2 competing hypotheses", []))
            _issue_map = [
                ("Hypotheses with no falsifying prediction",
                 _mc.get("hypotheses_without_falsifying_prediction")),
                ("Predictions missing provenance (origin)",
                 _mc.get("predictions_missing_origin_provenance")),
                ("Predictions missing a falsification criterion",
                 _mc.get("predictions_missing_falsification_criterion")),
                ("⚠ Circular confirmations (model fit to the data it's tested on)",
                 [c.get("prediction") if isinstance(c, dict) else c
                  for c in (_mc.get("circular_confirmations") or [])]),
                ("⚠ Supersessions with no discriminating observable (refinement vs new?)",
                 _mc.get("supersessions_without_discriminating_observable")),
            ]
            for _lbl, _items in _issue_map:
                if _items:
                    _issues.append((_lbl, _items))
            if _issues:
                with st.expander(f"🔬 Scientific rigor — {len(_issues)} open check(s)",
                                 expanded=False):
                    st.caption("Live audit against the manifest `method` + epistemic "
                               "guardrails (use-novelty; hypothesis individuation). "
                               "Advisory now — these are what make a claim auditable.")
                    for _lbl, _items in _issues:
                        _detail = (" — " + ", ".join(str(x) for x in _items[:6])
                                   ) if _items else ""
                        st.markdown(f"- **{_lbl}**{_detail}")
            else:
                st.caption("🔬 Scientific rigor — all method/guardrail checks clear.")

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

            tabImpact, tabA, tabB, tabE, tabC, tabJ = st.tabs([
                "🌊 Decision journey", "🧪 Hypotheses & provenance", "✅ Validation board",
                "🔎 Evidence & matrix", "📊 Compute ledger", "📓 Journal"])

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
                                        "status": _h["status"], "tip": _tip})
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
                    for _r in relations:
                        _clinks.append({"source": _r["from_hypothesis_id"],
                                        "target": _r["to_hypothesis_id"], "rel": _r["relation_type"]})
                    # the dense screened-descriptor field — the outer-ring complexity
                    for _name, _v in list(ev_idx.items())[:200]:
                        _stip = (f"<b>screened descriptor</b><br>{_esc(_name)}"
                                 f"<br>{(_v or {}).get('n', 1)} records in the corpus")
                        _cnodes.append({"id": "scr|" + _name, "label": _name, "kind": "screened",
                                        "n": (_v or {}).get("n", 1), "tip": _stip})
                    components.html(_constellation_html(
                        {"nodes": _cnodes, "links": _clinks,
                         "corpus": {"records": corpus or 0, "screened": n_desc, "cited": n_ev}},
                        st.session_state.ui_theme), height=600)
                    st.caption("**Drag to rotate.** The faint outer field is every descriptor "
                               "screened; bright nodes are cited evidence → predictions → the "
                               "hypothesis stars, each drawn toward the centre by its "
                               "confidence (`competes_with` ties push losers out). Every "
                               "position, size and colour is a real value.")

                    # ---- The river of belief: confidence evolution over the run ----
                    import re as _re
                    _chrono = list(reversed(events))  # oldest -> newest
                    _hmap = {h["hypothesis_id"]: h for h in hyps}
                    _traj = {hid: [] for hid in _hmap}
                    _created = {}
                    for _i, _e in enumerate(_chrono):
                        _hid = _e.get("hypothesis_id")
                        if _hid in _hmap:
                            if _e["event_type"] == "hypothesis_created" and _hid not in _created:
                                _created[_hid] = _i
                                _traj[_hid].append((_i, 0.0))
                            _mm = _re.search(r"confidence[^0-9]*([0-9]*\.?[0-9]+)",
                                             _e.get("summary") or "")
                            if _mm:
                                _traj[_hid].append((_i, float(_mm.group(1))))
                    _N = max(1, len(_chrono))
                    for _hid, _h in _hmap.items():
                        _traj[_hid].append((_N - 1, float(_h["confidence"] or 0)))
                        if not _traj[_hid] or _traj[_hid][0][0] > 0:
                            _traj[_hid].insert(0, (_created.get(_hid, 0), 0.0))
                    _T = 48
                    _steps = []
                    for _s in range(_T):
                        _ti = _s * (_N - 1) / (_T - 1) if (_T > 1 and _N > 1) else 0
                        _row = {"t": _s / (_T - 1) if _T > 1 else 0.0}
                        for _hid, _h in _hmap.items():
                            _c = 0.0
                            for (_idx, _cv) in sorted(_traj[_hid]):
                                if _idx <= _ti:
                                    _c = _cv
                                else:
                                    break
                            _row[_h["label"]] = _c
                        _steps.append(_row)
                    _markers = [{"t": _i / (_N - 1) if _N > 1 else 0.0}
                                for _i, _e in enumerate(_chrono)
                                if _e["event_type"] in ("prediction_evaluated", "compute_running",
                                                        "compute_submitted", "evidence_ingested",
                                                        "next_experiment_proposed")]
                    st.markdown("**The river of belief** — how confidence in each mechanism "
                                "evolved as evidence arrived")
                    components.html(_river_html(
                        {"steps": _steps,
                         "hyps": [{"label": _h["label"], "status": _h["status"],
                                   "conf": float(_h["confidence"] or 0)} for _h in hyps],
                         "markers": _markers},
                        st.session_state.ui_theme), height=320)
                    st.caption("Each ribbon is a hypothesis; thickness = confidence over the "
                               "run — the leader swells, eliminated mechanisms thin out. Dashed "
                               "marks are where evidence or compute landed. On resume, new "
                               "evidence extends the river: the discovery's evolution, live.")

            # ---- E: Evidence index (by descriptor) + discrimination matrix ----
            with tabE:
                st.markdown("**Discrimination matrix** — what each hypothesis "
                            "predicts for a measurable (drives next-experiment choice)")
                matrix = brief.get("discrimination_matrix", [])
                if matrix:
                    labels = sorted({e["hypothesis_label"]
                                     for m in matrix for e in (m["expected_by_hypothesis"] or [])})
                    rows = []
                    for m in matrix:
                        row = {"Prediction": m["prediction"], "Descriptor": m["descriptor"]}
                        exp = {e["hypothesis_label"]: e.get("expected")
                               for e in (m["expected_by_hypothesis"] or [])}
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
