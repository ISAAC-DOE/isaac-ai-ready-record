"""
ISAAC AI-Ready Record - Manual Entry Form
Streamlit-based form for creating ISAAC records manually
"""

import streamlit as st
import json
from datetime import datetime
import database
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


def get_vocab_values(section: str, category: str) -> list:
    """Get allowed values from vocabulary for dropdowns"""
    vocab = ontology.load_vocabulary()
    if section in vocab and category in vocab[section]:
        return vocab[section][category].get('values', [])
    return []


def render_extra_vocab_fields(section: str, handled_categories: list, prefix: str) -> dict:
    """
    Render selectboxes for any vocabulary categories in a section
    that aren't already handled by the hardcoded form fields.

    Returns dict of {category_key: selected_value} for categories rendered.
    """
    vocab = ontology.load_vocabulary()
    extra = {}
    if section not in vocab:
        return extra
    for cat_key, cat_data in vocab[section].items():
        if cat_key in handled_categories:
            continue
        values = cat_data.get('values', [])
        if not values:
            continue
        desc = cat_data.get('description', '')
        options = [""] + values
        selected = st.selectbox(
            cat_key,
            options,
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
            record_version = st.text_input("Record Version", value="1.0", disabled=True)

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
                ["sample.sample_form", "sample.material.provenance", "sample.material.identifiers.scheme"],
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
        with st.expander("6. Context (Optional)", expanded=False):
            st.caption("Experimental or simulation conditions")

            col1, col2 = st.columns(2)
            with col1:
                environment_options = [""] + get_vocab_values("Context", "context.environment")
                environment = st.selectbox("Environment", environment_options)
            with col2:
                temperature_k = st.number_input("Temperature (K)", min_value=0.0, value=None, format="%.2f")

            # Electrochemistry context
            st.write("**Electrochemistry**")
            col1, col2, col3 = st.columns(3)
            with col1:
                reaction_options = [""] + get_vocab_values("Context", "context.electrochemistry.reaction")
                echem_reaction = st.selectbox("Reaction", reaction_options)
            with col2:
                cell_type_options = [""] + get_vocab_values("Context", "context.electrochemistry.cell_type")
                echem_cell_type = st.selectbox("Cell Type", cell_type_options)
            with col3:
                potential_scale_options = [""] + get_vocab_values("Context", "context.electrochemistry.potential_scale")
                echem_potential_scale = st.selectbox("Potential Scale", potential_scale_options)

            context_additional_json = st.text_area(
                "Additional Context (JSON)",
                placeholder='{"pressure_Pa": 101325, "humidity_percent": 45}',
                height=80
            )

            extra_context = render_extra_vocab_fields(
                "Context",
                ["context.environment", "context.electrochemistry.reaction",
                 "context.electrochemistry.cell_type", "context.electrochemistry.potential_scale"],
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
                link_basis = st.text_input("Basis", placeholder="Reasoning for this link")
                link_notes = st.text_input("Notes", placeholder="Additional notes")

            extra_links = render_extra_vocab_fields(
                "Links",
                ["links.rel"],
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
                asset_sha256 = st.text_input("SHA256 Hash", placeholder="64-character hex string")
            asset_media_type = st.text_input("Media Type", placeholder="e.g., application/json")

            extra_assets = render_extra_vocab_fields(
                "Assets",
                ["assets.content_role"],
                "ast"
            )

        # =====================================================================
        # SECTION 10: Descriptors (Optional)
        # =====================================================================
        with st.expander("10. Descriptors (Optional)", expanded=False):
            st.caption("Scientific claims and extracted features")

            desc_policy_json = st.text_area("Policy (JSON)", placeholder='{"extraction_allowed": true}', height=60)

            st.write("**Output Set**")
            output_label = st.text_input("Output Label", placeholder="e.g., automated_analysis_v1")
            output_generated_by = st.text_input("Generated By", placeholder="e.g., ML model v2.1")

            st.write("**Descriptor**")
            col1, col2 = st.columns(2)
            with col1:
                desc_name = st.text_input("Descriptor Name", placeholder="e.g., band_gap")
                desc_kind_options = [""] + get_vocab_values("Descriptors", "descriptors.outputs.descriptors.kind")
                desc_kind = st.selectbox("Kind", desc_kind_options)
                desc_source = st.text_input("Source", placeholder="e.g., DFT calculation")
            with col2:
                desc_value = st.text_input("Value", placeholder="e.g., 1.12")
                desc_unit = st.text_input("Unit", placeholder="e.g., eV")
                desc_uncertainty = st.text_input("Uncertainty", placeholder="e.g., 0.05")

            extra_descriptors = render_extra_vocab_fields(
                "Descriptors",
                ["descriptors.outputs.descriptors.kind", "descriptors.theoretical_metric"],
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
        record = build_record(
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
            desc_policy_json=desc_policy_json,
            output_label=output_label,
            output_generated_by=output_generated_by,
            desc_name=desc_name,
            desc_kind=desc_kind,
            desc_source=desc_source,
            desc_value=desc_value,
            desc_unit=desc_unit,
            desc_uncertainty=desc_uncertainty,
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

        # Validate required fields
        errors = validate_record(record)

        if errors:
            st.error("Validation errors:")
            for err in errors:
                st.write(f"- {err}")
        else:
            if submitted:
                st.subheader("Record Preview")
                st.json(record)

            if save_submitted:
                if database.test_db_connection():
                    try:
                        saved_id = database.save_record(record)
                        st.success(f"Record saved successfully! ID: {saved_id}")
                        # Generate new ID for next record
                        st.session_state.record_id = generate_ulid()
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


def build_record(**kwargs) -> dict:
    """Build an ISAAC record from form inputs"""

    record = {
        "isaac_record_version": "1.0",
        "record_id": kwargs['record_id'],
        "record_type": kwargs['record_type'],
        "record_domain": kwargs['record_domain'],
        "timestamps": {},
    }

    # Source Type
    if kwargs['source_type']:
        record['source_type'] = kwargs['source_type']

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
    if kwargs['composition_json']:
        comp = parse_json_safe(kwargs['composition_json'])
        if comp:
            sample['composition'] = comp
    if kwargs['geometry_json']:
        geom = parse_json_safe(kwargs['geometry_json'])
        if geom:
            sample['geometry'] = geom
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
    if kwargs['temperature_k'] is not None and kwargs['temperature_k'] > 0:
        context['temperature_K'] = kwargs['temperature_k']
    if kwargs['echem_reaction'] or kwargs['echem_cell_type'] or kwargs['echem_potential_scale']:
        context['electrochemistry'] = {}
        if kwargs['echem_reaction']:
            context['electrochemistry']['reaction'] = kwargs['echem_reaction']
        if kwargs['echem_cell_type']:
            context['electrochemistry']['cell_type'] = kwargs['echem_cell_type']
        if kwargs['echem_potential_scale']:
            context['electrochemistry']['potential_scale'] = kwargs['echem_potential_scale']
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
    if kwargs['asset_id'] and kwargs['asset_role'] and kwargs['asset_uri'] and kwargs['asset_sha256']:
        asset = {
            'asset_id': kwargs['asset_id'],
            'content_role': kwargs['asset_role'],
            'uri': kwargs['asset_uri'],
            'sha256': kwargs['asset_sha256']
        }
        if kwargs['asset_media_type']:
            asset['media_type'] = kwargs['asset_media_type']
        record['assets'] = [asset]

    # Descriptors
    if kwargs['output_label'] or kwargs['desc_name']:
        descriptors = {'outputs': []}

        output = {}
        if kwargs['output_label']:
            output['label'] = kwargs['output_label']
        output['generated_utc'] = datetime.utcnow().isoformat() + "Z"
        if kwargs['output_generated_by']:
            output['generated_by'] = {'agent': kwargs['output_generated_by']}

        if kwargs['desc_name'] and kwargs['desc_kind'] and kwargs['desc_source']:
            desc = {
                'name': kwargs['desc_name'],
                'kind': kwargs['desc_kind'],
                'source': kwargs['desc_source']
            }
            # Parse value as number if possible
            if kwargs['desc_value']:
                try:
                    desc['value'] = float(kwargs['desc_value'])
                except ValueError:
                    desc['value'] = kwargs['desc_value']
            if kwargs['desc_unit']:
                desc['unit'] = kwargs['desc_unit']
            if kwargs['desc_uncertainty']:
                try:
                    desc['uncertainty'] = {'sigma': float(kwargs['desc_uncertainty'])}
                except ValueError:
                    pass
            output['descriptors'] = [desc]

        if output:
            descriptors['outputs'].append(output)

        if kwargs['desc_policy_json']:
            policy = parse_json_safe(kwargs['desc_policy_json'])
            if policy:
                descriptors['policy'] = policy

        if descriptors['outputs']:
            record['descriptors'] = descriptors

    # Merge any extra vocabulary fields that were dynamically rendered
    extra_vocab = kwargs.get('extra_vocab', {})
    for section_name, extras in extra_vocab.items():
        for cat_key, value in extras.items():
            _set_nested(record, cat_key, value)

    return record


def validate_record(record: dict) -> list:
    """Validate required fields in an ISAAC record"""
    errors = []

    if not record.get('record_id'):
        errors.append("Record ID is required")
    elif len(record['record_id']) != 26:
        errors.append("Record ID must be exactly 26 characters")

    if not record.get('record_type'):
        errors.append("Record Type is required")

    if not record.get('record_domain'):
        errors.append("Record Domain is required")

    if not record.get('timestamps', {}).get('created_utc'):
        errors.append("Created timestamp is required")

    if not record.get('source_type'):
        errors.append("Source Type is required")

    return errors
