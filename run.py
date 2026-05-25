"""
run.py — Entry point for the AR agent system.

One command to run the full morning briefing:
    python run.py

What it does:
    1. Runs the batch (analyzes all 50 customers, saves results JSON)
    2. Generates the dated morning briefing from the results
    3. Saves the briefing to briefings/briefing_YYYY-MM-DD.md

The batch costs ~$0.50 in Claude API calls and takes ~7 minutes.
To regenerate the briefing from existing results without re-running
the agent (free, instant), run:
    python -m src.briefing
"""

from src.orchestrator import run_batch
from src.briefing import generate_briefing


def main():
    print("AR Agent — Morning Briefing Run")
    print("=" * 60)
    print("Step 1: Running batch analysis...")
    results = run_batch(verbose=True)

    print("\nStep 2: Generating briefing...")
    generate_briefing(results, save=True)

    print("\nDone. Open briefings/ to view today's report.")


if __name__ == "__main__":
    main()