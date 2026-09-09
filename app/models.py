from datetime import datetime
from sqlalchemy import Column, BigInteger, Integer, String, Text, Boolean, DateTime, ForeignKey, Enum, UniqueConstraint, func
from sqlalchemy.orm import relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="USER") # ADMIN, USER
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Book(Base):
    __tablename__ = "books"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    book_id = Column(String(50), unique=True, nullable=False)
    title = Column(String(255), nullable=True)
    detail_url = Column(Text, nullable=False)
    language = Column(String(50), nullable=True)
    author = Column(String(255), nullable=True)
    publisher = Column(String(255), nullable=True)
    total_pages = Column(Integer, nullable=True)
    detail_text = Column(Text, nullable=True)
    detail_meta_description = Column(Text, nullable=True)
    has_audio = Column(Boolean, default=False)
    has_pdf = Column(Boolean, default=False)
    has_translation = Column(Boolean, default=False)
    scrape_status = Column(String(20), nullable=False, default="PENDING")
    last_scraped_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    pages = relationship("BookPage", back_populates="book", cascade="all, delete-orphan")
    files = relationship("File", back_populates="book", cascade="all, delete-orphan")
    errors = relationship("ScrapeError", back_populates="book")

class BookPage(Base):
    __tablename__ = "book_pages"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    book_id = Column(BigInteger, ForeignKey("books.id"), nullable=False)
    page_number = Column(Integer, nullable=False)
    page_url = Column(Text, nullable=False)
    page_title = Column(String(255), nullable=True)
    page_text = Column(Text(length=4294967295), nullable=True) # LONGTEXT equivalent
    page_meta_description = Column(Text, nullable=True)
    page_has_audio = Column(Boolean, nullable=True)
    page_has_pdf = Column(Boolean, nullable=True)
    page_has_translation = Column(Boolean, nullable=True)
    total_pages = Column(Integer, nullable=True)
    scrape_status = Column(String(20), nullable=False, default="PENDING")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (UniqueConstraint('book_id', 'page_number', name='uix_book_id_page_number'),)
    
    book = relationship("Book", back_populates="pages")

class ScrapeRun(Base):
    __tablename__ = "scrape_runs"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    started_by = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    status = Column(String(20), nullable=False, default="PENDING")
    total_books = Column(Integer, default=0)
    completed_books = Column(Integer, default=0)
    failed_books = Column(Integer, default=0)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

class ScrapeError(Base):
    __tablename__ = "scrape_errors"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(BigInteger, ForeignKey("scrape_runs.id"), nullable=True)
    book_id = Column(BigInteger, ForeignKey("books.id"), nullable=True)
    page_number = Column(Integer, nullable=True)
    error_type = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    technical_details = Column(Text, nullable=True)
    url = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    book = relationship("Book", back_populates="errors")

class File(Base):
    __tablename__ = "files"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    book_id = Column(BigInteger, ForeignKey("books.id"), nullable=True)
    file_type = Column(String(50), nullable=False) # e.g., SCRAPED_XLSX, LOG
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    book = relationship("Book", back_populates="files")

class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    action = Column(String(50), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(255), nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
