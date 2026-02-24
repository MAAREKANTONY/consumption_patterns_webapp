from sqlalchemy import Column, Integer, String, Text, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship
from .db import Base
from sqlalchemy.sql import func
from sqlalchemy import DateTime, Float

class Country(Base):
    __tablename__ = "countries"

    code = Column(String, primary_key=True)  # e.g. FR, IL, UK
    label = Column(String, nullable=False, default="")
    timezone = Column(String, nullable=False, default="UTC")

    patterns = relationship("PatternV2", back_populates="country")

class PatternV2(Base):
    __tablename__ = "patterns_v2"
    __table_args__ = (
        UniqueConstraint("country_code", "pattern_id", name="uq_country_pattern"),
    )

    id = Column(Integer, primary_key=True, index=True)
    country_code = Column(String, ForeignKey("countries.code"), index=True, nullable=False)

    pattern_id = Column(String, index=True, nullable=False)   # canonical id used for scoring
    label = Column(String, nullable=False, default="")
    dimensions_json = Column(Text, nullable=False)  # stores {"revenue_by_momentum":..., "category_mix_by_momentum":...}

    country = relationship("Country", back_populates="patterns")

# Legacy v1.1 model kept to avoid breaking existing DBs
class Pattern(Base):
    __tablename__ = "patterns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    country = Column(String, index=True)
    json_definition = Column(Text)


# --- Premiumisation ---

class PremiumProfile(Base):
    __tablename__ = "premium_profiles"
    __table_args__ = (
        UniqueConstraint("country_code", "profile_id", name="uq_country_premium_profile"),
    )

    id = Column(Integer, primary_key=True, index=True)
    country_code = Column(String, ForeignKey("countries.code"), index=True, nullable=False)
    profile_id = Column(String, index=True, nullable=False)  # e.g. FR_v1
    label = Column(String, nullable=False, default="")
    config_json = Column(Text, nullable=False)

    country = relationship("Country")


class PremiumTicketPercentiles(Base):
    __tablename__ = "premium_ticket_percentiles"
    __table_args__ = (
        UniqueConstraint("country_code", "segment_key", name="uq_country_segment_percentiles"),
    )

    id = Column(Integer, primary_key=True, index=True)
    country_code = Column(String, ForeignKey("countries.code"), index=True, nullable=False)
    segment_key = Column(String, index=True, nullable=False)

    p50 = Column(Float, nullable=False)
    p80 = Column(Float, nullable=False)
    p95 = Column(Float, nullable=False)
    computed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    country = relationship("Country")
