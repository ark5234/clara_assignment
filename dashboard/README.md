# Dashboard

Generate the presentation dashboard with:

```bash
python scripts/build_dashboard.py
```

To run the frontend locally:

```bash
python scripts/build_dashboard.py
python -m http.server 8000
```

Then open `http://localhost:8000/dashboard/` in a browser.
If you do not need a local server, you can also open `dashboard/index.html` directly.
The page is built entirely from the committed artifacts under `outputs/accounts/`.
