from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    seed_keywords: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    products: Mapped[list["Product"]] = relationship(back_populates="category_rel")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_high: Mapped[float | None] = mapped_column(Float, nullable=True)

    category_rel: Mapped[Category | None] = relationship(back_populates="products")
    aliases: Mapped[list["ProductAlias"]] = relationship(back_populates="product")
    raw_signals: Mapped[list["RawSignal"]] = relationship(back_populates="product")
    trend_scores: Mapped[list["TrendScore"]] = relationship(back_populates="product")
    price_history: Mapped[list["PriceHistory"]] = relationship(back_populates="product")


class ProductAlias(Base):
    __tablename__ = "product_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    alias_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)

    product: Mapped[Product] = relationship(back_populates="aliases")


class RawSignal(Base):
    __tablename__ = "raw_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    signal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON blob
    collected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    product_name: Mapped[str | None] = mapped_column(String(300), nullable=True)

    product: Mapped[Product | None] = relationship(back_populates="raw_signals")


class TrendScore(Base):
    __tablename__ = "trend_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    google_velocity: Mapped[float] = mapped_column(Float, default=0.0)
    reddit_accel: Mapped[float] = mapped_column(Float, default=0.0)
    amazon_accel: Mapped[float] = mapped_column(Float, default=0.0)
    tiktok_accel: Mapped[float] = mapped_column(Float, default=0.0)
    platform_count: Mapped[int] = mapped_column(Integer, default=0)
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)
    # Enhanced scoring columns
    search_accel: Mapped[float] = mapped_column(Float, default=0.0)
    social_velocity: Mapped[float] = mapped_column(Float, default=0.0)
    price_fit: Mapped[float] = mapped_column(Float, default=0.0)
    trend_shape: Mapped[float] = mapped_column(Float, default=0.0)
    purchase_intent: Mapped[float] = mapped_column(Float, default=0.0)
    recency: Mapped[float] = mapped_column(Float, default=0.0)
    shopify_momentum: Mapped[float] = mapped_column(Float, default=0.0)
    aliexpress_momentum: Mapped[float] = mapped_column(Float, default=0.0)
    # Future placeholders
    ad_longevity: Mapped[float] = mapped_column(Float, default=0.0)
    review_growth: Mapped[float] = mapped_column(Float, default=0.0)
    saturation: Mapped[float] = mapped_column(Float, default=0.0)
    scored_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="trend_scores")


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # "amazon" or "tiktok"
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="price_history")
