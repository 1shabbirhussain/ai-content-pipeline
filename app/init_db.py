import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine, Base
from app.models import User
from app.auth import get_password_hash
from sqlalchemy.orm import sessionmaker

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    admin = db.query(User).filter(User.username == "admin").first()
    if not admin:
        print("Creating admin user...")
        admin = User(
            username="admin",
            email="admin@example.com",
            password_hash=get_password_hash("admin"),
            role="ADMIN",
            is_active=True
        )
        db.add(admin)
        db.commit()
    else:
        print("Admin user already exists.")
        
    db.close()
    print("Database initialization complete.")

if __name__ == "__main__":
    init_db()
