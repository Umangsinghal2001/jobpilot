"""
L0 — Agent core proof-of-life.

Proves the foundation: a headless "clone of Claude" that
  (1) runs on your Claude SUBSCRIPTION (no API key),
  (2) uses a tool (WebSearch), and
  (3) writes a file to disk.

If this runs green, the whole product is viable and we build up from here.
Needs the Claude Code CLI logged in (Pro/Max).
"""
import asyncio
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, TextBlock, ResultMessage

HERE = Path(__file__).parent
OUT = HERE / "_proof.txt"


async def main() -> None:
    options = ClaudeAgentOptions(
        allowed_tools=["WebSearch", "Write"],   # let the agent search + write, no prompts
        permission_mode="acceptEdits",          # auto-accept the file write
        model="sonnet",                         # cheap worker for daily runs (current Sonnet)
        cwd=str(HERE),
    )
    prompt = (
        "Search the web for 'AI engineer jobs India remote 2026'. "
        f"Then write the 3 most relevant listings to '{OUT.name}' — one per line, "
        "each as 'Title — Company — URL'. Keep it short, no extra commentary."
    )

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print("AGENT:", block.text[:300])
        elif isinstance(message, ResultMessage):
            print("\nRESULT:", getattr(message, "terminal_reason", message.result))

    print("\n--- _proof.txt ---")
    print(OUT.read_text() if OUT.exists() else "NOT WRITTEN — check auth / allowed_tools below")


if __name__ == "__main__":
    asyncio.run(main())
