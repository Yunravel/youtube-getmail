import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import crawler as crawler_api
from db import Base
from services.crawler.config_rules import make_queries


class CrawlerProductsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        crawler_api._crawl_jobs.clear()

    def tearDown(self):
        crawler_api._crawl_jobs.clear()
        self.db.close()

    def test_custom_product_crud_is_persistent(self):
        created = crawler_api.create_product(
            crawler_api.CrawlerProductIn(
                name="New Product",
                keywords=["AI creator", " AI creator ", "video workflow"],
            ),
            _=None,
            db=self.db,
        )
        self.assertFalse(created["built_in"])
        self.assertEqual(created["keyword_count"], 2)

        products = crawler_api.list_products(_=None, db=self.db)
        custom = [item for item in products if not item["built_in"]]
        self.assertEqual(custom[0]["product"], "New Product")
        self.assertEqual(custom[0]["keywords"], ["AI creator", "video workflow"])

        updated = crawler_api.update_product(
            created["id"],
            crawler_api.CrawlerProductIn(name="Renamed", keywords=["one keyword"]),
            _=None,
            db=self.db,
        )
        self.assertEqual(updated["product"], "Renamed")
        self.assertEqual(updated["keyword_count"], 1)

        crawler_api.delete_product(created["id"], _=None, db=self.db)
        products = crawler_api.list_products(_=None, db=self.db)
        self.assertFalse(any(not item["built_in"] for item in products))

    def test_names_cannot_conflict_with_builtin_or_custom_product(self):
        with self.assertRaises(HTTPException) as built_in_error:
            crawler_api.create_product(
                crawler_api.CrawlerProductIn(name="dreamina", keywords=["keyword"]),
                _=None,
                db=self.db,
            )
        self.assertEqual(built_in_error.exception.status_code, 409)

        crawler_api.create_product(
            crawler_api.CrawlerProductIn(name="Custom One", keywords=["keyword"]),
            _=None,
            db=self.db,
        )
        with self.assertRaises(HTTPException) as duplicate_error:
            crawler_api.create_product(
                crawler_api.CrawlerProductIn(name="CUSTOM ONE", keywords=["other"]),
                _=None,
                db=self.db,
            )
        self.assertEqual(duplicate_error.exception.status_code, 409)

    def test_selected_custom_product_terms_are_passed_to_background_job(self):
        product = crawler_api.create_product(
            crawler_api.CrawlerProductIn(name="Future Tool", keywords=["future creator tool"]),
            _=None,
            db=self.db,
        )
        background = BackgroundTasks()
        result = crawler_api.start_crawl(
            crawler_api.CrawlIn(products=[product["product"]]),
            background,
            _=None,
            db=self.db,
        )
        self.assertTrue(result["job_id"])
        task = background.tasks[0]
        self.assertEqual(task.args[1], ["Future Tool"])
        self.assertEqual(task.args[-1], {"Future Tool": ["future creator tool"]})

        queries = make_queries(
            ["Future Tool"], {"Future Tool": ["future creator tool"]}
        )
        self.assertEqual(len(queries), 3)
        self.assertTrue(all(product_name == "Future Tool" for product_name, _ in queries))

    def test_unknown_product_is_rejected_at_start(self):
        with self.assertRaises(HTTPException) as error:
            crawler_api.start_crawl(
                crawler_api.CrawlIn(products=["Deleted Product"]),
                BackgroundTasks(),
                _=None,
                db=self.db,
            )
        self.assertEqual(error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
