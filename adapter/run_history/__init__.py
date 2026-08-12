"""
run_history — Run-time knowledge base for flow optimization.

Records every flow execution and uses historical data to guide
demo flow generation and final flow construction.

Usage:
    from adapter.run_history import FlowRecommender, RunRecorder, RunInput

    # Before first run: get demo flow advice
    recommender = FlowRecommender()
    advice = recommender.suggest_demo("gcd", "Nangate45", {"frequency": 200})

    # After run: record results
    recorder = RunRecorder()
    recorder.save(RunInput(design="gcd", ...), flow, result, run_type="demo")

    # After demo: get final flow advice
    final = recommender.suggest_final("gcd", "Nangate45",
                                       {"frequency": 200}, demo_diagnosis)
"""
from .schema import init_db, get_conn
from .recorder import record, RunInput
from .querier import RunQuerier
from .recommender import FlowRecommender, DemoAdvice, FinalAdvice
from .report import format_demo_report

# Auto-init on import
init_db()

__all__ = [
    "FlowRecommender", "RunQuerier", "record", "RunInput",
    "DemoAdvice", "FinalAdvice", "format_demo_report",
]
