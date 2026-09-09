from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.database import engine, Base
from app.routes import auth, admin, books, scraper, files

# For development, you can create all tables on startup (though we have init_db.py)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Simple Book Scraper UI")

# Mount static files and templates
import os
os.makedirs("app/static", exist_ok=True)
os.makedirs("app/templates", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# We can keep a global templates object if needed
templates = Jinja2Templates(directory="app/templates")

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(books.router, prefix="/books", tags=["books"])
app.include_router(scraper.router, prefix="/scraper", tags=["scraper"])
app.include_router(files.router, prefix="/files", tags=["files"])

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    # If user is not logged in, they will be redirected to /auth/login
    from app.auth import get_current_user_from_cookie
    from app.database import SessionLocal
    
    db = SessionLocal()
    user = await get_current_user_from_cookie(request, db)
    db.close()
    
    if not user:
        return RedirectResponse(url="/auth/login")
    
    if user.role == "ADMIN":
        return RedirectResponse(url="/admin/dashboard")
    return RedirectResponse(url="/books/dashboard")
