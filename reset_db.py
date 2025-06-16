#!/usr/bin/env python3

from db_models import reset_database_safe, reset_database, update_database_schema
import sys


def main():
    print("Database Reset Options:")
    print("1. Safe reset (drops tables in correct order)")
    print("2. Standard reset (uses SQLAlchemy metadata)")
    print("3. Update schema only (add missing tables/columns)")
    print("4. Exit")

    choice = input("Enter your choice (1-4): ").strip()

    if choice == "1":
        print("Performing safe database reset...")
        if reset_database_safe():
            print("✓ Database reset successfully!")
        else:
            print("✗ Database reset failed!")
            sys.exit(1)

    elif choice == "2":
        print("Performing standard database reset...")
        if reset_database():
            print("✓ Database reset successfully!")
        else:
            print("✗ Database reset failed!")
            sys.exit(1)

    elif choice == "3":
        print("Updating database schema...")
        if update_database_schema():
            print("✓ Database schema updated successfully!")
        else:
            print("✗ Database schema update failed!")
            sys.exit(1)

    elif choice == "4":
        print("Exiting...")
        sys.exit(0)

    else:
        print("Invalid choice!")
        sys.exit(1)


if __name__ == "__main__":
    main()