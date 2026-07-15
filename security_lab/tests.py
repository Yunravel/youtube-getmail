from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .core import EmailSecurityLab


class EmailSecurityLabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.lab = EmailSecurityLab(Path(self.temp.name) / "lab.sqlite3")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def view(
        self,
        request_id: str,
        *,
        ip: str = "198.51.100.10",
        device: str = "device-a",
        session: str = "session-a",
        grant: str = "",
    ):
        return self.lab.view_email(
            auth_token="lab-token-alice",
            channel_id="channel-001",
            ip=ip,
            device_id=device,
            session_id=session,
            request_id=request_id,
            captcha_grant=grant,
        )

    def exhaust_account_quota(self) -> None:
        for index in range(20):
            self.assertEqual(self.view(f"quota-{index}").status, 200)

    def test_account_daily_limit_returns_simulated_captcha(self):
        self.exhaust_account_quota()
        blocked = self.view("quota-blocked")
        self.assertEqual(blocked.status, 429)
        self.assertEqual(blocked.body["error"], "captcha_required")
        self.assertIn("account", blocked.body["limited_dimensions"])
        self.assertNotIn("email", blocked.body)

    def test_concurrent_burst_cannot_win_check_increment_race(self):
        def request(index: int):
            return self.view(f"race-{index}")

        with ThreadPoolExecutor(max_workers=32) as executor:
            responses = list(executor.map(request, range(80)))
        successes = [response for response in responses if response.status == 200]
        blocked = [response for response in responses if response.status == 429]
        self.assertEqual(len(successes), 20)
        self.assertEqual(len(blocked), 60)
        self.assertEqual(self.lab.counter("account", "alice"), 20)

    def test_rotating_ip_device_and_session_does_not_bypass_account_limit(self):
        self.exhaust_account_quota()
        for index in range(10):
            response = self.view(
                f"rotate-{index}",
                ip=f"203.0.113.{index + 1}",
                device=f"device-{index}",
                session=f"session-{index}",
            )
            self.assertEqual(response.status, 429)
            self.assertNotIn("email", response.body)
        self.assertEqual(self.lab.counter("account", "alice"), 20)

    def test_request_id_replay_does_not_increment_or_return_email(self):
        first = self.view("same-request")
        replay = self.view("same-request")
        self.assertEqual(first.status, 200)
        self.assertEqual(replay.status, 409)
        self.assertNotIn("email", replay.body)
        self.assertEqual(self.lab.counter("account", "alice"), 1)

    def test_captcha_challenge_and_grant_are_single_use(self):
        self.exhaust_account_quota()
        challenge_response = self.view("challenge-request")
        challenge = str(challenge_response.body["challenge_token"])
        solved = self.lab.verify_captcha(
            auth_token="lab-token-alice",
            challenge_token=challenge,
            answer="LAB-OK",
            ip="198.51.100.10",
            device_id="device-a",
            session_id="session-a",
        )
        self.assertEqual(solved.status, 200)
        grant = str(solved.body["captcha_grant"])
        allowed = self.view("grant-use", grant=grant)
        replay = self.view("grant-replay", grant=grant)
        self.assertEqual(allowed.status, 200)
        self.assertEqual(replay.status, 403)
        self.assertEqual(replay.body["error"], "replayed_grant")
        self.assertNotIn("email", replay.body)

        challenge_replay = self.lab.verify_captcha(
            auth_token="lab-token-alice",
            challenge_token=challenge,
            answer="LAB-OK",
            ip="198.51.100.10",
            device_id="device-a",
            session_id="session-a",
        )
        self.assertEqual(challenge_replay.status, 409)

    def test_grant_cannot_move_to_another_session(self):
        self.exhaust_account_quota()
        challenge = str(self.view("binding-challenge").body["challenge_token"])
        solved = self.lab.verify_captcha(
            auth_token="lab-token-alice",
            challenge_token=challenge,
            answer="LAB-OK",
            ip="198.51.100.10",
            device_id="device-a",
            session_id="session-a",
        )
        response = self.view(
            "binding-attack",
            session="attacker-session",
            grant=str(solved.body["captcha_grant"]),
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(response.body["error"], "grant_binding_mismatch")
        self.assertNotIn("email", response.body)

    def test_invalid_auth_never_returns_email(self):
        response = self.lab.view_email(
            auth_token="stolen-looking-but-invalid",
            channel_id="channel-001",
            ip="198.51.100.10",
            device_id="device-a",
            session_id="session-a",
            request_id="invalid-auth",
        )
        self.assertEqual(response.status, 401)
        self.assertNotIn("email", response.body)


if __name__ == "__main__":
    unittest.main()

