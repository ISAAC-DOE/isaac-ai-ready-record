"""
ISAAC AI-Ready Record - Manual Entry Form
Streamlit-based form for creating ISAAC records manually
"""

import streamlit as st
import json
from datetime import datetime
import database
import form_fields
import ontology

# Try to import ulid, fall back to simple generation if not available
try:
    import ulid
    def generate_ulid():
        return str(ulid.ULID())
except ImportError:
    import time
    import random
    import string
    def generate_ulid():
        """Fallback ULID-like generator"""
        chars = string.digits + string.ascii_uppercase
        timestamp = hex(int(time.time() * 1000))[2:].upper().zfill(10)
        random_part = ''.join(random.choices(chars, k=16))
        return (timestamp + random_part)[:26]


WIKI = "https://github.com/ISAAC-DOE/isaac-ai-ready-record/wiki"


def get_vocab_values(section: str, category: str) -> list:
    """Get allowed values from vocabulary for dropdowns"""
    vocab = ontology.load_vocabulary()
    if section in vocab and category in vocab[section]:
        return form_fields.options(vocab[section][category].get('values', []))
    return []


def render_extra_vocab_fields(section: str, handled_categories: list, prefix: str) -> dict:
    """
    Render selectboxes for the vocabulary categories of a section that name a text field of
    the record and aren't already handled by the hardcoded form fields (form_fields.py says
    which categories qualify).

    Returns dict of {category_key: selected_value} for categories rendered.
    """
    extra = {}
    for cat_key, values, desc in form_fields.extra_categories(section, handled_categories):
        selected = st.selectbox(
            cat_key,
            [""] + values,
            help=desc,
            key=f"{prefix}_{cat_key}"
        )
        if selected:
            extra[cat_key] = selected
    return extra


def render_form():
    """Render the complete ISAAC record entry form"""

    # Initialize session state for form data
    if 'record_id' not in st.session_state:
        st.session_state.record_id = generate_ulid()

    # Template management
    db_connected = database.test_db_connection()

    if db_connected:
        with st.expander("Templates", expanded=False):
            col1, col2 = st.columns(2)

            with col1:
                # Load template
                templates = database.list_templates()
                template_names = ["(Select template)"] + [t['name'] for t in templates]
                selected_template = st.selectbox("Load Template", template_names, key="template_select")

                if st.button("Load") and selected_template != "(Select template)":
                    template = database.get_template(selected_template)
                    if template:
                        st.session_state.template_data = template['data']
                        st.success(f"Loaded template: {selected_template}")
                        st.rerun()

            with col2:
                # Save template
                new_template_name = st.text_input("Save as Template", placeholder="Template name")
                if st.button("Save Template"):
                    if new_template_name:
                        # We'll capture the current form state after submission
                        st.info("Fill out the form and use 'Save as Template' after Preview to save current state.")

    st.divider()

    st.info(
        "**Before you enter a record**\n\n"
        "- One record is one sample, one technique and one set of conditions. Each value is a row in Results: "
        "H2, CO and C2H4 from one run are three rows of one record. A different sample, technique or condition "
        "set is a new record.\n"
        "- State each condition as measured or as the source states it, in the unit on the label. Leave out what "
        "was not measured or stated; never fill in a usual value.\n"
        "- Link two records only for a relation the source states (a reference sample, the same electrode, a "
        "specimen made from another). A shared paper, material or batch is no link.\n"
        "- In Produced by: group, name who measured or calculated the result: your group, or the authors' group "
        "for a paper.\n\n"
        f"Rules in full: [Write your first record]({WIKI}/Write-Your-First-Record) · "
        f"[What a record is]({WIKI}/Record-Granularity) · [Links]({WIKI}/Links)")

    # Initialize extra vocab dicts (populated inside expanders, used at submission)
    extra_record_info = {}
    extra_sample = {}
    extra_system = {}
    extra_context = {}
    extra_measurement = {}
    extra_links = {}
    extra_assets = {}
    extra_descriptors = {}

    # Main form
    with st.form("isaac_record_form"):

        # =====================================================================
        # SECTION 1: Core Information
        # =====================================================================
        st.subheader("1. Core Information")

        col1, col2 = st.columns(2)

        with col1:
            record_version = st.text_input("Record Version", value="1.05", disabled=True)

            record_id = st.text_input(
                "Record ID",
                value=st.session_state.record_id,
                help="26-character ULID identifier"
            )
            if st.form_submit_button("Generate New ID", type="secondary"):
                st.session_state.record_id = generate_ulid()
                st.rerun()

        with col2:
            record_type_options = [""] + get_vocab_values("Record Info", "record_type")
            record_type = st.selectbox(
                "Record Type *",
                record_type_options,
                help="Fundamental nature of the record"
            )

            record_domain_options = [""] + get_vocab_values("Record Info", "record_domain")
            record_domain = st.selectbox(
                "Record Domain *",
                record_domain_options,
                help="Scientific domain classification"
            )

        extra_record_info = render_extra_vocab_fields(
            "Record Info",
            ["record_type", "record_domain", "source_type"],
            "ri"
        )

        # =====================================================================
        # SECTION 2: Timestamps
        # =====================================================================
        st.subheader("2. Timestamps")

        col1, col2, col3 = st.columns(3)

        with col1:
            created_date = st.date_input("Created Date *", value=datetime.now().date())
            created_time = st.time_input("Created Time *", value=datetime.now().time())

        with col2:
            acquired_start_date = st.date_input("Acquisition Start Date", value=None)
            acquired_start_time = st.time_input("Acquisition Start Time", value=None)

        with col3:
            acquired_end_date = st.date_input("Acquisition End Date", value=None)
            acquired_end_time = st.time_input("Acquisition End Time", value=None)

        # =====================================================================
        # SECTION 3: Source Type
        # =====================================================================
        st.subheader("3. Source Type *")
        st.caption("Origin of the data acquisition (facility details go in System block)")

        source_type_options = [""] + get_vocab_values("Record Info", "source_type")
        source_type = st.selectbox("Source Type *", source_type_options)

        col1, col2 = st.columns(2)
        with col1:
            produced_by_group = st.text_input(
                "Produced by: group *",
                placeholder="e.g. your own group; for a result from a paper, the authors' group",
                help="Who produced the result (a measurement or a calculation), as distinct from who uploads "
                     "the record. Required on evidence records.")
        with col2:
            produced_by_org = st.text_input(
                "Produced by: organization",
                placeholder="e.g. SLAC National Accelerator Laboratory",
                help="Use the canonical name from system.organizations (Controlled-Vocabulary wiki page).")
        source_doi = st.text_input(
            "Source DOI (required for literature records)",
            placeholder="e.g. 10.1002/advs.202520469",
            help="The paper the values come from.")

        tags_raw = st.text_input(
            "Tags (optional)",
            placeholder="comma-separated, e.g. jcap-hte, nifecoce-oer-2014",
            help="Free-form grouping labels (lowercase-hyphenated, scoped). A record may carry several.")

        # =====================================================================
        # SECTION 4: Sample (Optional)
        # =====================================================================
        with st.expander("4. Sample (Optional)", expanded=False):
            st.caption("Material identity and physical realization")

            col1, col2 = st.columns(2)
            with col1:
                material_name = st.text_input("Material Name", placeholder="e.g., Copper nanoparticles")
                material_formula = st.text_input("Chemical Formula", placeholder="e.g., Cu")
            with col2:
                provenance_options = [""] + get_vocab_values("Sample", "sample.material.provenance")
                material_provenance = st.selectbox("Provenance", provenance_options)

                sample_form_options = [""] + get_vocab_values("Sample", "sample.sample_form")
                sample_form = st.selectbox("Sample Form", sample_form_options)
                electrode_type = st.selectbox("Electrode type",
                                              [""] + get_vocab_values("Sample", "sample.electrode_type"))
            col1, col2 = st.columns(2)
            with col1:
                geometric_area_cm2 = st.number_input("Electrode geometric area (cm²)", value=None, min_value=0.0,
                                                     format="%.4f")
            with col2:
                catalyst_loading_mg_cm2 = st.number_input("Catalyst loading (mg/cm²)", value=None, min_value=0.0,
                                                          format="%.4f")

            composition_json = st.text_area(
                "Composition (JSON)",
                placeholder='{"elements": ["Cu"], "stoichiometry": [1.0]}',
                height=80
            )

            geometry_json = st.text_area(
                "Geometry (JSON)",
                placeholder='{"shape": "rectangular", "dimensions_mm": [10, 10, 0.5]}',
                height=80
            )

            extra_sample = render_extra_vocab_fields(
                "Sample",
                ["sample.sample_form", "sample.material.provenance", "sample.material.identifiers.scheme",
                 "sample.electrode_type"],
                "samp"
            )

        # =====================================================================
        # SECTION 5: System (Optional)
        # =====================================================================
        with st.expander("5. System (Optional)", expanded=False):
            st.caption("Infrastructure and configuration")

            domain_options = [""] + get_vocab_values("System", "system.domain")
            system_domain = st.selectbox("Domain", domain_options)

            technique_options = [""] + get_vocab_values("System", "system.technique")
            system_technique = st.selectbox("Technique *", technique_options,
                help="Primary technique or computational method")

            col1, col2 = st.columns(2)
            with col1:
                instrument_type_options = [""] + get_vocab_values("System", "system.instrument.instrument_type")
                instrument_type = st.selectbox("Instrument Type", instrument_type_options)
                instrument_name = st.text_input("Instrument Name", placeholder="e.g., XRD Diffractometer")
            with col2:
                instrument_id = st.text_input("Instrument ID", placeholder="Unique identifier")

            configuration_json = st.text_area(
                "Configuration (flat key-value JSON)",
                placeholder='{"voltage_kV": 40, "current_mA": 15, "scan_mode": "continuous"}',
                height=80,
                help="Values must be string, number, or boolean only"
            )

            extra_system = render_extra_vocab_fields(
                "System",
                ["system.domain", "system.technique", "system.instrument.instrument_type"],
                "sys"
            )

        # =====================================================================
        # SECTION 6: Context (Optional)
        # =====================================================================
        with st.expander("6. Conditions", expanded=False):
            st.caption("The conditions of the measurement or calculation, as measured or as the source states them")

            col1, col2, col3 = st.columns(3)
            with col1:
                environment_options = [""] + get_vocab_values("Context", "context.environment")
                environment = st.selectbox("Environment", environment_options)
            with col2:
                temperature_k = st.number_input("Temperature", value=None, format="%.2f")
                temperature_unit = st.radio("Temperature unit", ["°C", "K"], horizontal=True)
            with col3:
                temperature_basis = st.selectbox(
                    "Temperature is", [""] + get_vocab_values("Context", "context.temperature_basis"),
                    help="stated: a number was measured or given. room_temperature: only 'room temperature' is "
                         "known (298.15 K is written). not_reported: no temperature is known (leave K empty).")

            # Reaction (any chemistry): the one home of the reaction is context.reaction
            st.write("**Reaction**")
            col1, col2, col3 = st.columns(3)
            with col1:
                reaction_options = [""] + get_vocab_values("Context", "context.reaction.name")
                echem_reaction = st.selectbox("Reaction", reaction_options)
            with col2:
                drive_options = [""] + get_vocab_values("Context", "context.reaction.drive")
                reaction_drive = st.selectbox("Drive", drive_options,
                                              help="What drives the reaction (electrochemical, thermal, photochemical, ...)")
            with col3:
                catalysis_options = [""] + get_vocab_values("Context", "context.reaction.catalysis")
                reaction_catalysis = st.selectbox("Catalysis", catalysis_options,
                                                  help="heterogeneous (solid catalyst or electrode), homogeneous, enzymatic, uncatalyzed")

            # Electrochemistry context
            st.write("**Electrochemistry** (for electrochemically driven reactions)")
            col1, col2, col3 = st.columns(3)
            with col1:
                cell_type_options = [""] + get_vocab_values("Context", "context.electrochemistry.cell_type")
                echem_cell_type = st.selectbox("Cell Type", cell_type_options)
                control_mode = st.selectbox(
                    "Control mode", [""] + get_vocab_values("Context", "context.electrochemistry.control_mode"),
                    help="potentiostatic: held at a potential. galvanostatic: held at a current.")
                current_mA_cm2 = st.number_input("Applied current density (mA/cm², galvanostatic runs only)",
                                                 value=None, format="%.3f",
                                                 help="The current a galvanostatic run was held at; reduction "
                                                      "currents are negative. A measured current density goes "
                                                      "in Results.")
            with col2:
                potential_V = st.number_input("Potential (V, as reported)", value=None, format="%.4f",
                                              help="Potentiostatic: the applied potential. Galvanostatic: the "
                                                   "measured operating potential. Stored on the scale below, never "
                                                   "converted.")
                potential_scale_options = [""] + get_vocab_values("Context", "context.electrochemistry.potential_scale")
                echem_potential_scale = st.selectbox("Potential Scale", potential_scale_options)
                reference_electrode = st.selectbox(
                    "Reference electrode",
                    [""] + get_vocab_values("Context", "context.electrochemistry.reference_electrode.type"),
                    help="The physical reference electrode the potential was measured against.")
            with col3:
                electrolyte_name = st.text_input("Electrolyte at the working electrode",
                                                 placeholder="e.g. KHCO3 (the catholyte in a divided cell)")
                electrolyte_concentration_M = st.number_input("Electrolyte concentration (M)", value=None,
                                                              min_value=0.0, format="%.4f")
                anolyte_name = st.text_input("Anolyte, if different", placeholder="e.g. KOH")
                anolyte_concentration_M = st.number_input("Anolyte concentration (M)", value=None,
                                                          min_value=0.0, format="%.4f")
                pH = st.number_input("pH", value=None, format="%.2f")
                pH_basis = st.selectbox("pH is", [""] + get_vocab_values("Context", "context.electrochemistry.pH_basis"),
                                        help="measured, nominal (from the recipe), or buffered_assumed")

            col1, col2, col3 = st.columns(3)
            with col1:
                ir_method = st.selectbox(
                    "iR compensation",
                    [""] + get_vocab_values("Context", "context.electrochemistry.ir_compensation.method"))
            with col2:
                ir_percent = st.number_input("iR compensation (%)", value=None, min_value=0.0, max_value=100.0,
                                             format="%.1f")
            with col3:
                ir_corrected = st.selectbox("Reported potential is iR-corrected", ["", "yes", "no", "unknown"])

            st.write("**Feed and pressure** (for thermal, photo- or flow catalysis)")
            col1, col2, col3 = st.columns(3)
            with col1:
                feed_phase = st.selectbox("Feed phase", [""] + get_vocab_values("Context", "context.transport.feed.phase"))
                pressure_bar = st.number_input("Pressure (bar)", value=None, min_value=0.0, format="%.4f")
            with col2:
                feed_composition = st.text_input("Feed composition", placeholder="e.g. 1% CO, 1% O2 in He")
            with col3:
                flow_rate = st.number_input("Flow rate", value=None, min_value=0.0, format="%.3f")
                flow_rate_unit = st.text_input("Flow rate unit", placeholder="e.g. mL/min")

            context_additional_json = st.text_area(
                "Additional Context (JSON)",
                placeholder='{"pressure_Pa": 101325, "humidity_percent": 45}',
                height=80
            )

            extra_context = render_extra_vocab_fields(
                "Context",
                ["context.environment", "context.electrochemistry.reaction", "context.reaction.name",
                 "context.reaction.drive", "context.reaction.catalysis",
                 "context.electrochemistry.cell_type", "context.electrochemistry.potential_scale",
                 "context.temperature_basis", "context.electrochemistry.control_mode",
                 "context.electrochemistry.pH_basis", "context.electrochemistry.reference_electrode.type",
                 "context.transport.feed.phase", "context.electrochemistry.ir_compensation.method"],
                "ctx"
            )

        # =====================================================================
        # SECTION 7: Measurement (Optional)
        # =====================================================================
        with st.expander("7. Measurement (Optional)", expanded=False):
            st.caption("Measurement series and quality control")

            # Simple single series for now
            st.write("**Measurement Series**")
            series_id = st.text_input("Series ID", placeholder="e.g., spectrum_001")

            col1, col2 = st.columns(2)
            with col1:
                ind_var_name = st.text_input("Independent Variable Name", placeholder="e.g., energy")
                ind_var_unit = st.text_input("Independent Variable Unit", placeholder="e.g., eV")
                ind_var_values = st.text_input("Values (comma-separated)", placeholder="e.g., 1.0, 2.0, 3.0")

            with col2:
                channel_name = st.text_input("Channel Name", placeholder="e.g., intensity")
                channel_unit = st.text_input("Channel Unit", placeholder="e.g., counts")
                channel_role_options = [""] + get_vocab_values("Measurement", "measurement.series.channels.role")
                channel_role = st.selectbox("Channel Role", channel_role_options)
                channel_values = st.text_input("Channel Values (comma-separated)", placeholder="e.g., 100, 150, 200")

            st.write("**Quality Control**")
            qc_status = st.text_input("QC Status", placeholder="e.g., passed, pending, failed")
            qc_details_json = st.text_area("QC Details (JSON)", placeholder='{"checks": ["range"], "passed": true}', height=60)

            processing_json = st.text_area("Processing Details (JSON)", placeholder='{"steps": ["normalization"]}', height=60)

            extra_measurement = render_extra_vocab_fields(
                "Measurement",
                ["measurement.series.channels.role"],
                "meas"
            )

        # =====================================================================
        # SECTION 8: Links (Optional)
        # =====================================================================
        with st.expander("8. Links (Optional)", expanded=False):
            st.caption("Relationships to other records")

            link_rel_options = [""] + get_vocab_values("Links", "links.rel")

            col1, col2 = st.columns(2)
            with col1:
                link_rel = st.selectbox("Relationship", link_rel_options)
                link_target = st.text_input("Target Record ID", placeholder="26-character ULID")
            with col2:
                link_basis = st.selectbox("Basis", [""] + get_vocab_values("Links", "links.basis"),
                                          help="The kind of evidence for the link. If none fits, choose "
                                               "unspecified and quote the source in Notes.")
                link_notes = st.text_input("Notes", placeholder="The passage or locator in the source that states it")

            extra_links = render_extra_vocab_fields(
                "Links",
                ["links.rel", "links.basis"],
                "lnk"
            )

        # =====================================================================
        # SECTION 9: Assets (Optional)
        # =====================================================================
        with st.expander("9. Assets (Optional)", expanded=False):
            st.caption("External file references")

            asset_role_options = [""] + get_vocab_values("Assets", "assets.content_role")

            col1, col2 = st.columns(2)
            with col1:
                asset_id = st.text_input("Asset ID", placeholder="Unique asset identifier")
                asset_role = st.selectbox("Content Role", asset_role_options)
            with col2:
                asset_uri = st.text_input("URI", placeholder="https://...")
                asset_sha256 = st.text_input(
                    "SHA-256 of the file", placeholder="64 hexadecimal characters, or leave empty",
                    help="The SHA-256 of the file's bytes (shasum -a 256 <file>). Leave empty if you do not hold "
                         "the file: the form writes not_available. Never zeros.")
            asset_media_type = st.text_input("Media Type", placeholder="e.g., application/json")

            extra_assets = render_extra_vocab_fields(
                "Assets",
                ["assets.content_role"],
                "ast"
            )

        # =====================================================================
        # SECTION 10: Descriptors (Optional)
        # =====================================================================
        with st.expander("10. Results", expanded=False):
            st.caption("One row per value, with its unit. Write a Faradaic efficiency or a selectivity as a "
                       "fraction (0.428, unit fraction) or with unit percent (42.8). Leave the uncertainty empty "
                       "when none was measured or stated: the record then says it was not reported.")

            col1, col2 = st.columns(2)
            with col1:
                output_label = st.text_input("Label for these values", placeholder="e.g. steady_state")
            with col2:
                output_generated_by = st.text_input(
                    "Analysis or software that produced these values (optional)",
                    placeholder="e.g. GC analysis script v2")

            descriptor_rows = st.data_editor(
                [{"name": "", "value": "", "unit": "", "uncertainty": ""}],
                num_rows="dynamic", key="descriptor_rows",
                column_config={
                    "name": st.column_config.TextColumn(
                        "Name", help="e.g. faradaic_efficiency.C2H4, steady_state_current_density, conversion.CO, "
                                     "turnover_frequency (Descriptors wiki page)"),
                    "value": st.column_config.TextColumn("Value"),
                    "unit": st.column_config.TextColumn("Unit", help="e.g. fraction, mA/cm2, 1/s, eV"),
                    "uncertainty": st.column_config.TextColumn("Uncertainty (one standard deviation, a number)"),
                })
            desc_name = desc_value = desc_unit = desc_uncertainty = desc_kind = desc_source = None

            extra_descriptors = render_extra_vocab_fields(
                "Descriptors",
                ["descriptors.outputs.descriptors.kind", "descriptors.outputs.descriptors.source",
                 "descriptors.theoretical_metric", "descriptors.uncertainty_basis"],
                "desc"
            )

        # =====================================================================
        # Form Actions
        # =====================================================================
        st.divider()

        col1, col2, col3 = st.columns(3)

        with col1:
            submitted = st.form_submit_button("Preview JSON", type="secondary")
        with col2:
            save_submitted = st.form_submit_button("Save to Database", type="primary")
        with col3:
            download_submitted = st.form_submit_button("Download JSON", type="secondary")

    # Process form submission
    if submitted or save_submitted or download_submitted:
        # Build the record
        entry = dict(
            record_id=record_id or st.session_state.record_id,
            record_type=record_type,
            record_domain=record_domain,
            created_date=created_date,
            created_time=created_time,
            acquired_start_date=acquired_start_date,
            acquired_start_time=acquired_start_time,
            acquired_end_date=acquired_end_date,
            acquired_end_time=acquired_end_time,
            source_type=source_type,
            produced_by_group=produced_by_group,
            produced_by_org=produced_by_org,
            source_doi=source_doi,
            tags=[s.strip() for s in tags_raw.split(",") if s.strip()] if tags_raw else [],
            material_name=material_name,
            material_formula=material_formula,
            material_provenance=material_provenance,
            sample_form=sample_form,
            composition_json=composition_json,
            geometry_json=geometry_json,
            system_domain=system_domain,
            instrument_type=instrument_type,
            instrument_name=instrument_name,
            instrument_id=instrument_id,
            system_technique=system_technique,
            configuration_json=configuration_json,
            environment=environment,
            temperature_k=temperature_k,
            echem_reaction=echem_reaction,
            reaction_drive=reaction_drive,
            reaction_catalysis=reaction_catalysis,
            echem_cell_type=echem_cell_type,
            echem_potential_scale=echem_potential_scale,
            context_additional_json=context_additional_json,
            series_id=series_id,
            ind_var_name=ind_var_name,
            ind_var_unit=ind_var_unit,
            ind_var_values=ind_var_values,
            channel_name=channel_name,
            channel_unit=channel_unit,
            channel_role=channel_role,
            channel_values=channel_values,
            qc_status=qc_status,
            qc_details_json=qc_details_json,
            processing_json=processing_json,
            link_rel=link_rel,
            link_target=link_target,
            link_basis=link_basis,
            link_notes=link_notes,
            asset_id=asset_id,
            asset_role=asset_role,
            asset_uri=asset_uri,
            asset_sha256=asset_sha256,
            asset_media_type=asset_media_type,
            output_label=output_label,
            output_generated_by=output_generated_by,
            desc_name=desc_name,
            desc_kind=desc_kind,
            desc_source=desc_source,
            desc_value=desc_value,
            desc_unit=desc_unit,
            desc_uncertainty=desc_uncertainty,
            descriptor_rows=(descriptor_rows.to_dict("records") if hasattr(descriptor_rows, "to_dict")
                             else list(descriptor_rows or [])),
            temperature_basis=temperature_basis,
            control_mode=control_mode,
            potential_V=potential_V,
            current_mA_cm2=current_mA_cm2,
            reference_electrode=reference_electrode,
            electrolyte_name=electrolyte_name,
            electrolyte_concentration_M=electrolyte_concentration_M,
            pH=pH,
            pH_basis=pH_basis,
            pressure_bar=pressure_bar,
            feed_phase=feed_phase,
            feed_composition=feed_composition,
            flow_rate=flow_rate,
            flow_rate_unit=flow_rate_unit,
            temperature_unit=temperature_unit,
            anolyte_name=anolyte_name,
            anolyte_concentration_M=anolyte_concentration_M,
            ir_method=ir_method,
            ir_percent=ir_percent,
            ir_corrected=ir_corrected,
            electrode_type=electrode_type,
            geometric_area_cm2=geometric_area_cm2,
            catalyst_loading_mg_cm2=catalyst_loading_mg_cm2,
            extra_vocab={
                "Record Info": extra_record_info,
                "Sample": extra_sample,
                "System": extra_system,
                "Context": extra_context,
                "Measurement": extra_measurement,
                "Links": extra_links,
                "Assets": extra_assets,
                "Descriptors": extra_descriptors,
            },
        )
        record = build_record(**entry)

        # Validate with the portal's validator: errors block, warnings are shown too
        import validation
        full = validation.validate_record_full(record)
        errors = entry_problems(entry) + validation.format_errors_flat(full)
        notes = (full.get("warnings") or []) + (full.get("info") or [])

        if errors:
            st.error("Validation errors:")
            for err in errors:
                st.write(f"- {err}")
        if notes:
            with st.expander(f"{len(notes)} warning(s) and note(s). Fix what the source allows; a hold warning "
                             f"keeps the record private until it is fixed", expanded=bool(save_submitted and not errors)):
                for w in notes:
                    st.write(f"- **{w.get('code')}** at `{w.get('path')}`: {w.get('message')}")
        if not errors:
            if submitted:
                st.subheader("Record Preview")
                st.json(record)

            if save_submitted:
                if database.test_db_connection():
                    try:
                        try:
                            _hdrs = st.context.headers
                        except Exception:
                            _hdrs = {}
                        # Trust the identity for write attribution only when the
                        # request carries the edge secret (C1).
                        _user, _ = ontology.trusted_identity(_hdrs)
                        if _user == "anonymous":
                            _user = None
                        saved_id = database.save_record(record, uploaded_by=_user, mode="insert")
                        st.success(f"Record saved successfully! ID: {saved_id}")
                        # Generate new ID for next record
                        st.session_state.record_id = generate_ulid()
                    except database.RecordHeldError as held:
                        st.warning(f"Record {held.record_id} is held, not published: only you can see it. "
                                   f"Fix the fields below and save it again with the same ID; a version "
                                   f"without these warnings is published.")
                        for w in held.warnings:
                            if w.get("code") in held.hold:
                                st.write(f"- **{w.get('code')}** at `{w.get('path')}`: {w.get('message')}")
                    except database.HeldBacklogError as backlog:
                        st.error(f"You have {backlog.held} held records, the limit. Fix or discard them on the "
                                 f"Saved Records page before saving more records that would be held.")
                    except Exception as e:
                        st.error(f"Failed to save record: {e}")
                else:
                    st.error("Database not connected. Cannot save record.")

            if download_submitted:
                json_str = json.dumps(record, indent=2)
                st.download_button(
                    label="Click to Download",
                    data=json_str,
                    file_name=f"isaac_record_{record['record_id']}.json",
                    mime="application/json"
                )


def _set_nested(d: dict, dotted_key: str, value):
    """Set a value in a nested dict using a dotted key like 'context.electrochemistry.control_mode'."""
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


def parse_json_safe(text: str):
    """Safely parse JSON, return None on failure"""
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def parse_values(text: str):
    """Parse comma-separated numeric values"""
    if not text or not text.strip():
        return None
    try:
        return [float(v.strip()) for v in text.split(',') if v.strip()]
    except ValueError:
        return None


def _number(value):
    """A float from a form value, or None when the box is empty or not a number. A leading ± or +/- (as people
    type an uncertainty) is dropped."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str):
        value = value.strip().lstrip('±').strip()
        if value.startswith('+/-'):
            value = value[3:].strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def entry_problems(kwargs) -> list:
    """What the form can say before the validator: entries it cannot turn into a faithful record."""
    problems = []
    if _number(kwargs.get('current_mA_cm2')) is not None and kwargs.get('control_mode') != 'galvanostatic':
        problems.append("Applied current density is for galvanostatic runs. Put a measured current density in "
                        "Results (for example steady_state_current_density, mA/cm2).")
    for i, row in enumerate(kwargs.get('descriptor_rows') or [], 1):
        if not (row.get('name') or '').strip():
            continue
        for column in ('value', 'uncertainty'):
            text = row.get(column)
            if column == 'uncertainty' and (text is None or not str(text).strip()):
                continue
            if column == 'value' and _number(text) is None and not str(text or '').strip():
                problems.append(f"Results row {i} ({row['name']}): no value.")
            elif column == 'uncertainty' and _number(text) is None:
                problems.append(f"Results row {i} ({row['name']}): the uncertainty '{text}' is not a number.")
    if (kwargs.get('feed_composition') or '').strip() and not kwargs.get('feed_phase'):
        problems.append("Feed: choose the feed phase (gas or liquid).")
    return problems


def _electrochemistry(kwargs) -> dict:
    """context.electrochemistry from the labelled fields. The potential is stored on the scale it was given;
    it is projected to RHE only when it was given vs RHE: the form converts nothing."""
    ec = {}
    for key, field in (('cell_type', 'echem_cell_type'), ('control_mode', 'control_mode'),
                       ('potential_scale', 'echem_potential_scale'), ('pH_basis', 'pH_basis')):
        if kwargs.get(field):
            ec[key] = kwargs[field]
    potential = _number(kwargs.get('potential_V'))
    scale = kwargs.get('echem_potential_scale')
    galvanostatic = kwargs.get('control_mode') == 'galvanostatic'
    corrected = kwargs.get('ir_corrected')
    if potential is not None:
        # A potentiostatic setpoint is the primary on its scale. A galvanostatic run's measured potential is
        # context only when given vs RHE; on another scale it becomes a result row (see _potential_row).
        if not galvanostatic:
            ec['potential_setpoint_V'] = potential
        if scale == 'RHE':
            ec['potential_vs_RHE'] = {'value_V': potential, 'rhe_basis': 'reported_as_RHE'}
            if corrected in ('yes', 'no', 'unknown'):
                ec['potential_vs_RHE']['ir_corrected'] = corrected
    current = _number(kwargs.get('current_mA_cm2'))
    if current is not None and galvanostatic:
        ec['current_setpoint_mA_cm2'] = current
    ir = {}
    if kwargs.get('ir_method'):
        ir['method'] = kwargs['ir_method']
    if _number(kwargs.get('ir_percent')) is not None:
        ir['percent'] = _number(kwargs.get('ir_percent'))
    if corrected in ('yes', 'no'):
        ir['applied_to_reported_potential'] = corrected == 'yes'
    if ir:
        ec['ir_compensation'] = ir
    if kwargs.get('reference_electrode'):
        ec['reference_electrode'] = {'type': kwargs['reference_electrode']}
    name = (kwargs.get('electrolyte_name') or '').strip()
    concentration = _number(kwargs.get('electrolyte_concentration_M'))
    if name and concentration is not None:
        ec['electrolyte'] = {'name': name, 'concentration_M': concentration}
    elif name:
        ec['notes'] = f"Electrolyte: {name} (concentration not given)."
    anolyte = (kwargs.get('anolyte_name') or '').strip()
    anolyte_concentration = _number(kwargs.get('anolyte_concentration_M'))
    if anolyte and anolyte_concentration is not None:
        ec['anolyte'] = {'name': anolyte, 'concentration_M': anolyte_concentration}
    pH = _number(kwargs.get('pH'))
    if pH is not None:
        ec['pH'] = pH
    return ec


def _potential_row(kwargs):
    """The measured potential of a galvanostatic run on a scale other than RHE, kept as a result on its scale."""
    potential = _number(kwargs.get('potential_V'))
    scale = kwargs.get('echem_potential_scale')
    if potential is None or kwargs.get('control_mode') != 'galvanostatic' or scale in (None, '', 'RHE'):
        return None
    return {'name': 'steady_state_potential', 'value': potential, 'unit': 'V_SHE' if scale == 'SHE' else 'V_ref',
            'definition': f'Measured potential vs {scale} during the galvanostatic run, as reported, not converted.'}


def _feed(kwargs) -> dict:
    """context.transport.feed: what flows over or through the catalyst (gas or liquid)."""
    composition = (kwargs.get('feed_composition') or '').strip()
    if not composition:
        return {}
    feed = {'composition': composition}
    if kwargs.get('feed_phase'):
        feed['phase'] = kwargs['feed_phase']
    flow = _number(kwargs.get('flow_rate'))
    if flow is not None:
        feed['flow_rate'] = flow
        if (kwargs.get('flow_rate_unit') or '').strip():
            feed['flow_rate_unit'] = kwargs['flow_rate_unit'].strip()
    return feed


def _descriptor(row: dict) -> dict:
    """One value row of the form as a descriptor."""
    desc = {'name': row['name'].strip(), 'kind': row.get('kind') or 'absolute',
            'source': row.get('source') if row.get('source') in ('auto', 'manual', 'imported') else 'manual'}
    value = row.get('value')
    number = _number(value)
    desc['value'] = number if number is not None else (value.strip() if isinstance(value, str) else value)
    unit = (row.get('unit') or '').strip() if isinstance(row.get('unit'), str) else row.get('unit')
    if unit:
        desc['unit'] = unit
    if row.get('definition'):
        desc['definition'] = row['definition']
    sigma = _number(row.get('uncertainty'))
    desc['uncertainty'] = ({'sigma': sigma, **({'unit': unit} if unit else {}), 'basis': 'reported'}
                           if sigma is not None else {'basis': 'not_reported'})
    return desc


def build_record(**kwargs) -> dict:
    """Build an ISAAC record from form inputs"""

    record = {
        "isaac_record_version": "1.05",
        "record_id": kwargs['record_id'],
        "record_type": kwargs['record_type'],
        "record_domain": kwargs['record_domain'],
        "timestamps": {},
    }

    # Source Type
    if kwargs['source_type']:
        record['source_type'] = kwargs['source_type']

    # Tags (free-form grouping labels)
    if kwargs.get('tags'):
        record['tags'] = kwargs['tags']

    # Who produced the result (required on evidence records; uploaded_by is stamped by the server)
    produced_by = {k: v.strip() for k, v in (('group', kwargs.get('produced_by_group') or ''),
                                             ('organization', kwargs.get('produced_by_org') or '')) if v.strip()}
    if produced_by:
        record['attribution'] = {'produced_by': produced_by}

    # Timestamps
    if kwargs['created_date'] and kwargs['created_time']:
        dt = datetime.combine(kwargs['created_date'], kwargs['created_time'])
        record['timestamps']['created_utc'] = dt.isoformat() + "Z"

    if kwargs['acquired_start_date'] and kwargs['acquired_start_time']:
        dt = datetime.combine(kwargs['acquired_start_date'], kwargs['acquired_start_time'])
        record['timestamps']['acquired_start_utc'] = dt.isoformat() + "Z"

    if kwargs['acquired_end_date'] and kwargs['acquired_end_time']:
        dt = datetime.combine(kwargs['acquired_end_date'], kwargs['acquired_end_time'])
        record['timestamps']['acquired_end_utc'] = dt.isoformat() + "Z"

    # Sample
    sample = {}
    if kwargs['material_name'] or kwargs['material_formula']:
        sample['material'] = {}
        if kwargs['material_name']:
            sample['material']['name'] = kwargs['material_name']
        if kwargs['material_formula']:
            sample['material']['formula'] = kwargs['material_formula']
        if kwargs['material_provenance']:
            sample['material']['provenance'] = kwargs['material_provenance']
    if kwargs['sample_form']:
        sample['sample_form'] = kwargs['sample_form']
    if kwargs.get('electrode_type'):
        sample['electrode_type'] = kwargs['electrode_type']
    if kwargs['composition_json']:
        comp = parse_json_safe(kwargs['composition_json'])
        if comp:
            sample['composition'] = comp
    if kwargs['geometry_json']:
        geom = parse_json_safe(kwargs['geometry_json'])
        if geom:
            sample['geometry'] = geom
    if _number(kwargs.get('geometric_area_cm2')) is not None:
        sample.setdefault('geometry', {})['geometric_area_cm2'] = _number(kwargs.get('geometric_area_cm2'))
    if _number(kwargs.get('catalyst_loading_mg_cm2')) is not None:
        sample.setdefault('composition', {})['catalyst_loading_mg_cm2'] = _number(kwargs.get('catalyst_loading_mg_cm2'))
    if sample:
        record['sample'] = sample

    # System
    system = {}
    if kwargs['system_domain']:
        system['domain'] = kwargs['system_domain']
    if kwargs['instrument_type'] or kwargs['instrument_name'] or kwargs['instrument_id']:
        system['instrument'] = {}
        if kwargs['instrument_type']:
            system['instrument']['instrument_type'] = kwargs['instrument_type']
        if kwargs['instrument_name']:
            system['instrument']['instrument_name'] = kwargs['instrument_name']
        if kwargs['instrument_id']:
            system['instrument']['instrument_id'] = kwargs['instrument_id']
    if kwargs['system_technique']:
        system['technique'] = kwargs['system_technique']
    if kwargs['configuration_json']:
        config = parse_json_safe(kwargs['configuration_json'])
        if config:
            system['configuration'] = config
    if system:
        record['system'] = system

    # Context
    context = {}
    if kwargs['environment']:
        context['environment'] = kwargs['environment']
    temperature = _number(kwargs.get('temperature_k'))
    if temperature is not None and kwargs.get('temperature_unit') == '°C':
        temperature = round(temperature + 273.15, 6)
    if temperature is not None and temperature > 0:
        context['temperature_K'] = temperature
    if kwargs['echem_reaction']:
        # context.reaction is the one home of the reaction (the electrochemistry field is deprecated).
        # Without an explicit drive, an electrochemistry section on the form implies an
        # electrochemical drive; the validator rejects any inconsistent combination.
        drive = kwargs.get('reaction_drive') or (
            'electrochemical' if (kwargs['echem_cell_type'] or kwargs['echem_potential_scale']) else None)
        context['reaction'] = {'name': kwargs['echem_reaction']}
        if drive:
            context['reaction']['drive'] = drive
        if kwargs.get('reaction_catalysis'):
            context['reaction']['catalysis'] = kwargs['reaction_catalysis']
    if kwargs.get('temperature_basis'):
        context['temperature_basis'] = kwargs['temperature_basis']
    ec = _electrochemistry(kwargs)
    if ec:
        context['electrochemistry'] = ec
    pressure_bar = kwargs.get('pressure_bar')
    if pressure_bar is not None:
        context.setdefault('thermodynamics', {})['pressure_Pa'] = round(float(pressure_bar) * 1e5, 6)
    feed = _feed(kwargs)
    if feed:
        context.setdefault('transport', {})['feed'] = feed
    if kwargs['context_additional_json']:
        additional = parse_json_safe(kwargs['context_additional_json'])
        if additional:
            context.update(additional)
    if context:
        record['context'] = context

    # Measurement
    measurement = {}
    if kwargs['series_id'] or kwargs['ind_var_name'] or kwargs['channel_name']:
        series = {'series_id': kwargs['series_id'] or 'series_1'}

        # Independent variables
        if kwargs['ind_var_name']:
            ind_var = {'name': kwargs['ind_var_name']}
            if kwargs['ind_var_unit']:
                ind_var['unit'] = kwargs['ind_var_unit']
            values = parse_values(kwargs['ind_var_values'])
            if values:
                ind_var['values'] = values
            series['independent_variables'] = [ind_var]

        # Channels
        if kwargs['channel_name']:
            channel = {'name': kwargs['channel_name']}
            if kwargs['channel_unit']:
                channel['unit'] = kwargs['channel_unit']
            if kwargs['channel_role']:
                channel['role'] = kwargs['channel_role']
            values = parse_values(kwargs['channel_values'])
            if values:
                channel['values'] = values
            series['channels'] = [channel]

        measurement['series'] = [series]

    if kwargs['qc_status']:
        measurement['qc'] = {'status': kwargs['qc_status']}
        if kwargs['qc_details_json']:
            details = parse_json_safe(kwargs['qc_details_json'])
            if details:
                measurement['qc'].update(details)

    if kwargs['processing_json']:
        processing = parse_json_safe(kwargs['processing_json'])
        if processing:
            measurement['processing'] = processing

    if measurement:
        record['measurement'] = measurement

    # Links
    if kwargs['link_rel'] and kwargs['link_target']:
        link = {'rel': kwargs['link_rel'], 'target': kwargs['link_target']}
        if kwargs['link_basis']:
            link['basis'] = kwargs['link_basis']
        if kwargs['link_notes']:
            link['notes'] = kwargs['link_notes']
        record['links'] = [link]

    # Assets
    if kwargs['asset_id'] and kwargs['asset_role'] and kwargs['asset_uri']:
        asset = {
            'asset_id': kwargs['asset_id'],
            'content_role': kwargs['asset_role'],
            'uri': kwargs['asset_uri'],
            # The SHA-256 of the file's bytes; a file the uploader does not hold says so (never zeros).
            'sha256': (kwargs.get('asset_sha256') or '').strip() or 'not_available'
        }
        if kwargs['asset_media_type']:
            asset['media_type'] = kwargs['asset_media_type']
        record['assets'] = [asset]

    # The source paper of a literature record
    doi = (kwargs.get('source_doi') or '').strip()
    for prefix in ('https://doi.org/', 'http://doi.org/', 'https://dx.doi.org/', 'doi.org/', 'doi:'):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):]
    if doi:
        record.setdefault('assets', []).append({
            'asset_id': 'source_paper', 'content_role': 'documentation', 'uri': f'https://doi.org/{doi}',
            'sha256': 'not_available_literature_source', 'citation': {'relation': 'source', 'doi': doi}})

    # Descriptors: one row per value. A value with no stated uncertainty says so ({basis: not_reported});
    # values typed into the form are 'manual'.
    rows = [r for r in (kwargs.get('descriptor_rows') or []) if (r.get('name') or '').strip()]
    potential_row = _potential_row(kwargs)
    if potential_row:
        rows = rows + [potential_row]
    if not rows and kwargs.get('desc_name'):
        rows = [{'name': kwargs['desc_name'], 'value': kwargs.get('desc_value'), 'unit': kwargs.get('desc_unit'),
                 'uncertainty': kwargs.get('desc_uncertainty'), 'kind': kwargs.get('desc_kind'),
                 'source': kwargs.get('desc_source')}]
    if rows:
        output = {'label': (kwargs.get('output_label') or '').strip() or 'results',
                  'generated_utc': datetime.utcnow().isoformat() + "Z",
                  'generated_by': {'agent': (kwargs.get('output_generated_by') or '').strip()
                                   or 'entered in the ISAAC portal record form'},
                  'descriptors': [_descriptor(r) for r in rows]}
        record['descriptors'] = {'outputs': [output]}

    # Merge any extra vocabulary fields that were dynamically rendered
    extra_vocab = kwargs.get('extra_vocab', {})
    for section_name, extras in extra_vocab.items():
        for cat_key, value in extras.items():
            _set_nested(record, cat_key, value)

    # A temperature the source does not state is null with temperature_basis 'not_reported'
    # (the schema requires the key; the form leaves it out when the box is empty).
    ctx = record.get('context')
    if isinstance(ctx, dict) and ctx.get('temperature_basis') == 'not_reported':
        ctx.setdefault('temperature_K', None)
    # "Room temperature" without a number is 298.15 K, flagged by its basis (contract step 7).
    if isinstance(ctx, dict) and ctx.get('temperature_basis') == 'room_temperature':
        ctx.setdefault('temperature_K', 298.15)

    return record


def validate_record(record: dict) -> list:
    """
    FULL ISAAC validation via the shared portal/validation.py module —
    the same schema + vocabulary + semantic checks the REST API enforces.

    (This replaces a former 5-field presence check that let records into
    the database which the API would have rejected. database.save_record
    also re-validates internally, so even if this call were skipped the
    record could not be persisted invalid.)
    """
    import validation
    result = validation.validate_record_full(record)
    return validation.format_errors_flat(result)
