"""
Pipeline — sequential stages where each output feeds the next.

Stage 1 identifies a city, Stage 2 looks up its population.

Setup:
    cp .env.example .env    # then fill in your token
    pip install -e "..[sdk]"

Run:
    python pipeline.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

from agentpool import AgentPoolConfig, Pipeline, Stage

load_dotenv()


async def main():
    config = AgentPoolConfig(timeout=60)

    pipeline = Pipeline(
        stages=[
            Stage(
                name="research",
                prompt="What is the capital of Japan? Reply with just the city name.",
            ),
            Stage(
                name="expand",
                prompt=(
                    "The previous stage identified: {previous_response}\n\n"
                    "What is the approximate population of this city? "
                    "Reply with just the number."
                ),
            ),
        ],
        config=config,
        workspace=Path("."),
    )

    result = await pipeline.run()

    print(f"Success: {result.success}")
    print(f"Total duration: {result.total_duration:.1f}s")
    print()
    for stage_result in result.stages:
        print(f"[{stage_result.agent_id}] {stage_result.response[:100]}")


if __name__ == "__main__":
    asyncio.run(main())
