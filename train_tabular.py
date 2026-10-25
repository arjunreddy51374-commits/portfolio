"""Train and evaluate the project's tabular WDBC model candidates.

This command writes held-out and cross-validation results to
``data/model_evaluation.json``. The Flask app fits its live demonstration model
at startup; no serialized model file is required.
"""
from ml.evaluate import main


if __name__ == "__main__":
    main()