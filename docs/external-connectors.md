# External connectors — source policy & endpoint register

All sources below were **verified live on 2026-09-14** (status codes and payload
shapes checked against the production services).

## Permitted sources in use

| # | Source | Base URL | Auth | Licence / terms | Used for |
|---|---|---|---|---|---|
| 1 | Enhetsregisteret (entity registry) | `https://data.brreg.no/enhetsregisteret/api` | none | NLOD | identity, address, industry, employees, homepage |
| 2 | Regnskapsregisteret (filed accounts) | `https://data.brreg.no/regnskapsregisteret` | none | NLOD | financial key figures (**only** permitted financial source) |
| 3 | Enhetsregisteret bulk CSV | `https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv` | none | NLOD | universe sampling (1 request/run) |
| 4 | NAV job vacancy feed | `https://pam-stilling-feed.nav.no` | public rotating bearer token (`/api/publicToken`) | NAV terms of use | hiring signal |

## Excluded sources (deliberately)

LinkedIn, Meta/Facebook, Glassdoor, Indeed, Google Search, and any scraped
third-party aggregator. Reasons: outside the challenge's permitted-source
policy, and every one of them requires *name-based* entity matching — the
highest-risk failure mode for wrong-company publication.

Company-owned websites are fetched **only** to evaluate the identity gates in
`identity.py`, only after robots.txt is checked, at ≤ 1 page per company.

## Endpoint register (verified paths)

| Endpoint | Method | Notes |
|---|---|---|
| `/enhetsregisteret/api/enheter/{orgnr}` | GET | 200 with entity JSON; 404 = unknown/dissolved → explicit `not_available` |
| `/regnskapsregisteret/regnskap/{orgnr}` | GET | JSON array of filed-account records; 404 = none filed. Records carry `virksomhet.organisasjonsnummer` — matched for equality before use |
| `/enhetsregisteret/api/enheter/lastned/csv` | GET | full universe CSV, ~350 MB, streamed to disk |
| `/api/publicToken` (NAV) | GET | plain text containing the JWT; rotates at irregular intervals — re-fetched per run |
| `/api/v1/feed` (NAV) | GET | jsonfeed page; `next_url` pagination; `If-Modified-Since` supported; ad orgnr in detail payload `json.employer.orgnr` |

## Politeness & budget

- Meaningful `User-Agent` on every request (kildespor/1.0 + repo URL).
- ≥ 0.3 s between requests (2 req/s ceiling) via `PoliteClient`.
- Hard request cap (default 2,000) enforced by a budget meter that *refuses*
  further requests rather than best-effort throttling.
- Every successful response snapshotted (canonical-JSON bytes + sha256) under
  `data/snapshots/` so each published fact is re-verifiable after the fact.
- Financial lookups skipped entirely for org forms with no filing duty
  (`FLI`, `ORGL`, `SAM`, `ESEK`, `KTRF`, `VOFO`, `PMSA`, `SAU`).

## Change management

If an endpoint moves (Brreg occasionally reorganises paths), the expected place
to patch is `connectors/brreg.py` / `connectors/nav.py` only; extraction maps are
declarative (`FIELD -> JSON path`) so the diff stays a one-line change. The
smoke test in the commit history documents working response shapes per endpoint.
