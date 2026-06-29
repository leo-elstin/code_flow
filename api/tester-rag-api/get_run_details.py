import asyncio
from app.orchestration.runner import CodeAgentRunner
import json

async def run():
    runner = CodeAgentRunner()
    st = await runner.get_state("d435450e-3d7a-46b5-835a-c3264148f6a8")
    if not st:
        print("Run not found")
        return
    
    verifier_summary = st.get("verifier_summary", {})
    print("Verifier Blockers:")
    for b in verifier_summary.get("blockers", []):
        print("-", b)
        
    print("\nMessages:")
    for m in st.get("messages", [])[-3:]:
        print(m)

asyncio.run(run())
