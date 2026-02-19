import re

_PLATFORM_STOPWORDS = {"amazon", "walmart", "ebay", "tiktok", "reddit", "aliexpress", "etsy"}


def normalize_product_name(name: str) -> str:
    """Normalize a product name for consistent matching."""
    name = name.lower().strip()
    # Remove common noise words and special chars
    name = re.sub(r"[^\w\s-]", "", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name)
    # Remove leading articles
    name = re.sub(r"^(the|a|an)\s+", "", name)
    # Remove platform/marketplace names that add no matching value
    tokens = [t for t in name.split() if t not in _PLATFORM_STOPWORDS]
    name = " ".join(tokens)
    return name.strip()
