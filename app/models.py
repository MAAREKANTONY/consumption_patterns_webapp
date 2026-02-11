from sqlalchemy import Column, Integer, String, Text, DateTime, Float
from sqlalchemy.sql import func
from .db import Base

class Pattern(Base):
    __tablename__ = "patterns"
    id = Column(Integer, primary_key=True, index=True)
    pattern_id = Column(String, index=True)     # e.g. fast_food
    label = Column(String, index=True)          # e.g. Fast food
    country_profile = Column(String, index=True) # e.g. FR
    json_definition = Column(Text)              # full pattern JSON (1 object)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Upload(Base):
    __tablename__ = "uploads"
    id = Column(Integer, primary_key=True, index=True)
    country_profile = Column(String, index=True)
    filename = Column(String)
    stored_path = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class RunResult(Base):
    __tablename__ = "run_results"
    id = Column(Integer, primary_key=True, index=True)
    upload_id = Column(Integer, index=True)
    country_profile = Column(String, index=True)
    selected_pattern_id = Column(String, nullable=True)
    signature_json = Column(Text)          # computed signature JSON
    scores_json = Column(Text)             # list of {pattern_id, distance, probability}
    created_at = Column(DateTime(timezone=True), server_default=func.now())
