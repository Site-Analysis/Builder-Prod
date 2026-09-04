# Builder-Prod

Production repo for the Qnit Builders module. Features are pushed one at a time via PRs to feature branches.

## Status

| Feature | Branch | Status |
|---------|--------|--------|
| Karnataka Cadastral Explorer | `Cadestral` | In progress |

## Dev setup

```bash
cp .env.example .env   # fill values
npm install
npm run dev            # http://localhost:3000
```

Requires Keycloak running at `KEYCLOAK_URL` for auth.
