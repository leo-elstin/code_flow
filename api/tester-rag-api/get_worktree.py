import asyncio
from app.orchestration.runner import CodeAgentRunner

async def run():
    runner = CodeAgentRunner()
    st = await runner.get_state("d435450e-3d7a-46b5-835a-c3264148f6a8")
    print(st.get("worktree_path"))

asyncio.run(run())
