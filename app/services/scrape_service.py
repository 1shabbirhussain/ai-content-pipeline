import threading
import traceback
import os
import json
import queue
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
        self.log_lock = threading.Lock()
        
    def add_log(self, message: str):
        with self.log_lock:
            print(f"[Scraper] {message}")
            self.logs.append(f"{datetime.utcnow().isoformat()} - {message}")
            if len(self.logs) > 1000:
                self.logs = self.logs[-1000:]
            
    def get_logs(self):
        with self.log_lock:
            return list(self.logs)

scraper_state = ScraperState()
# A lock to protect ScrapeRun row updates since multiple threads will increment it
db_update_lock = threading.Lock()

def scraper_worker_thread(worker_id: int, run_id: int, job_queue: queue.Queue):
    """
    A single worker thread that runs a dedicated Chromium instance
    and pulls books from the job_queue until empty.
    """
    db = SessionLocal()
    scraper_state.add_log(f"Worker-{worker_id} started.")
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            
            while not scraper_state.should_stop:
                try:
                    book_id_pk = job_queue.get(block=False)
                except queue.Empty:
                    break # No more books in queue
                    
                # Mark book as processing
                with db_update_lock:
                    book = db.query(Book).get(book_id_pk)
                    if not book:
                        job_queue.task_done()
                        continue
                    book.scrape_status = "PROCESSING"
                    db.commit()
                    book_book_id = book.book_id
                    book_detail_url = book.detail_url
                
                scraper_state.add_log(f"Worker-{worker_id} processing Book {book_book_id}")
                
                try:
                    # do_scrape_book_logic uses the passed db_session.
                    do_scrape_book_logic(
                        browser=browser,
                        db_session=db,
                        book_id_pk=book_id_pk,
                        book_detail_url=book_detail_url,
                        log_cb=scraper_state.add_log
                    )
                    # Success
                    with db_update_lock:
                        run = db.query(ScrapeRun).get(run_id)
                        run.completed_books += 1
                        db.commit()
                        
                except Exception as e:
                    scraper_state.add_log(f"Worker-{worker_id} Error on Book {book_book_id}: {str(e)}")
                    with db_update_lock:
                        book = db.query(Book).get(book_id_pk)
                        book.scrape_status = "FAILED"
                        
                        run = db.query(ScrapeRun).get(run_id)
                        run.failed_books += 1
                        
                        error = ScrapeError(
                            run_id=run.id,
                            book_id=book.id,
                            error_type=type(e).__name__,
                            error_message=str(e),
                            technical_details=traceback.format_exc()
                        )
                        db.add(error)
                        db.commit()
                        
                finally:
                    job_queue.task_done()
                    
            browser.close()
    except Exception as e:
        scraper_state.add_log(f"Worker-{worker_id} encountered fatal error: {str(e)}")
    finally:
        db.close()
        scraper_state.add_log(f"Worker-{worker_id} shutting down.")


def scraper_manager(run_id: int):
    """
    The master thread that orchestrates the concurrent workers.
    """
    db = SessionLocal()
    run = db.query(ScrapeRun).get(run_id)
    if not run:
        db.close()
        scraper_state.is_running = False
        return
        
    run.status = "RUNNING"
    db.commit()
    scraper_state.add_log(f"Run {run.id} started. Preparing job queue...")
    
    # Get all pending books
    pending_books = db.query(Book).filter(
        or_(Book.scrape_status == "PENDING", Book.scrape_status == "PROCESSING")
    ).order_by(Book.id.asc()).all()
    
    job_queue = queue.Queue()
    for b in pending_books:
        job_queue.put(b.id)
        
    db.close()
    
    total_jobs = job_queue.qsize()
    if total_jobs == 0:
        scraper_state.add_log("No pending books found. Run complete.")
        db = SessionLocal()
        run = db.query(ScrapeRun).get(run_id)
        run.status = "COMPLETED"
        run.completed_at = datetime.utcnow()
        db.commit()
        db.close()
        scraper_state.is_running = False
        scraper_state.should_stop = False
        return

    # Determine number of concurrent workers (Max 5 for safety)
    MAX_WORKERS = 5
    num_workers = min(MAX_WORKERS, total_jobs)
    scraper_state.add_log(f"Spawning {num_workers} concurrent workers for {total_jobs} books...")
    
    threads = []
    for i in range(num_workers):
        t = threading.Thread(target=scraper_worker_thread, args=(i+1, run_id, job_queue), daemon=True)
        t.start()
        threads.append(t)
        
    # Wait for all workers to finish
    for t in threads:
        t.join()
        
    scraper_state.add_log("All workers have finished.")
    
    # Finalize run
    db = SessionLocal()
    run = db.query(ScrapeRun).get(run_id)
    if scraper_state.should_stop:
        run.status = "STOPPED"
        scraper_state.add_log("Scraping was stopped early by user.")
    else:
        run.status = "COMPLETED"
        
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
    
    thread = threading.Thread(target=scraper_manager, args=(run.id,), daemon=True)
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
            
    status_data["global_stats"] = {
        "total": db.query(Book).count(),
        "pending": db.query(Book).filter(Book.scrape_status == "PENDING").count(),
        "processing": db.query(Book).filter(Book.scrape_status == "PROCESSING").count(),
        "scraped": db.query(Book).filter(Book.scrape_status == "SCRAPED").count(),
        "failed": db.query(Book).filter(Book.scrape_status == "FAILED").count(),
    }
    db.close()
    return status_data
