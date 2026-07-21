import unittest
from urllib.parse import parse_qs

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Kol
from services.snov_client import SnovClient
from services.snov_export import SnovListCreateError, create_snov_list_from_kols


class FakeSnovExportClient:
    def __init__(self, *, fail_create=False):
        self.fail_create = fail_create
        self.created = []
        self.added = []

    def create_prospect_list(self, name):
        self.created.append(name)
        if self.fail_create:
            raise RuntimeError("list API unavailable")
        return [{"success": True, "data": {"id": 7654321}}]

    def add_prospect_to_list(self, list_id, prospect):
        self.added.append((list_id, prospect))
        if prospect["email"] == "fail@example.com":
            return {"success": False, "errors": ["rejected"]}
        if prospect["email"] == "existing@example.com":
            return {"success": True, "id": "existing-id", "added": False, "updated": True}
        return {"success": True, "id": "new-id", "added": True, "updated": False}


class SnovExportTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def add_kol(self, email, *, status="pending", **fields):
        kol = Kol(name=fields.pop("name", "Creator"), email=email, status=status, **fields)
        self.db.add(kol)
        self.db.commit()
        return kol

    def test_partial_success_skips_ineligible_and_updates_snov_metadata(self):
        added = self.add_kol(
            "NEW@Example.com",
            full_name="New Creator",
            first_name="New",
            last_name="Creator",
            phones="+1 111; +1 222",
            country="United States",
            locality="Austin",
            position="Creator",
            company_name="Studio",
            company_site="https://studio.example",
            linkedin_url="https://linkedin.com/in/new",
        )
        existing = self.add_kol("existing@example.com", snov_list_ids=["10"])
        failed = self.add_kol("fail@example.com")
        sent = self.add_kol("sent@example.com", status="sent")
        missing_email = self.add_kol("")
        client = FakeSnovExportClient()

        result = create_snov_list_from_kols(
            self.db,
            client,
            list_name="待发送-测试",
            kol_ids=[added.id, existing.id, failed.id, sent.id, missing_email.id, added.id, 999999],
        )

        self.assertEqual(client.created, ["待发送-测试"])
        self.assertEqual(result["list_id"], "7654321")
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["skipped"], 4)
        self.assertEqual(result["successful_kol_ids"], [added.id, existing.id])

        self.db.refresh(added)
        self.db.refresh(existing)
        self.db.refresh(failed)
        self.assertEqual(added.status, "pending")
        self.assertEqual(added.snov_prospect_id, "new-id")
        self.assertEqual(added.snov_list_id, "7654321")
        self.assertEqual(added.snov_list_name, "待发送-测试")
        self.assertEqual(existing.snov_list_ids, ["10", "7654321"])
        self.assertIsNone(failed.snov_list_id)

        payload = client.added[0][1]
        self.assertEqual(payload["email"], "new@example.com")
        self.assertEqual(payload["fullName"], "New Creator")
        self.assertEqual(payload["phones"], ["+1 111", "+1 222"])
        self.assertEqual(payload["linkedin_url"], "https://linkedin.com/in/new")

    def test_no_eligible_contacts_does_not_create_empty_list(self):
        sent = self.add_kol("sent@example.com", status="sent")
        client = FakeSnovExportClient()
        result = create_snov_list_from_kols(
            self.db, client, list_name="empty", kol_ids=[sent.id, 123456]
        )
        self.assertEqual(client.created, [])
        self.assertIsNone(result["list_id"])
        self.assertEqual(result["skipped"], 2)

    def test_list_creation_failure_does_not_update_local_record(self):
        kol = self.add_kol("creator@example.com")
        with self.assertRaises(SnovListCreateError):
            create_snov_list_from_kols(
                self.db,
                FakeSnovExportClient(fail_create=True),
                list_name="will-fail",
                kol_ids=[kol.id],
            )
        self.db.refresh(kol)
        self.assertIsNone(kol.snov_list_id)
        self.assertEqual(kol.status, "pending")

    def test_client_form_payload_and_401_token_refresh(self):
        tokens_issued = []
        list_attempts = []
        add_payloads = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/oauth/access_token":
                token = f"token-{len(tokens_issued) + 1}"
                tokens_issued.append(token)
                return httpx.Response(200, json={"access_token": token, "expires_in": 3600})
            form = parse_qs(request.content.decode())
            if request.url.path == "/v1/lists":
                list_attempts.append(form)
                if form["access_token"] == ["token-1"]:
                    return httpx.Response(401, json={"error": "expired"})
                return httpx.Response(200, json=[{"success": True, "data": {"id": 55}}])
            if request.url.path == "/v1/add-prospect-to-list":
                add_payloads.append(form)
                return httpx.Response(
                    200, json={"success": True, "id": "p-55", "added": True, "updated": False}
                )
            return httpx.Response(404)

        client = SnovClient("client", "secret")
        client._client.close()
        client._client = httpx.Client(
            base_url="https://api.snov.io",
            transport=httpx.MockTransport(handler),
        )
        try:
            created = client.create_prospect_list("待发送")
            result = client.add_prospect_to_list("55", {
                "email": "creator@example.com",
                "fullName": "Creator",
                "phones": ["+1 111", "+1 222"],
                "linkedin_url": "https://linkedin.com/in/creator",
            })
        finally:
            client._client.close()

        self.assertEqual(created[0]["data"]["id"], 55)
        self.assertTrue(result["success"])
        self.assertEqual(tokens_issued, ["token-1", "token-2"])
        self.assertEqual(len(list_attempts), 2)
        self.assertEqual(add_payloads[0]["access_token"], ["token-2"])
        self.assertEqual(add_payloads[0]["updateContact"], ["true"])
        self.assertEqual(add_payloads[0]["createDuplicates"], ["false"])
        self.assertEqual(add_payloads[0]["phones"], ["+1 111", "+1 222"])


if __name__ == "__main__":
    unittest.main()
