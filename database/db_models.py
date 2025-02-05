from pgvector.utils import Vector
from sqlalchemy import Integer, Column, Text, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class DocumentEntry(Base):
    """
    SQLAlchemy model for storing documents in PostgreSQL.
    This model includes:
      - the original page content,
      - metadata (stored as JSONB),
      - a vector column (using pgvector) to store embeddings,
      - additional columns (e.g. 'source').
    """
    __tablename__ = 'documents'

    id = Column(Integer, primary_key=True, autoincrement=True)
    page_content = Column(Text, nullable=False)
    metadata = Column(JSONB)
    vector = Column(Vector(1536))  # Adjust the dimension as needed.
    # An additional column example:
    source = Column(String, nullable=True)

    def __repr__(self):
        return f"<DocumentEntry(id={self.id}, source={self.source})>"
