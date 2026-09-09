from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_current_user
from app.services.scrape_service import start_scraper, stop_scraper, get_scraper_status

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/dashboard", response_class=HTMLResponse)
async def scraper_dashboard(request: Request, current_user = Depends(get_current_user)):
    return templates.TemplateResponse(request, "user/scraper.html", {
        "request": request,
        "user": current_user
    })

@router.post("/start")
async def start_scraper_route(current_user = Depends(get_current_user)):
    res = start_scraper(current_user.id)
    return RedirectResponse(url="/scraper/dashboard", status_code=303)

@router.post("/stop")
async def stop_scraper_route(current_user = Depends(get_current_user)):
    res = stop_scraper()
    return RedirectResponse(url="/scraper/dashboard", status_code=303)

@router.get("/status")
async def scraper_status(current_user = Depends(get_current_user)):
    return JSONResponse(content=get_scraper_status())
