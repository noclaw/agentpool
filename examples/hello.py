"""
Simplest possible agentpool example — one agent, one prompt.

Setup:
    cp .env.example .env    # then fill in your token
    pip install -e "..[sdk]"

Run:
    python hello.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

from agentpool import AgentPool, Task

load_dotenv()


async def main():
    async with AgentPool(max_agents=1, workspace=Path(".")) as pool:
        pool.submit(Task(prompt="What is 2 + 2? Reply with just the number."))
        results = await pool.run()

    r = results[0]
    print(f"Status: {r.status.value}")
    print(f"Response: {r.response}")
    print(f"Duration: {r.duration_seconds:.1f}s")
    print(f"Model: {r.model_used}")


if __name__ == "__main__":
    asyncio.run(main())
