# trex-web-gui frontend

React + TypeScript + Vite single-page app. `make build` builds it into `frontend/dist`, which the FastAPI backend
serves; see [`CONTRIBUTING.md`](../CONTRIBUTING.md#commands) for the commands.

To develop it, run both processes: `make dev` starts the backend on `127.0.0.1:8765`, and
`npm --prefix frontend run dev` starts Vite on `:5173`, which proxies `/api` to the backend. Vite alone is not a
usable app: open the `:5173` login URL the backend prints.
