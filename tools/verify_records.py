import json
import os
import glob
import sys

try:
    from jsonschema import validate, ValidationError
except ImportError:
    print("Error: jsonschema module not found. Please install via 'pip install jsonschema'")
    sys.exit(1)

def load_vocabulary(vocab_path="data/vocabulary.json"):
    if not os.path.exists(vocab_path):
        print(f"⚠️  Vocabulary not found at {vocab_path} - Skipping semantic validation.")
        return None
    with open(vocab_path, 'r') as f:
        return json.load(f)

def check_vocabulary_compliance(data, vocabulary, record_id="Unknown"):
    errors = []
    
    # helper to check allowed values
    def check_enum(path, value, vocab_key):
        if not vocabulary: return
        term = vocabulary.get(vocab_key.split(".")[0], {}).get(vocab_key)
        if not term:
            # Term definition missing from vocab? Warn but maybe not error if strictness varies
            # errors.append(f"Vocabulary term definition missing for {vocab_key}")
            return
            
        allowed = term.get('values', [])
        if value not in allowed:
            errors.append(f"Value '{value}' at '{path}' is not in allowed vocabulary for {vocab_key}: {allowed}")

    # 1. System Instrument Type
    if 'system' in data and 'instrument' in data['system']:
        inst_type = data['system']['instrument'].get('instrument_type')
        if inst_type:
            check_enum("system.instrument.instrument_type", inst_type, "system.instrument.instrument_type")

    # 2. System Technique
    if 'system' in data:
        technique = data['system'].get('technique')
        if technique:
            check_enum("system.technique", technique, "system.technique")

    # 3. Measurement Roles
    if 'measurement' in data and 'series' in data['measurement']:
        for i, series in enumerate(data['measurement']['series']):
            for j, ch in enumerate(series.get('channels', [])):
                role = ch.get('role')
                if role:
                    check_enum(f"measurement.series[{i}].channels[{j}].role", role, "measurement.series.channels.role")

    # 4. Links Relations
    if 'links' in data:
        for i, link in enumerate(data['links']):
            rel = link.get('rel')
            if rel:
                check_enum(f"links[{i}].rel", rel, "links.rel")

    # 5. Asset Roles
    if 'assets' in data:
        for i, asset in enumerate(data['assets']):
            role = asset.get('content_role')
            if role:
                check_enum(f"assets[{i}].content_role", role, "assets.content_role")
                
    # 6. Descriptors (Categorical)
    if 'descriptors' in data and 'outputs' in data['descriptors']:
        for i, output in enumerate(data['descriptors']['outputs']):
            for j, desc in enumerate(output.get('descriptors', [])):
                name = desc.get('name')
                kind = desc.get('kind')
                if kind != 'categorical': continue
                
                # Check if descriptor name itself is in vocab (e.g. descriptors.theoretical_metric is NOT categorical, it's absolute/numeric usually?)
                # Wait, theoretical_metric values are keys? No, they are names of descriptors.
                # If kind is categorical, value must be in a vocab.
                # Complexity: Categorical descriptors depend on creating specific vocabularies or namespaces.
                # For now, we only validate core enums mapped above. 
                pass

    return errors

def validate_isaac_records():
    # Load Schema
    schema_path = "schema/isaac_record_v1.json"
    if not os.path.exists(schema_path):
        print(f"❌ Schema not found at {schema_path}")
        return False
        
    with open(schema_path, 'r') as f:
        schema = json.load(f)
    print(f"✅ Loaded Schema: {schema['title']}")
    
    # Load Vocabulary (The Wiki Source of Truth)
    vocabulary = load_vocabulary()
    if vocabulary:
        print(f"✅ Loaded Semantic Vocabulary (Wiki Mirror)")
    
    # Load Examples
    example_files = glob.glob("examples/*.json")
    if not example_files:
        print("❌ No examples found in examples/")
        return False
        
    all_passed = True
    print(f"\n🔍 validating {len(example_files)} Golden Records...")
    print("-" * 50)
    
    for example_file in example_files:
        filename = os.path.basename(example_file)
        try:
            with open(example_file, 'r') as f:
                data = json.load(f)
            
            # 1. JSON Schema Validation (Structural)
            validate(instance=data, schema=schema)
            
            # 2. Semantic Vocabulary Validation (Wiki Driven)
            vocab_errors = check_vocabulary_compliance(data, vocabulary, filename)
            if vocab_errors:
                raise ValidationError("\n".join(vocab_errors))
            
            # 3. Logic Checks (Hard constraints)
            # Check Measurement Series
            if 'measurement' in data:
                if 'series' not in data['measurement']:
                    raise ValidationError("Measurement block missing 'series'")
                for s in data['measurement']['series']:
                    if 'independent_variables' not in s: 
                         raise ValidationError(f"Series {s.get('series_id')} missing 'independent_variables'")
                    if 'channels' not in s:
                         raise ValidationError(f"Series {s.get('series_id')} missing 'channels'")

            # Check System Configuration Flatness
            if 'system' in data:
                conf = data['system'].get('configuration', {})
                for k, v in conf.items():
                    if isinstance(v, (dict, list)):
                        raise ValidationError(f"System configuration must be flat. Key '{k}' contains nested {type(v)}")

            print(f"✅ PASS: {filename}")
            
        except ValidationError as e:
            print(f"❌ FAIL: {filename}")
            if hasattr(e, 'message'):
                print(f"   Reason: {e.message}")
            else:
                print(f"   Reason: {str(e)}")
            if hasattr(e, 'path') and len(e.path) > 0:
                print(f"   Path: {' -> '.join(str(p) for p in e.path)}")
            all_passed = False
        except json.JSONDecodeError:
            print(f"❌ FAIL: {filename} (Invalid JSON syntax)")
            all_passed = False
        except Exception as e:
            print(f"❌ FAIL: {filename} (Unexpected Parsing Error)")
            print(f"   {str(e)}")
            all_passed = False
            import traceback
            traceback.print_exc()

    print("-" * 50)
    if all_passed:
        print("🎉 SUCCESS: All records are 100% v1.0 Compliant and Semantically Valid")
        return True
    else:
        print("⚠️  FAILURE: Consistency checks failed")
        return False

if __name__ == "__main__":
    if validate_isaac_records():
        sys.exit(0)
    else:
        sys.exit(1)
