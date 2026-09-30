"""Which vocabulary categories the record form may offer as dropdowns.

The vocabulary holds two kinds of entries: the allowed values of a record field
(context.environment, system.technique), and data that the validator and the discovery engine
read (system.organizations, a registry of names with their ROR ids; placeholder lists;
descriptor class lists; unit lists). The form writes a dropdown's value into the record at the
category's dotted path, so only the first kind may become a dropdown: a category whose path
reaches a text field of the record through objects alone. Anything else, offered as a
dropdown, either crashes the form (a registry is a dict, not a list) or writes a field the
schema rejects.
"""
import ontology
import validation


def is_text_field(dotted: str) -> bool:
    """True when the dotted path reaches a string field of the record through objects only."""
    node = validation.ISAAC_SCHEMA
    for part in dotted.split("."):
        props = node.get("properties") if isinstance(node, dict) else None
        if not isinstance(props, dict) or part not in props:
            return False
        node = props[part]
    types = node.get("type")
    types = set(types) if isinstance(types, list) else {types}
    return "string" in types and not types & {"array", "object"}


def options(values) -> list:
    """The selectable values of a category, stored as a list or as a registry keyed by value."""
    if isinstance(values, dict):
        return [str(k) for k in values]
    if isinstance(values, (list, tuple)):
        return [v for v in values if isinstance(v, str)]
    return []


def extra_categories(section: str, handled: list, vocab: dict = None) -> list:
    """[(category, options, description)] for the text-field categories of a vocabulary section
    that the form does not render itself."""
    vocab = ontology.load_vocabulary() if vocab is None else vocab
    out = []
    for key, data in (vocab.get(section) or {}).items():
        if key in handled or not isinstance(data, dict) or not is_text_field(key):
            continue
        opts = options(data.get("values"))
        if opts:
            out.append((key, opts, data.get("description", "")))
    return out
