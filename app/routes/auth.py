from datetime import timedelta
from fastapi import APIRouter, Depends, Request, Form, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.auth import verify_password, create_access_token, get_current_user_from_cookie
from app.config import ACCESS_TOKEN_EXPIRE_MINUTES

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, db: Session = Depends(get_db)):
    user = await get_current_user_from_cookie(request, db)
    if user:
        if user.role == "ADMIN":
            return RedirectResponse(url="/admin/dashboard")
        return RedirectResponse(url="/books/dashboard")
    return templates.TemplateResponse(request, "login.html", {"request": request})

@router.post("/login")
async def login_post(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": "Invalid username or password"})
    
    if not user.is_active:
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": "User is inactive"})

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role}, expires_delta=access_token_expires
    )
    
    # We create a redirect response and set the cookie
    redirect_url = "/admin/dashboard" if user.role == "ADMIN" else "/books/dashboard"
    res = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    res.set_cookie(
        key="access_token", 
        value=f"Bearer {access_token}", 
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
    )
    return res

@router.get("/logout")
async def logout(response: Response):
    res = RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    res.delete_cookie(key="access_token")
    return res
