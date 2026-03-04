import asyncio
import os

from agent_jupyter_toolkit.kernel import ServerConfig, SessionConfig, create_session


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing required env var: {name}")
    return value


async def main() -> None:
    base_url = _require_env("JUPYTER_BASE_URL")
    token = os.getenv("JUPYTER_TOKEN")
    kernel_name = os.getenv("JUPYTER_KERNEL_NAME", "python3")

    server = ServerConfig(base_url=base_url, token=token, kernel_name=kernel_name)
    session = create_session(SessionConfig(mode="server", server=server))

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
