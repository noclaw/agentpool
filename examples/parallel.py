"""
Parallel execution — two agents working on independent tasks simultaneously.

Setup:
    cp .env.example .env    # then fill in your token
    pip install -e "..[sdk]"

Run:
    python parallel.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

from agentpool import AgentPool, AgentPoolConfig, Task

load_dotenv()


async def main():
    config = AgentPoolConfig(
        max_agents=2,
        default_model="claude-sonnet-4-5",
        log_level="WARNING",
        timeout=60,
    )

    async with AgentPool(config=config, workspace=Path(".")) as pool:
        pool.submit(Task(
            prompt="What is the capital of France? Reply with just the city name.",
            agent_id="geo",
        ))
        pool.submit(Task(
            prompt="What is 10 * 7? Reply with just the number.",
            agent_id="math",
        ))
        results = await pool.run()

    for r in results:
        print(f"[{r.agent_id}] {r.status.value} ({r.duration_seconds:.1f}s): {r.response[:100]}")


if __name__ == "__main__":
    asyncio.run(main())
