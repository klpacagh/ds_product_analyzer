import re


def normalize_product_name(name: str) -> str:
    """Normalize a product name for consistent matching."""
    name = name.lower().strip()
    # Remove common noise words and special chars
    name = re.sub(r"[^\w\s-]", "", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name)
    # Remove leading articles
    name = re.sub(r"^(the|a|an)\s+", "", name)
    return name.strip()
