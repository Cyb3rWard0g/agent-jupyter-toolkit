import asyncio

from agent_jupyter_toolkit.kernel import SessionConfig, create_session


async def main() -> None:
    session = create_session(SessionConfig(mode="local", kernel_name="python3"))
    async with session:
        result = await session.execute(
            "import os\n"
            "from platform import node\n"
            "user = os.environ.get('USER', 'friend')\n"
            "print(f'Hello {user} from {node()}')\n"
        )

    print("status:", result.status)
    print("execution_count:", result.execution_count)
    print("stdout:", result.stdout.strip())
    print("outputs:", result.outputs)


if __name__ == "__main__":
    asyncio.run(main())
