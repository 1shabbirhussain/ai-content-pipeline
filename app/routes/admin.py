from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Book, ScrapeRun, ScrapeError, AuditLog, BookPage
from app.auth import get_current_admin_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_admin_user)):
    total_books = db.query(Book).count()
    pending = db.query(Book).filter(Book.scrape_status == "PENDING").count()
    processing = db.query(Book).filter(Book.scrape_status == "PROCESSING").count()
    scraped = db.query(Book).filter(Book.scrape_status == "SCRAPED").count()
    failed = db.query(Book).filter(Book.scrape_status == "FAILED").count()
    
    last_run = db.query(ScrapeRun).order_by(ScrapeRun.id.desc()).first()
    recent_errors = db.query(ScrapeError).order_by(ScrapeError.id.desc()).limit(5).all()
    
    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "request": request,
        "user": current_user,
        "stats": {
            "total_books": total_books,
            "pending": pending,
            "processing": processing,
            "scraped": scraped,
            "failed": failed,
        },
        "last_run": last_run,
        "recent_errors": recent_errors
    })

@router.get("/books", response_class=HTMLResponse)
async def admin_books(request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_admin_user)):
    books = db.query(Book).order_by(Book.id.desc()).all()
    return templates.TemplateResponse(request, "admin/books.html", {
        "request": request,
        "user": current_user,
        "books": books
    })

@router.post("/books/add")
async def add_book(
    request: Request,
    book_id: str = Form(...),
    detail_url: str = Form(...),
    title: str = Form(""),
    status: str = Form("PENDING"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_admin_user)
):
    existing = db.query(Book).filter(Book.book_id == book_id).first()
    if existing:
        return RedirectResponse(url="/admin/books?error=Book+ID+already+exists", status_code=303)
        
    book = Book(
        book_id=book_id,
        detail_url=detail_url,
        title=title,
        scrape_status=status
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    
    audit = AuditLog(
        user_id=current_user.id,
        action="BOOK_ADDED",
        entity_type="Book",
        entity_id=book_id
    )
    db.add(audit)
    db.commit()
    
    return RedirectResponse(url="/admin/books?success=Book+added", status_code=303)

@router.post("/books/{b_id}/edit")
async def edit_book(
    request: Request,
    b_id: int,
    detail_url: str = Form(...),
    title: str = Form(...),
    status: str = Form(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_admin_user)
):
    book = db.query(Book).filter(Book.id == b_id).first()
    if not book:
        return RedirectResponse(url="/admin/books?error=Book+not+found", status_code=303)
        
    old_url = book.detail_url
    old_status = book.scrape_status
    
    book.detail_url = detail_url
    book.title = title
    book.scrape_status = status
    db.commit()
    
    # Audit log
    if old_url != detail_url:
        db.add(AuditLog(
            user_id=current_user.id,
            action="URL_UPDATED",
            entity_type="Book",
            entity_id=book.book_id,
            old_value=old_url,
            new_value=detail_url
        ))
    if old_status != status:
        db.add(AuditLog(
            user_id=current_user.id,
            action="STATUS_CHANGED",
            entity_type="Book",
            entity_id=book.book_id,
            old_value=old_status,
            new_value=status
        ))
    db.commit()
    
    return RedirectResponse(url="/admin/books?success=Book+updated", status_code=303)

@router.post("/books/{b_id}/delete")
async def delete_book(
    request: Request,
    b_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_admin_user)
):
    book = db.query(Book).filter(Book.id == b_id).first()
    if book:
        book_identifier = book.book_id
        db.delete(book)
        db.add(AuditLog(
            user_id=current_user.id,
            action="BOOK_DELETED",
            entity_type="Book",
            entity_id=book_identifier
        ))
        db.commit()
    return RedirectResponse(url="/admin/books?success=Book+deleted", status_code=303)

@router.post("/books/{b_id}/retry")
async def retry_book(
    request: Request,
    b_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_admin_user)
):
    book = db.query(Book).filter(Book.id == b_id).first()
    if book:
        old_status = book.scrape_status
        book.scrape_status = "PENDING"
        db.add(AuditLog(
            user_id=current_user.id,
            action="SCRAPE_RETRIED",
            entity_type="Book",
            entity_id=book.book_id,
            old_value=old_status,
            new_value="PENDING"
        ))
        db.commit()
    return RedirectResponse(url="/admin/books?success=Book+queued+for+retry", status_code=303)
