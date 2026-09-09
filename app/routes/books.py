from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database import get_db
from app.models import Book, BookPage
from app.auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/dashboard", response_class=HTMLResponse)
async def books_dashboard(
    request: Request,
    search: str = "",
    status: str = "",
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    query = db.query(Book)
    
    if search:
        query = query.filter(or_(
            Book.title.ilike(f"%{search}%"),
            Book.book_id.ilike(f"%{search}%")
        ))
    if status:
        query = query.filter(Book.scrape_status == status)
        
    books = query.order_by(Book.id.desc()).all()
    
    return templates.TemplateResponse(request, "user/books.html", {
        "request": request,
        "user": current_user,
        "books": books,
        "search": search,
        "status": status
    })

@router.get("/{b_id}/pages", response_class=HTMLResponse)
async def book_pages(
    request: Request,
    b_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    book = db.query(Book).filter(Book.id == b_id).first()
    if not book:
        return HTMLResponse("Book not found", status_code=404)
        
    pages = db.query(BookPage).filter(BookPage.book_id == b_id).order_by(BookPage.page_number).all()
    
    return templates.TemplateResponse(request, "user/pages.html", {
        "request": request,
        "user": current_user,
        "book": book,
        "pages": pages
    })
