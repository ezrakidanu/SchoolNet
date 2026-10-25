"""Development server entry point for SchoolNet."""
from __future__ import annotations

import os

from schoolnet import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "3000")), debug=False)
