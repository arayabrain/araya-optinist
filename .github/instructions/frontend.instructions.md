---
applyTo: "frontend/src/**"
---

# Frontend

- HTTP calls live under `frontend/src/api/<area>/`, use the shared axios instance in `frontend/src/utils/axios.ts`, and are dispatched from Redux thunks in `frontend/src/store/slice/`. Flag ad hoc `fetch` or a new axios instance; the one exception is an endpoint that must not send auth, which must say why (today `frontend/src/api/registration/Registration.ts`).
- No `any` or unchecked casts that hide a mismatch with a backend schema.
- Long-running actions (runs, uploads, ROI edits) show progress and surface errors.
- Do not keep whole image stacks or long time series in Redux or component state; fetch by index or range and release what is off-screen. Watch for Plotly traces built from full-resolution data.
