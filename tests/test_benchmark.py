from prometheus_assistant.benchmark import run_acceptance_benchmark

def test_v06_acceptance_benchmark_passes():
    result=run_acceptance_benchmark()
    assert result.failed==0
    assert result.passed==7
    assert result.failures==()
