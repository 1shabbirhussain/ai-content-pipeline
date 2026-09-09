import requests
import time
import sys

BASE_URL = "http://127.0.0.1:8000"

def run_tests():
    session = requests.Session()
    
    print("1. Login as Admin...")
    r = session.post(f"{BASE_URL}/auth/login", data={"username": "admin", "password": "admin"}, allow_redirects=False)
    if r.status_code != 302:
        print(f"Login failed! Status: {r.status_code}")
        sys.exit(1)
        
    print("2. Add a real book...")
    r = session.post(f"{BASE_URL}/admin/books/add", data={
        "book_id": "1001",
        "detail_url": "https://www.dawateislami.net/bookslibrary/ur/nehar-ki-sadain",
        "title": "Nehar Ki Sadain",
        "status": "PENDING"
    }, allow_redirects=False)
    if r.status_code != 303:
        print(f"Add book failed! Status: {r.status_code}")
        sys.exit(1)
        
    print("3. Add a book that will fail (invalid url)...")
    r = session.post(f"{BASE_URL}/admin/books/add", data={
        "book_id": "9999",
        "detail_url": "https://www.dawateislami.net/bookslibrary/ur/invalid-book-url",
        "title": "Invalid Book",
        "status": "PENDING"
    }, allow_redirects=False)
        
    print("4. Edit book URL...")
    # Get books to find the id
    r = session.get(f"{BASE_URL}/admin/books")
    if "1001" not in r.text:
        print("Book 1001 not found in books list!")
        sys.exit(1)
        
    # We know the first book id is 1 and second is 2 since DB is fresh.
    # Let's edit book 1.
    r = session.post(f"{BASE_URL}/admin/books/1/edit", data={
        "detail_url": "https://www.dawateislami.net/bookslibrary/ur/nehar-ki-sadain",
        "title": "Nehar Ki Sadain Edited",
        "status": "PENDING"
    }, allow_redirects=False)
    if r.status_code != 303:
        print(f"Edit book failed! Status: {r.status_code}")
        sys.exit(1)
        
    print("5. Start scraper...")
    r = session.post(f"{BASE_URL}/scraper/start", allow_redirects=False)
    if r.status_code != 303:
        print(f"Start scraper failed! Status: {r.status_code}")
        sys.exit(1)
        
    print("6. Waiting for scraper to finish (polling status)...")
    max_wait = 180 # 3 minutes
    start_time = time.time()
    
    while True:
        r = session.get(f"{BASE_URL}/scraper/status")
        status_data = r.json()
        print(f"   Status: {status_data.get('stats', {}).get('status')} - Done: {status_data.get('stats', {}).get('completed')} - Failed: {status_data.get('stats', {}).get('failed')}")
        if not status_data.get("is_running") or status_data.get("stats", {}).get("status") == "COMPLETED":
            break
        if time.time() - start_time > max_wait:
            print("Timeout waiting for scraper!")
            sys.exit(1)
        time.time()
        time.sleep(5)
        
    print("Scraping completed!")
    
    print("7. Verify scraped XLSX is generated...")
    r = session.get(f"{BASE_URL}/files/download/1")
    if r.status_code != 200:
        print(f"Failed to download XLSX! Status: {r.status_code}")
        sys.exit(1)
    
    print("Successfully downloaded XLSX!")
    print("All tests passed.")
    
if __name__ == "__main__":
    run_tests()
