"""Conversation orchestration with limited rolling context and injectable API client."""
import os

SYSTEM_PROMPT = """You are BMO, a cheerful, curious, tiny robot companion inspired by a friendly game console.
Use natural, short English replies by default; respond in Korean if spoken to in Korean.
Be expressive but not overly verbose. Never claim to have moved your arms, seen the room,
read an IMU, or pressed buttons unless the host actually provides that observation/action.
Do not pretend to have private memories across restarts. You are a fan-built robot, not the
official character. Avoid quoting dialogue from the show.
"""

class BMOBrain:
    def __init__(self, client, model=None, max_turns=8):
        self.client = client
        self.model = model or os.getenv("BMO_CHAT_MODEL", "gpt-4.1-mini")
        self.max_turns = max_turns
        self.history = []

    def ask(self, message):
        message = message.strip()
        if not message:
            return ""
        candidate = self.history + [{"role": "user", "content": message}]
        recent = candidate[-self.max_turns * 2:]
        response = self.client.responses.create(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=recent,
            max_output_tokens=180,
        )
        answer = (response.output_text or "").strip()
        if answer:
            self.history = (candidate + [{"role": "assistant", "content": answer}])[-self.max_turns * 2:]
        return answer
