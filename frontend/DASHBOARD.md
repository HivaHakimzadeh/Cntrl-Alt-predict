# GUARD dashboard

Run `npm ci` once, then `npm run dev`. Open the printed URL (normally
`http://localhost:5173`). The backend allows this origin; if Vite chooses a
different port, stop the other instance or update backend CORS configuration.

Start the API from `backend/` with `python -m uvicorn main:app --reload` in an
environment containing the backend dependencies. Set the dashboard endpoint
to `http://localhost:8000`, then click **Connect / refresh**.

For an API already running on LinuxONE port 8000, use an SSH tunnel:

```
ssh -L 8000:localhost:8000 linux1@148.100.109.158
```

Keep the SSH session open. Use the same localhost endpoint in the dashboard.
The selected endpoint is remembered in browser local storage.

## Demo

1. Evaluate Routine payment (known supplier).
2. Evaluate External sharing, then open Human review and approve or reject it.
3. Evaluate Injected instruction or File deletion to inspect blocking evidence.
4. Inspect previous evaluations from Audit trail; expand the proposed tool call
   to see its full input.

All scores and decisions come from the backend `/dashboard` API adapter.
The team's existing `/evaluate` contract is unchanged. Presets only fill request
inputs. No action is executed, including after human approval. Dashboard audit
history and review queue live in the API process and clear on restart. Statistics
deduplicate action IDs in the most recent 100 audit events; they are not
lifetime totals. Activity refreshes after evaluation/review and automatically
every five seconds while the dashboard is visible. Changing the endpoint pauses
polling until **Connect / refresh** is clicked.

Verify with `npm run build` and `npm run lint`.
