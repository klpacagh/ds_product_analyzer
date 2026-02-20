#!/usr/bin/env python3
"""One-off cleanup: remove non-product entries created by the #tiktokshop collector bug.

Usage:
  python scripts/cleanup_non_products.py           # dry-run (default)
  python scripts/cleanup_non_products.py --execute # apply deletions
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from ds_product_analyzer.db.models import (
    PriceHistory, Product, ProductAlias, RawSignal, TrendScore,
)
from ds_product_analyzer.pipeline.normalizer import normalize_product_name

_NON_PRODUCT_RE = re.compile(
    r"(?i)\b(how\s+to|tutorial|step[\s-]+by[\s-]+step|beginner'?s?\s+guide|"
    r"shop\s+setup|setting\s+up|start\s+(a\s+|your\s+)?shop|"
    r"open\s+(a\s+)?shop|selling\s+on|make\s+money|earn\s+(online|money)|"
    r"passive\s+income|dropship(?:ping)?|supplier)\b"
)

DB_URL = "sqlite:///./data.db"


def is_non_product(text: str | None) -> bool:
    return bool(text and _NON_PRODUCT_RE.search(text))


def main(execute: bool) -> None:
    engine = create_engine(DB_URL)
    with Session(engine) as session:
        # --- Phase 1: products whose canonical_name is a non-product ---
        bad_products = [
            p for p in session.execute(select(Product)).scalars()
            if is_non_product(p.canonical_name)
        ]
        bad_ids = {p.id for p in bad_products}

        print(f"\n{'[DRY RUN] ' if not execute else ''}Phase 1 — Non-product Product rows: {len(bad_products)}")
        for p in bad_products:
            print(f"  id={p.id}  name={p.canonical_name!r}")

        # --- Phase 2: signals on real products where product_name is non-product ---
        contaminated = [
            s for s in session.execute(
                select(RawSignal).where(RawSignal.product_id.is_not(None))
            ).scalars()
            if s.product_id not in bad_ids and is_non_product(s.product_name)
        ]
        print(f"\n{'[DRY RUN] ' if not execute else ''}Phase 2 — Contaminated signals on real products: {len(contaminated)}")
        for s in contaminated:
            print(f"  signal id={s.id}  product_id={s.product_id}  name={s.product_name!r}")

        if not execute:
            print("\nRe-run with --execute to apply.")
            return

        # Apply Phase 1 deletions
        for pid in bad_ids:
            session.execute(delete(TrendScore).where(TrendScore.product_id == pid))
            session.execute(delete(PriceHistory).where(PriceHistory.product_id == pid))
            session.execute(delete(ProductAlias).where(ProductAlias.product_id == pid))
            session.execute(delete(RawSignal).where(RawSignal.product_id == pid))
        if bad_ids:
            session.execute(delete(Product).where(Product.id.in_(bad_ids)))

        # Apply Phase 2 deletions
        for sig in contaminated:
            # Clear bad source_url from the real product if it came from this signal
            if sig.metadata_json:
                try:
                    meta = json.loads(sig.metadata_json)
                    bad_url = meta.get("product_url") or meta.get("url")
                    if bad_url:
                        product = session.get(Product, sig.product_id)
                        if product and product.source_url == bad_url:
                            product.source_url = None
                            print(f"  Cleared source_url on product id={product.id} ({product.canonical_name!r})")
                except (json.JSONDecodeError, TypeError):
                    pass

            # Remove matching alias
            alias_name = normalize_product_name(sig.product_name)
            session.execute(
                delete(ProductAlias).where(
                    ProductAlias.product_id == sig.product_id,
                    ProductAlias.alias_name == alias_name,
                )
            )
            # Delete the signal itself
            session.execute(delete(RawSignal).where(RawSignal.id == sig.id))

        session.commit()
        print(
            f"\nDone. Deleted {len(bad_products)} non-product(s) and "
            f"{len(contaminated)} contaminated signal(s)."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Clean up non-product entries from the TikTok #tiktokshop collector bug."
    )
    parser.add_argument("--execute", action="store_true", help="Apply deletions (default is dry-run)")
    args = parser.parse_args()
    main(execute=args.execute)
