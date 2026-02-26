"""BSR-to-estimated-monthly-sales lookup tables.

Uses Jungle Scout methodology: tiered (bsr_rank, monthly_sales) breakpoints
with linear interpolation between tiers.
"""

# Category-specific tiered tables: list of (bsr_rank, monthly_sales) tuples
# Each table is sorted by ascending bsr_rank.
_TABLES: dict[str, list[tuple[int, int]]] = {
    "electronics": [
        (1, 50000), (10, 30000), (50, 15000), (100, 8000),
        (500, 3000), (1000, 1500), (5000, 400), (10000, 150),
        (50000, 30), (100000, 10),
    ],
    "home-kitchen": [
        (1, 45000), (10, 25000), (50, 12000), (100, 6000),
        (500, 2500), (1000, 1200), (5000, 350), (10000, 120),
        (50000, 25), (100000, 8),
    ],
    "beauty": [
        (1, 40000), (10, 22000), (50, 10000), (100, 5000),
        (500, 2000), (1000, 900), (5000, 280), (10000, 100),
        (50000, 20), (100000, 6),
    ],
    "sports": [
        (1, 35000), (10, 18000), (50, 8000), (100, 4000),
        (500, 1600), (1000, 750), (5000, 220), (10000, 80),
        (50000, 15), (100000, 5),
    ],
    "pets": [
        (1, 30000), (10, 16000), (50, 7500), (100, 3800),
        (500, 1500), (1000, 700), (5000, 200), (10000, 70),
        (50000, 12), (100000, 4),
    ],
    "toys": [
        (1, 38000), (10, 20000), (50, 9000), (100, 4500),
        (500, 1800), (1000, 850), (5000, 250), (10000, 90),
        (50000, 18), (100000, 6),
    ],
    "office": [
        (1, 25000), (10, 13000), (50, 6000), (100, 3000),
        (500, 1200), (1000, 550), (5000, 160), (10000, 55),
        (50000, 10), (100000, 3),
    ],
    "health": [
        (1, 42000), (10, 23000), (50, 11000), (100, 5500),
        (500, 2200), (1000, 1000), (5000, 300), (10000, 110),
        (50000, 22), (100000, 7),
    ],
    "clothing": [
        (1, 55000), (10, 30000), (50, 14000), (100, 7000),
        (500, 2800), (1000, 1300), (5000, 380), (10000, 135),
        (50000, 28), (100000, 9),
    ],
    "tools": [
        (1, 20000), (10, 10000), (50, 4800), (100, 2400),
        (500, 950), (1000, 440), (5000, 130), (10000, 45),
        (50000, 8), (100000, 2),
    ],
    "automotive": [
        (1, 18000), (10, 9000), (50, 4200), (100, 2100),
        (500, 840), (1000, 390), (5000, 115), (10000, 40),
        (50000, 7), (100000, 2),
    ],
    "video-games": [
        (1, 32000), (10, 17000), (50, 8000), (100, 4000),
        (500, 1600), (1000, 750), (5000, 220), (10000, 80),
        (50000, 14), (100000, 4),
    ],
}

_DEFAULT_TABLE: list[tuple[int, int]] = [
    (1, 30000), (10, 15000), (50, 7000), (100, 3500),
    (500, 1400), (1000, 650), (5000, 190), (10000, 65),
    (50000, 12), (100000, 4),
]


def _interpolate(table: list[tuple[int, int]], bsr_rank: int) -> int:
    """Linear interpolation between adjacent table breakpoints."""
    if bsr_rank <= table[0][0]:
        return table[0][1]
    if bsr_rank >= table[-1][0]:
        # Extrapolate downward (sales drop off quickly at high ranks)
        last_rank, last_sales = table[-1]
        ratio = last_rank / bsr_rank
        return max(1, int(last_sales * ratio))

    for i in range(len(table) - 1):
        r0, s0 = table[i]
        r1, s1 = table[i + 1]
        if r0 <= bsr_rank <= r1:
            frac = (bsr_rank - r0) / (r1 - r0)
            return int(s0 + frac * (s1 - s0))

    return table[-1][1]


def estimate_monthly_sales(bsr_rank: int, category: str) -> int:
    """Estimate monthly unit sales from BSR rank and category.

    Uses category-specific tiered lookup tables (Jungle Scout methodology)
    with linear interpolation between breakpoints.
    """
    table = _TABLES.get(category, _DEFAULT_TABLE)
    return _interpolate(table, bsr_rank)


def estimate_monthly_revenue(bsr_rank: int, category: str, price: float) -> float:
    """Estimate monthly revenue from BSR rank, category, and price."""
    sales = estimate_monthly_sales(bsr_rank, category)
    return round(sales * price, 2)
