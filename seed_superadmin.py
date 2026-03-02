"""
Promote an existing user to super_admin by email.
Usage: python seed_superadmin.py <email>
"""
import sys
import os

def promote_superadmin(email):
    os.environ.setdefault("DATABASE_URL", "")
    from app import app
    from models import db, User

    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if not user:
            print(f"No user found with email: {email}")
            print("Please register the user first via /registro, then run this script.")
            sys.exit(1)

        if user.role == 'super_admin':
            print(f"User {email} is already super_admin.")
            return

        old_role = user.role
        user.role = 'super_admin'
        db.session.commit()
        print(f"User {email} promoted from '{old_role}' to 'super_admin'.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python seed_superadmin.py <email>")
        sys.exit(1)
    promote_superadmin(sys.argv[1])
