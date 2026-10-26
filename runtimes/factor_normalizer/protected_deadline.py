"""Fixed numeric normalizer image entrypoint; no native parser in trusted PID1."""

from runtimes.factor_execution.numeric_deadline import run_normalizer

if __name__ == "__main__":
    run_normalizer()
