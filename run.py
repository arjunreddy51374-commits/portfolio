"""Convenient local launcher for the portfolio and ML research dashboard."""
from app import app

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
