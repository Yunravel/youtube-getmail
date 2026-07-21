import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Kol
from services.snov_contacts import sync_snov_contacts


class FakeSnovContactsClient:
    def list_prospect_lists(self):
        return [
            {"id": 10, "name": "Creators", "isDeleted": False},
            {"id": 11, "name": "Deleted", "isDeleted": True},
        ]

    def get_prospects_in_list(self, list_id, page=1, per_page=5000):
        return {
            "list": {"contacts": 2},
            "prospects": [
                {
                    "id": "prospect-1",
                    "name": "Ada Lovelace",
                    "firstName": "Ada",
                    "lastName": "Lovelace",
                    "country": "United Kingdom",
                    "locality": "London",
                    "position": "Content Creator",
                    "companyName": "Ada Studio",
                    "companySite": "https://ada.example",
                    "phones": ["+44 20 1234 5678"],
                    "socialLinks": {"linkedIn": "https://linkedin.com/in/ada"},
                    "emails": [{"email": "Ada@Example.com"}],
                    "customFields": {
                        "platform": "YouTube",
                        "socialHandle": "@ada",
                        "profileUrl": "https://youtube.com/@ada",
                        "followers": "125,000",
                        "priority": "p1",
                        "contentCategory": "Tech",
                        "source": "Snov",
                        "notes": "Preferred creator",
                    },
                },
                {"id": "prospect-no-email", "name": "Skipped", "emails": []},
            ],
        }


class SnovContactsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_template_fields_are_mapped_and_sync_is_idempotent(self):
        client = FakeSnovContactsClient()
        first = sync_snov_contacts(self.db, client)
        second = sync_snov_contacts(self.db, client)

        self.assertEqual(first["created"], 1)
        self.assertEqual(first["skipped"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(self.db.query(Kol).count(), 1)

        kol = self.db.query(Kol).one()
        self.assertEqual(kol.email, "ada@example.com")
        self.assertEqual(kol.full_name, "Ada Lovelace")
        self.assertEqual(kol.first_name, "Ada")
        self.assertEqual(kol.last_name, "Lovelace")
        self.assertEqual(kol.followers, 125000)
        self.assertEqual(kol.priority, "P1")
        self.assertEqual(kol.profile_url, "https://youtube.com/@ada")
        self.assertEqual(kol.snov_list_ids, ["10"])


if __name__ == "__main__":
    unittest.main()
