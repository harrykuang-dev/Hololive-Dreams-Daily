"""AgentServer entry point launched by the Project Interface client."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maa.agent.agent_server import AgentServer
from daily.actions import RunDailyTask


def main():
    if len(sys.argv) < 2:
        raise SystemExit('This agent requires the socket identifier supplied by MFAAvalonia.')
    AgentServer.register_custom_action('RunDailyTask', RunDailyTask())
    AgentServer.start_up(sys.argv[-1])
    try:
        AgentServer.join()
    finally:
        AgentServer.shut_down()


if __name__ == '__main__':
    main()
