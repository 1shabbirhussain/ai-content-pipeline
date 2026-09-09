# Book Scraper Web Application

This project has been upgraded from a simple Python script into a fully functional FastAPI web application backed by MySQL. It provides a simple, robust interface to manage books, run the Playwright-based web scraper in the background, view live progress logs, and download the scraped data as an `.xlsx` file.

## Features
- **MySQL Source of Truth**: Replaces the old Excel-based input queue.
- **Admin Dashboard**: Add, edit, and delete books manually. Monitor scraper health.
- **User Dashboard**: Start the scraper, monitor live progress via UI, and download results.
- **Graceful Failure Handling**: If a book fails during the scraping process, the background daemon logs the error and immediately continues to the next book.

## Setup Requirements
- Python 3.10+
- MySQL (Running locally or remotely)
- Google Chrome / Chromium (installed automatically via Playwright)

## Installation & Setup

1. **Clone the repository & create a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. **Install the dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Playwright browsers:**
   ```bash
   playwright install chromium
   ```

4. **Configure the Environment Variables:**
   - Copy the example `.env` file to set up your configurations.
   ```bash
   cp .env.example .env
   ```
   - *Note*: Ensure you have a MySQL server running locally on port 3306. Update `DB_USER` and `DB_PASSWORD` in the `.env` file if necessary.

5. **Initialize the Database:**
   - First, create the database in MySQL:
     ```bash
     mysql -u root -e "CREATE DATABASE IF NOT EXISTS scraper_db;"
     ```
   - Then, run the initialization script to create the tables and the default Admin user:
     ```bash
     python app/init_db.py
     ```

## Running the Web Application

1. **Start the FastAPI Server:**
   ```bash
   uvicorn app.main:app --reload
   ```

2. **Access the Web Interface:**
   - Open your web browser and go to: [http://127.0.0.1:8000](http://127.0.0.1:8000)
   - Log in using the default admin credentials:
     - **Username**: `admin`
     - **Password**: `admin`

## Usage Workflow

1. **Add Books**: Log in as an Admin, navigate to "Manage Books", and click "Add Book". Provide the Book ID and Detail URL.
2. **Start Scraping**: Navigate to the "Scraper" dashboard. Click "Start Scraping" to trigger the background daemon. 
3. **Monitor**: Watch the live logs and progress bars in real-time.
4. **Download**: Once a book is marked as `SCRAPED`, go to the "All Books" dashboard and click the green **Download XLSX** button to retrieve the extracted book pages.

## Managing the Database Externally
If you are new to MySQL, you can easily view your tables using a GUI tool:
- Download **MySQL Workbench** or **DBeaver**.
- Connect to `127.0.0.1:3306` with your user credentials.
- Navigate to the `scraper_db` schema to view or edit the `books` and `book_pages` tables directly.
