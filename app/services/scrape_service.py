import threading
import traceback
import os
import json
from datetime import datetime
from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database import SessionLocal
from app.models import Book, ScrapeRun, ScrapeError
from app.scraper.scraper import do_scrape_book_logic

# Global state to keep track of the run and provide live logs
class ScraperState:
    def __init__(self):
        self.is_running = False
        self.should_stop = False
        self.current_run_id = None
        self.logs = []
        
    def add_log(self, message: str):
        print(f"[Scraper] {message}")
        self.logs.append(f"{datetime.utcnow().isoformat()} - {message}")
        # keep last 1000 logs
        if len(self.logs) > 1000:
            self.logs = self.logs[-1000:]
            
    def get_logs(self):
        return self.logs

scraper_state = ScraperState()

def scraper_worker(run_id: int):
    db: Session = SessionLocal()
    run = db.query(ScrapeRun).get(run_id)
    if not run:
        db.close()
        scraper_state.is_running = False
        return
        
    run.status = "RUNNING"
    db.commit()
    scraper_state.add_log(f"Run {run.id} started")
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            
            while not scraper_state.should_stop:
                # Get next PENDING book
                book = db.query(Book).filter(
                    or_(Book.scrape_status == "PENDING", Book.scrape_status == "PROCESSING")
                ).order_by(Book.id.asc()).first()
                
                if not book:
                    scraper_state.add_log("No more PENDING books found. Run complete.")
                    run.status = "COMPLETED"
                    break
                    
                book.scrape_status = "PROCESSING"
                db.commit()
                
                try:
                    do_scrape_book_logic(
                        browser=browser,
                        db_session=db,
                        book_id_pk=book.id,
                        book_detail_url=book.detail_url,
                        log_cb=scraper_state.add_log
                    )
                    run.completed_books += 1
                    db.commit()
                except Exception as e:
                    scraper_state.add_log(f"Error on Book {book.book_id}: {str(e)}")
                    book.scrape_status = "FAILED"
                    db.commit()
                    run.failed_books += 1
                    
                    # Store error
                    error = ScrapeError(
                        run_id=run.id,
                        book_id=book.id,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        technical_details=traceback.format_exc()
                    )
                    db.add(error)
                    db.commit()
                    
                db.refresh(run)
                
            browser.close()
            
    except Exception as e:
        scraper_state.add_log(f"Fatal scraper error: {str(e)}")
        run.status = "FAILED"
    finally:
        if scraper_state.should_stop:
            run.status = "STOPPED"
            scraper_state.add_log("Scraping stopped by user")
            
        run.completed_at = datetime.utcnow()
        db.commit()
        db.close()
        
        scraper_state.is_running = False
        scraper_state.should_stop = False

def start_scraper(user_id: int):
    if scraper_state.is_running:
        return {"status": "already running"}
        
    db = SessionLocal()
    total_pending = db.query(Book).filter(Book.scrape_status == "PENDING").count()
    run = ScrapeRun(started_by=user_id, status="PENDING", total_books=total_pending)
    db.add(run)
    db.commit()
    db.refresh(run)
    db.close()
    
    scraper_state.is_running = True
    scraper_state.should_stop = False
    scraper_state.current_run_id = run.id
    scraper_state.logs = []
    
    thread = threading.Thread(target=scraper_worker, args=(run.id,), daemon=True)
    thread.start()
    
    return {"status": "started", "run_id": run.id}

def stop_scraper():
    if not scraper_state.is_running:
        return {"status": "not running"}
    scraper_state.should_stop = True
    return {"status": "stopping"}

def get_scraper_status():
    db = SessionLocal()
    status_data = {
        "is_running": scraper_state.is_running,
        "should_stop": scraper_state.should_stop,
        "logs": scraper_state.get_logs(),
        "stats": {}
    }
    if scraper_state.current_run_id:
        run = db.query(ScrapeRun).get(scraper_state.current_run_id)
        if run:
            status_data["stats"] = {
                "total": run.total_books,
                "completed": run.completed_books,
                "failed": run.failed_books,
                "status": run.status
            }
            
    # Also get global book stats
    status_data["global_stats"] = {
        "total": db.query(Book).count(),
        "pending": db.query(Book).filter(Book.scrape_status == "PENDING").count(),
        "processing": db.query(Book).filter(Book.scrape_status == "PROCESSING").count(),
        "scraped": db.query(Book).filter(Book.scrape_status == "SCRAPED").count(),
        "failed": db.query(Book).filter(Book.scrape_status == "FAILED").count(),
    }
    db.close()
    return status_data
