import streamlit as st
import pandas as pd
import json
import requests
import ontology
import database
import branding
import agent
import os
import re
import importlib
import streamlit.components.v1 as components
from datetime import datetime, timezone

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

# ISAAC logo at the top of every page
branding.render_header()

# Initialize database tables on startup (if configured)
if database.is_db_configured():
    database.init_tables()

# Check database status
db_connected = database.test_db_connection()

# Extract current user from Authentik headers
try:
    _headers = st.context.headers
    current_username = _headers.get("X-authentik-username", "anonymous")
except Exception:
    current_username = "anonymous"

user_is_admin = ontology.is_admin(current_username)

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
            ontology.sync_from_wiki(synced_by="auto")
    except Exception:
        pass

# Initialize page state
if "current_page" not in st.session_state:
    st.session_state.current_page = "Dashboard"

PAGES = ["Dashboard", "Ontology Editor", "Record Form", "Record Validator", "Saved Records", "nano ISAAC", "API Keys", "API Documentation", "About"]
if user_is_admin:
    # Insert Admin Review after Ontology Editor
    PAGES.insert(2, "Admin Review")

# --- Top navigation bar: hamburger menu + DB status + user info ---
nav_col, status_col, user_col = st.columns([6, 1, 2])
with nav_col:
    with st.popover("☰ Menu"):
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
    if db_connected:
        st.success("DB Online")
    else:
        st.warning("DB Offline")
with user_col:
    _logout_url = "https://isaac.slac.stanford.edu/outpost.goauthentik.io/flows/logout/?rd=https://isaac.slac.stanford.edu/"
    st.markdown(
        f"👤 **{current_username}** &nbsp;|&nbsp; [Logout]({_logout_url})"
    )

page = st.session_state.current_page

# --- CONFIG: Display Names ---
DISPLAY_MAP = {
    "Record Info": "1. Record Info",
    "Sample": "2. Sample",
    "Context": "3. Context",
    "System": "4. System",
    "Measurement": "5. Measurement",
    "Assets": "6. Assets",
    "Links": "7. Links",
    "Descriptors": "8. Descriptors"
}

def get_display_name(key):
    return DISPLAY_MAP.get(key, key)

# --- CONFIG: Wiki Mapping ---
WIKI_BASE = "https://github.com/ISAAC-DOE/isaac-ai-ready-record/wiki"

WIKI_MAP = {
    "Record Info": "Record-Overview",
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
    "Measurement", "Assets", "Links", "Descriptors",
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
                ).set_index("Record Type")
                st.bar_chart(type_df)
            else:
                st.info("No records yet. Use the Record Validator or Record Form to add data.")

        except Exception as e:
            st.error(f"Error loading dashboard: {e}")


# =============================================================================
# PAGE: Ontology Editor
# =============================================================================
elif page == "Ontology Editor":
    st.header("Living Ontology")
    st.info("Browse the ISAAC vocabulary below. Use the Propose form to suggest changes.")

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
                    if st.button("Sync from Wiki", type="secondary"):
                        with st.spinner("Syncing from wiki..."):
                            ok, msg = ontology.sync_from_wiki(synced_by=current_username)
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

    # API URL configuration
    api_url = os.environ.get("ISAAC_API_URL", "http://localhost:8502")

    json_file = st.file_uploader("Upload JSON", type=["json"])

    if json_file:
        try:
            raw_text = json_file.read().decode("utf-8")
            record_data = json.loads(raw_text)

            with st.expander("Record Preview", expanded=False):
                st.json(record_data)

            if st.button("Validate", type="primary"):
                # Schema validation
                from jsonschema import Draft202012Validator
                schema_path = os.path.join(os.path.dirname(__file__), "..", "schema", "isaac_record_v1.json")
                with open(schema_path) as f:
                    schema = json.load(f)
                validator = Draft202012Validator(schema)

                schema_errors = []
                for err in validator.iter_errors(record_data):
                    schema_errors.append({
                        "path": "/".join(str(p) for p in err.absolute_path) or "(root)",
                        "message": err.message,
                    })

                # Vocabulary validation
                vocab_errors = ontology.validate_record_vocabulary(record_data)

                # --- Display results ---
                col_schema, col_vocab = st.columns(2)

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

                if not schema_errors and not vocab_errors:
                    st.balloons()
                    st.success("This record is fully compliant with the ISAAC schema and vocabulary!")

                    # Offer save-to-database button
                    if st.button("Save to Database", key="save_json_btn"):
                        if database.test_db_connection():
                            try:
                                saved_id = database.save_record(record_data)
                                st.success(f"Record saved! ID: `{saved_id}`")
                            except Exception as exc:
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
                records = database.list_records(limit=50)

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

                        # Delete button (with confirmation)
                        with st.expander("Danger Zone"):
                            st.warning("This action cannot be undone!")
                            if st.button(f"Delete Record {selected_id}", type="secondary"):
                                if database.delete_record(selected_id):
                                    st.success("Record deleted.")
                                    st.rerun()
                                else:
                                    st.error("Failed to delete record.")
            else:
                st.info("No records found. Create records using the Excel Validator or Record Form.")

        except Exception as e:
            st.error(f"Error loading records: {e}")


# =============================================================================
# PAGE: nano ISAAC
# =============================================================================
elif page == "nano ISAAC":
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

                    create_resp = requests.post(
                        f"{authentik_api_url}/api/v3/core/tokens/",
                        headers=admin_headers,
                        json={
                            "identifier": identifier,
                            "intent": "api",
                            "user": user_pk,
                            "description": f"ISAAC Portal API key for {current_username}",
                            "expiring": False,
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

                    st.success("API key created. Copy it now — it will not be shown again.")
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
    "isaac_record_version": "1.0",
    "record_id": "01JFH3Q8Z1Q9F0XG3V7N4K2M8C",
    "record_type": "evidence",
    "record_domain": "characterization",
    "source_type": "facility",
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
    | `valid` | bool | `true` only if **both** schema and vocabulary pass |
    | `schema_valid` | bool | JSON Schema validation result |
    | `vocabulary_valid` | bool | Living-ontology vocabulary check result |
    | `schema_errors` | list | Schema validation errors |
    | `vocabulary_errors` | list | Vocabulary validation errors |
    | `errors` | list | Combined list (schema + vocabulary) for backward compatibility |
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
    st.markdown(f"**Schema version: ISAAC AI-Ready Record v1.0**")


# =============================================================================
# PAGE: About
# =============================================================================
elif page == "About":
    st.markdown("""
    Features:
    - **Dashboard**: Database health, record stats, and access metrics at a glance
    - **Ontology Editor**: Browse and edit the ISAAC vocabulary
    - **Record Validator**: Validate Excel files against the schema and save to database
    - **Record Form**: Manually create ISAAC records
    - **Saved Records**: View and manage records in the database
    - **API Keys**: Generate and manage API keys for programmatic access
    - **API Documentation**: REST API reference for programmatic access
    """)
    st.markdown("**Schema version: ISAAC AI-Ready Record v1.0**")

# =============================================================================
# FOOTER: Partner & DOE logos on every page
# =============================================================================
branding.render_footer()
