# Toss OpenAPI fixtures

The `toss_*.json` payloads are extracted from the official examples in the
Toss Securities OpenAPI document at
`https://openapi.tossinvest.com/openapi-docs/latest/openapi.json`, version
`1.2.14`. Each endpoint payload is unmodified; `toss_buying_power_sample.json`
only groups the separate official KRW and USD examples under currency keys for
the test suite. These are specification examples, not real-account responses.
