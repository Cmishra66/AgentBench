"""Locust load test for any agent that speaks the AgentBench agent protocol.

AgentBench's built-in load suite (`agentbench load`) grades every response and reports quality
under load. Use this Locust file when you want Locust's distributed workers and live web UI.

    pip install "agentbench[load]"
    agentbench agent serve --preset secure --port 8080          # or your own agent
    locust -f loadtest/locustfile.py --host http://127.0.0.1:8080 --users 50 --spawn-rate 5
"""

from __future__ import annotations

import random
import uuid

from locust import HttpUser, between, task

QUESTIONS = [
    "According to company policy, how long are invoices and payment records retained?",
    "How quickly are express shipments delivered?",
    "What is the daily meal allowance in Europe?",
    "Can you remind me of my invoice amount?",
]


class AgentUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.user_id = f"locust-{uuid.uuid4().hex[:8]}"
        self.session_id = f"s-{uuid.uuid4().hex[:8]}"

    @task(4)
    def ask_policy_question(self) -> None:
        self._chat(random.choice(QUESTIONS))

    @task(1)
    def share_then_recall(self) -> None:
        amount = f"{random.uniform(100, 9000):,.2f}"
        self._chat(f"My invoice INV-LOAD-{random.randint(1000, 9999)} is for {amount}.")
        with self.client.post("/chat", json=self._body("Can you remind me of my invoice amount?"),
                              name="/chat recall", catch_response=True) as resp:
            if resp.status_code == 200 and amount not in resp.json().get("reply", ""):
                resp.failure("agent did not recall this user's own amount")

    def _body(self, message: str) -> dict:
        return {"user_id": self.user_id, "session_id": self.session_id, "message": message}

    def _chat(self, message: str) -> None:
        self.client.post("/chat", json=self._body(message), name="/chat")
