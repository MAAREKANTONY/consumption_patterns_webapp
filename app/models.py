from sqlalchemy import Column, Integer, String, Text
from .db import Base

class Pattern(Base):
    __tablename__ = "patterns"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    country = Column(String, index=True)
    json_definition = Column(Text)
