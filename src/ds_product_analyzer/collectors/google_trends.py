import asyncio
import logging
import time

from pytrends.request import TrendReq

from .base import BaseCollector, RawSignalData

logger = logging.getLogger(__name__)

BATCH_SIZE = 5
RATE_LIMIT_SECS = 2.0


class GoogleTrendsCollector(BaseCollector):
    source_name = "google_trends"

    def __init__(self):
        self._pytrends = TrendReq(hl="en-US", tz=360)

    async def collect(self, keywords: list[str]) -> list[RawSignalData]:
        signals: list[RawSignalData] = []

        # Process in batches of 5 (pytrends limit)
        batches = [keywords[i : i + BATCH_SIZE] for i in range(0, len(keywords), BATCH_SIZE)]

        for batch in batches:
            batch_signals = await asyncio.to_thread(self._collect_batch, batch)
            signals.extend(batch_signals)
            await asyncio.sleep(RATE_LIMIT_SECS)

        return signals

    def _collect_batch(self, keywords: list[str]) -> list[RawSignalData]:
        signals: list[RawSignalData] = []

        try:
            self._pytrends.build_payload(keywords, timeframe="today 3-m", geo="US")
        except Exception as e:
            logger.warning("Failed to build payload for %s: %s", keywords, e)
            return signals

        # Interest over time — compute velocity (recent vs older)
        try:
            df = self._pytrends.interest_over_time()
            if df is not None and not df.empty:
                for kw in keywords:
                    if kw not in df.columns:
                        continue
                    series = df[kw]
                    if len(series) < 4:
                        continue

                    recent = series.iloc[-4:].mean()  # last ~4 weeks
                    older = series.iloc[:-4].mean()
                    velocity = ((recent - older) / max(older, 1)) * 100

                    signals.append(RawSignalData(
                        source=self.source_name,
                        product_name=kw,
                        signal_type="search_velocity",
                        value=round(velocity, 2),
                        metadata={
                            "recent_avg": round(recent, 2),
                            "older_avg": round(older, 2),
                            "latest_value": int(series.iloc[-1]),
                        },
                    ))
        except Exception as e:
            logger.warning("Interest over time failed for %s: %s", keywords, e)

        time.sleep(RATE_LIMIT_SECS)

        # Related queries — look for breakout terms
        try:
            related = self._pytrends.related_queries()
            for kw in keywords:
                if kw not in related or related[kw] is None:
                    continue
                rising = related[kw].get("rising")
                if rising is None or rising.empty:
                    continue
                for _, row in rising.iterrows():
                    query = row.get("query", "")
                    val = row.get("value", 0)
                    if not query:
                        continue
                    signals.append(RawSignalData(
                        source=self.source_name,
                        product_name=query,
                        signal_type="breakout" if val == "Breakout" or val > 1000 else "rising",
                        value=float(val) if isinstance(val, (int, float)) else 5000.0,
                        metadata={"parent_keyword": kw},
                    ))
        except Exception as e:
            logger.warning("Related queries failed for %s: %s", keywords, e)

        return signals
